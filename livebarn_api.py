"""LiveBarn OAuth and live-playback API client."""

import base64
import hashlib
import json
import sqlite3
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlparse

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from curl_cffi import requests
from playwright.async_api import async_playwright


API_ORIGIN = "https://prod-watch-oktaapi.livebarn.com"
API_ROOT = f"{API_ORIGIN}/api/v2.0.0"
WATCH_ORIGIN = "https://watch.livebarn.com"
REDIRECT_URL = f"{WATCH_ORIGIN}/authorize"
OAUTH_BASIC_TOKEN = "{{LBW_OAUTH_BASIC_TOKEN}}"
HLS_ACCESS_TOKEN = "e00e2487-cc76-4718-8153-3ce565933dd2"
TOKEN_EXPIRY_SKEW_SECONDS = 120


class LiveBarnError(RuntimeError):
    """Raised for an actionable LiveBarn authentication or playback failure."""


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def credential_fingerprint(credentials: dict[str, str]) -> str:
    """Return a non-reversible identifier used to invalidate cached OAuth state."""
    value = f"{credentials['email']}\0{credentials['password']}".encode()
    return hashlib.sha256(value).hexdigest()


def create_dpop_proof(
    key: ec.EllipticCurvePrivateKey,
    method: str,
    url: str,
    now: int | None = None,
) -> str:
    """Create an ES256 DPoP proof for a LiveBarn request URL."""
    public_numbers = key.public_key().public_numbers()
    header = {
        "alg": "ES256",
        "typ": "dpop+jwt",
        "jwk": {
            "kty": "EC",
            "crv": "P-256",
            "x": _base64url(public_numbers.x.to_bytes(32, "big")),
            "y": _base64url(public_numbers.y.to_bytes(32, "big")),
        },
    }
    claims = {
        "jti": str(uuid.uuid4()),
        "htm": method.upper(),
        "htu": url,
        "iat": int(time.time()) if now is None else now,
    }
    protected = _base64url(
        json.dumps(header, separators=(",", ":")).encode("utf-8")
    )
    payload = _base64url(
        json.dumps(claims, separators=(",", ":")).encode("utf-8")
    )
    signing_input = f"{protected}.{payload}".encode("ascii")
    der_signature = key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
    r_value, s_value = decode_dss_signature(der_signature)
    signature = r_value.to_bytes(32, "big") + s_value.to_bytes(32, "big")
    return f"{protected}.{payload}.{_base64url(signature)}"


def first_playlist_url(master_url: str, playlist_text: str) -> str:
    """Return the first playable child URL from an HLS master playlist."""
    for line in playlist_text.splitlines():
        candidate = line.strip()
        if candidate and not candidate.startswith("#"):
            return urljoin(master_url, candidate)
    raise LiveBarnError("LiveBarn returned an empty playback playlist")


class LiveBarnClient:
    """Authenticate once, cache the DPoP-bound token, and call playback APIs."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.session = requests.Session(impersonate="chrome")
        self.key: ec.EllipticCurvePrivateKey | None = None
        self.access_token = ""
        self.user_id = ""

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS livebarn_oauth_session (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                credential_fingerprint TEXT NOT NULL,
                access_token TEXT NOT NULL,
                expires_at REAL NOT NULL,
                dpop_private_key TEXT NOT NULL,
                user_id TEXT NOT NULL
            )
            """
        )
        conn.commit()

    def _load_cached_session(self, credentials: dict[str, str]) -> bool:
        with sqlite3.connect(self.db_path, timeout=3) as conn:
            self._ensure_schema(conn)
            row = conn.execute(
                """
                SELECT credential_fingerprint, access_token, expires_at,
                       dpop_private_key, user_id
                FROM livebarn_oauth_session
                WHERE id = 1
                """
            ).fetchone()
        if not row:
            return False
        if row[0] != credential_fingerprint(credentials):
            return False
        if float(row[2]) <= time.time() + TOKEN_EXPIRY_SKEW_SECONDS:
            return False
        try:
            key = serialization.load_pem_private_key(row[3].encode(), password=None)
        except (TypeError, ValueError):
            return False
        if not isinstance(key, ec.EllipticCurvePrivateKey):
            return False
        self.access_token = row[1]
        self.key = key
        self.user_id = row[4]
        return True

    def _save_session(
        self,
        credentials: dict[str, str],
        expires_at: float,
    ) -> None:
        if self.key is None:
            raise LiveBarnError("LiveBarn authentication key was not initialized")
        private_key = self.key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("ascii")
        with sqlite3.connect(self.db_path, timeout=3) as conn:
            self._ensure_schema(conn)
            conn.execute(
                """
                INSERT INTO livebarn_oauth_session (
                    id, credential_fingerprint, access_token, expires_at,
                    dpop_private_key, user_id
                ) VALUES (1, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    credential_fingerprint = excluded.credential_fingerprint,
                    access_token = excluded.access_token,
                    expires_at = excluded.expires_at,
                    dpop_private_key = excluded.dpop_private_key,
                    user_id = excluded.user_id
                """,
                (
                    credential_fingerprint(credentials),
                    self.access_token,
                    expires_at,
                    private_key,
                    self.user_id,
                ),
            )
            conn.commit()

    def _headers(self, method: str, url: str, authenticated: bool = True) -> dict:
        if self.key is None:
            raise LiveBarnError("LiveBarn authentication key was not initialized")
        headers = {
            "Accept": "application/json",
            "DPoP": create_dpop_proof(self.key, method, url),
            "Origin": WATCH_ORIGIN,
            "Referer": f"{WATCH_ORIGIN}/",
        }
        if authenticated:
            headers["Authorization"] = f"Bearer {self.access_token}"
        return headers

    def _api_get(self, path: str, **params):
        url = f"{API_ROOT}{path}"
        response = self.session.get(
            url,
            params=params or None,
            headers=self._headers("GET", url),
            timeout=30,
        )
        if not response.ok:
            raise LiveBarnError(
                f"LiveBarn API request failed ({response.status_code}) for {path}"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise LiveBarnError("LiveBarn API returned an invalid response") from exc

    async def authenticate(self, credentials: dict[str, str]) -> None:
        """Use a cached 12-hour token or perform browser-assisted Auth0 login."""
        if self._load_cached_session(credentials):
            return

        self.key = ec.generate_private_key(ec.SECP256R1())
        migration_url = f"{API_ROOT}/migration/check"
        migration_response = self.session.post(
            migration_url,
            headers={
                **self._headers("POST", migration_url, authenticated=False),
                "Content-Type": "application/json",
            },
            json={"email": credentials["email"]},
            timeout=30,
        )
        try:
            migration_data = migration_response.json()
        except ValueError:
            migration_data = {}
        if not migration_response.ok:
            raise LiveBarnError(
                f"LiveBarn migration check failed ({migration_response.status_code})"
            )
        if migration_data.get("migrated") is False:
            raise LiveBarnError(
                "LiveBarn does not recognize the saved email as a migrated account. "
                "Verify it exactly matches your https://watch.livebarn.com login, "
                "then sign in there once and complete any prompts before retrying."
            )

        code = await self._acquire_authorization_code(credentials)
        token_url = f"{API_ORIGIN}/oauth2/token"
        token_response = self.session.post(
            token_url,
            headers={
                **self._headers("POST", token_url, authenticated=False),
                "Authorization": f"Basic {OAUTH_BASIC_TOKEN}",
                "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
            },
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URL,
                "device_id": str(uuid.uuid4()),
            },
            timeout=30,
        )
        try:
            token_data = token_response.json()
        except ValueError:
            token_data = {}
        if not token_response.ok or not token_data.get("access_token"):
            raise LiveBarnError(
                f"LiveBarn token exchange failed ({token_response.status_code})"
            )

        self.access_token = token_data["access_token"]
        account = self._api_get("/user/getAccount")
        self.user_id = str(account.get("user", {}).get("id", ""))
        if not self.user_id:
            raise LiveBarnError("LiveBarn account response did not include a user ID")
        expires_at = time.time() + int(token_data.get("expires_in", 0))
        self._save_session(credentials, expires_at)

    async def _acquire_authorization_code(
        self,
        credentials: dict[str, str],
    ) -> str:
        """Complete the WAF-protected Auth0 step and capture its one-time code."""
        if self.key is None:
            raise LiveBarnError("LiveBarn authentication key was not initialized")
        code_url = ""

        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                context = await browser.new_context()
                page = await context.new_page()

                def capture_code(request) -> None:
                    nonlocal code_url
                    parsed = urlparse(request.url)
                    query = parse_qs(parsed.query)
                    if (
                        parsed.hostname == "watch.livebarn.com"
                        and parsed.path == "/authorize"
                        and query.get("code")
                    ):
                        code_url = request.url

                page.on("request", capture_code)
                await context.route(
                    f"{API_ORIGIN}/oauth2/token", lambda route: route.abort()
                )

                start_path = "/oauth/auth/start"
                start_url = (
                    f"{API_ORIGIN}{start_path}?"
                    + urlencode({"redirect_uri": REDIRECT_URL, "prompt": "login"})
                )
                start_response = await context.request.get(
                    start_url,
                    headers={
                        "Accept": "application/json",
                        "DPoP": create_dpop_proof(
                            self.key, "GET", f"{API_ORIGIN}{start_path}"
                        ),
                        "Origin": WATCH_ORIGIN,
                        "Referer": f"{WATCH_ORIGIN}/",
                    },
                    timeout=30000,
                )
                if not start_response.ok:
                    await browser.close()
                    raise LiveBarnError(
                        f"LiveBarn OAuth initialization failed ({start_response.status})"
                    )

                auth_url = (
                    f"{API_ORIGIN}/oauth2/authorization/auth0?ui_locales=en&"
                    f"login_hint={quote(credentials['email'])}"
                )
                await page.goto(
                    auth_url, wait_until="domcontentloaded", timeout=30000
                )
                await page.locator('input[name="password"]').wait_for(timeout=15000)
                await page.fill('input[name="password"]', credentials["password"])
                await page.locator('button[type="submit"]').click()

                deadline = time.monotonic() + 30
                while not code_url and time.monotonic() < deadline:
                    await page.wait_for_timeout(200)
                await browser.close()
        except LiveBarnError:
            raise
        except Exception as exc:
            raise LiveBarnError(
                "LiveBarn sign-in could not complete; verify the saved credentials"
            ) from exc

        code = (parse_qs(urlparse(code_url).query).get("code") or [""])[0]
        if not code:
            raise LiveBarnError(
                "LiveBarn sign-in was rejected; verify the saved credentials"
            )
        return code

    async def get_live_playlist(
        self,
        surface_id: int,
        feed_mode_id: int,
        credentials: dict[str, str],
        pin: str | None = None,
    ) -> str:
        """Return a header-free child HLS playlist URL for a live surface."""
        await self.authenticate(credentials)
        params: dict[str, object] = {"feedModeId": feed_mode_id}
        if pin:
            params["code"] = pin
        live = self._api_get(
            f"/surface/akamai/surfaceid/{surface_id}",
            **params,
        )
        if live.get("privateSession"):
            if pin:
                raise LiveBarnError("PIN code was rejected by LiveBarn")
            raise LiveBarnError(
                "Surface requires a PIN code — set one in the favorites panel"
            )
        required = ("url", "venueUuid", "streamName", "dns", "streamingToken")
        if any(not live.get(name) for name in required):
            raise LiveBarnError("LiveBarn did not return an active playback stream")

        master_response = self.session.get(
            live["url"],
            params={
                "venueuuid": live["venueUuid"],
                "streamname": live["streamName"],
                "ip": live["dns"],
            },
            headers={
                "X-LB-ACCESS-TOKEN": HLS_ACCESS_TOKEN,
                "X-LB-STREAMING-TOKEN": live["streamingToken"],
                "X-LB-USER-ID": self.user_id,
            },
            timeout=30,
        )
        if not master_response.ok:
            raise LiveBarnError(
                f"LiveBarn playlist request failed ({master_response.status_code})"
            )
        return first_playlist_url(str(master_response.url), master_response.text)

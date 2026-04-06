#!/usr/bin/env python3
"""
Refresh a single stream by surface_id.
Used by livebarn_manager.py for mode-aware on-demand refresh.
"""

import asyncio
import json
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright

DB_PATH = Path(os.getenv('DB_PATH', '/data/livebarn.db'))
WATCH_URL = 'https://watch.livebarn.com'

FEED_MODE_IDS = {
    'pano': 4,
    'auto': 5,
}
FEED_MODE_NAMES = {value: key for key, value in FEED_MODE_IDS.items()}


def normalize_feed_mode(mode: str | None) -> str:
    """Normalize user input to one of default/pano/auto."""
    value = (mode or 'default').strip().lower()
    if value in {'default', 'pano', 'auto'}:
        return value
    return 'default'


def ensure_runtime_schema(conn: sqlite3.Connection) -> None:
    """Add mode-related columns when running against an older database."""
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(favorites)")
    favorite_columns = {row[1] for row in cursor.fetchall()}
    if 'preferred_feed_mode' not in favorite_columns:
        cursor.execute(
            "ALTER TABLE favorites ADD COLUMN preferred_feed_mode TEXT NOT NULL DEFAULT 'default'"
        )

    cursor.execute("PRAGMA table_info(surface_streams)")
    stream_columns = {row[1] for row in cursor.fetchall()}
    if 'feed_mode' not in stream_columns:
        cursor.execute("ALTER TABLE surface_streams ADD COLUMN feed_mode TEXT")
    if 'feed_mode_id' not in stream_columns:
        cursor.execute("ALTER TABLE surface_streams ADD COLUMN feed_mode_id INTEGER")
    conn.commit()


def get_credentials():
    """Get credentials from environment or JSON file."""
    email = os.getenv('LIVEBARN_EMAIL')
    password = os.getenv('LIVEBARN_PASSWORD')

    if email and password:
        return {'email': email, 'password': password}

    creds_file = Path(__file__).parent / 'livebarn_credentials.json'
    if creds_file.exists():
        with open(creds_file) as f:
            return json.load(f)

    raise ValueError(
        "No credentials found. Set LIVEBARN_EMAIL and LIVEBARN_PASSWORD environment variables."
    )


def get_surface_details(surface_id: int) -> tuple[str, str, str]:
    """Return venue name, surface name, and preferred mode."""
    conn = sqlite3.connect(DB_PATH)
    ensure_runtime_schema(conn)
    cursor = conn.cursor()
    cursor.execute(
        '''
        SELECT
            v.name,
            s.name,
            COALESCE(f.preferred_feed_mode, 'default')
        FROM surfaces s
        JOIN venues v ON s.venue_id = v.id
        LEFT JOIN favorites f ON f.surface_id = s.id
        WHERE s.id = ?
        ''',
        (surface_id,),
    )
    result = cursor.fetchone()
    conn.close()

    if not result:
        raise ValueError(f"Surface {surface_id} not found")

    venue_name, surface_name, preferred_mode = result
    return venue_name, surface_name, normalize_feed_mode(preferred_mode)


def extract_playlist_candidate(url: str) -> str | None:
    """Return the top-level signed HLS playlist URL when present."""
    if 'cdn-akamai-livebarn.akamaized.net' not in url:
        return None
    if '.m3u8' not in url or 'hdnts=' not in url:
        return None
    if 'chunklist_' in url.lower():
        return None
    return url


async def read_edge_server(page) -> tuple[int | None, str]:
    """Read LiveBarn's current player edge state from sessionStorage."""
    raw = await page.evaluate('''() => sessionStorage.getItem("edgeServer") || ""''')
    if not raw:
        return None, ''

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None, ''

    mode_id = parsed.get('feedModeId')
    file_url = parsed.get('file', '')
    return mode_id, file_url


async def wait_for_playlist_capture(
    captured_urls: list[str],
    timeout_seconds: float,
    previous_url: str | None = None,
) -> str | None:
    """Wait for a newly observed signed playlist URL."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        for url in reversed(captured_urls):
            if previous_url and url == previous_url:
                continue
            return url
        await asyncio.sleep(0.2)
    return None


async def click_requested_mode(page, requested_mode: str) -> bool:
    """Switch the LiveBarn player to the requested mode."""
    if requested_mode == 'default':
        return False

    label = requested_mode.upper()
    locator = page.get_by_text(label, exact=True).first
    try:
        await locator.wait_for(state='visible', timeout=5000)
        await locator.click(timeout=5000)
    except Exception:
        return False

    await page.wait_for_timeout(2500)
    return True


async def login_and_capture_stream(
    surface_id: int,
    requested_mode: str,
    creds: dict,
) -> tuple[str, str, int | None]:
    """
    Log into LiveBarn, optionally switch the player mode, and capture the signed
    top-level playlist URL requested by the web player.
    """
    captured_urls: list[str] = []
    page_text = ''

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        def handle_request(request):
            candidate = extract_playlist_candidate(request.url)
            if candidate:
                captured_urls.append(candidate)

        async def handle_response(response):
            candidate = extract_playlist_candidate(response.url)
            if candidate:
                captured_urls.append(candidate)

        page.on('request', handle_request)
        page.on('response', handle_response)

        await page.goto(WATCH_URL, wait_until='domcontentloaded')
        await page.fill('input[name="username"]', creds['email'])
        await page.fill('input[type="password"]', creds['password'])
        await page.click('button:has-text("LOG IN")')
        await page.wait_for_timeout(3000)

        await page.goto(
            f'{WATCH_URL}/en/video/{surface_id}/live',
            wait_until='domcontentloaded',
            timeout=30000,
        )
        await page.wait_for_timeout(3000)
        initial_url = await wait_for_playlist_capture(captured_urls, timeout_seconds=10)
        page_text = await page.evaluate('''() => document.body.innerText''')

        if requested_mode in {'pano', 'auto'}:
            previous_url = initial_url
            if await click_requested_mode(page, requested_mode):
                switched_url = await wait_for_playlist_capture(
                    captured_urls,
                    timeout_seconds=8,
                    previous_url=previous_url,
                )
                if switched_url:
                    initial_url = switched_url

        resolved_mode_id, _ = await read_edge_server(page)
        await browser.close()

    if not initial_url:
        if 'This live stream has ended' in page_text:
            raise RuntimeError("LiveBarn reports that this live stream has ended")
        raise RuntimeError("Failed to capture LiveBarn playback playlist URL")

    resolved_mode = FEED_MODE_NAMES.get(resolved_mode_id, 'default')
    return initial_url, resolved_mode, resolved_mode_id


async def refresh_single_stream(surface_id: int, requested_mode: str | None = None):
    """Refresh a single stream, preferring the requested or configured feed mode."""
    venue_name, surface_name, preferred_mode = get_surface_details(surface_id)
    mode_request = normalize_feed_mode(requested_mode or preferred_mode)
    creds = get_credentials()
    captured_url, resolved_mode, resolved_mode_id = await login_and_capture_stream(
        surface_id,
        mode_request,
        creds,
    )

    if mode_request in {'pano', 'auto'} and resolved_mode != mode_request:
        raise RuntimeError(
            f"Requested feed mode {mode_request} is unavailable; resolved {resolved_mode} instead"
        )

    conn = sqlite3.connect(DB_PATH)
    ensure_runtime_schema(conn)
    cursor = conn.cursor()
    cursor.execute(
        '''
        INSERT OR REPLACE INTO surface_streams (
            surface_id,
            venue_name,
            surface_name,
            playlist_url,
            full_captured_url,
            captured_at,
            feed_mode,
            feed_mode_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            surface_id,
            venue_name,
            surface_name,
            captured_url,
            captured_url,
            datetime.now().isoformat(),
            resolved_mode,
            resolved_mode_id,
        ),
    )
    conn.commit()
    conn.close()

    print(
        f"SUCCESS: Refreshed {venue_name} - {surface_name} "
        f"(requested={mode_request}, resolved={resolved_mode})"
    )
    return True


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python refresh_single.py <surface_id> [default|pano|auto]", file=sys.stderr)
        sys.exit(1)

    surface_id = int(sys.argv[1])
    requested_mode = sys.argv[2] if len(sys.argv) > 2 else None
    try:
        success = asyncio.run(refresh_single_stream(surface_id, requested_mode))
        sys.exit(0 if success else 1)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

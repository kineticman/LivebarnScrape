"""Persist and resolve LiveBarn credentials without exposing secrets to the UI."""

import os
import sqlite3
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def ensure_credentials_schema(conn: sqlite3.Connection) -> None:
    """Create the singleton credential override table when needed."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS credential_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            email TEXT NOT NULL,
            password TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.commit()


def _clear_oauth_session(conn: sqlite3.Connection) -> None:
    """Discard any DPoP-bound token when the active credentials change."""
    exists = conn.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type = 'table' AND name = 'livebarn_oauth_session'
        """
    ).fetchone()
    if exists:
        conn.execute("DELETE FROM livebarn_oauth_session")


def _load_saved_credentials(db_path: Path) -> Optional[dict[str, str]]:
    with sqlite3.connect(db_path, timeout=3) as conn:
        ensure_credentials_schema(conn)
        row = conn.execute(
            "SELECT email, password FROM credential_settings WHERE id = 1"
        ).fetchone()
    if not row:
        return None
    return {"email": row[0], "password": row[1]}


def save_credentials(db_path: Path, email: str, password: str) -> None:
    """Save an admin-page credential override in the persistent database."""
    with sqlite3.connect(db_path, timeout=3) as conn:
        ensure_credentials_schema(conn)
        conn.execute(
            """
            INSERT INTO credential_settings (id, email, password, updated_at)
            VALUES (1, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                email = excluded.email,
                password = excluded.password,
                updated_at = excluded.updated_at
            """,
            (email, password, datetime.now(timezone.utc).isoformat()),
        )
        _clear_oauth_session(conn)
        conn.commit()


def clear_saved_credentials(db_path: Path) -> None:
    """Delete the saved override so environment credentials become active."""
    with sqlite3.connect(db_path, timeout=3) as conn:
        ensure_credentials_schema(conn)
        conn.execute("DELETE FROM credential_settings WHERE id = 1")
        _clear_oauth_session(conn)
        conn.commit()


def resolve_credentials(
    db_path: Path,
    environment: Optional[Mapping[str, str]] = None,
) -> dict[str, str]:
    """Resolve saved credentials first, then fall back to the environment."""
    try:
        saved = _load_saved_credentials(db_path)
    except sqlite3.Error:
        saved = None
    if saved and saved["email"] and saved["password"]:
        return {**saved, "source": "admin"}

    env = os.environ if environment is None else environment
    email = env.get("LIVEBARN_EMAIL", "").strip()
    password = env.get("LIVEBARN_PASSWORD", "")
    if email and password:
        return {"email": email, "password": password, "source": "environment"}

    raise ValueError(
        "No LiveBarn credentials configured. Save them in the admin page or set "
        "LIVEBARN_EMAIL and LIVEBARN_PASSWORD."
    )


def _mask_email(email: str) -> str:
    if not email:
        return ""
    local, separator, domain = email.partition("@")
    if not separator:
        return f"{local[:1]}***"
    return f"{local[:1]}***@{domain}"


def get_credential_status(
    db_path: Path,
    environment: Optional[Mapping[str, str]] = None,
) -> dict[str, object]:
    """Return secret-safe credential metadata for the admin page."""
    env = os.environ if environment is None else environment
    env_email = env.get("LIVEBARN_EMAIL", "").strip()
    env_password = env.get("LIVEBARN_PASSWORD", "")
    environment_configured = bool(env_email and env_password)
    saved = _load_saved_credentials(db_path)

    if saved and saved["email"] and saved["password"]:
        source = "admin"
        configured = True
        email_hint = _mask_email(saved["email"])
    else:
        source = "environment"
        configured = environment_configured
        email_hint = _mask_email(env_email)

    return {
        "source": source,
        "configured": configured,
        "email_hint": email_hint,
        "environment_configured": environment_configured,
        "saved_override": bool(saved),
    }

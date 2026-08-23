#!/usr/bin/env python3
"""
Refresh a single stream by surface_id.
Used by livebarn_manager.py for mode-aware on-demand refresh.
"""

import asyncio
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from credential_store import resolve_credentials
from livebarn_api import LiveBarnClient, LiveBarnError

DB_PATH = Path(os.getenv('DB_PATH', '/data/livebarn.db'))

FEED_MODE_IDS = {
    'pano': 4,
    'auto': 5,
}


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

    cursor.execute("PRAGMA table_info(favorites)")
    favorite_columns = {row[1] for row in cursor.fetchall()}
    if 'pin_code' not in favorite_columns:
        cursor.execute("ALTER TABLE favorites ADD COLUMN pin_code TEXT")

    conn.commit()


def get_credentials():
    """Get credentials from the admin override or environment."""
    return resolve_credentials(DB_PATH)


def get_surface_details(surface_id: int) -> tuple[str, str, str, str | None]:
    """Return venue name, surface name, and preferred mode."""
    conn = sqlite3.connect(DB_PATH)
    ensure_runtime_schema(conn)
    cursor = conn.cursor()
    cursor.execute(
        '''
        SELECT
            v.name,
            s.name,
            COALESCE(f.preferred_feed_mode, 'default'),
            f.pin_code
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

    venue_name, surface_name, preferred_mode, pin_code = result
    return venue_name, surface_name, normalize_feed_mode(preferred_mode), pin_code


async def login_and_capture_stream(
    surface_id: int,
    requested_mode: str,
    creds: dict,
    pin: str | None = None,
) -> tuple[str, str, int | None]:
    """
    Resolve a playback URL through LiveBarn's OAuth and playback APIs.

    The 12-hour DPoP-bound access token is cached in the runtime database, so
    normal stream refreshes use curl-cffi and do not launch a browser.
    """
    feed_mode_id = FEED_MODE_IDS.get(requested_mode, FEED_MODE_IDS['pano'])
    client = LiveBarnClient(DB_PATH)
    try:
        playlist_url = await client.get_live_playlist(
            surface_id,
            feed_mode_id,
            creds,
            pin=pin,
        )
    except LiveBarnError as exc:
        raise RuntimeError(str(exc)) from exc

    resolved_mode = requested_mode if requested_mode != 'default' else 'default'
    return playlist_url, resolved_mode, feed_mode_id


async def refresh_single_stream(surface_id: int, requested_mode: str | None = None):
    """Refresh a single stream, preferring the requested or configured feed mode."""
    venue_name, surface_name, preferred_mode, pin_code = get_surface_details(surface_id)
    mode_request = normalize_feed_mode(requested_mode or preferred_mode)
    creds = get_credentials()
    captured_url, resolved_mode, resolved_mode_id = await login_and_capture_stream(
        surface_id,
        mode_request,
        creds,
        pin=pin_code,
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

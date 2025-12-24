#!/usr/bin/env python3
"""
LiveBarn Streamlink Proxy Server with Favorites Management
Browse venues, manage favorites, auto-capture streams
"""

import sqlite3
import subprocess
import sys
import logging
import json
import time
import os
import re
import requests
from datetime import datetime, timedelta, time as dt_time
from collections import deque
from typing import List, Dict, Tuple, Optional
from flask import Flask, Response, request, jsonify, render_template_string, g
from pathlib import Path
import socket
from apscheduler.schedulers.background import BackgroundScheduler
import xml.etree.ElementTree as ET 

# Import modular schedule providers
from schedule_providers import ALL_PROVIDERS
from schedule_utils import group_events_by_surface, fill_gaps_with_open_ice 


INVALID_FS_CHARS = set('\\/:*?"<>|')

def sanitize_title_for_filesystem(text: str) -> str:
    """
    Sanitize titles/names so that downstream DVRs (like Channels) don't
    create invalid filesystem paths on Windows/exFAT.

    - Replaces control characters (including tabs/newlines) with a space
    - Replaces Windows-invalid path characters: \/:*?"<>|
    - Collapses multiple spaces
    """
    if not text:
        return ""
    cleaned_chars = []
    for ch in text:
        if ord(ch) < 32 or ch in INVALID_FS_CHARS:
            cleaned_chars.append(" ")
        else:
            cleaned_chars.append(ch)
    cleaned = "".join(cleaned_chars)
    # Collapse whitespace
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

# In-memory log buffer for UI live logs
LOG_BUFFER = deque(maxlen=500)

class LogPollFilter(logging.Filter):
    """Filter out polling and health check requests to avoid log pollution"""
    def filter(self, record):
        message = record.getMessage()
        # Exclude polling requests and health checks from logs
        if 'GET /api/logs' in message or 'GET /api/favorites' in message:
            return False
        # Filter health check requests (every 30s from Docker)
        if 'GET / HTTP' in message and '127.0.0.1' in message:
            return False
        return True

class UILogHandler(logging.Handler):
    def emit(self, record):
        try:
            msg = self.format(record)
        except Exception:
            msg = record.getMessage()
        LOG_BUFFER.append(msg)

# Attach UI log handler to root logger
_ui_handler = UILogHandler()
_ui_handler.setLevel(logging.INFO)
_ui_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
logging.getLogger().addHandler(_ui_handler)

# Add filter to Flask's werkzeug logger to exclude polling requests
werkzeug_logger = logging.getLogger('werkzeug')
werkzeug_logger.addFilter(LogPollFilter())

# --- Environment Variables ---
SERVER_PORT = int(os.getenv('SERVER_PORT', '5000'))
# Port that clients should use in URLs (M3U, XMLTV, UI).
# Defaults to SERVER_PORT so bare-metal runs don't need extra config.
PUBLIC_PORT = int(os.getenv('PUBLIC_PORT', str(SERVER_PORT)))
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
LIVEBARN_EMAIL = os.getenv('LIVEBARN_EMAIL')
LIVEBARN_PASSWORD = os.getenv('LIVEBARN_PASSWORD')

# Set log level from environment first
logging.getLogger().setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))

# LAN IP Configuration - Use env var if set, otherwise auto-detect  
LAN_IP = os.getenv('LAN_IP')
print(f"DEBUG: LAN_IP from env = '{LAN_IP}'")  # Debug print
print(f"DEBUG: LAN_IP bool = {bool(LAN_IP)}")  # Debug print

if LAN_IP:
    SERVER_HOST_URL = LAN_IP
    logger.info(f"✅ Using configured LAN_IP: {LAN_IP}")
    print(f"DEBUG: Set SERVER_HOST_URL to {SERVER_HOST_URL}")
else:
    # Import get_lan_ip here to avoid forward reference
    def _get_lan_ip():
        """Get the local non-loopback IP address."""
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        except Exception:
            ip = "127.0.0.1"
        finally:
            s.close()
        return ip
    
    SERVER_HOST_URL = _get_lan_ip()
    logger.info(f"⚠️  Auto-detected IP: {SERVER_HOST_URL}")
    print(f"DEBUG: Auto-detected SERVER_HOST_URL = {SERVER_HOST_URL}")

# --- Database Configuration ---
DB_PATH = Path(os.getenv('DB_PATH', '/data/livebarn.db'))

# CRITICAL FIX: Ensure the database directory exists
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
logger.info(f"📁 Database directory: {DB_PATH.parent}")
logger.info(f"💾 Database file: {DB_PATH}")

# Keep this fairly short so UI errors out quickly instead of appearing frozen on locks
SQLITE_TIMEOUT = 3

# Global cache for schedule data (all providers)
SCHEDULE_CACHE = {
    'events_by_surface': {},
    'last_updated': None
}

def get_lan_ip():
    """Get the local non-loopback IP address."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Connect to an external host; doesn't have to be reachable.
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def refresh_schedule():
    """
    Background job to refresh schedule data from all providers
    Uses modular provider system - automatically fetches from all enabled providers
    """
    global SCHEDULE_CACHE
    
    try:
        logger.info("🔄 Refreshing schedules from all providers...")
        
        now = datetime.now()
        today_start = datetime.combine(now.date(), dt_time(0, 0))
        tomorrow_end = datetime.combine(now.date() + timedelta(days=2), dt_time(0, 0))
        
        # Collect all events from all providers
        all_events = []
        provider_stats = []
        
        for provider in ALL_PROVIDERS:
            if not provider.is_enabled():
                logger.info(f"⏭️  Skipping {provider.name} (disabled)")
                continue
            
            try:
                events = provider.fetch_schedule(today_start, tomorrow_end)
                all_events.extend(events)
                provider_stats.append(f"{len(events)} {provider.name}")
                logger.info(f"✅ {provider.name}: {len(events)} events")
            except Exception as e:
                logger.error(f"❌ {provider.name} failed: {e}")
        
        # Group events by surface using utility function
        events_by_surface = group_events_by_surface(all_events)
        
        # Update cache
        SCHEDULE_CACHE['events_by_surface'] = events_by_surface
        SCHEDULE_CACHE['last_updated'] = datetime.now()
        
        total_events = len(all_events)
        stats_str = " + ".join(provider_stats) if provider_stats else "0"
        logger.info(f"✅ Schedule refreshed: {stats_str} = {total_events} total events")
        
    except Exception as e:
        logger.error(f"❌ Failed to refresh schedules: {e}")


# --- HTML Template (Embedded) ---
HTML_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>LiveBarn Favorites Manager</title>
    <style>
        body {
            font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            margin: 0;
            padding: 0;
            background: #0b1020;
            color: #f5f5f5;
        }

        header {
            background: linear-gradient(135deg, #0f172a, #020617);
            padding: 16px 24px;
            border-bottom: 1px solid rgba(148, 163, 184, 0.35);
            display: flex;
            align-items: center;
            justify-content: space-between;
            position: sticky;
            top: 0;
            z-index: 10;
        }

        header .left {
            display: flex;
            align-items: center;
            gap: 16px;
        }

        header h1 {
            margin: 0;
            font-size: 24px;
            font-weight: 600;
            color: #f8fafc;
        }

        header .badge {
            background: rgba(59, 130, 246, 0.15);
            border: 1px solid rgba(59, 130, 246, 0.3);
            color: #60a5fa;
            padding: 4px 12px;
            border-radius: 6px;
            font-size: 13px;
            font-weight: 500;
        }

        .container {
            max-width: 1400px;
            margin: 0 auto;
            padding: 24px;
        }

        .search-bar {
            background: #1e293b;
            padding: 20px;
            border-radius: 10px;
            margin-bottom: 24px;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.3);
        }

        .search-bar form {
            display: flex;
            gap: 12px;
            flex-wrap: wrap;
            align-items: center;
        }

        .search-bar input[type="text"],
        .search-bar select {
            padding: 10px 16px;
            border-radius: 8px;
            border: 1px solid #334155;
            background: #0f172a;
            color: #f1f5f9;
            font-size: 14px;
            min-width: 200px;
            flex: 1;
        }

        .search-bar button {
            padding: 10px 24px;
            border-radius: 8px;
            border: none;
            background: #3b82f6;
            color: white;
            font-size: 14px;
            font-weight: 500;
            cursor: pointer;
            transition: background 0.2s;
        }

        .search-bar button:hover {
            background: #2563eb;
        }

        .search-bar a {
            padding: 10px 24px;
            border-radius: 8px;
            background: #475569;
            color: white;
            text-decoration: none;
            font-size: 14px;
            font-weight: 500;
            transition: background 0.2s;
        }

        .search-bar a:hover {
            background: #64748b;
        }

        .section {
            background: #1e293b;
            border-radius: 10px;
            padding: 24px;
            margin-bottom: 24px;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.3);
        }

        .section h2 {
            margin: 0 0 20px 0;
            font-size: 20px;
            font-weight: 600;
            color: #f8fafc;
            display: flex;
            align-items: center;
            gap: 10px;
        }

        .section h2::before {
            content: "★";
            font-size: 24px;
            color: #fbbf24;
        }

        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 14px;
        }

        table thead {
            background: #0f172a;
        }

        table th {
            text-align: left;
            padding: 12px 16px;
            font-weight: 600;
            color: #cbd5e1;
            border-bottom: 2px solid #334155;
        }

        table td {
            padding: 12px 16px;
            border-bottom: 1px solid #334155;
        }

        table tbody tr {
            transition: background 0.15s;
        }

        table tbody tr:hover {
            background: rgba(59, 130, 246, 0.08);
        }

        .venue-link {
            color: #60a5fa;
            text-decoration: none;
            font-weight: 500;
            transition: color 0.2s;
        }

        .venue-link:hover {
            color: #93c5fd;
            text-decoration: underline;
        }

        .favorite-badge {
            display: inline-block;
            padding: 4px 10px;
            border-radius: 6px;
            font-size: 12px;
            font-weight: 600;
            background: rgba(34, 197, 94, 0.15);
            border: 1px solid rgba(34, 197, 94, 0.3);
            color: #4ade80;
        }

        .btn {
            padding: 8px 16px;
            border-radius: 6px;
            border: none;
            font-size: 13px;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.2s;
            text-decoration: none;
            display: inline-block;
        }

        .btn-primary {
            background: #3b82f6;
            color: white;
        }

        .btn-primary:hover {
            background: #2563eb;
        }

        .btn-danger {
            background: #ef4444;
            color: white;
        }

        .btn-danger:hover {
            background: #dc2626;
        }

        .empty-state {
            text-align: center;
            padding: 60px 20px;
            color: #94a3b8;
        }

        .empty-state svg {
            width: 64px;
            height: 64px;
            margin-bottom: 16px;
            opacity: 0.5;
        }

        .status-indicator {
            display: inline-block;
            width: 8px;
            height: 8px;
            border-radius: 50%;
            margin-right: 8px;
        }

        .status-active {
            background: #22c55e;
            box-shadow: 0 0 8px rgba(34, 197, 94, 0.6);
        }

        .status-inactive {
            background: #64748b;
        }

        .info-box {
            background: rgba(59, 130, 246, 0.1);
            border-left: 4px solid #3b82f6;
            padding: 16px;
            border-radius: 8px;
            margin-bottom: 24px;
        }

        .info-box code {
            background: #0f172a;
            padding: 2px 8px;
            border-radius: 4px;
            font-family: 'Courier New', monospace;
            color: #60a5fa;
        }

        footer {
            text-align: center;
            padding: 24px;
            color: #64748b;
            font-size: 13px;
        }
    </style>
</head>
<body>
    <header>
        <div class="left">
            <h1>🏒 LiveBarn Manager</h1>
            <span class="badge">{{ venues|length }} Venues</span>
        </div>
    </header>

    <div class="container">
        <div class="search-bar">
            <form method="GET" action="/">
                <input type="text" name="search" placeholder="Search venues or cities..." value="{{ request.args.get('search', '') }}">
                <select name="state">
                    <option value="">All States</option>
                    {% for state in state_list %}
                    <option value="{{ state }}" {% if request.args.get('state') == state %}selected{% endif %}>{{ state }}</option>
                    {% endfor %}
                </select>
                <button type="submit">🔍 Search</button>
                <a href="/">Clear Filters</a>
            </form>
        </div>

        <div class="section">
            <h2>Venues</h2>
            {% if venues %}
            <table>
                <thead>
                    <tr>
                        <th>Name</th>
                        <th>Location</th>
                        <th>Country</th>
                        <th>Favorites</th>
                        <th>Actions</th>
                    </tr>
                </thead>
                <tbody>
                    {% for venue in venues %}
                    <tr>
                        <td>
                            <a href="/venue/{{ venue.id }}" class="venue-link">{{ venue.name }}</a>
                        </td>
                        <td>{{ venue.city }}, {{ venue.state }}</td>
                        <td>{{ venue.country }}</td>
                        <td>
                            {% if venue.favorite_count > 0 %}
                            <span class="favorite-badge">{{ venue.favorite_count }}</span>
                            {% else %}
                            <span style="color: #64748b;">0</span>
                            {% endif %}
                        </td>
                        <td>
                            <a href="/venue/{{ venue.id }}" class="btn btn-primary">View Surfaces</a>
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
            {% else %}
            <div class="empty-state">
                <p>No venues found. Try adjusting your search filters.</p>
            </div>
            {% endif %}
        </div>

        <div class="info-box">
            <strong>📡 Playlist URL:</strong> <code>http://{{ server_host }}:{{ server_port }}/playlist.m3u</code><br>
            <strong>📺 XMLTV Guide:</strong> <code>http://{{ server_host }}:{{ server_port }}/xmltv</code><br>
            <strong>💾 Database:</strong> <code>{{ db_path }}</code>
        </div>
    </div>

    <footer>
        LiveBarn Favorites Manager &copy; 2025
    </footer>
</body>
</html>
"""

# --- Database Helpers ---

def get_db():
    """Returns the current SQLite connection, or opens a new one."""

    if 'db' not in g:
        # isolation_level=None → autocommit mode, keeps locks very short-lived
        g.db = sqlite3.connect(
            DB_PATH,
            timeout=SQLITE_TIMEOUT,
            detect_types=sqlite3.PARSE_DECLTYPES,
            isolation_level=None,
        )
        g.db.row_factory = sqlite3.Row

        # Make this connection friendlier for concurrent readers/writers
        cur = g.db.cursor()
        try:
            # WAL allows one writer + multiple readers without blocking everything
            cur.execute("PRAGMA journal_mode=WAL;")
            # Match the Python-level timeout
            cur.execute(f"PRAGMA busy_timeout={int(SQLITE_TIMEOUT * 1000)};")
            cur.execute("PRAGMA foreign_keys=ON;")
        finally:
            cur.close()

    return g.db

@app.teardown_appcontext
def close_connection(exception):
    """Closes the database connection at the end of the request."""
    db = g.pop('db', None)
    if db is not None:
        db.close()


def get_all_venues(search=None, state=None, limit=None, offset=0):
    """Get venues with optional filtering"""
    conn = get_db() 
    c = conn.cursor()
    
    query = '''
        SELECT 
            v.id, v.name, v.city, v.state, v.country, v.uuid,
            COUNT(DISTINCT f.id) as favorite_count,
            CASE WHEN COUNT(f.id) > 0 THEN 1 ELSE 0 END as is_favorite_venue
        FROM venues v
        LEFT JOIN surfaces s ON s.venue_id = v.id
        LEFT JOIN favorites f ON f.surface_id = s.id
        WHERE 1=1
    '''
    
    params = []
    
    if search:
        query += ' AND (v.name LIKE ? OR v.city LIKE ?)'
        search_param = f'%{search}%'
        params.extend([search_param, search_param])
    
    if state:
        query += ' AND v.state = ?'
        params.append(state)
        
    query += ' GROUP BY v.id, v.name, v.city, v.state, v.country'
    query += ' ORDER BY v.name'
    
    if limit is not None:
        query += ' LIMIT ? OFFSET ?'
        params.extend([limit, offset])
        
    c.execute(query, params)
    
    venues = [dict(row) for row in c.fetchall()]
    
    return venues

def get_all_favorites():
    """Get all favorited surfaces with venue and stream details"""
    conn = get_db() 
    c = conn.cursor()
    
    c.execute('''
        SELECT 
            v.id as venue_id, v.name as venue_name, v.city, v.state,
            s.id as surface_id, s.name as surface_name, s.uuid as stream_name,
            ss.playlist_url, ss.full_captured_url
        FROM favorites f
        JOIN surfaces s ON f.surface_id = s.id
        JOIN venues v ON s.venue_id = v.id
        LEFT JOIN surface_streams ss ON ss.surface_id = s.id
        ORDER BY v.name, s.name
    ''')
    
    favorites = [dict(row) for row in c.fetchall()]
    return favorites

def get_surfaces_for_venue(venue_id):
    """Get all surfaces for a venue and whether each is favorited"""
    conn = get_db()
    c = conn.cursor()
    
    c.execute('''
        SELECT 
            s.id, s.name, s.uuid, s.venue_id,
            CASE WHEN f.id IS NOT NULL THEN 1 ELSE 0 END as is_favorite,
            ss.playlist_url as captured_playlist_url,
            ss.full_captured_url as captured_full_url
        FROM surfaces s
        LEFT JOIN favorites f ON f.surface_id = s.id
        LEFT JOIN surface_streams ss ON ss.surface_id = s.id
        WHERE s.venue_id = ?
        ORDER BY s.name
    ''', (venue_id,))
    
    surfaces = [dict(row) for row in c.fetchall()]
    
    return surfaces

def toggle_favorite(surface_id):
    """
    Toggles favorite status with a small retry loop so the UI stays responsive
    even if another process briefly locks the database.
    """
    conn = get_db()
    c = conn.cursor()
    action = None

    max_retries = 3
    delay = 0.25  # seconds

    for attempt in range(max_retries):
        try:
            # Check if already favorite
            c.execute('SELECT id FROM favorites WHERE surface_id = ?', (surface_id,))
            existing = c.fetchone()

            if existing:
                # Remove from favorites
                c.execute('DELETE FROM favorites WHERE surface_id = ?', (surface_id,))
                action = 'removed'
            else:
                # Add to favorites

                # 1. Get necessary data from surfaces/venues
                c.execute('''
                    SELECT 
                        s.name as surface_name,
                        v.name as venue_name,
                        s.uuid as stream_name,
                        v.uuid as venue_uuid
                    FROM surfaces s
                    JOIN venues v ON s.venue_id = v.id
                    WHERE s.id = ?
                ''', (surface_id,))
                surface_data = c.fetchone()

                if surface_data:
                    surface_name, venue_name, stream_name, venue_uuid = surface_data

                    # 2. Insert/Update surface_streams row (INSERT OR IGNORE)
                    c.execute('''
                        INSERT OR IGNORE INTO surface_streams 
                        (surface_id, venue_uuid, stream_name, venue_name, surface_name)
                        VALUES (?, ?, ?, ?, ?)
                    ''', (
                        surface_id,
                        venue_uuid,
                        stream_name,
                        venue_name,
                        surface_name
                    ))

                # 3. Insert into favorites
                c.execute('''
                    INSERT INTO favorites (surface_id, added_at)
                    VALUES (?, ?)
                ''', (surface_id, datetime.utcnow().isoformat()))
                action = 'added'

            # In autocommit mode this is effectively a safety no-op but harmless
            conn.commit()
            return action

        except sqlite3.OperationalError as e:
            msg = str(e).lower()

            # Treat lock errors as transient – retry a couple of times
            if 'database is locked' in msg or 'database table is locked' in msg:
                if attempt < max_retries - 1:
                    logger.warning(
                        f"Database locked when toggling favorite "
                        f"(surface_id={surface_id}); retrying in {delay:.2f}s "
                        f"[attempt {attempt + 1}/{max_retries}]"
                    )
                    time.sleep(delay)
                    delay *= 2
                    continue

            # Non-lock error or out of retries → let the API handler surface it
            raise

        finally:
            # Connection is cleaned up by @app.teardown_appcontext
            pass

def get_stream_info(surface_id):
    """Retrieve the stream URL and metadata for a given surface_id from surface_streams."""
    conn = get_db()
    c = conn.cursor()
    
    c.execute('''
        SELECT 
            ss.playlist_url, 
            ss.full_captured_url,
            ss.venue_name,
            ss.surface_name
        FROM surface_streams ss
        WHERE ss.surface_id = ?
    ''', (surface_id,))
    
    result = c.fetchone()
    
    if result:
        return {
            'playlist_url': result['playlist_url'],
            'full_captured_url': result['full_captured_url'],
            'venue_name': result['venue_name'],
            'surface_name': result['surface_name']
        }
    return None

# --- Flask Routes ---

@app.route('/')
def index():
    """Main page to list venues, search, filter, and show favorites."""
    search = request.args.get('search', '').strip()
    state = request.args.get('state', '').strip()
    
    venues = get_all_venues(
        search=search or None,
        state=state or None
    )
    
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT DISTINCT state FROM venues WHERE state IS NOT NULL AND state != "" ORDER BY state')
    state_list = [row['state'] for row in c.fetchall()]
    
    return render_template_string(
        HTML_TEMPLATE,
        venues=venues,
        state_list=state_list,
        server_host=SERVER_HOST_URL,
        server_port=PUBLIC_PORT,
        db_path=str(DB_PATH)
    )

@app.route('/venue/<int:venue_id>')
def list_surfaces_for_venue(venue_id):
    conn = get_db()
    c = conn.cursor()
    
    c.execute('SELECT * FROM venues WHERE id = ?', (venue_id,))
    venue = c.fetchone()
    
    if not venue:
        return f"Venue with id {venue_id} not found.", 404
    
    venue_dict = dict(venue)
    surfaces = get_surfaces_for_venue(venue_id)
    
    rows_html = []
    for s in surfaces:
        favorite_label = "Yes" if s["is_favorite"] else "No"
        rows_html.append(f"""
            <tr>
                <td>{s["name"]}</td>
                <td>{s["uuid"]}</td>
                <td>{favorite_label}</td>
                <td>
                    <form method="POST" action="/toggle_favorite" style="display:inline;">
                        <input type="hidden" name="surface_id" value="{s['id']}">
                        <button type="submit">
                            {"Remove from Favorites" if s["is_favorite"] else "Add to Favorites"}
                        </button>
                    </form>
                </td>
            </tr>
        """)
    
    table_html = f"""
    <html>
    <head><title>Surfaces for {venue_dict['name']}</title></head>
    <body>
        <h1>Surfaces for {venue_dict['name']}</h1>
        <p><a href="/">← Back to all venues</a></p>
        <table border="1" cellpadding="5" cellspacing="0">
            <thead>
                <tr>
                    <th>Surface Name</th>
                    <th>UUID</th>
                    <th>Favorite?</th>
                    <th>Actions</th>
                </tr>
            </thead>
            <tbody>
                {"".join(rows_html)}
            </tbody>
        </table>
    </body>
    </html>
    """
    return table_html

@app.route('/toggle_favorite', methods=['POST'])
def toggle_favorite_form():
    """Form endpoint that redirects back."""
    surface_id = int(request.form.get('surface_id'))
    action = toggle_favorite(surface_id)
    # Redirect to referrer or home
    return_to = request.referrer or '/'
    return f"""
    <html>
    <head><meta http-equiv="refresh" content="0; url={return_to}"></head>
    <body>
        <p>{action.capitalize()} surface #{surface_id}. Redirecting...</p>
    </body>
    </html>
    """

@app.route('/api/toggle_favorite/<int:surface_id>', methods=['POST'])
def api_toggle_favorite(surface_id):
    """JSON API for toggling favorites."""
    try:
        action = toggle_favorite(surface_id)
        if action == 'added':
            message = "Added to favorites"
        elif action == 'removed':
            message = "Removed from favorites"
        else:
            message = "No change"
        
        return jsonify({
            "success": True,
            "action": action,
            "message": message
        })
    except sqlite3.OperationalError as e:
        logger.error(f"Database error during API toggle_favorite: {e}")
        return jsonify({
            "success": False,
            "message": "Database is busy/locked. Please try again."
        }), 503
    except Exception as e:
        logger.error(f"Unexpected error during API toggle_favorite: {e}")
        return jsonify({
            "success": False,
            "message": "Unexpected server error."
        }), 500


@app.route('/api/favorites', methods=['GET'])
def api_get_favorites():
    """JSON API to return all favorites."""
    try:
        favorites = get_all_favorites()
        return jsonify({
            "success": True,
            "favorites": favorites
        })
    except Exception as e:
        logger.error(f"Error fetching favorites: {e}")
        return jsonify({
            "success": False,
            "message": "Error fetching favorites."
        }), 500



@app.route('/api/logs', methods=['GET'])
def api_get_logs():
    """Return recent log lines for the UI."""
    try:
        return jsonify({
            "lines": list(LOG_BUFFER)
        })
    except Exception as e:
        logger.error(f"Error returning logs: {e}")
        return jsonify({
            "lines": [],
            "error": "Failed to read logs."
        }), 500


@app.route('/api/regenerate', methods=['POST'])
def api_regenerate_playlists():
    """
    Trigger regeneration of M3U/XMLTV files.
    This doesn't actually write files, but forces a refresh of the schedule cache
    so the next /playlist.m3u and /xmltv requests will have fresh data.
    """
    try:
        logger.info("=" * 70)
        logger.info("🔄 MANUAL REGENERATION TRIGGERED VIA API")
        logger.info("=" * 70)
        
        # Force refresh the schedule cache
        refresh_schedule()
        
        # Get cache stats
        events_by_surface = SCHEDULE_CACHE.get('events_by_surface', {})
        last_updated = SCHEDULE_CACHE.get('last_updated')
        
        total_surfaces = len(events_by_surface)
        total_events = sum(len(events) for events in events_by_surface.values())
        
        logger.info(f"📊 Cache updated: {total_surfaces} surfaces, {total_events} events")
        logger.info(f"⏰ Last updated: {last_updated}")
        logger.info("=" * 70)
        
        return jsonify({
            "success": True,
            "message": f"M3U/XMLTV refreshed: {total_surfaces} surfaces, {total_events} events"
        })
    except Exception as e:
        logger.error(f"❌ Error during manual regeneration: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "message": f"Error refreshing data: {str(e)}"
        }), 500


@app.route('/playlist.m3u')
def generate_playlist():
    """
    Generate an M3U playlist with Channels DVR custom tags
    """
    favorites = get_all_favorites()
    
    lines = ['#EXTM3U']
    for fav in favorites:
        surface_id = fav['surface_id']
        venue_name = fav.get('venue_name', 'Unknown Venue')
        surface_name = fav.get('surface_name', 'Surface')
        city = fav.get('city', '')
        state = fav.get('state', '')
        
        raw_title = f"{venue_name} - {surface_name}"
        title = sanitize_title_for_filesystem(raw_title)
        if city or state:
            location = f" ({city}, {state})" if city and state else f" ({city or state})"
        else:
            location = ""
        
        # Build guide description
        description = f"🎥 Live camera feed from {venue_name} - {surface_name}"
        if city and state:
            description += f" in {city}, {state}"
        
        proxy_url = f"http://{SERVER_HOST_URL}:{PUBLIC_PORT}/proxy/{surface_id}"
        
        # Channels DVR custom tags
        extinf_line = (
            f'#EXTINF:-1 '
            f'channel-id="{surface_id}" '
            f'channel-number="{surface_id}" '
            f'tvg-id="{surface_id}" '
            f'tvg-name="{title}" '
            f'group-title="LiveBarn" '
            f'tvc-guide-title="LIVE: {title}" '
            f'tvc-guide-description="{description}" '
            f'tvc-guide-tags="Live, HDTV" '
            f'tvc-guide-genres="Sports" '
            f'tvc-guide-placeholders="3600",'  # 1 hour blocks
            f'{title}{location}'
        )
        
        lines.append(extinf_line)
        lines.append(proxy_url)
    
    playlist_content = '\n'.join(lines)
    return Response(playlist_content, mimetype='application/x-mpegURL')


@app.route('/xmltv')
def xmltv_endpoint():
    """
    Generate XMLTV guide for favorited surfaces with schedule integration.
    Creates programs with real event schedules from all providers.
    """
    # Get all favorites
    favorites = get_all_favorites()
    
    # Get schedule events from cache
    events_by_surface = SCHEDULE_CACHE.get('events_by_surface', {})
    
    # Build XMLTV
    root = ET.Element('tv')
    root.set('generator-info-name', 'LiveBarn Manager')
    root.set('generator-info-url', 'https://github.com/yourusername/livebarn-manager')
    
    now = datetime.now()
    today_start = datetime.combine(now.date(), dt_time(0, 0))
    tomorrow_end = datetime.combine(now.date() + timedelta(days=2), dt_time(0, 0))
    
    for fav in favorites:
        surface_id = fav['surface_id']
        venue_name = fav.get('venue_name', 'Unknown Venue')
        surface_name = fav.get('surface_name', 'Surface')
        city = fav.get('city', '')
        state = fav.get('state', '')
        
        raw_title = f"{venue_name} - {surface_name}"
        title = sanitize_title_for_filesystem(raw_title)
        
        # Channel element
        channel = ET.SubElement(root, 'channel')
        channel.set('id', str(surface_id))
        
        display_name = ET.SubElement(channel, 'display-name')
        display_name.text = title
        
        # Get schedule events for this surface
        surface_events = events_by_surface.get(surface_id, [])
        
        # Fill gaps with "Open Ice" and create programs
        programs = fill_gaps_with_open_ice(surface_events, today_start, tomorrow_end)
        
        for start_time, end_time, program_title in programs:
            programme = ET.SubElement(root, 'programme')
            programme.set('channel', str(surface_id))
            programme.set('start', start_time.strftime('%Y%m%d%H%M%S +0000'))
            programme.set('stop', end_time.strftime('%Y%m%d%H%M%S +0000'))
            
            title_elem = ET.SubElement(programme, 'title')
            title_elem.set('lang', 'en')
            title_elem.text = program_title
            
            desc_elem = ET.SubElement(programme, 'desc')
            desc_elem.set('lang', 'en')
            if program_title == "Open Ice":
                desc_elem.text = f"Open practice time at {venue_name} - {surface_name}"
            else:
                desc_elem.text = f"{program_title} at {venue_name} - {surface_name}"
            
            category = ET.SubElement(programme, 'category')
            category.set('lang', 'en')
            category.text = 'Sports'
    
    # Convert to string
    xml_str = ET.tostring(root, encoding='utf-8', method='xml')
    return Response(xml_str, mimetype='application/xml')


@app.route('/proxy/<int:surface_id>')
def proxy_stream(surface_id):
    """
    Proxy the HLS stream for a given surface using streamlink
    Auto-refreshes expired tokens
    """
    # Get stream info
    stream_info = get_stream_info(surface_id)
    
    # Check if we need to refresh
    needs_refresh = False
    
    if not stream_info or not stream_info.get('playlist_url'):
        logger.info(f"⚠️  No stream URL found for surface_id={surface_id}, needs refresh")
        needs_refresh = True
    else:
        # Check if URL has hdnts token with expiry
        playlist_url = stream_info['playlist_url']
        
        # Parse expiry from hdnts token
        import re
        match = re.search(r'exp=(\d+)', playlist_url)
        if match:
            exp_timestamp = int(match.group(1))
            from datetime import datetime
            exp_datetime = datetime.fromtimestamp(exp_timestamp)
            now = datetime.now()
            
            # Refresh if expired or expiring soon (within 5 minutes)
            minutes_left = (exp_datetime - now).total_seconds() / 60
            
            if minutes_left < 5:
                logger.info(f"🔄 Token expiring in {minutes_left:.1f} minutes, auto-refreshing...")
                needs_refresh = True
            else:
                logger.info(f"✅ Token valid for {minutes_left:.0f} more minutes")
    
    # Auto-refresh if needed
    if needs_refresh:
        logger.info(f"🔄 Auto-refreshing stream for surface_id={surface_id}...")
        
        # Quick refresh using browser automation
        import subprocess as sp
        try:
            # Run a quick single-stream refresh
            result = sp.run(
                ['python', 'refresh_single.py', str(surface_id)],
                capture_output=True,
                text=True,
                timeout=45,  # Increased from 30 to 45 seconds
                cwd=str(Path(__file__).parent)
            )
            
            if result.returncode == 0:
                logger.info(f"✅ Auto-refresh succeeded!")
                # Re-fetch stream info
                stream_info = get_stream_info(surface_id)
            else:
                logger.error(f"❌ Auto-refresh failed: {result.stderr}")
                return f"Auto-refresh failed for surface_id={surface_id}", 500
                
        except sp.TimeoutExpired:
            logger.error(f"❌ Auto-refresh timeout")
            return f"Auto-refresh timeout for surface_id={surface_id}", 500
        except Exception as e:
            logger.error(f"❌ Auto-refresh error: {e}")
            return f"Auto-refresh error: {e}", 500
    
    if not stream_info or not stream_info.get('playlist_url'):
        return f"No stream found for surface_id={surface_id} even after refresh", 404
    
    playlist_url = stream_info['playlist_url']
    venue_name = stream_info.get('venue_name', 'Unknown')
    surface_name = stream_info.get('surface_name', 'Unknown')
    stream_name = f"{venue_name} - {surface_name}"
    
    logger.info(f"📺 Streaming surface_id={surface_id}: {stream_name}")
    logger.info(f"   URL: {playlist_url[:80]}...")
    
    def generate():
        """Generator with pre-buffering to prevent VLC 'end of stream' error"""
        logger.info(f"   🚀 Launching streamlink subprocess")
        
        process = subprocess.Popen(
            [
                'streamlink',
                '--stdout',
                '--loglevel', 'error',
                playlist_url,
                'best'
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0  # No buffering - immediate data
        )
        
        # PRE-BUFFER: Wait for first chunk before yielding
        logger.info(f"   ⏳ Waiting for first video chunk...")
        start_time = time.time()
        first_chunk = None
        
        while time.time() - start_time < 30:  # 30 second timeout
            chunk = process.stdout.read(8192)
            if chunk:
                first_chunk = chunk
                elapsed = time.time() - start_time
                logger.info(f"   ✅ Got first chunk after {elapsed:.1f}s ({len(chunk)} bytes)")
                break
            time.sleep(0.05)
        
        if not first_chunk:
            logger.error(f"   ❌ No data from streamlink after 30 seconds")
            stderr = process.stderr.read().decode('utf-8', errors='ignore')
            if stderr:
                logger.error(f"   Streamlink error: {stderr}")
            process.terminate()
            process.wait()
            return
        
        # Yield the first chunk
        yield first_chunk
        
        # Now continue streaming the rest
        chunk_count = 1
        try:
            while True:
                chunk = process.stdout.read(8192)
                if not chunk:
                    logger.info(f"   ✓ Stream ended after {chunk_count} chunks")
                    break
                chunk_count += 1
                if chunk_count % 1000 == 0:  # Log every 1000 chunks (~8MB)
                    logger.info(f"   📊 Streamed {chunk_count} chunks so far...")
                yield chunk
        except GeneratorExit:
            logger.info(f"   ⚠️  Client disconnected after {chunk_count} chunks")
        except Exception as e:
            logger.error(f"   ❌ Streaming error: {e}")
        finally:
            logger.info(f"   🛑 Terminating streamlink (streamed {chunk_count} chunks total)")
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                logger.warning(f"   ⚠️  Had to kill streamlink process")
                process.kill()
    
    return Response(
        generate(),
        mimetype='video/mp2t',
        headers={
            'Cache-Control': 'no-cache, no-store, must-revalidate',
            'Pragma': 'no-cache',
            'Expires': '0',
            'Access-Control-Allow-Origin': '*',
            'Content-Type': 'video/mp2t'
        }
    )



def init_db_if_needed():
    """
    If the DB doesn't exist or is missing tables, we just print a warning.
    This script expects that some other process has created/populated
    'venues', 'surfaces', 'favorites', and 'surface_streams'.
    """
    if not DB_PATH.exists():
        logger.warning(f"⚠️  Database file does not exist at: {DB_PATH}")
        logger.warning(f"⚠️  Please run build_catalog.py first to create the database")
        return
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    required_tables = ['venues', 'surfaces', 'favorites', 'surface_streams']
    missing = []
    
    for t in required_tables:
        c.execute("""
            SELECT name 
            FROM sqlite_master 
            WHERE type='table' AND name=?
        """, (t,))
        row = c.fetchone()
        if not row:
            missing.append(t)
    
    if missing:
        logger.warning(f"⚠️  The following required tables are missing: {missing}")
        logger.warning(f"⚠️  Please run build_catalog.py to create missing tables")
    else:
        logger.info("✅ All expected tables found in database")
        
        # Check favorites count
        c.execute('SELECT COUNT(*) FROM favorites')
        fav_count = c.fetchone()[0]
        logger.info(f"📊 Current favorites count: {fav_count}")
    
    conn.close()


if __name__ == '__main__':
    init_db_if_needed()
    
    print("=" * 70)
    print(" LiveBarn Favorites Manager & Streamlink Proxy ".center(70, "="))
    print(f"  Database: {DB_PATH}")
    print(f"  Playlist: http://{SERVER_HOST_URL}:{PUBLIC_PORT}/playlist.m3u")
    print(f"  XMLTV:    http://{SERVER_HOST_URL}:{PUBLIC_PORT}/xmltv")
    print(f"  Server:   http://{SERVER_HOST_URL} (LAN IP detected)")
    print("=" * 70)
    print()
    print(f"📖 Open http://localhost:{PUBLIC_PORT} in your browser to:")
    print("   1. Browse venues and add/remove favorites.")
    print("   2. Get the proxy playlist URL for your video player.")
    print()
    
    # Initialize and start APScheduler
    scheduler = BackgroundScheduler()
    
    # Schedule refresh at 3:00 AM daily (all providers)
    scheduler.add_job(
        func=refresh_schedule,
        trigger='cron',
        hour=3,
        minute=0,
        id='schedule_refresh',
        name='Daily Schedule Refresh (All Providers)'
    )
    
    scheduler.start()
    logger.info("⏰ Scheduler started - Schedule refresh at 3:00 AM daily")
    
    # Do initial schedule refresh on startup
    logger.info("🔄 Performing initial schedule refresh...")
    refresh_schedule()
    
    print("\n✅ Background scheduler active")
    print("   → Schedule refreshes daily at 3:00 AM")
    print("\nPress Ctrl+C to stop the server.")
    
    # Run the Flask app
    try:
        app.run(host='0.0.0.0', port=SERVER_PORT, debug=False, threaded=True, use_reloader=False)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down scheduler...")
        scheduler.shutdown()
    except Exception as e:
        logger.error(f"Server crashed: {e}")
        scheduler.shutdown()

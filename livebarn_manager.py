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

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

# In-memory log buffer for UI live logs
LOG_BUFFER = deque(maxlen=500)

class LogPollFilter(logging.Filter):
    """Filter out /api/logs polling requests to avoid log pollution"""
    def filter(self, record):
        message = record.getMessage()
        # Exclude /api/logs GET requests from logs
        if 'GET /api/logs' in message or 'GET /api/favorites' in message:
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
            gap: 14px;
        }

        header h1 {
            font-size: 20px;
            margin: 0;
            letter-spacing: 0.03em;
            font-weight: 600;
            display: flex;
            align-items: center;
            gap: 10px;
        }

        header h1 span.badge {
            font-size: 11px;
            padding: 2px 8px;
            border-radius: 999px;
            background: rgba(37, 99, 235, 0.15);
            color: #93c5fd;
            border: 1px solid rgba(59, 130, 246, 0.45);
            text-transform: uppercase;
            letter-spacing: 0.12em;
        }

        header .subtitle {
            font-size: 12px;
            color: #9ca3af;
            margin-top: 2px;
        }

        header .status-pill {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            font-size: 11px;
            padding: 3px 10px;
            border-radius: 999px;
            background: rgba(22, 163, 74, 0.1);
            color: #bbf7d0;
            border: 1px solid rgba(34, 197, 94, 0.5);
        }

        header .status-pill::before {
            content: "";
            width: 7px;
            height: 7px;
            border-radius: 999px;
            background: #22c55e;
            box-shadow: 0 0 0 4px rgba(34, 197, 94, 0.25);
        }

        main {
            padding: 16px 24px 24px;
            display: grid;
            grid-template-columns: minmax(0, 360px) minmax(0, 1fr);
            gap: 18px;
            align-items: flex-start;
        }

        @media (max-width: 960px) {
            main {
                grid-template-columns: minmax(0, 1fr);
            }
        }

        .panel {
            background: radial-gradient(circle at top left, rgba(59, 130, 246, 0.12), transparent 55%),
                        radial-gradient(circle at bottom right, rgba(147, 51, 234, 0.14), transparent 55%),
                        rgba(15, 23, 42, 0.96);
            border-radius: 16px;
            padding: 12px 12px 10px;
            border: 1px solid rgba(148, 163, 184, 0.45);
            box-shadow:
                0 18px 40px rgba(15, 23, 42, 0.85),
                0 0 0 1px rgba(15, 23, 42, 0.9),
                0 0 40px rgba(37, 99, 235, 0.18);
            backdrop-filter: blur(16px);
        }

        .panel-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 8px;
        }

        .panel-title {
            font-size: 13px;
            text-transform: uppercase;
            letter-spacing: 0.14em;
            color: #9ca3af;
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .panel-title span.dot {
            width: 6px;
            height: 6px;
            border-radius: 999px;
            background: #4ade80;
            box-shadow: 0 0 12px rgba(74, 222, 128, 0.75);
        }

        .panel-subtitle {
            font-size: 12px;
            color: #6b7280;
        }

        .pill {
            font-size: 11px;
            padding: 2px 8px;
            border-radius: 999px;
            border: 1px solid rgba(148, 163, 184, 0.6);
            color: #e5e7eb;
            background: linear-gradient(135deg, rgba(15, 23, 42, 0.95), rgba(17, 24, 39, 0.9));
            display: inline-flex;
            align-items: center;
            gap: 6px;
        }

        .pill code {
            font-size: 10px;
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
            color: #a5b4fc;
        }

        .pill .dot {
            width: 4px;
            height: 4px;
            border-radius: 999px;
            background: #a855f7;
        }

        .pill strong {
            font-weight: 600;
            color: #e5e7eb;
        }

        .pill span.tip {
            color: #9ca3af;
            font-weight: 400;
        }

        .favorites-panel {
            position: sticky;
            top: 84px;
        }
        .logs-panel {
            grid-column: 2;
        }

        .logs-container {
            margin-top: 8px;
            background: rgba(15, 23, 42, 0.96);
            border-radius: 12px;
            border: 1px solid rgba(31, 41, 55, 0.95);
            max-height: 600px;
            overflow-y: auto;
            padding: 14px 16px;
        }

        #liveLogs {
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
            font-size: 13px;
            line-height: 1.6;
            margin: 0;
            white-space: pre-wrap;
            color: #e5e7eb;
        }


        .favorites-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
            margin-bottom: 8px;
        }

        .favorites-header-left {
            display: flex;
            flex-direction: column;
            gap: 2px;
        }

        .favorites-title {
            font-size: 14px;
            font-weight: 600;
            display: flex;
            align-items: center;
            gap: 6px;
        }

        .favorites-title .star {
            color: #fbbf24;
            text-shadow: 0 0 10px rgba(251, 191, 36, 0.75);
        }

        .favorites-caption {
            font-size: 11px;
            color: #9ca3af;
        }

        .favorites-count-pill {
            font-size: 11px;
            padding: 2px 8px;
            border-radius: 999px;
            background: rgba(59, 130, 246, 0.08);
            color: #bfdbfe;
            border: 1px solid rgba(59, 130, 246, 0.5);
        }

        .playlist-card {
            margin-bottom: 8px;
            padding: 8px 10px;
            background: radial-gradient(circle at top left, rgba(59, 130, 246, 0.15), transparent 55%),
                        rgba(15, 23, 42, 0.98);
            border-radius: 12px;
            border: 1px solid rgba(96, 165, 250, 0.55);
            box-shadow:
                0 0 0 1px rgba(15, 23, 42, 0.9),
                0 10px 30px rgba(15, 23, 42, 0.9),
                0 0 30px rgba(59, 130, 246, 0.25);
        }

        .playlist-label {
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.16em;
            color: #93c5fd;
            margin-bottom: 6px;
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .playlist-label span.indicator {
            width: 7px;
            height: 7px;
            border-radius: 999px;
            background: #60a5fa;
            box-shadow: 0 0 18px rgba(59, 130, 246, 0.9);
        }

        .playlist-url-row {
            display: grid;
            grid-template-columns: minmax(0, 1fr) auto;
            gap: 8px;
            align-items: center;
        }

        .playlist-url {
            font-size: 12px;
            background: rgba(15, 23, 42, 0.9);
            border-radius: 8px;
            padding: 7px 10px;
            border: 1px solid rgba(30, 64, 175, 0.8);
            font-family: ui-monospace, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
            color: #e5e7eb;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            cursor: pointer;
        }

        .playlist-url:hover {
            border-color: rgba(129, 140, 248, 0.9);
            box-shadow: 0 0 0 1px rgba(129, 140, 248, 0.4);
        }

        .playlist-url code {
            font-size: 11px;
        }

        .btn-copy {
            padding: 6px 10px;
            font-size: 11px;
            border-radius: 999px;
            border: 1px solid rgba(129, 140, 248, 0.85);
            background: radial-gradient(circle at top left, rgba(129, 140, 248, 0.35), transparent 55%),
                        rgba(17, 24, 39, 0.95);
            color: #e5e7eb;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 6px;
        }

        .btn-copy:hover {
            transform: translateY(-0.5px);
            box-shadow:
                0 0 0 1px rgba(129, 140, 248, 0.6),
                0 10px 28px rgba(15, 23, 42, 0.9);
            background: radial-gradient(circle at top left, rgba(129, 140, 248, 0.45), transparent 55%),
                        rgba(15, 23, 42, 0.98);
        }

        .btn-copy span.icon {
            font-size: 13px;
        }

        .playlist-hint {
            margin-top: 5px;
            font-size: 11px;
            color: #9ca3af;
        }

        .playlist-hint code {
            font-size: 10px;
            color: #a5b4fc;
        }

        .favorites-list {
            border-radius: 12px;
            background: rgba(15, 23, 42, 0.96);
            border: 1px solid rgba(55, 65, 81, 0.9);
            max-height: 380px;
            overflow-y: auto;
            display: flex;
            flex-direction: column;
        }

        .favorites-empty {
            padding: 16px 20px;
            text-align: center;
            border-bottom: 1px solid rgba(31, 41, 55, 0.95);
            background: radial-gradient(circle at top, rgba(148, 163, 184, 0.22), transparent 55%);
        }

        .favorites-empty h3 {
            margin: 0 0 4px;
            font-size: 12px;
        }

        .favorites-empty p {
            margin: 0;
            font-size: 11px;
            color: #9ca3af;
        }

        .favorites-empty strong {
            color: #e5e7eb;
        }

        .favorites-scroll {
            overflow-y: auto;
            max-height: 380px;
        }

        .favorites-item {
            padding: 10px 14px;
            border-bottom: 1px solid rgba(31, 41, 55, 0.95);
            display: flex;
            align-items: center;
            gap: 10px;
        }

        .favorites-item:last-child {
            border-bottom: none;
        }

        .favorites-leading {
            width: 8px;
            height: 100%;
            border-radius: 999px;
            background: linear-gradient(to bottom, #fbbf24, #f97316);
            box-shadow: 0 0 18px rgba(249, 115, 22, 0.75);
        }

        .favorites-main {
            flex: 1;
            min-width: 0;
        }

        .favorites-main .venue-name {
            font-size: 13px;
            font-weight: 500;
            display: flex;
            align-items: center;
            gap: 6px;
        }

        .favorites-main .surface-name {
            font-size: 12px;
            color: #9ca3af;
        }

        .favorites-meta {
            font-size: 11px;
            color: #6b7280;
        }

        .favorites-meta span {
            margin-right: 8px;
        }

        .favorites-actions {
            display: flex;
            flex-direction: column;
            gap: 6px;
        }

        .btn-unfavorite {
            padding: 4px 9px;
            border-radius: 999px;
            border: 1px solid rgba(239, 68, 68, 0.8);
            background: rgba(127, 29, 29, 0.95);
            color: #fecaca;
            font-size: 11px;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 6px;
        }

        .btn-unfavorite:hover {
            background: rgba(153, 27, 27, 0.95);
            box-shadow:
                0 0 0 1px rgba(239, 68, 68, 0.6),
                0 10px 24px rgba(15, 23, 42, 0.9);
        }

        .btn-unfavorite span.icon {
            font-size: 13px;
        }

        .avatar {
            font-size: 20px;
            color: #fbbf24;
            text-shadow: 0 0 14px rgba(251, 191, 36, 0.85);
        }

        .filters-row {
            display: grid;
            grid-template-columns: minmax(0, 1fr) minmax(0, 180px);
            gap: 10px;
            margin-bottom: 10px;
        }

        @media (max-width: 768px) {
            .filters-row {
                grid-template-columns: minmax(0, 1fr);
            }
        }

        .filters-row label {
            display: block;
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.12em;
            color: #9ca3af;
            margin-bottom: 4px;
        }

        .filters-row label span {
            font-size: 10px;
            color: #6b7280;
            text-transform: none;
            letter-spacing: 0;
        }

        .input, .select {
            width: 100%;
            padding: 7px 10px;
            font-size: 12px;
            background: rgba(15, 23, 42, 0.96);
            border-radius: 8px;
            border: 1px solid rgba(55, 65, 81, 0.9);
            color: #e5e7eb;
            font-family: inherit;
        }

        .input::placeholder {
            color: #6b7280;
        }

        .input:focus, .select:focus {
            outline: none;
            border-color: rgba(59, 130, 246, 0.8);
            box-shadow: 0 0 0 1px rgba(59, 130, 246, 0.4);
        }

        .venues-container {
            border-radius: 12px;
            background: rgba(15, 23, 42, 0.96);
            border: 1px solid rgba(55, 65, 81, 0.9);
            overflow: hidden;
        }

        .venues-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 8px 12px;
            border-bottom: 1px solid rgba(31, 41, 55, 0.95);
            background: radial-gradient(circle at top left, rgba(148, 163, 184, 0.22), transparent 55%);
        }

        .venues-header-left {
            display: flex;
            align-items: center;
            gap: 6px;
            font-size: 11px;
            color: #9ca3af;
        }

        .venues-header-left .count {
            font-size: 16px;
            font-weight: 600;
            color: #e5e7eb;
        }

        .venues-header-left .dim {
            color: #6b7280;
        }

        .venues-header-right {
            font-size: 11px;
            color: #9ca3af;
        }

        .venues-list {
            overflow-y: auto;
            max-height: 520px;
        }

        .venue-row {
            padding: 10px 12px;
            border-bottom: 1px solid rgba(31, 41, 55, 0.95);
            display: flex;
            align-items: center;
            gap: 10px;
            cursor: pointer;
        }

        .venue-row:last-child {
            border-bottom: none;
        }

        .venue-row:hover {
            background: rgba(30, 64, 175, 0.12);
        }

        .venue-marker {
            width: 8px;
            height: 100%;
            border-radius: 999px;
            background: linear-gradient(to bottom, #3b82f6, #2563eb);
            box-shadow: 0 0 16px rgba(59, 130, 246, 0.65);
        }

        .venue-main {
            flex: 1;
            min-width: 0;
        }

        .venue-name-line {
            display: flex;
            align-items: center;
            gap: 6px;
            font-size: 13px;
            font-weight: 500;
        }

        .venue-name-line .badge {
            font-size: 10px;
            padding: 2px 6px;
            border-radius: 999px;
            background: rgba(251, 191, 36, 0.12);
            color: #fbbf24;
            border: 1px solid rgba(251, 191, 36, 0.6);
            text-transform: uppercase;
            letter-spacing: 0.08em;
        }

        .venue-location {
            font-size: 12px;
            color: #9ca3af;
        }

        .venue-meta {
            font-size: 11px;
            color: #6b7280;
        }

        .venue-meta span {
            margin-right: 8px;
        }

        .venue-actions {
            display: flex;
            flex-direction: column;
            gap: 6px;
        }

        .btn-view-surfaces {
            padding: 4px 9px;
            border-radius: 999px;
            border: 1px solid rgba(59, 130, 246, 0.8);
            background: rgba(30, 64, 175, 0.9);
            color: #bfdbfe;
            font-size: 11px;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 6px;
        }

        .btn-view-surfaces:hover {
            background: rgba(30, 64, 175, 0.95);
            box-shadow:
                0 0 0 1px rgba(59, 130, 246, 0.6),
                0 10px 24px rgba(15, 23, 42, 0.9);
        }

        .btn-view-surfaces span.icon {
            font-size: 13px;
        }

        .badge-count {
            font-size: 10px;
            padding: 1px 6px;
            border-radius: 999px;
            background: rgba(15, 23, 42, 0.96);
            border: 1px solid rgba(55, 65, 81, 0.9);
            color: #9ca3af;
        }

        .toast {
            position: fixed;
            bottom: 18px;
            right: 18px;
            background: rgba(15, 23, 42, 0.98);
            border-radius: 999px;
            padding: 8px 12px;
            font-size: 12px;
            color: #e5e7eb;
            border: 1px solid rgba(55, 65, 81, 0.9);
            box-shadow:
                0 0 0 1px rgba(15, 23, 42, 0.9),
                0 16px 40px rgba(15, 23, 42, 0.95);
            display: none;
            align-items: center;
            gap: 8px;
            z-index: 50;
        }

        .toast span.icon {
            font-size: 14px;
        }

        .toast.toast-success {
            border-color: rgba(34, 197, 94, 0.9);
            color: #bbf7d0;
        }

        .toast.toast-error {
            border-color: rgba(239, 68, 68, 0.9);
            color: #fecaca;
        }

        .toast.toast-info {
            border-color: rgba(59, 130, 246, 0.9);
            color: #bfdbfe;
        }

        .toast button {
            background: transparent;
            border: none;
            color: inherit;
            font-size: 14px;
            cursor: pointer;
            padding: 0;
            margin-left: 4px;
        }

        .pill-list {
            margin-top: 6px;
            display: flex;
            flex-wrap: wrap;
            gap: 4px;
            font-size: 11px;
            color: #9ca3af;
        }

        .pill-list span {
            padding: 2px 7px;
            border-radius: 999px;
            border: 1px solid rgba(55, 65, 81, 0.9);
            background: rgba(15, 23, 42, 0.96);
        }

        .pill-list span strong {
            color: #e5e7eb;
        }

        .nav-bar {
            display: flex;
            align-items: center;
            gap: 6px;
        }

        .nav-badge {
            font-size: 11px;
            padding: 2px 9px;
            border-radius: 999px;
            border: 1px solid rgba(148, 163, 184, 0.7);
            background: rgba(15, 23, 42, 0.96);
            color: #e5e7eb;
            display: inline-flex;
            align-items: center;
            gap: 7px;
        }

        .nav-badge span.dot {
            width: 6px;
            height: 6px;
            border-radius: 999px;
            background: #f97316;
            box-shadow: 0 0 12px rgba(249, 115, 22, 0.7);
        }

        .nav-badge code {
            font-size: 10px;
            color: #a5b4fc;
        }

        .nav-links {
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .nav-link {
            font-size: 11px;
            color: #9ca3af;
            text-decoration: none;
            padding: 3px 8px;
            border-radius: 999px;
            border: 1px solid transparent;
        }

        .nav-link:hover {
            border-color: rgba(55, 65, 81, 0.9);
            background: rgba(15, 23, 42, 0.96);
            color: #e5e7eb;
        }

        .nav-link-primary {
            border-color: rgba(59, 130, 246, 0.7);
            color: #bfdbfe;
            background: rgba(30, 64, 175, 0.9);
        }

        .nav-link-primary:hover {
            background: rgba(30, 64, 175, 0.98);
            box-shadow:
                0 0 0 1px rgba(37, 99, 235, 0.6),
                0 10px 26px rgba(15, 23, 42, 0.9);
        }

        .pill-small {
            font-size: 10px;
            padding: 2px 6px;
            border-radius: 999px;
            border: 1px solid rgba(55, 65, 81, 0.9);
            background: rgba(15, 23, 42, 0.96);
            color: #9ca3af;
        }

        .pill-small strong {
            color: #e5e7eb;
        }

        .btn-state-filter {
            padding: 2px 8px;
            font-size: 10px;
            border-radius: 999px;
            border: 1px solid rgba(55, 65, 81, 0.9);
            background: rgba(15, 23, 42, 0.96);
            color: #9ca3af;
            cursor: pointer;
        }

        .btn-state-filter.active {
            border-color: rgba(59, 130, 246, 0.8);
            background: rgba(30, 64, 175, 0.9);
            color: #bfdbfe;
        }

        .state-badges {
            margin-top: 6px;
            display: flex;
            flex-wrap: wrap;
            gap: 4px;
        }

        .btn-minimal {
            padding: 0;
            border: none;
            background: transparent;
            color: #9ca3af;
            font-size: 11px;
            cursor: pointer;
            text-decoration: underline;
        }

        .btn-minimal:hover {
            color: #e5e7eb;
        }
    </style>
</head>
<body>
    <header>
        <div class="left">
            <div>
                <h1>
                    LiveBarn Manager
                    <span class="badge">v2.0</span>
                </h1>
                <div class="subtitle">Favorites • Playlists • Streamlink Proxy</div>
            </div>
        </div>
        <div class="status-pill">
            Server Online
        </div>
    </header>

    <main>
        <!-- Favorites Panel (Left) -->
        <div class="panel favorites-panel">
            <div class="favorites-header">
                <div class="favorites-header-left">
                    <div class="favorites-title">
                        <span class="star">★</span>
                        Favorites
                    </div>
                    <div class="favorites-caption" id="favoritesCount">0 selected</div>
                </div>
                <button class="favorites-count-pill" onclick="refreshFavoritesList()" title="Refresh favorites">
                    🔄 Refresh
                </button>
            </div>

            <!-- M3U Playlist Card -->
            <div class="playlist-card">
                <div class="playlist-label">
                    <span class="indicator"></span>
                    M3U Playlist
                </div>
                <div class="playlist-url-row">
                    <div class="playlist-url" onclick="copyPlaylistUrl()" title="Click to copy">
                        <code id="playlistUrl">Loading...</code>
                    </div>
                    <button class="btn-copy" onclick="copyPlaylistUrl()" title="Copy to clipboard">
                        <span class="icon">📋</span>
                        Copy
                    </button>
                </div>
                <div class="playlist-hint">
                    Use this URL in <code>Channels DVR → Sources → Custom Channels</code>
                </div>
            </div>

            <!-- XMLTV Guide Card -->
            <div class="playlist-card">
                <div class="playlist-label">
                    <span class="indicator"></span>
                    XMLTV Guide
                </div>
                <div class="playlist-url-row">
                    <div class="playlist-url" onclick="copyXmltvUrl()" title="Click to copy">
                        <code>http://{{ server_host }}:{{ server_port }}/xmltv</code>
                    </div>
                    <button class="btn-copy" onclick="copyXmltvUrl()" title="Copy to clipboard">
                        <span class="icon">📋</span>
                        Copy
                    </button>
                </div>
                <div class="playlist-hint">
                    Use this URL in <code>Channels DVR → Settings → Guide Data → XMLTV</code>
                </div>
            </div>

            <!-- Favorites List -->
            <div class="favorites-list">
                <div id="favoritesListContainer">
                    <div class="favorites-empty">
                        <h3>Loading favorites...</h3>
                    </div>
                </div>
            </div>
        </div>

        <!-- Venues / Surfaces Panel -->
        <div class="panel">
            <div class="panel-header">
                <div>
                    <div class="panel-title">
                        <span class="dot"></span>
                        VENUES &amp; SURFACES
                    </div>
                    <div class="panel-subtitle">
                        Search by name/city, filter by state, then drill into each venue to select surfaces.
                    </div>
                </div>
                <div class="pill">
                    <span class="dot"></span>
                    <span><strong>DB:</strong> <code>{{ db_path }}</code></span>
                </div>
            </div>

            <div class="filters-row">
                <div>
                    <label for="searchInput">
                        Search
                        <span>(venue or city)</span>
                    </label>
                    <input
                        type="text"
                        id="searchInput"
                        class="input"
                        placeholder="Start typing to filter venue list..."
                        onkeydown="if (event.key === 'Enter') onFiltersChange()"
                    />
                </div>
                <div>
                    <label for="stateSelect">
                        State
                        <span>(optional)</span>
                    </label>
                    <select id="stateSelect" class="select" onchange="onFiltersChange()">
                        <option value="">All states</option>
                        {% for state in state_list %}
                        <option value="{{ state }}">{{ state }}</option>
                        {% endfor %}
                    </select>
                </div>
            </div>

            <div class="venues-container">
                <div class="venues-header">
                    <div class="venues-header-left">
                        <span class="count" id="venueCount">{{ venues|length }}</span>
                        venues
                        <span class="dim">(scrollable)</span>
                    </div>
                    <div class="venues-header-right">
                        <span class="pill-small">
                            <strong>Hint:</strong> Click a venue row to open its surfaces.
                        </span>
                    </div>
                </div>

                <div class="venues-list" id="venuesList">
                    {% for venue in venues %}
                    <div class="venue-row" onclick="openVenue({{ venue.id }})">
                        <div class="venue-marker"></div>
                        <div class="venue-main">
                            <div class="venue-name-line">
                                <span>{{ venue.name }}</span>
                                {% if venue.is_favorite_venue %}
                                <span class="badge">HAS FAVORITES</span>
                                {% endif %}
                            </div>
                            <div class="venue-location">
                                {{ venue.city }}, {{ venue.state }}{% if venue.country %}, {{ venue.country }}{% endif %}
                            </div>
                            <div class="venue-meta">
                                <span>Venue ID: {{ venue.id }}</span>
                                <span>UUID: {{ venue.uuid }}</span>
                            </div>
                        </div>
                        <div class="venue-actions">
                            {% if venue.favorite_count and venue.favorite_count > 0 %}
                            <span class="badge-count">{{ venue.favorite_count }} favorited surfaces</span>
                            {% else %}
                            <span class="badge-count">No favorites yet</span>
                            {% endif %}
                            <button class="btn-view-surfaces" type="button">
                                <span class="icon">➡</span>
                                View surfaces
                            </button>
                        </div>
                    </div>
                    {% endfor %}
                </div>
            </div>
        </div>
    
        <!-- Live Logs Panel -->
        <div class="panel logs-panel">
            <div class="panel-header">
                <div>
                    <div class="panel-title">
                        <span class="dot"></span>
                        LIVE LOGS
                    </div>
                    <div class="panel-subtitle">
                        Recent activity from the LiveBarn manager &amp; proxy (auto-refreshes every 4 seconds).
                    </div>
                </div>
            </div>
            <div class="logs-container">
                <pre id="liveLogs">Loading logs...</pre>
            </div>
        </div>
</main>

    <div class="toast" id="toast">
        <span class="icon">🔔</span>
        <span id="toastMessage"></span>
        <button onclick="hideToast()" aria-label="Close toast">&times;</button>
    </div>

    <script>
        const serverHost = "{{ server_host }}";
        const serverPort = "{{ server_port }}";

        function showToast(message, type = "info") {
            const toast = document.getElementById("toast");
            const msg = document.getElementById("toastMessage");

            toast.classList.add("toast-" + type);
            msg.textContent = message;
            toast.style.display = "flex";

            setTimeout(() => {
                hideToast();
            }, 3000);
        }

        function hideToast() {
            const toast = document.getElementById("toast");
            toast.style.display = "none";
        }

        async function copyPlaylistUrl() {
            const url = `http://${serverHost}:${serverPort}/playlist.m3u`;
            try {
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    await navigator.clipboard.writeText(url);
                } else {
                    // Fallback for older browsers / non-secure contexts
                    const tempInput = document.createElement("input");
                    tempInput.style.position = "fixed";
                    tempInput.style.left = "-1000px";
                    tempInput.value = url;
                    document.body.appendChild(tempInput);
                    tempInput.focus();
                    tempInput.select();
                    document.execCommand("copy");
                    document.body.removeChild(tempInput);
                }
                showToast("Playlist URL copied to clipboard", "success");
            } catch (err) {
                console.error("Clipboard error:", err);
                showToast("Failed to copy URL. Right-click and copy manually.", "error");
            }
        }

        async function copyXmltvUrl() {
            const url = `http://${serverHost}:${serverPort}/xmltv`;
            try {
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    await navigator.clipboard.writeText(url);
                } else {
                    // Fallback for older browsers / non-secure contexts
                    const tempInput = document.createElement("input");
                    tempInput.style.position = "fixed";
                    tempInput.style.left = "-1000px";
                    tempInput.value = url;
                    document.body.appendChild(tempInput);
                    tempInput.focus();
                    tempInput.select();
                    document.execCommand("copy");
                    document.body.removeChild(tempInput);
                }
                showToast("XMLTV URL copied to clipboard", "success");
            } catch (err) {
                console.error("Clipboard error:", err);
                showToast("Failed to copy XMLTV URL. Right-click and copy manually.", "error");
            }
        }

        function onFiltersChange() {
            const search = document.getElementById("searchInput").value;
            const state = document.getElementById("stateSelect").value;

            const params = new URLSearchParams(window.location.search);
            if (search) {
                params.set("search", search);
            } else {
                params.delete("search");
            }

            if (state) {
                params.set("state", state);
            } else {
                params.delete("state");
            }

            const newUrl =
                window.location.pathname + (params.toString() ? "?" + params.toString() : "");
            window.location.href = newUrl;
        }

        async function openVenue(venueId) {
            window.location.href = "/venue/" + venueId;
        }

        async function toggleFavorite(surfaceId) {
            try {
                const response = await fetch(`/api/favorites/${surfaceId}`, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json"
                    }
                });

                const data = await response.json();
                if (!response.ok || !data.success) {
                    showToast(data.message || "Failed to update favorite", "error");
                    return;
                }

                await refreshFavoritesList();
                showToast(data.message || "Favorite updated", "success");
            } catch (err) {
                console.error("toggleFavorite error:", err);
                showToast("Error updating favorite. Is the DB locked?", "error");
            }
        }

        async function refreshFavoritesList() {
            try {
                const response = await fetch("/api/favorites");
                const data = await response.json();

                const container = document.getElementById("favoritesListContainer");
                const countLabel = document.getElementById("favoritesCount");

                if (!data.success) {
                    container.innerHTML = `
                        <div class="favorites-empty">
                            <h3>Could not load favorites</h3>
                            <p>${data.message || "Unknown error."}</p>
                        </div>
                    `;
                    countLabel.textContent = "0 selected";
                    return;
                }

                const favorites = data.favorites || [];

                countLabel.textContent =
                    favorites.length === 1
                        ? "1 favorite"
                        : `${favorites.length} favorites`;

                if (!favorites.length) {
                    container.innerHTML = `
                        <div class="favorites-empty">
                            <h3>No favorites yet</h3>
                            <p>
                                Browse <strong>Venues & Surfaces</strong> on the right →
                                Click the <strong>star icon</strong> next to a surface to add it here.
                            </p>
                        </div>
                    `;
                    return;
                }

                let html = "";
                for (const fav of favorites) {
                    html += `
                        <div class="favorites-item">
                            <div class="favorites-leading"></div>
                            <div class="favorites-main">
                                <div class="venue-name">
                                    ${fav.venue_name || "Unknown venue"}
                                </div>
                                <div class="surface-name">
                                    ${fav.surface_name || "Surface"}
                                </div>
                                <div class="favorites-meta">
                                    <span>${fav.city || ""}, ${fav.state || ""}</span>
                                    <span>Surface ID: ${fav.surface_id}</span>
                                </div>
                            </div>
                            <div class="favorites-actions">
                                <div class="avatar">★</div>
                                <button
                                    class="btn-unfavorite"
                                    type="button"
                                    onclick="toggleFavorite(${fav.surface_id}); event.stopPropagation();"
                                >
                                    <span class="icon">✕</span>
                                    Remove
                                </button>
                            </div>
                        </div>
                    `;
                }
                container.innerHTML = html;
            } catch (err) {
                console.error("refreshFavoritesList error:", err);
            }
        }

        async function refreshLogs() {
            const el = document.getElementById("liveLogs");
            if (!el) return;
            try {
                const response = await fetch("/api/logs");
                if (!response.ok) {
                    throw new Error("HTTP " + response.status);
                }
                const data = await response.json();
                const lines = data.lines || [];
                el.textContent = lines.join("\n");
                // auto-scroll to bottom
                el.scrollTop = el.scrollHeight;
            } catch (err) {
                console.error("refreshLogs error:", err);
                el.textContent = "Error loading logs: " + err.message;
            }
        }

        function startLogPolling() {
            refreshLogs();
            setInterval(refreshLogs, 4000);
        }

        window.addEventListener("DOMContentLoaded", () => {
            refreshFavoritesList();
            const playlistUrlElem = document.getElementById("playlistUrl");
            if (playlistUrlElem) {
                playlistUrlElem.textContent = `http://${serverHost}:${serverPort}/playlist.m3u`;
            }
            startLogPolling();
        });
    </script>
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
        server_port=SERVER_PORT,
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
            <tr>
                <th>Surface Name</th>
                <th>Stream UUID</th>
                <th>Favorite?</th>
                <th>Action</th>
            </tr>
            {''.join(rows_html)}
        </table>
    </body>
    </html>
    """
    return table_html


@app.route('/toggle_favorite', methods=['POST'])
def toggle_favorite_route():
    """Legacy form-based endpoint (still works if you use the old table UI)."""
    surface_id = request.form.get('surface_id')
    if not surface_id:
        return "Missing surface_id", 400
    
    try:
        sid_int = int(surface_id)
    except ValueError:
        return "Invalid surface_id", 400
    
    try:
        action = toggle_favorite(sid_int)
    except sqlite3.OperationalError as e:
        logger.error(f"Database error during toggle_favorite: {e}")
        return "Database error (possibly locked). Try again.", 500
    
    return f"Favorite toggle action: {action}. <a href=\"/\">Back</a>"


@app.route('/api/favorites/<int:surface_id>', methods=['POST'])
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
        
        title = f"{venue_name} - {surface_name}"
        if city or state:
            location = f" ({city}, {state})" if city and state else f" ({city or state})"
        else:
            location = ""
        
        # Build guide description
        description = f"🎥 Live camera feed from {venue_name} - {surface_name}"
        if city and state:
            description += f" in {city}, {state}"
        
        proxy_url = f"http://{SERVER_HOST_URL}:{SERVER_PORT}/proxy/{surface_id}"
        
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
    
    # Use cached schedule data from all providers
    events_by_surface = SCHEDULE_CACHE.get('events_by_surface', {})
    last_updated = SCHEDULE_CACHE.get('last_updated')
    
    # Create root TV element
    tv = ET.Element('tv')
    tv.set('generator-info-name', 'LiveBarn Manager + Chiller')
    tv.set('generator-info-url', f'http://{SERVER_HOST_URL}:{SERVER_PORT}')
    
    # Time range for programs
    now = datetime.now()
    tz_offset = now.astimezone().strftime('%z')
    today_start = datetime.combine(now.date(), dt_time(0, 0))
    tomorrow_end = datetime.combine(now.date() + timedelta(days=2), dt_time(0, 0))
    
    # Create channels
    for fav in favorites:
        surface_id = fav['surface_id']
        venue_name = fav.get('venue_name', 'Unknown Venue')
        surface_name = fav.get('surface_name', 'Surface')
        city = fav.get('city', '')
        state = fav.get('state', '')
        
        title = f"{venue_name} - {surface_name}"
        if city and state:
            location_str = f"{city}, {state}"
        elif city or state:
            location_str = city or state
        else:
            location_str = ""
        
        channel = ET.SubElement(tv, 'channel')
        channel.set('id', str(surface_id))
        
        display_name = ET.SubElement(channel, 'display-name')
        display_name.text = title
        
        if location_str:
            display_name_loc = ET.SubElement(channel, 'display-name')
            display_name_loc.text = location_str
        
        # Icon
        icon = ET.SubElement(channel, 'icon')
        icon.set('src', 'https://www.thechiller.com/assets/images/logo_300.png')
    
    # Create programs
    for fav in favorites:
        surface_id = fav['surface_id']
        venue_name = fav.get('venue_name', 'Unknown Venue')
        surface_name = fav.get('surface_name', 'Surface')
        city = fav.get('city', '')
        state = fav.get('state', '')
        
        # Get Chiller events for this surface
        surface_events = events_by_surface.get(surface_id, [])
        
        if surface_events:
            # We have Chiller schedule data - create real programs with Open Ice fillers
            programs = fill_gaps_with_open_ice(surface_events, today_start, tomorrow_end)
        else:
            # No Chiller data - create generic 24-hour live block
            start_time = now - timedelta(hours=6)
            end_time = now + timedelta(hours=18)
            programs = [(start_time, end_time, f"🔴 LIVE: {venue_name} - {surface_name}")]
        
        # Create programme elements
        for prog_start, prog_end, prog_title in programs:
            programme = ET.SubElement(tv, 'programme')
            programme.set('channel', str(surface_id))
            programme.set('start', prog_start.strftime('%Y%m%d%H%M%S ') + tz_offset)
            programme.set('stop', prog_end.strftime('%Y%m%d%H%M%S ') + tz_offset)
            
            # Program title
            title_elem = ET.SubElement(programme, 'title')
            title_elem.set('lang', 'en')
            title_elem.text = prog_title
            
            # Description
            desc_parts = [prog_title, f"{venue_name} - {surface_name}"]
            desc = ET.SubElement(programme, 'desc')
            desc.set('lang', 'en')
            desc.text = "\n".join(desc_parts)
            
            # Category / sub-category (skip for Open Ice placeholders)
            if "Open Ice" not in prog_title:
                category = ET.SubElement(programme, 'category')
                category.set('lang', 'en')
                category.text = "Sports"
                
                sub_category = ET.SubElement(programme, 'category')
                sub_category.set('lang', 'en')
                sub_category.text = "Ice Hockey"
                
                provider_category = ET.SubElement(programme, 'category')
                provider_category.set('lang', 'en')
                provider_category.text = "Livebarn"
                
                # Live flag
                ET.SubElement(programme, 'live')
    
    # Convert to string with proper XML declaration
    xml_string = ET.tostring(tv, encoding='unicode')
    xml_output = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml_output += '<!DOCTYPE tv SYSTEM "xmltv.dtd">\n'
    xml_output += xml_string
    
    return Response(
        xml_output,
        mimetype='application/xml',
        headers={
            'Content-Type': 'application/xml; charset=utf-8'
        }
    )





@app.route('/proxy/<int:surface_id>')
def proxy_stream(surface_id):
    """
    Streamlink proxy with automatic token refresh.
    If stream URL is expired/old, auto-refreshes it before streaming.
    """
    stream_info = get_stream_info(surface_id)
    
    # Check if we need to refresh the token
    needs_refresh = False
    
    if not stream_info or not stream_info.get('playlist_url'):
        logger.warning(f"❌ No stream found for surface_id={surface_id}, will try to capture")
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
        logger.warning(f"Database file does not exist at: {DB_PATH}")
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
        logger.warning(f"The following required tables are missing: {missing}")
    else:
        logger.info("All expected tables found in database.")
    
    conn.close()


if __name__ == '__main__':
    init_db_if_needed()
    
    print("=" * 70)
    print(" LiveBarn Favorites Manager & Streamlink Proxy ".center(70, "="))
    print(f"  Database: {DB_PATH}")
    print(f"  Playlist: http://{SERVER_HOST_URL}:{SERVER_PORT}/playlist.m3u")
    print(f"  XMLTV:    http://{SERVER_HOST_URL}:{SERVER_PORT}/xmltv")
    print(f"  Server:   http://{SERVER_HOST_URL} (LAN IP detected)")
    print("=" * 70)
    print()
    print("📖 Open http://localhost:{SERVER_PORT} in your browser to:")
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


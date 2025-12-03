#!/usr/bin/env python3
"""
Refresh a single stream by surface_id
Used by livebarn_manager.py for auto-refresh on demand
"""

import asyncio
import sys
import sqlite3
import json
import os
from pathlib import Path
from datetime import datetime
from playwright.async_api import async_playwright

DB_PATH = Path(os.getenv('DB_PATH', '/data/livebarn.db'))

def get_credentials():
    """Get credentials from environment or JSON file"""
    email = os.getenv('LIVEBARN_EMAIL')
    password = os.getenv('LIVEBARN_PASSWORD')
    
    if email and password:
        return {'email': email, 'password': password}
    
    # Fallback to credentials file
    creds_file = Path(__file__).parent / 'livebarn_credentials.json'
    if creds_file.exists():
        with open(creds_file) as f:
            return json.load(f)
    
    raise ValueError("No credentials found. Set LIVEBARN_EMAIL and LIVEBARN_PASSWORD environment variables.")

async def refresh_single_stream(surface_id):
    """Refresh a single stream quickly"""
    
    # Get stream info
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute('''
        SELECT s.id, v.name, s.name
        FROM surfaces s
        JOIN venues v ON s.venue_id = v.id
        WHERE s.id = ?
    ''', (surface_id,))
    
    result = c.fetchone()
    conn.close()
    
    if not result:
        print(f"Surface {surface_id} not found", file=sys.stderr)
        return False
    
    _, venue_name, surface_name = result
    
    # Load credentials
    creds = get_credentials()
    
    # Quick browser capture
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)  # Use chromium, not chrome channel
        context = await browser.new_context()
        page = await context.new_page()
        
        captured_url = None
        
        async def handle_response(response):
            nonlocal captured_url
            url = response.url
            if 'cdn-akamai-livebarn.akamaized.net' in url and '.m3u8' in url and 'hdnts=' in url:
                if 'chunklist_' not in url.lower() and not captured_url:
                    captured_url = url
        
        page.on('response', handle_response)
        
        # Navigate and login
        await page.goto('https://watch.livebarn.com')
        await page.wait_for_load_state('domcontentloaded')
        await asyncio.sleep(1)
        
        try:
            await page.fill('input[name="username"]', creds['email'])
            await page.fill('input[type="password"]', creds['password'])
            await page.click('button:has-text("LOG IN")')
            await asyncio.sleep(2)
        except:
            pass
        
        # Navigate to stream
        stream_url = f'https://watch.livebarn.com/en/video/{surface_id}/live'
        try:
            await page.goto(stream_url, wait_until='domcontentloaded', timeout=20000)
        except Exception as e:
            print(f"Navigation error: {e}", file=sys.stderr)
            await browser.close()
            return False
        
        # Wait a bit for stream to load
        await asyncio.sleep(3)
        
        # Wait for URL
        import time
        start = time.time()
        while not captured_url and (time.time() - start) < 15:
            await asyncio.sleep(0.2)
        
        await browser.close()
        
        if captured_url:
            # Save to database
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            
            c.execute('''
                INSERT OR REPLACE INTO surface_streams (
                    surface_id, venue_name, surface_name, playlist_url,
                    full_captured_url, captured_at
                ) VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                surface_id, venue_name, surface_name, captured_url,
                captured_url, datetime.now().isoformat()
            ))
            
            conn.commit()
            conn.close()
            
            print(f"SUCCESS: Refreshed {venue_name} - {surface_name}")
            return True
        else:
            print(f"FAILED: Could not capture {venue_name} - {surface_name}", file=sys.stderr)
            return False

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python refresh_single.py <surface_id>", file=sys.stderr)
        sys.exit(1)
    
    surface_id = int(sys.argv[1])
    success = asyncio.run(refresh_single_stream(surface_id))
    sys.exit(0 if success else 1)

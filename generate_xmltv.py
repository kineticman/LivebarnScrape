#!/usr/bin/env python3
"""
LiveBarn XMLTV EPG Generator with Chiller Schedule Integration
Creates EPG guide data for Channels DVR with real event schedules
"""

import sqlite3
import socket
import requests
from pathlib import Path
from datetime import datetime, timedelta, time
from typing import List, Dict, Tuple, Optional
import xml.etree.ElementTree as ET
from xml.dom import minidom

DB_PATH = Path(__file__).parent / 'livebarn.db'
SERVER_PORT = 5000
CHILLER_API_BASE = "https://thechiller.com/admin/scheduler/init-scheduler-live.cfm"
LGRIA_SCHEDULE_URL = "https://lgria.finnlyconnect.com/schedule/201"

# Map Chiller product IDs to LiveBarn surface IDs
CHILLER_TO_LIVEBARN = {
    "1": 864,   # Dublin 1
    "2": 865,   # Dublin 2
    "5": 867,   # Easton 1
    "6": 866,   # Easton 2
    "8": 868,   # North 1
    "9": 869,   # North 2
    "13": 872,  # Ice Haus
    "14": 871,  # Ice Works
    "16": 873,  # Springfield
    "24": 870,  # North 3
}

# Reverse mapping for quick lookups
LIVEBARN_TO_CHILLER = {v: k for k, v in CHILLER_TO_LIVEBARN.items()}

# Ice sheet product IDs (skip rooms/gyms)
ICE_SHEET_PRODUCT_IDS = {"1", "2", "5", "6", "8", "9", "13", "14", "16", "24"}

# Lou and Gib Reese Ice Arena - Newark
LGRIA_SURFACE_ID = 2445


def get_lan_ip():
    """Get the local non-loopback IP address"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def extract_js_list_variable(html: str, var_name: str) -> str:
    """
    Find a JS variable assignment like:
        var_name = [ {...}, {...}, ... ];
    and return the raw text of the [...] part (as a string that is valid JSON).
    """
    import json
    marker = var_name + " ="
    idx = html.find(marker)
    if idx == -1:
        raise RuntimeError(f"Could not find variable {var_name!r} in HTML")

    # Find first '[' after the assignment
    start = html.find("[", idx)
    if start == -1:
        raise RuntimeError(f"No '[' found after {var_name!r} assignment")

    depth = 0
    end = None
    for i, ch in enumerate(html[start:], start=start):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                end = i
                break

    if end is None:
        raise RuntimeError(f"Could not find matching ']' for {var_name!r}")

    return html[start : end + 1]


def get_all_streams():
    """Get all streams with venue/surface info"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute('''
        SELECT 
            surf.id as surface_id,
            v.name as venue_name,
            v.address,
            v.city,
            v.state,
            v.country,
            surf.name as surface_name,
            ss.playlist_url IS NOT NULL as has_stream
        FROM surfaces surf
        JOIN venues v ON surf.venue_id = v.id
        LEFT JOIN favorites f ON surf.id = f.surface_id
        LEFT JOIN surface_streams ss ON surf.id = ss.surface_id
        WHERE f.id IS NOT NULL  -- Only favorites
        ORDER BY v.name, surf.name
    ''')
    
    streams = c.fetchall()
    conn.close()
    
    return streams


def fetch_chiller_schedule(start_date: datetime, end_date: datetime) -> List[Dict[str, str]]:
    """
    Fetch schedule data from Chiller API
    Returns list of event dicts with keys: id, start_date, end_date, text, productid, etc.
    """
    try:
        params = {
            "timeshift": "300",  # Eastern (UTC-5)
            "uid": "1",
            "from": start_date.strftime("%Y-%m-%d"),
            "to": end_date.strftime("%Y-%m-%d"),
        }
        
        print(f"   🔍 Fetching Chiller schedule: {start_date.date()} to {end_date.date()}")
        
        resp = requests.get(CHILLER_API_BASE, params=params, timeout=15)
        resp.raise_for_status()
        
        root = ET.fromstring(resp.text)
        events: List[Dict[str, str]] = []
        
        for ev in root.findall("event"):
            record: Dict[str, str] = {"id": ev.get("id", "")}
            for child in ev:
                record[child.tag] = (child.text or "").strip()
            
            # Only include ice sheet events
            if record.get("productid") in ICE_SHEET_PRODUCT_IDS:
                events.append(record)
        
        print(f"   ✅ Found {len(events)} ice sheet events")
        return events
        
    except Exception as e:
        print(f"   ⚠️  Failed to fetch Chiller schedule: {e}")
        return []


def fetch_lgria_schedule() -> List[Dict]:
    """
    Fetch schedule data from LGRIA website
    Returns list of event dicts with keys: StartTime, EndTime, EventName, etc.
    Events are in EST timezone.
    """
    import json
    try:
        print(f"   🔍 Fetching LGRIA schedule...")
        
        resp = requests.get(LGRIA_SCHEDULE_URL, timeout=15)
        resp.raise_for_status()
        html = resp.text
        
        # Extract the JavaScript array
        raw_list = extract_js_list_variable(html, "_onlineScheduleList")
        events = json.loads(raw_list)
        
        print(f"   ✅ Found {len(events)} LGRIA events")
        return events
        
    except Exception as e:
        print(f"   ⚠️  Failed to fetch LGRIA schedule: {e}")
        return []


def parse_chiller_datetime(dt_str: str) -> Optional[datetime]:
    """Parse Chiller datetime string: '2025-12-02 09:30:00.0'"""
    try:
        return datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S.%f")
    except (ValueError, AttributeError):
        return None


def parse_lgria_datetime(dt_str: str) -> Optional[datetime]:
    """
    Parse LGRIA datetime string: '2025-11-26T12:00:00' (ISO 8601 format)
    These datetimes are already in EST (UTC-5).
    """
    try:
        return datetime.strptime(dt_str, "%Y-%m-%dT%H:%M:%S")
    except (ValueError, AttributeError):
        return None


def group_events_by_surface(events: List[Dict[str, str]]) -> Dict[int, List[Dict[str, str]]]:
    """Group Chiller events by LiveBarn surface_id"""
    grouped: Dict[int, List[Dict[str, str]]] = {}
    
    for event in events:
        product_id = event.get("productid", "")
        surface_id = CHILLER_TO_LIVEBARN.get(product_id)
        
        if surface_id:
            if surface_id not in grouped:
                grouped[surface_id] = []
            grouped[surface_id].append(event)
    
    # Sort events by start time for each surface
    for surface_id in grouped:
        grouped[surface_id].sort(key=lambda e: e.get("start_date", ""))
    
    return grouped


def process_lgria_events(lgria_events: List[Dict], start_date: datetime, end_date: datetime) -> List[Dict[str, str]]:
    """
    Convert LGRIA events to standardized format compatible with Chiller format.
    Filter to only include events within the date range.
    Returns list of dicts with 'start_date', 'end_date', 'text' keys.
    """
    processed = []
    
    for event in lgria_events:
        # Use correct field names: EventStartTime, EventEndTime
        start_time = parse_lgria_datetime(event.get("EventStartTime", ""))
        end_time = parse_lgria_datetime(event.get("EventEndTime", ""))
        
        if not start_time or not end_time:
            continue
        
        # Filter to date range
        if start_time < start_date or start_time >= end_date:
            continue
        
        # Use Description as the event name, fallback to AccountName
        event_name = event.get("Description", "") or event.get("AccountName", "Ice Time")
        
        # Convert to Chiller-compatible format
        processed.append({
            "start_date": start_time.strftime("%Y-%m-%d %H:%M:%S.0"),
            "end_date": end_time.strftime("%Y-%m-%d %H:%M:%S.0"),
            "text": event_name.strip()
        })
    
    # Sort by start time
    processed.sort(key=lambda e: e["start_date"])
    return processed


def fill_gaps_with_open_ice(events: List[Dict[str, str]], start: datetime, end: datetime) -> List[Tuple[datetime, datetime, str]]:
    """
    Take sorted events and fill gaps with 'Open Ice' programs
    Returns list of (start_time, end_time, title) tuples
    """
    programs: List[Tuple[datetime, datetime, str]] = []
    current_time = start
    
    for event in events:
        event_start = parse_chiller_datetime(event.get("start_date", ""))
        event_end = parse_chiller_datetime(event.get("end_date", ""))
        
        if not event_start or not event_end:
            continue
        
        # Fill gap before this event with "Open Ice" in 1-hour blocks
        while current_time < event_start:
            gap_end = min(current_time + timedelta(hours=1), event_start)
            # Generic open ice block
            programs.append((current_time, gap_end, "Open Ice"))
            current_time = gap_end
        
        # Add the actual event
        event_title = event.get("text", "Ice Time").strip()
        if event_title:
            programs.append((event_start, event_end, event_title))
        else:
            programs.append((event_start, event_end, "Ice Time"))
        
        current_time = event_end
    
    # Fill remaining time until end with "Open Ice"
    while current_time < end:
        gap_end = min(current_time + timedelta(hours=1), end)
        programs.append((current_time, gap_end, "Open Ice"))
        current_time = gap_end
    
    return programs


def create_xmltv():
    """Generate XMLTV file for all streams with Chiller schedule integration"""
    
    print("=" * 70)
    print("  📺 LiveBarn XMLTV EPG Generator (with Chiller Schedule)")
    print("=" * 70)
    print()
    
    host = get_lan_ip()
    streams = get_all_streams()
    
    if not streams:
        print("⚠️  No favorite streams found")
        return False
    
    print(f"📋 Found {len(streams)} favorite streams")
    print()
    
    # Fetch Chiller schedule for today and tomorrow
    now = datetime.now()
    # Local timezone offset string for XMLTV (e.g. "-0500")
    tz_offset = now.astimezone().strftime('%z')
    today_start = datetime.combine(now.date(), time(0, 0))
    tomorrow_end = datetime.combine(now.date() + timedelta(days=2), time(0, 0))

    chiller_events = fetch_chiller_schedule(today_start, tomorrow_end)
    events_by_surface = group_events_by_surface(chiller_events)
    
    # Fetch LGRIA schedule
    lgria_raw_events = fetch_lgria_schedule()
    lgria_events = process_lgria_events(lgria_raw_events, today_start, tomorrow_end)
    
    # Add LGRIA events to events_by_surface
    if lgria_events:
        events_by_surface[LGRIA_SURFACE_ID] = lgria_events
    
    print()
    print(f"🗓️  Generating EPG from {today_start.date()} to {tomorrow_end.date()}")
    print()
    
    # Create root element
    tv = ET.Element('tv')
    tv.set('generator-info-name', 'LiveBarn XMLTV Generator + Chiller')
    tv.set('generator-info-url', f'http://{host}:{SERVER_PORT}')
    
    # Create channels and programs
    for surface_id, venue_name, address, city, state, country, surface_name, has_stream in streams:
        
        # Channel ID
        channel_id = f'livebarn.{surface_id}'
        
        # Full channel name
        if venue_name and surface_name:
            full_name = f"{venue_name} - {surface_name}"
        else:
            full_name = f"Surface {surface_id}"
        
        # Location string
        location_parts = [city, state, country]
        location = ", ".join([p for p in location_parts if p])
        
        # Create channel element
        channel = ET.SubElement(tv, 'channel')
        channel.set('id', channel_id)
        
        # Display name
        display_name = ET.SubElement(channel, 'display-name')
        display_name.text = full_name
        
        # Icon for this channel
        icon = ET.SubElement(channel, 'icon')
        icon.set('src', 'https://www.thechiller.com/assets/images/logo_300.png')
        
        # Generate programs based on Chiller schedule or generic blocks
        surface_events = events_by_surface.get(surface_id, [])
        
        if surface_events:
            # We have Chiller schedule data for this rink!
            programs = fill_gaps_with_open_ice(surface_events, today_start, tomorrow_end)
            print(f"   ✅ {full_name}: {len(programs)} programs from Chiller schedule")
        else:
            # No Chiller data - create generic 24-hour live blocks
            start_time = now - timedelta(hours=6)
            end_time = now + timedelta(hours=18)
            programs = [(start_time, end_time, f"🔴 LIVE: {full_name}")]
            print(f"   📹 {full_name}: Generic live feed (no Chiller schedule)")
        
        # Create programme elements
        for prog_start, prog_end, prog_title in programs:
            programme = ET.SubElement(tv, 'programme')
            programme.set('channel', channel_id)
            # Include explicit local timezone offset so Channels DVR lines up times correctly
            programme.set('start', prog_start.strftime('%Y%m%d%H%M%S ') + tz_offset)
            programme.set('stop', prog_end.strftime('%Y%m%d%H%M%S ') + tz_offset)
            
            # Program title
            title = ET.SubElement(programme, 'title')
            title.set('lang', 'en')
            title.text = prog_title
            
            # Description with details (simple title + rink)
            desc_parts = []
            # First line: programme title again
            desc_parts.append(prog_title)
            # Second line: facility / rink label
            desc_parts.append(f"{venue_name} - {surface_name}")


# URL intentionally omitted from description; guide should stay clean.

            desc = ET.SubElement(programme, 'desc')
            desc.set('lang', 'en')
            desc.text = "\n".join(desc_parts)


# Category / sub-category
            # For Open Ice placeholder blocks, do not set any category tags.
            if "Open Ice" not in prog_title:
                category = ET.SubElement(programme, 'category')
                category.set('lang', 'en')
                category.text = "Sports"

                sub_category = ET.SubElement(programme, 'category')
                sub_category.set('lang', 'en')
                sub_category.text = "Ice Hockey"

                # Additional provider/category tag to identify LiveBarn-originated content
                provider_category = ET.SubElement(programme, 'category')
                provider_category.set('lang', 'en')
                provider_category.text = "Livebarn"


# Live flag: only tag non-placeholder (non-"Open Ice") blocks as live
            if "Open Ice" not in prog_title:
                live = ET.SubElement(programme, 'live')
    
    # Pretty print XML
    xml_string = ET.tostring(tv, encoding='unicode')
    dom = minidom.parseString(xml_string)
    pretty_xml = dom.toprettyxml(indent='  ')
    
    # Remove extra blank lines
    lines = [line for line in pretty_xml.split('\n') if line.strip()]
    pretty_xml = '\n'.join(lines)
    
    # Save to file
    output_file = Path('livebarn.xml')
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(pretty_xml)
    
    print()
    print("=" * 70)
    print(f"✅ XMLTV file generated!")
    print(f"   File: {output_file.absolute()}")
    print(f"   Channels: {len(streams)}")
    chiller_count = sum(1 for sid in [s[0] for s in streams] if sid in events_by_surface and sid != LGRIA_SURFACE_ID)
    lgria_count = 1 if LGRIA_SURFACE_ID in events_by_surface and LGRIA_SURFACE_ID in [s[0] for s in streams] else 0
    print(f"   Chiller-enhanced: {chiller_count} channels")
    if lgria_count:
        print(f"   LGRIA-enhanced: {lgria_count} channel")
    print()
    print("📺 Add to Channels DVR:")
    print(f"   1. M3U URL: http://{host}:{SERVER_PORT}/playlist.m3u")
    print(f"   2. XMLTV URL: http://{host}:{SERVER_PORT}/xmltv")
    print()
    print("   Or use local files:")
    print(f"   1. M3U: {Path('livebarn.m3u').absolute()}")
    print(f"   2. XMLTV: {output_file.absolute()}")
    print("=" * 70)
    
    return True


if __name__ == '__main__':
    import sys
    
    success = create_xmltv()
    sys.exit(0 if success else 1)

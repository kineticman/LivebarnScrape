"""
Schedule utilities for managing and converting schedule events
"""

from datetime import datetime, timedelta
from typing import List, Dict, Tuple
from schedule_providers import ScheduleEvent


def events_to_legacy_format(events: List[ScheduleEvent]) -> List[Dict[str, str]]:
    """
    Convert ScheduleEvent objects to legacy dict format for backward compatibility
    Legacy format: {"start_date": "2025-12-03 09:00:00.0", "end_date": "...", "text": "..."}
    """
    legacy_events = []
    
    for event in events:
        legacy_events.append({
            "start_date": event.start_time.strftime("%Y-%m-%d %H:%M:%S.0"),
            "end_date": event.end_time.strftime("%Y-%m-%d %H:%M:%S.0"),
            "text": event.title
        })
    
    return legacy_events


def group_events_by_surface(events: List[ScheduleEvent]) -> Dict[int, List[Dict[str, str]]]:
    """
    Group events by surface_id and convert to legacy format
    Returns: {surface_id: [legacy_event_dicts]}
    """
    grouped: Dict[int, List[Dict[str, str]]] = {}
    
    for event in events:
        if event.surface_id not in grouped:
            grouped[event.surface_id] = []
        
        # Convert to legacy format
        legacy_event = {
            "start_date": event.start_time.strftime("%Y-%m-%d %H:%M:%S.0"),
            "end_date": event.end_time.strftime("%Y-%m-%d %H:%M:%S.0"),
            "text": event.title
        }
        grouped[event.surface_id].append(legacy_event)
    
    # Sort events by start time for each surface
    for surface_id in grouped:
        grouped[surface_id].sort(key=lambda e: e["start_date"])
    
    return grouped


def fill_gaps_with_open_ice(events: List[Dict[str, str]], start: datetime, end: datetime) -> List[Tuple[datetime, datetime, str]]:
    """
    Take sorted events and fill gaps with 'Open Ice' programs
    Returns list of (start_time, end_time, title) tuples
    
    NOTE: This function still uses legacy format for backward compatibility
    """
    from datetime import datetime
    
    def parse_datetime(dt_str: str) -> datetime:
        """Parse legacy datetime format"""
        return datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S.%f")
    
    programs: List[Tuple[datetime, datetime, str]] = []
    current_time = start
    
    for event in events:
        event_start = parse_datetime(event.get("start_date", ""))
        event_end = parse_datetime(event.get("end_date", ""))
        
        if not event_start or not event_end:
            continue
        
        # Fill gap before this event with "Open Ice" in 1-hour blocks
        while current_time < event_start:
            gap_end = min(current_time + timedelta(hours=1), event_start)
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

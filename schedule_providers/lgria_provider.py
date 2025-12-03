"""
Lou & Gib Reese Ice Arena (LGRIA) Schedule Provider
Fetches schedule from embedded JavaScript on webpage
"""

import requests
import logging
import json
from datetime import datetime
from typing import List, Dict, Optional

from .base_provider import ScheduleProvider, ScheduleEvent

logger = logging.getLogger(__name__)


class LGRIAProvider(ScheduleProvider):
    """Schedule provider for Lou & Gib Reese Ice Arena"""
    
    SCHEDULE_URL = "https://lgria.finnlyconnect.com/schedule/201"
    SURFACE_ID = 2445  # Lou & Gib Reese Ice Arena - Newark
    
    @property
    def name(self) -> str:
        return "Lou & Gib Reese Ice Arena"
    
    @property
    def surface_mappings(self) -> Dict[str, int]:
        # LGRIA only has one rink, so we use a simple mapping
        return {"rink1": self.SURFACE_ID}
    
    def fetch_schedule(self, start_date: datetime, end_date: datetime) -> List[ScheduleEvent]:
        """Fetch schedule from LGRIA website"""
        try:
            logger.info(f"🔍 Fetching {self.name} schedule...")
            
            resp = requests.get(self.SCHEDULE_URL, timeout=15)
            resp.raise_for_status()
            html = resp.text
            
            # Extract the JavaScript array
            raw_list = self._extract_js_list_variable(html, "_onlineScheduleList")
            raw_events = json.loads(raw_list)
            
            logger.info(f"✅ Found {len(raw_events)} {self.name} raw events")
            
            # Convert to standardized events
            events: List[ScheduleEvent] = []
            
            for raw_event in raw_events:
                start_time = self._parse_datetime(raw_event.get("EventStartTime", ""))
                end_time = self._parse_datetime(raw_event.get("EventEndTime", ""))
                
                if not start_time or not end_time:
                    continue
                
                # Filter to date range
                if start_time < start_date or start_time >= end_date:
                    continue
                
                # Use Description as event name, fallback to AccountName
                event_name = raw_event.get("Description", "") or raw_event.get("AccountName", "Ice Time")
                
                event = ScheduleEvent(
                    surface_id=self.SURFACE_ID,
                    start_time=start_time,
                    end_time=end_time,
                    title=event_name.strip(),
                    description=raw_event.get("ScheduleNotes", ""),
                    event_type=raw_event.get("EventTypeName", ""),
                    raw_data=raw_event
                )
                events.append(event)
            
            # Sort by start time
            events.sort(key=lambda e: e.start_time)
            
            logger.info(f"✅ Processed {len(events)} {self.name} events in date range")
            return events
            
        except Exception as e:
            logger.error(f"⚠️  Failed to fetch {self.name} schedule: {e}")
            return []
    
    def _extract_js_list_variable(self, html: str, var_name: str) -> str:
        """
        Find a JS variable assignment like:
            var_name = [ {...}, {...}, ... ];
        and return the raw text of the [...] part (as a string that is valid JSON).
        """
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
    
    def _parse_datetime(self, dt_str: str) -> Optional[datetime]:
        """
        Parse LGRIA datetime string: '2025-11-26T12:00:00' (ISO 8601 format)
        These datetimes are already in EST (UTC-5).
        """
        try:
            return datetime.strptime(dt_str, "%Y-%m-%dT%H:%M:%S")
        except (ValueError, AttributeError):
            return None


# Create singleton instance for easy import
lgria_provider = LGRIAProvider()

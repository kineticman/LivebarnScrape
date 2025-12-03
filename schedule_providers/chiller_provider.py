"""
OhioHealth Chiller Ice Rinks Schedule Provider
Fetches schedules from Chiller's XML API
"""

import requests
import logging
from datetime import datetime
from typing import List, Dict, Optional
import xml.etree.ElementTree as ET

from .base_provider import ScheduleProvider, ScheduleEvent

logger = logging.getLogger(__name__)


class ChillerProvider(ScheduleProvider):
    """Schedule provider for OhioHealth Chiller ice rinks"""
    
    API_BASE = "https://thechiller.com/admin/scheduler/init-scheduler-live.cfm"
    
    # Map Chiller product IDs to LiveBarn surface IDs
    SURFACE_MAPPINGS = {
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
    
    # Ice sheet product IDs (skip rooms/gyms)
    ICE_SHEET_PRODUCT_IDS = {"1", "2", "5", "6", "8", "9", "13", "14", "16", "24"}
    
    @property
    def name(self) -> str:
        return "OhioHealth Chiller"
    
    @property
    def surface_mappings(self) -> Dict[str, int]:
        return self.SURFACE_MAPPINGS
    
    def fetch_schedule(self, start_date: datetime, end_date: datetime) -> List[ScheduleEvent]:
        """Fetch schedule from Chiller XML API"""
        try:
            params = {
                "timeshift": "300",  # Eastern (UTC-5)
                "uid": "1",
                "from": start_date.strftime("%Y-%m-%d"),
                "to": end_date.strftime("%Y-%m-%d"),
            }
            
            logger.info(f"🔍 Fetching {self.name} schedule: {start_date.date()} to {end_date.date()}")
            
            resp = requests.get(self.API_BASE, params=params, timeout=15)
            resp.raise_for_status()
            
            root = ET.fromstring(resp.text)
            events: List[ScheduleEvent] = []
            
            for ev in root.findall("event"):
                product_id = None
                raw_event = {"id": ev.get("id", "")}
                
                for child in ev:
                    raw_event[child.tag] = (child.text or "").strip()
                
                product_id = raw_event.get("productid", "")
                
                # Only include ice sheet events
                if product_id not in self.ICE_SHEET_PRODUCT_IDS:
                    continue
                
                # Get surface ID
                surface_id = self.SURFACE_MAPPINGS.get(product_id)
                if not surface_id:
                    continue
                
                # Parse datetime
                start_time = self._parse_datetime(raw_event.get("start_date", ""))
                end_time = self._parse_datetime(raw_event.get("end_date", ""))
                
                if not start_time or not end_time:
                    continue
                
                event = ScheduleEvent(
                    surface_id=surface_id,
                    start_time=start_time,
                    end_time=end_time,
                    title=raw_event.get("text", "Ice Time").strip(),
                    raw_data=raw_event
                )
                events.append(event)
            
            logger.info(f"✅ Found {len(events)} {self.name} ice sheet events")
            return events
            
        except Exception as e:
            logger.error(f"⚠️  Failed to fetch {self.name} schedule: {e}")
            return []
    
    def _parse_datetime(self, dt_str: str) -> Optional[datetime]:
        """Parse Chiller datetime string: '2025-12-02 09:30:00.0'"""
        try:
            return datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S.%f")
        except (ValueError, AttributeError):
            return None


# Create singleton instance for easy import
chiller_provider = ChillerProvider()

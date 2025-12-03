"""
Base class for schedule providers
All schedule providers should inherit from this
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Dict, Optional
from dataclasses import dataclass


@dataclass
class ScheduleEvent:
    """Standardized event format that all providers return"""
    surface_id: int
    start_time: datetime
    end_time: datetime
    title: str
    description: Optional[str] = None
    event_type: Optional[str] = None  # "game", "practice", "public_skate", etc.
    raw_data: Optional[Dict] = None  # Original data for debugging


class ScheduleProvider(ABC):
    """
    Abstract base class for schedule providers.
    Each rink/facility should implement this interface.
    """
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name for logging"""
        pass
    
    @property
    @abstractmethod
    def surface_mappings(self) -> Dict[str, int]:
        """
        Map external IDs to LiveBarn surface IDs
        Example: {"1": 864, "2": 865} for Chiller Dublin rinks
        """
        pass
    
    @abstractmethod
    def fetch_schedule(self, start_date: datetime, end_date: datetime) -> List[ScheduleEvent]:
        """
        Fetch schedule events from the provider
        Returns list of standardized ScheduleEvent objects
        """
        pass
    
    def is_enabled(self) -> bool:
        """
        Check if provider is enabled (can be overridden)
        Default: always enabled
        """
        return True
    
    def get_surface_ids(self) -> List[int]:
        """Get all LiveBarn surface IDs this provider covers"""
        return list(self.surface_mappings.values())

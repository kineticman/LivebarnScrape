"""
Schedule Providers Package

This package contains modular schedule providers for different ice rinks.
Each provider implements the ScheduleProvider interface and can be easily added/removed.
"""

from .base_provider import ScheduleProvider, ScheduleEvent
from .chiller_provider import ChillerProvider, chiller_provider
from .lgria_provider import LGRIAProvider, lgria_provider

# Registry of all available providers
ALL_PROVIDERS = [
    chiller_provider,
    lgria_provider,
]

__all__ = [
    'ScheduleProvider',
    'ScheduleEvent',
    'ChillerProvider',
    'LGRIAProvider',
    'chiller_provider',
    'lgria_provider',
    'ALL_PROVIDERS',
]

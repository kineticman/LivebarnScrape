# Adding New Schedule Providers

The schedule system is now modular! Adding a new rink is easy and requires NO changes to `livebarn_manager.py`.

## Quick Start

1. Copy `schedule_providers/example_provider.py` to `schedule_providers/your_rink_provider.py`
2. Follow the TODOs in the file to implement:
   - Schedule URL/API endpoint
   - Surface ID mappings
   - fetch_schedule() logic
   - datetime parsing
3. Add your provider to `schedule_providers/__init__.py`
4. Restart the container

That's it! Your rink's schedule will automatically be fetched and integrated.

## File Structure

```
schedule_providers/
├── __init__.py                # Registry of all providers
├── base_provider.py           # Abstract base class
├── chiller_provider.py        # OhioHealth Chiller (example)
├── lgria_provider.py          # LGRIA (example)
├── example_provider.py        # Template for new providers
└── your_rink_provider.py      # Your new provider!
```

## Step-by-Step Example

Let's add a fictional "Ice Palace" rink:

### Step 1: Create the Provider File

```python
# schedule_providers/icepalace_provider.py

import requests
import logging
from datetime import datetime
from typing import List, Dict, Optional
from .base_provider import ScheduleProvider, ScheduleEvent

logger = logging.getLogger(__name__)


class IcePalaceProvider(ScheduleProvider):
    """Schedule provider for Ice Palace Arena"""
    
    SCHEDULE_URL = "https://icepalace.com/api/schedule"
    
    SURFACE_MAPPINGS = {
        "main": 9999,   # Main Rink LiveBarn ID
        "studio": 10000  # Studio Rink LiveBarn ID
    }
    
    @property
    def name(self) -> str:
        return "Ice Palace Arena"
    
    @property
    def surface_mappings(self) -> Dict[str, int]:
        return self.SURFACE_MAPPINGS
    
    def fetch_schedule(self, start_date: datetime, end_date: datetime) -> List[ScheduleEvent]:
        try:
            logger.info(f"🔍 Fetching {self.name} schedule...")
            
            # Make API request
            resp = requests.get(
                self.SCHEDULE_URL,
                params={
                    'start': start_date.isoformat(),
                    'end': end_date.isoformat()
                },
                timeout=15
            )
            resp.raise_for_status()
            data = resp.json()
            
            events: List[ScheduleEvent] = []
            
            for item in data['events']:
                start_time = self._parse_datetime(item['start_time'])
                end_time = self._parse_datetime(item['end_time'])
                
                if not start_time or not end_time:
                    continue
                
                surface_id = self.SURFACE_MAPPINGS.get(item['rink'])
                if not surface_id:
                    continue
                
                event = ScheduleEvent(
                    surface_id=surface_id,
                    start_time=start_time,
                    end_time=end_time,
                    title=item['event_name'],
                    description=item.get('notes', ''),
                    event_type=item.get('type', ''),
                    raw_data=item
                )
                events.append(event)
            
            logger.info(f"✅ Found {len(events)} {self.name} events")
            return events
            
        except Exception as e:
            logger.error(f"⚠️  Failed to fetch {self.name} schedule: {e}")
            return []
    
    def _parse_datetime(self, dt_str: str) -> Optional[datetime]:
        """Parse ISO 8601 datetime"""
        try:
            return datetime.fromisoformat(dt_str)
        except (ValueError, AttributeError):
            return None


# Create singleton instance
icepalace_provider = IcePalaceProvider()
```

### Step 2: Register the Provider

Edit `schedule_providers/__init__.py`:

```python
from .base_provider import ScheduleProvider, ScheduleEvent
from .chiller_provider import ChillerProvider, chiller_provider
from .lgria_provider import LGRIAProvider, lgria_provider
from .icepalace_provider import IcePalaceProvider, icepalace_provider  # NEW!

# Registry of all available providers
ALL_PROVIDERS = [
    chiller_provider,
    lgria_provider,
    icepalace_provider,  # NEW!
]

__all__ = [
    'ScheduleProvider',
    'ScheduleEvent',
    'ChillerProvider',
    'LGRIAProvider',
    'IcePalaceProvider',  # NEW!
    'chiller_provider',
    'lgria_provider',
    'icepalace_provider',  # NEW!
    'ALL_PROVIDERS',
]
```

### Step 3: Restart Container

```bash
docker-compose restart
```

That's it! The logs will now show:

```
🔄 Refreshing schedules from all providers...
✅ OhioHealth Chiller: 206 events
✅ Lou & Gib Reese Ice Arena: 16 events
✅ Ice Palace Arena: 42 events
✅ Schedule refreshed: 206 OhioHealth Chiller + 16 Lou & Gib Reese Ice Arena + 42 Ice Palace Arena = 264 total events
```

## Common Patterns

### Pattern 1: JSON API

```python
resp = requests.get(url, params={...}, timeout=15)
resp.raise_for_status()
data = resp.json()

for event in data['events']:
    # Process each event
```

### Pattern 2: XML API

```python
import xml.etree.ElementTree as ET

resp = requests.get(url, timeout=15)
resp.raise_for_status()
root = ET.fromstring(resp.text)

for event in root.findall("event"):
    # Process each event
```

### Pattern 3: HTML Scraping with Embedded JSON

```python
resp = requests.get(url, timeout=15)
resp.raise_for_status()
html = resp.text

# Extract JavaScript variable
marker = "scheduleData ="
idx = html.find(marker)
start = html.find("[", idx)
end = html.find("]", start) + 1
json_str = html[start:end]

import json
events = json.loads(json_str)
```

### Pattern 4: HTML Scraping with BeautifulSoup

```python
from bs4 import BeautifulSoup

resp = requests.get(url, timeout=15)
resp.raise_for_status()
soup = BeautifulSoup(resp.text, 'html.parser')

for row in soup.find_all('tr', class_='event-row'):
    # Extract data from HTML elements
    title = row.find('td', class_='title').text
    # ...
```

## Datetime Format Examples

Common datetime formats and how to parse them:

```python
# ISO 8601: "2025-12-03T14:30:00"
datetime.strptime(dt_str, "%Y-%m-%dT%H:%M:%S")

# ISO 8601 with timezone: "2025-12-03T14:30:00-05:00"
datetime.fromisoformat(dt_str)

# US Format with AM/PM: "12/3/2025 2:30:00 PM"
datetime.strptime(dt_str, "%m/%d/%Y %I:%M:%S %p")

# SQL Format: "2025-12-03 14:30:00.0"
datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S.%f")

# Unix Timestamp: "1733241000"
datetime.fromtimestamp(int(dt_str))
```

## Finding LiveBarn Surface IDs

To find the LiveBarn surface ID for your rink:

1. **In LiveBarn web app**: Navigate to the rink, check the URL
   - Example: `https://app.livebarn.com/player/1234/5678`
   - Surface ID = 5678

2. **In your database**: Query the favorites table
   ```sql
   SELECT surf.id, v.name, surf.name 
   FROM surfaces surf 
   JOIN venues v ON surf.venue_id = v.id 
   WHERE v.name LIKE '%Ice Palace%';
   ```

3. **In logs**: When you add a rink to favorites, the ID is logged

## Disabling a Provider

To temporarily disable a provider without removing it:

```python
class MyProvider(ScheduleProvider):
    # ... other code ...
    
    def is_enabled(self) -> bool:
        return False  # Disabled!
```

Or remove it from `ALL_PROVIDERS` in `__init__.py`.

## Testing Your Provider

Before deploying, test your provider:

```bash
docker exec -it livebarn-manager python

>>> from schedule_providers import icepalace_provider
>>> from datetime import datetime, timedelta
>>> 
>>> start = datetime.now()
>>> end = start + timedelta(days=2)
>>> events = icepalace_provider.fetch_schedule(start, end)
>>> 
>>> print(f"Found {len(events)} events")
>>> for event in events[:3]:
...     print(f"{event.title}: {event.start_time} - {event.end_time}")
```

## Benefits of This System

✅ **No main code changes**: Never edit `livebarn_manager.py` again
✅ **Self-contained**: Each provider is independent
✅ **Easy to add**: Copy template, implement 3 methods, register
✅ **Easy to remove**: Delete file and remove from `ALL_PROVIDERS`
✅ **Testable**: Test providers in isolation
✅ **Auto-discovery**: System automatically uses all registered providers
✅ **Clean logs**: Each provider logs its own status

## Troubleshooting

**Provider not fetching?**
- Check it's in `ALL_PROVIDERS` list
- Verify `is_enabled()` returns `True`
- Check logs for error messages

**Events not showing in guide?**
- Verify surface IDs match LiveBarn IDs
- Check date filtering is correct
- Ensure events are in proper date range

**Need help?**
- Look at `chiller_provider.py` and `lgria_provider.py` as examples
- Check the base class in `base_provider.py`
- Review logs for detailed error messages

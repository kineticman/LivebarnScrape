# LiveBarn Stream Manager

A Docker-based application that creates a unified streaming interface for LiveBarn hockey rink cameras with EPG (Electronic Program Guide) integration from local ice rink schedules.

## Features

- 🎥 **Stream Management**: Browse and favorite LiveBarn venue streams
- 📺 **M3U Playlist**: Generate M3U playlists compatible with Channels DVR, Plex, and other media servers
- 📋 **EPG Integration**: Automatic schedule fetching from:
  - OhioHealth Chiller ice rinks
  - Lou & Gib Reese Ice Arena (LGRIA)
- 🔄 **Auto-Refresh**: Daily schedule updates at 3:00 AM
- 🌐 **Web UI**: Manage favorites and view streams through a clean web interface
- 🔗 **Streamlink Proxy**: Direct video streaming without tokens expiring

## Screenshots

The web interface allows you to:
- Browse all available LiveBarn venues
- Add/remove favorites
- View real-time stream status
- Get M3U playlist and XMLTV URLs

## Requirements

- Docker and Docker Compose (or Portainer)
- LiveBarn account credentials
- Network access to LiveBarn streams

## Installation

### Option 1: Portainer (Recommended)

1. **Access Portainer** and navigate to **Stacks** → **Add Stack**

2. **Configure the Stack:**
   - **Name**: `livebarn-manager`
   - **Build method**: Repository
   - **Repository URL**: `https://github.com/kineticman/LivebarnScrape.git`
   - **Compose path**: `docker-compose.yml`

3. **Set Environment Variables:**

   Click on "Environment variables" and add:

   | Variable | Value | Description |
   |----------|-------|-------------|
   | `LIVEBARN_EMAIL` | your@email.com | Your LiveBarn account email |
   | `LIVEBARN_PASSWORD` | yourpassword | Your LiveBarn account password |
   | `LAN_IP` | 192.168.1.100 | Your server's LAN IP (optional, auto-detected) |
   | `SERVER_PORT` | 5000 | **External** web interface port (optional, default: 5000) |
   | `LOG_LEVEL` | INFO | Logging level (optional, default: INFO) |

   > Note: `PUBLIC_PORT` is automatically derived from `SERVER_PORT` in Docker/Portainer
   > installs. You usually don’t need to set it manually.

4. **Deploy the Stack:**
   - Click **Deploy the stack**
   - Wait for the container to start

5. **Access the Application:**
   - Web UI: `http://YOUR_SERVER_IP:SERVER_PORT`
   - M3U Playlist: `http://YOUR_SERVER_IP:SERVER_PORT/playlist.m3u`
   - XMLTV EPG: `http://YOUR_SERVER_IP:SERVER_PORT/xmltv`

   For example, with the default `SERVER_PORT=5000`:
   - Web UI: `http://YOUR_SERVER_IP:5000`
   - M3U Playlist: `http://YOUR_SERVER_IP:5000/playlist.m3u`
   - XMLTV EPG: `http://YOUR_SERVER_IP:5000/xmltv`

### Option 2: Docker Compose (Command Line)

1. **Clone the repository:**
   ```bash
   git clone https://github.com/kineticman/LivebarnScrape.git
   cd LivebarnScrape
   ```

2. **Create `.env` file:**
   ```bash
   cat > .env << EOF
   LIVEBARN_EMAIL=your@email.com
   LIVEBARN_PASSWORD=yourpassword
   LAN_IP=192.168.1.100
   SERVER_PORT=5000   # External port you’ll use in the browser/M3U/XMLTV URLs
   LOG_LEVEL=INFO
   EOF
   ```

3. **Start the container:**
   ```bash
   docker-compose up -d
   ```

4. **View logs:**
   ```bash
   docker-compose logs -f
   ```

### Option 3: Docker Run (Manual)

```bash
docker run -d \
  --name livebarn-manager \
  -p 5000:5000 \
  -v livebarn-data:/data \
  -e LIVEBARN_EMAIL=your@email.com \
  -e LIVEBARN_PASSWORD=yourpassword \
  -e LAN_IP=192.168.1.100 \
  -e PUBLIC_PORT=5000 \
  --restart unless-stopped \
  ghcr.io/kineticman/livebarn-manager:latest
```

If you want to expose the app on a different host port (e.g. 8653):

```bash
docker run -d \
  --name livebarn-manager \
  -p 8653:5000 \
  -v livebarn-data:/data \
  -e LIVEBARN_EMAIL=your@email.com \
  -e LIVEBARN_PASSWORD=yourpassword \
  -e LAN_IP=192.168.1.100 \
  -e PUBLIC_PORT=8653 \
  --restart unless-stopped \
  ghcr.io/kineticman/livebarn-manager:latest
```

## Initial Setup

**No manual setup required!** The container automatically builds the venue catalog on first startup.

1. **Deploy the container** using one of the installation methods above

2. **First startup** (takes 1-2 minutes):
   ```
   🔨 Building venue catalog (first-time setup)...
      This may take 1-2 minutes...
   
   ✅ Catalog build complete!
   ```

3. **Access Web Interface:**
   - Open `http://YOUR_SERVER_IP:5000` in your browser

4. **Add Favorites:**
   - Browse available venues
   - Click "Add to Favorites" on rinks you want to monitor
   - Favorites automatically appear in your M3U playlist

5. **Refresh Streams:**
   - Click "Refresh All Streams" to capture current stream URLs
   - This happens automatically but can be triggered manually

## Integration with Channels DVR

1. **Open Channels DVR Settings**

2. **Add Custom Channel:**
   - Go to **Settings** → **TV Sources** → **Custom Channels**
   - Click **Add Source**

3. **Configure Source:**
   - **Nickname**: LiveBarn Streams
   - **Stream Format**: HLS
   - **Source**: M3U Playlist
   - **M3U URL**: `http://YOUR_SERVER_IP:5000/playlist.m3u`
   - **Refresh**: Every 24 hours

4. **Add EPG Guide:**
   - **XMLTV URL**: `http://YOUR_SERVER_IP:5000/xmltv`
   - **Refresh**: Every 12 hours

5. **Save and Scan**

Your LiveBarn streams will now appear as channels in Channels DVR with full EPG data showing rink schedules!

## EPG Schedule Integration

### Supported Rinks

The system uses a **modular provider architecture** to automatically fetch schedules from multiple sources:

#### OhioHealth Chiller Locations
- Chiller Dublin (Rinks 1 & 2)
- Chiller Easton (Rinks 1 & 2)
- Chiller North (Rinks 1, 2, & 3)
- Chiller Ice Haus
- Chiller Ice Works
- NTPRD Chiller

#### Other Rinks
- Lou & Gib Reese Ice Arena (Newark, OH)

### Schedule Features

- **Real Events**: Shows actual scheduled events (games, practices, public skate)
- **Gap Filling**: Fills unscheduled time with "Open Ice" placeholders
- **Auto-Refresh**: Schedules update daily at 3:00 AM
- **Time Range**: Covers today and next 2 days
- **Modular System**: Easy to add new rinks without modifying core code

### Adding More Rinks

The system uses a **modular provider architecture** that makes adding new rinks simple:

1. **Copy the provider template:**
   ```bash
   cp schedule_providers/example_provider.py schedule_providers/your_rink_provider.py
   ```

2. **Implement 3 methods:**
   - `name` - Display name for your rink
   - `surface_mappings` - Map rink IDs to LiveBarn surface IDs
   - `fetch_schedule()` - Fetch and parse schedule data

3. **Register your provider:**
   Add it to `ALL_PROVIDERS` in `schedule_providers/__init__.py`

4. **Restart the container:**
   ```bash
   docker-compose restart
   ```

**That's it!** No changes to core code needed.

📖 **Detailed guide:** See [ADDING_PROVIDERS.md](ADDING_PROVIDERS.md) for step-by-step instructions and examples.

### Example: Adding a New Rink

```python
# schedule_providers/icepalace_provider.py
from .base_provider import ScheduleProvider, ScheduleEvent

class IcePalaceProvider(ScheduleProvider):
    SCHEDULE_URL = "https://icepalace.com/api/schedule"
    SURFACE_MAPPINGS = {"main": 9999}  # LiveBarn surface ID
    
    @property
    def name(self) -> str:
        return "Ice Palace Arena"
    
    @property
    def surface_mappings(self):
        return self.SURFACE_MAPPINGS
    
    def fetch_schedule(self, start_date, end_date):
        # Your implementation here
        pass
```

See existing providers in `schedule_providers/` for complete examples.

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LIVEBARN_EMAIL` | *required* | LiveBarn account email |
| `LIVEBARN_PASSWORD` | *required* | LiveBarn account password |
| `LAN_IP` | auto-detect | Server's LAN IP address |
| `SERVER_PORT` | 5000 | Port the web server listens on (and external port in Docker/Portainer examples) |
| `PUBLIC_PORT` | auto | Public/external port used in generated URLs (defaults to `SERVER_PORT`) |
| `LOG_LEVEL` | INFO | Logging verbosity (DEBUG, INFO, WARNING, ERROR) |
| `DB_PATH` | /data/livebarn.db | SQLite database path |

### Port Mapping

- **5000**: Web interface and API endpoints

### Volume Mounts

- `/data`: Persistent storage for database and stream cache

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Web interface |
| `/api/venues` | GET | List all venues |
| `/api/favorites` | GET | List favorite surfaces |
| `/api/favorites` | POST | Add surface to favorites |
| `/api/favorites/<id>` | DELETE | Remove surface from favorites |
| `/api/refresh-all` | POST | Refresh all favorite streams |
| `/api/refresh-surface/<id>` | POST | Refresh single surface stream |
| `/playlist.m3u` | GET | M3U playlist of favorites |
| `/xmltv` | GET | XMLTV EPG data |
| `/stream/<surface_id>` | GET | Direct HLS stream proxy |

## Troubleshooting

### Container Won't Start

**Check logs:**
```bash
docker logs livebarn-manager
```

**Common issues:**
- Missing credentials in environment variables
- Port 5000 already in use
- Database permissions issues
- First startup taking longer than expected (catalog building)

### No Venues Showing

The container automatically builds the venue catalog on first startup. If the catalog is empty:

1. **Check startup logs** for catalog build success
2. **Manually rebuild catalog:**
   ```bash
   docker exec livebarn-manager python build_catalog.py
   ```
3. **Verify credentials** are correct
4. **Check network connectivity** to LiveBarn

### No Streams Available

1. **Verify credentials** are correct
2. **Check LiveBarn website** - can you log in normally?
3. **Refresh streams** manually via web UI
4. **Check logs** for authentication errors

### EPG Not Showing in Channels DVR

1. **Verify XMLTV URL** is accessible: `http://YOUR_SERVER_IP:5000/xmltv`
2. **Check schedule cache**: Look for "Schedule refreshed" in logs
3. **Wait for refresh**: Initial fetch happens at 3:00 AM or on container start
4. **Force refresh**: Restart the container to trigger immediate fetch
5. **Check provider logs**: Each provider logs its fetch status separately

### Schedule Provider Issues

**Provider not fetching:**
```bash
# Check logs for provider-specific errors
docker logs livebarn-manager | grep "OhioHealth Chiller"
docker logs livebarn-manager | grep "Lou & Gib Reese"
```

**Add a new provider:**
- See [ADDING_PROVIDERS.md](ADDING_PROVIDERS.md) for complete guide
- Providers are in `schedule_providers/` directory
- No core code changes needed

### Streams Buffer or Disconnect

- LiveBarn streams can be unstable depending on rink connectivity
- Token expiration is handled automatically (re-authentication)
- Check your network connection to LiveBarn

## Development

### Project Structure

```
LivebarnScrape/
├── livebarn_manager.py           # Main Flask application
├── schedule_utils.py             # Schedule utility functions
├── schedule_providers/           # Modular schedule providers
│   ├── __init__.py              # Provider registry
│   ├── base_provider.py         # Abstract base class
│   ├── chiller_provider.py      # OhioHealth Chiller
│   ├── lgria_provider.py        # Lou & Gib Reese
│   └── example_provider.py      # Template for new providers
├── build_catalog.py              # Venue catalog builder
├── refresh_single.py             # Single stream refresh utility
├── Dockerfile                    # Container image definition
├── docker-compose.yml            # Docker Compose configuration
├── entrypoint.sh                 # Container startup script
├── requirements.txt              # Python dependencies
├── README.md                     # This file
└── ADDING_PROVIDERS.md           # Guide for adding new rinks
```

### Local Development

1. **Install Python dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Set environment variables:**
   ```bash
   export LIVEBARN_EMAIL=your@email.com
   export LIVEBARN_PASSWORD=yourpassword
   export DB_PATH=./livebarn.db
   ```

3. **Build initial venue catalog:**
   ```bash
   python build_catalog.py
   ```

4. **Run the application:**
   ```bash
   python livebarn_manager.py
   ```

5. **Test a schedule provider:**
   ```bash
   python -c "
   from schedule_providers import lgria_provider
   from datetime import datetime, timedelta
   events = lgria_provider.fetch_schedule(datetime.now(), datetime.now() + timedelta(days=2))
   print(f'Found {len(events)} events')
   "
   ```

### Building Docker Image

```bash
docker build -t livebarn-manager .
```

## Contributing

Contributions are welcome! The modular architecture makes it easy to contribute:

### Easy Contributions:
- ✅ **Add schedule providers** for new rinks (see [ADDING_PROVIDERS.md](ADDING_PROVIDERS.md))
- ✅ **Improve existing providers** with better parsing or error handling
- ✅ **Add tests** for providers or core functionality

### Areas for Improvement:
- [ ] Advanced filtering/search in web UI
- [ ] Recording/DVR functionality
- [ ] Multi-user support with authentication
- [ ] Mobile app or responsive design improvements
- [ ] Notifications for favorite teams/games
- [ ] Provider health monitoring dashboard
- [ ] Configuration UI for managing providers
- [ ] Support for more streaming protocols

### Adding a Schedule Provider

The easiest way to contribute is by adding support for your local rink:

1. Fork the repository
2. Create a new provider in `schedule_providers/`
3. Follow the template in `example_provider.py`
4. Test it locally
5. Submit a pull request

See [ADDING_PROVIDERS.md](ADDING_PROVIDERS.md) for detailed instructions.

Please open an issue or pull request on GitHub.

## License

This project is for personal use only. LiveBarn content is subject to their Terms of Service. This tool does not bypass any security measures or violate copyright - it simply provides a unified interface for streams you already have access to via your LiveBarn subscription.

## Acknowledgments

- **LiveBarn** for providing the streaming service
- **Channels DVR** for excellent DVR software
- **OhioHealth Chiller** for public schedule API
- **Lou & Gib Reese Ice Arena** for public schedule data

## Support

For issues, questions, or feature requests:
- GitHub Issues: https://github.com/kineticman/LivebarnScrape/issues

## Disclaimer

This is an unofficial tool and is not affiliated with, endorsed by, or connected to LiveBarn, Streaming Sports Productions LLC, OhioHealth, or any ice rink facilities. Use at your own risk. You must have a valid LiveBarn subscription to use this tool.

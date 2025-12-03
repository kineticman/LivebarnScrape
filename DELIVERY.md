# 🎉 LiveBarn Docker Deployment - Complete Package

## 📦 What You Got

### Core Application Files
1. **livebarn_manager.py** - Main Flask server with:
   - ✅ Web UI for managing favorites
   - ✅ M3U playlist endpoint
   - ✅ XMLTV endpoint with Chiller integration
   - ✅ On-demand stream token refresh
   - ✅ APScheduler for daily Chiller updates (3am)
   - ✅ Environment variable support
   - ✅ Improved logging with poll filtering

2. **refresh_single.py** - Single stream token refresher
   - ✅ Playwright + Chrome automation
   - ✅ Environment variable support
   - ✅ Called automatically by manager on token expiry

3. **build_catalog.py** - Venue database builder
   - ✅ Downloads all LiveBarn venues/surfaces
   - ✅ Environment variable support
   - ✅ Run once on initial setup

### Docker Infrastructure
4. **Dockerfile** - Multi-stage build with:
   - Python 3.11 slim base
   - Playwright + Chromium browser
   - Streamlink for HLS streaming
   - All Python dependencies
   - Health checks

5. **docker-compose.yml** - Easy orchestration:
   - Port mapping (5000)
   - Volume mounting for persistent data
   - Environment variable injection
   - Automatic restart policy

6. **requirements.txt** - Python dependencies:
   - flask
   - playwright
   - requests
   - apscheduler
   - streamlink

7. **entrypoint.sh** - Startup script:
   - Validates credentials
   - Checks database existence
   - Starts manager with logging

### Configuration
8. **.env.example** - Template for credentials
9. **README.md** - Comprehensive documentation (3,000+ words)
10. **QUICKSTART.md** - 5-minute setup guide

## 🔄 Architecture Changes

### What Changed from Previous Setup:

**DEPRECATED (No Longer Needed):**
- ❌ `auto_refresh.py` - Batch token refresh
- ❌ `capture_favorites.py` - Wrapper script
- ❌ `generate_xmltv.py` - Standalone XMLTV generator
- ❌ `livebarn_credentials.json` - JSON config file

**NEW Approach:**
- ✅ **On-demand token refresh** - Only when streams are accessed
- ✅ **Integrated XMLTV** - Built into manager with Chiller cache
- ✅ **Environment variables** - Secure credential management
- ✅ **APScheduler** - Daily Chiller refresh at 3am
- ✅ **Lighter weight** - No batch Playwright sessions

### Benefits:

1. **Reduced Overhead**
   - No periodic token refreshes for unused streams
   - Playwright only runs when needed
   - Smaller memory footprint

2. **Better Reliability**
   - Tokens auto-refresh on first access
   - No stale tokens if refresh job fails
   - Each stream managed independently

3. **Simpler Management**
   - Single container deployment
   - Environment-based configuration
   - Unified logging

4. **Chiller Integration**
   - Daily schedule refresh (3am)
   - Real event names in EPG
   - "Open Ice" placeholder blocks
   - Cached for fast XMLTV generation

## 🚀 Deployment Steps

1. **Copy all files to server**
2. **Create `.env` with credentials**
3. **Run `docker-compose up -d`**
4. **Build catalog: `docker exec livebarn-manager python build_catalog.py`**
5. **Open web UI, add favorites**
6. **Add URLs to Channels DVR**

## 📊 How It Works

```
┌─────────────────────────────────────────────────────┐
│              Docker Container                        │
│                                                      │
│  ┌────────────────────────────────────────────┐    │
│  │  Flask Web Server (Port 5000)              │    │
│  │  ├── / (Web UI)                            │    │
│  │  ├── /playlist.m3u (M3U)                   │    │
│  │  ├── /xmltv (EPG with Chiller)             │    │
│  │  └── /proxy/<id> (Streamlink)              │    │
│  └────────────────────────────────────────────┘    │
│                                                      │
│  ┌────────────────────────────────────────────┐    │
│  │  APScheduler (Background)                  │    │
│  │  └── 3:00 AM → Refresh Chiller schedule    │    │
│  └────────────────────────────────────────────┘    │
│                                                      │
│  ┌────────────────────────────────────────────┐    │
│  │  On-Demand Token Refresh                   │    │
│  │  └── Playwright + Chrome (when needed)     │    │
│  └────────────────────────────────────────────┘    │
│                                                      │
└─────────────────────────────────────────────────────┘
         │
         └── Volume: /data
             └── livebarn.db (persistent)
```

## 🎯 Key Features

### Automatic Token Management
- Tokens refresh automatically when expired
- 5-minute expiry threshold
- Background Playwright automation
- No manual intervention needed

### Chiller Schedule Integration
- Maps Chiller product IDs to LiveBarn surfaces
- Fetches 2-day schedule from Chiller API
- Fills gaps with "Open Ice" blocks
- Updates daily at 3:00 AM
- Cached for performance

### Supported Chiller Rinks (Ohio)
- Dublin Ice Rinks (1 & 2)
- Easton Ice Rinks (1 & 2)  
- Chiller North (1, 2 & 3)
- Ice Haus
- Ice Works
- Springfield

### Web UI Features
- Browse all LiveBarn venues
- Search by name/city
- Filter by state
- Add/remove favorites
- View live logs
- Copy M3U/XMLTV URLs

## 🔒 Security

- Credentials via environment variables
- Database stored in persistent volume
- No hardcoded secrets
- Isolated Docker network
- Recommend: Use on local network only

## 📝 Environment Variables

**Required:**
- `LIVEBARN_EMAIL` - Your LiveBarn login
- `LIVEBARN_PASSWORD` - Your password

**Optional:**
- `SERVER_PORT` - Default: 5000
- `DB_PATH` - Default: /data/livebarn.db
- `TZ` - Default: America/New_York
- `LOG_LEVEL` - Default: INFO

## 🐛 Testing Checklist

After deployment, verify:

- [ ] Container starts successfully
- [ ] Web UI accessible at port 5000
- [ ] Database created after running build_catalog.py
- [ ] Can browse venues in UI
- [ ] Can add favorites (star icon)
- [ ] M3U playlist URL works
- [ ] XMLTV URL returns valid XML
- [ ] Streams play in Channels DVR
- [ ] Logs show Chiller schedule refresh
- [ ] Token auto-refresh works on stream access

## 📚 Documentation

- **README.md** - Complete reference guide
- **QUICKSTART.md** - Fast setup guide
- **This file** - Delivery summary

## 🙏 What Was Accomplished

✅ Converted standalone scripts to Docker container
✅ Added APScheduler for background jobs
✅ Integrated Chiller schedule API
✅ Environment variable support throughout
✅ On-demand token refresh (lighter weight)
✅ Unified logging with filtering
✅ Health checks and monitoring
✅ Persistent data volumes
✅ Complete documentation

## 🎊 Ready to Deploy!

All files are in `/mnt/user-data/outputs/`:
- Dockerfile
- docker-compose.yml
- requirements.txt
- .env.example
- entrypoint.sh
- livebarn_manager.py
- build_catalog.py
- refresh_single.py
- README.md
- QUICKSTART.md
- DELIVERY.md (this file)

**Next Step:** Copy to your server and run `docker-compose up -d`!

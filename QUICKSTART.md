# LiveBarn Docker - Quick Start

## 🚀 5-Minute Setup

### Step 1: Download Files
Put all these files in a directory:
- `Dockerfile`
- `docker-compose.yml`
- `requirements.txt`
- `livebarn_manager.py`
- `build_catalog.py`
- `refresh_single.py`
- `entrypoint.sh`
- `.env.example`

### Step 2: Configure Credentials
```bash
cp .env.example .env
nano .env
```

Add your LiveBarn credentials:
```
LIVEBARN_EMAIL=your-email@example.com
LIVEBARN_PASSWORD=your-password-here
```

### Step 3: Start Container
```bash
docker-compose up -d
```

### Step 4: Build Catalog (First Time Only)
```bash
docker exec livebarn-manager python build_catalog.py
```

### Step 5: Open Web UI
Go to: `http://localhost:5000`

- Browse venues
- Click a venue to see surfaces
- Star (⭐) your favorite surfaces

### Step 6: Add to Channels DVR

In Channels DVR → Sources → Custom Channels:

**M3U Playlist:**
```
http://YOUR_SERVER_IP:5000/playlist.m3u
```

**XMLTV Guide:**
```
http://YOUR_SERVER_IP:5000/xmltv
```

## ✅ Done!

Your streams will now appear in Channels DVR with:
- ✅ Real event schedules from Chiller (if applicable)
- ✅ Auto-refreshing stream tokens
- ✅ Live EPG data

## 📋 Useful Commands

```bash
# View logs
docker-compose logs -f

# Restart
docker-compose restart

# Stop
docker-compose down

# Update (pull changes)
docker-compose build
docker-compose up -d
```

## 🔧 Troubleshooting

**No streams appearing?**
- Make sure you ran `build_catalog.py`
- Add favorites in the web UI (star icon)

**Container won't start?**
- Check `.env` file has correct credentials
- View logs: `docker-compose logs`

**Streams not playing?**
- First access triggers token capture (takes 5-10 seconds)
- Check logs for Playwright/Chrome errors

## 🎯 What's New vs Old Setup

### ✅ Improvements:
- **No manual token refresh needed** - happens on-demand
- **Lighter weight** - only refreshes when you watch
- **Better Chiller integration** - automatic 3am daily refresh
- **Environment variables** - no more JSON config files
- **Single container** - everything in one place

### 🗑️ Removed:
- `auto_refresh.py` - no longer needed
- `capture_favorites.py` - no longer needed  
- `generate_xmltv.py` - built into manager now

## 📚 More Info

See `README.md` for complete documentation.

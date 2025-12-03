#!/bin/bash
set -e

echo "=========================================="
echo " LiveBarn Manager - Docker Startup"
echo "=========================================="
echo ""

# Check if database exists
if [ ! -f /data/livebarn.db ]; then
    echo "⚠️  Database not found at /data/livebarn.db"
    echo "   Run: docker exec livebarn-manager python build_catalog.py"
    echo "   to download the venue catalog"
    echo ""
else
    echo "✅ Database found at /data/livebarn.db"
    echo ""
fi

# Check credentials
if [ -z "$LIVEBARN_EMAIL" ] || [ -z "$LIVEBARN_PASSWORD" ]; then
    echo "❌ ERROR: LiveBarn credentials not set!"
    echo "   Set LIVEBARN_EMAIL and LIVEBARN_PASSWORD environment variables"
    exit 1
fi

echo "✅ Credentials configured"
echo ""

# Start the manager
echo "🚀 Starting LiveBarn Manager..."
echo ""
exec python livebarn_manager.py

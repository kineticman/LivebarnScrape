#!/bin/bash
set -e

APP_VERSION="$(cat /app/VERSION 2>/dev/null || echo dev)"

echo "=========================================="
echo " LiveBarn Manager v${APP_VERSION} - Docker Startup"
echo "=========================================="
echo ""

# Environment credentials are optional because they can be saved from the admin UI.
if [ -z "$LIVEBARN_EMAIL" ] || [ -z "$LIVEBARN_PASSWORD" ]; then
    echo "⚠️  LiveBarn environment credentials are not set"
    echo "   Configure them from the admin page or set LIVEBARN_EMAIL and LIVEBARN_PASSWORD"
    echo ""
else
    echo "✅ Environment credentials configured"
    echo ""
fi

# Check if database exists and has data
DB_EXISTS=false
DB_HAS_DATA=false

if [ -f /data/livebarn.db ]; then
    DB_EXISTS=true
    
    # Check if database has venues/surfaces (quick check)
    VENUE_COUNT=$(sqlite3 /data/livebarn.db "SELECT COUNT(*) FROM venues;" 2>/dev/null || echo "0")
    
    if [ "$VENUE_COUNT" -gt "0" ]; then
        DB_HAS_DATA=true
        echo "✅ Database found at /data/livebarn.db"
        echo "   📊 Contains $VENUE_COUNT venues"
        echo ""
    fi
fi

# Auto-build catalog if needed
if [ "$DB_EXISTS" = false ] || [ "$DB_HAS_DATA" = false ]; then
    echo "🔨 Building venue catalog (first-time setup)..."
    echo "   This may take 1-2 minutes..."
    echo ""
    
    if python build_catalog.py; then
        echo ""
        echo "✅ Catalog build complete!"
        echo ""
    else
        echo ""
        echo "❌ Catalog build failed!"
        echo "   You can rebuild manually with:"
        echo "   docker exec livebarn-manager python build_catalog.py"
        echo ""
        echo "   Continuing startup anyway..."
        echo ""
    fi
fi

# Start the manager
echo "🚀 Starting LiveBarn Manager v${APP_VERSION}..."
echo ""
exec python livebarn_manager.py

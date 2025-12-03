#!/bin/bash
# LiveBarn Docker Deployment Verification Script

echo "=========================================="
echo " LiveBarn Docker - Deployment Test"
echo "=========================================="
echo ""

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check function
check() {
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✓${NC} $1"
        return 0
    else
        echo -e "${RED}✗${NC} $1"
        return 1
    fi
}

warn() {
    echo -e "${YELLOW}⚠${NC} $1"
}

# 1. Check if Docker is installed
echo "Checking prerequisites..."
docker --version &> /dev/null
check "Docker installed"

docker-compose --version &> /dev/null
check "Docker Compose installed"

echo ""

# 2. Check if required files exist
echo "Checking project files..."
FILES=(
    "Dockerfile"
    "docker-compose.yml"
    "requirements.txt"
    "livebarn_manager.py"
    "build_catalog.py"
    "refresh_single.py"
    "entrypoint.sh"
)

all_files_exist=true
for file in "${FILES[@]}"; do
    if [ -f "$file" ]; then
        check "$file exists"
    else
        echo -e "${RED}✗${NC} $file missing"
        all_files_exist=false
    fi
done

echo ""

# 3. Check .env file
echo "Checking configuration..."
if [ -f ".env" ]; then
    check ".env file exists"
    
    # Check for required variables
    if grep -q "LIVEBARN_EMAIL" .env && grep -q "LIVEBARN_PASSWORD" .env; then
        check "Credentials configured in .env"
        
        # Check if they're not still default values
        if grep -q "your-email@example.com" .env; then
            warn "Credentials appear to be default values - update .env!"
        fi
    else
        echo -e "${RED}✗${NC} Missing credentials in .env"
        warn "Copy .env.example to .env and add your credentials"
    fi
else
    echo -e "${RED}✗${NC} .env file not found"
    warn "Copy .env.example to .env and add your credentials"
fi

echo ""

# 4. Check if container is running
echo "Checking container status..."
if docker ps | grep -q "livebarn-manager"; then
    check "Container is running"
    
    # Check if web UI is accessible
    if curl -sf http://localhost:5000/ > /dev/null 2>&1; then
        check "Web UI responding at http://localhost:5000"
    else
        echo -e "${RED}✗${NC} Web UI not responding"
        warn "Container may still be starting up"
    fi
else
    echo -e "${YELLOW}⚠${NC} Container not running"
    echo "   Start with: docker-compose up -d"
fi

echo ""

# 5. Check database
echo "Checking database..."
if [ -d "data" ]; then
    check "Data directory exists"
    
    if [ -f "data/livebarn.db" ]; then
        check "Database file exists"
        
        # Check database size
        size=$(du -h data/livebarn.db | cut -f1)
        echo "   Database size: $size"
        
        if [ "$size" == "0" ] || [ "$size" == "4.0K" ]; then
            warn "Database appears empty - run build_catalog.py"
        fi
    else
        echo -e "${YELLOW}⚠${NC} Database not found"
        echo "   Run: docker exec livebarn-manager python build_catalog.py"
    fi
else
    warn "Data directory will be created on first run"
fi

echo ""

# 6. Summary
echo "=========================================="
echo " Summary"
echo "=========================================="

if [ "$all_files_exist" = true ] && [ -f ".env" ]; then
    echo -e "${GREEN}✓${NC} All required files present"
    echo ""
    echo "Next steps:"
    echo "  1. Verify credentials in .env"
    echo "  2. docker-compose up -d"
    echo "  3. docker exec livebarn-manager python build_catalog.py"
    echo "  4. Open http://localhost:5000"
else
    echo -e "${RED}✗${NC} Missing required files"
    echo ""
    echo "Required actions:"
    echo "  1. Ensure all files are in this directory"
    echo "  2. Create .env from .env.example"
    echo "  3. Add your LiveBarn credentials to .env"
fi

echo ""
echo "=========================================="

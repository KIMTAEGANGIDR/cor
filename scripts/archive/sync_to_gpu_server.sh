#!/bin/bash
# ILIS OCR - Sync files to GPU Server
# Usage: ./scripts/sync_to_gpu_server.sh

set -e

# Configuration
SERVER="kim@192.168.0.113"
REMOTE_DIR="/mnt/workspace/images"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== ILIS OCR - GPU Server Sync ===${NC}"
echo ""

# Check SSH connection
echo -e "${YELLOW}Checking connection to ${SERVER}...${NC}"
if ! ssh -o ConnectTimeout=5 "$SERVER" "echo 'Connected!'" 2>/dev/null; then
    echo -e "${RED}Cannot connect to server!${NC}"
    echo "Please check:"
    echo "  1. Server is running"
    echo "  2. SSH key is configured"
    echo "  3. Network connection"
    exit 1
fi

echo -e "${GREEN}Connection OK${NC}"
echo ""

# Create remote directories
echo -e "${YELLOW}Creating remote directories...${NC}"
ssh "$SERVER" "mkdir -p $REMOTE_DIR/headers $REMOTE_DIR/ocr_worker"

# Files to sync
echo ""
echo -e "${GREEN}Files to sync:${NC}"
echo "  1. Header images (peraturan/data/headers/) -> $REMOTE_DIR/headers/"
echo "  2. OCR database (peraturan/data/ocr_pipeline.db) -> $REMOTE_DIR/"
echo "  3. Worker scripts (peraturan/src/ocr/gpu_worker/) -> $REMOTE_DIR/ocr_worker/"
echo "  4. Setup docs (docs/GPU_SERVER_SETUP.md) -> $REMOTE_DIR/"
echo ""

# Confirm
read -p "Continue with sync? (y/N): " confirm
if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 0
fi

# 1. Sync header images (largest - use rsync with progress)
echo ""
echo -e "${YELLOW}[1/4] Syncing header images (3.8GB)...${NC}"
rsync -avz --progress \
    peraturan/data/headers/ \
    "$SERVER:$REMOTE_DIR/headers/"

# 2. Sync OCR database
echo ""
echo -e "${YELLOW}[2/4] Syncing OCR database...${NC}"
rsync -avz --progress \
    peraturan/data/ocr_pipeline.db \
    "$SERVER:$REMOTE_DIR/"

# 3. Sync worker scripts
echo ""
echo -e "${YELLOW}[3/4] Syncing worker scripts...${NC}"
rsync -avz --progress \
    peraturan/src/ocr/gpu_worker/ \
    "$SERVER:$REMOTE_DIR/ocr_worker/"

# 4. Sync documentation
echo ""
echo -e "${YELLOW}[4/4] Syncing documentation...${NC}"
rsync -avz --progress \
    docs/GPU_SERVER_SETUP.md \
    "$SERVER:$REMOTE_DIR/"

echo ""
echo -e "${GREEN}=== Sync Complete! ===${NC}"
echo ""
echo "Next steps on GPU server:"
echo "  1. ssh $SERVER"
echo "  2. cd $REMOTE_DIR"
echo "  3. pip install -r ocr_worker/requirements.txt"
echo "  4. python ocr_worker/worker.py test"
echo "  5. python ocr_worker/worker.py run"

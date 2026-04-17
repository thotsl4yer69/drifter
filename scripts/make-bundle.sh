#!/bin/bash
# ============================================
# MZ1312 DRIFTER — USB Bundle Creator
# UNCAGED TECHNOLOGY — EST 1991
# ============================================
# Creates a self-contained tarball you can put on a USB stick.
# On the Pi: plug in USB, extract, run install.sh. No internet needed.
#
# Usage (from repo root):
#   ./scripts/make-bundle.sh
#   # Output: drifter-bundle.tar.gz (copy to USB)
#
# On Pi:
#   tar xzf drifter-bundle.tar.gz
#   cd drifter && sudo ./install.sh
# ============================================

set -euo pipefail

CYAN='\033[0;36m'
GREEN='\033[0;32m'
NC='\033[0m'

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BUNDLE_NAME="drifter-bundle.tar.gz"
OUTPUT="${REPO_DIR}/${BUNDLE_NAME}"

echo -e "${CYAN}DRIFTER — Creating deployment bundle...${NC}"

cd "${REPO_DIR}"

tar czf "${OUTPUT}" \
    --exclude='__pycache__' \
    --exclude='.git' \
    --exclude='.venv' \
    --exclude='*.pyc' \
    --exclude='.pytest_cache' \
    --exclude='drifter-bundle.tar.gz' \
    --transform='s,^\.,drifter,' \
    .

SIZE=$(du -h "${OUTPUT}" | cut -f1)
echo -e "${GREEN}Bundle created: ${OUTPUT} (${SIZE})${NC}"
echo ""
echo "To deploy:"
echo "  1. Copy ${BUNDLE_NAME} to a USB stick"
echo "  2. On the Pi:"
echo "       mount /dev/sda1 /mnt"
echo "       tar xzf /mnt/${BUNDLE_NAME}"
echo "       cd drifter && sudo ./install.sh"
echo ""

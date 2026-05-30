#!/bin/bash
# ============================================
# MZ1312 DRIFTER — Navigation Installer
# UNCAGED TECHNOLOGY — EST 1991
# ============================================
# Usage: sudo ./scripts/install-nav.sh
# ============================================

set -e

CYAN='\033[0;36m'
RED='\033[0;31m'
GREEN='\033[0;32m'
AMBER='\033[0;33m'
NC='\033[0m'

DRIFTER_DIR="/opt/drifter"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

ok()   { echo -e "${GREEN}  ✓ $1${NC}"; }
warn() { echo -e "${AMBER}  ! $1${NC}"; }
fail() { echo -e "${RED}  ✗ $1${NC}"; exit 1; }
step() { echo -e "\n${AMBER}[$1] $2${NC}"; }

echo -e "${CYAN}  DRIFTER — Navigation Installer${NC}\n"
if [ "$EUID" -ne 0 ]; then fail "Run as root: sudo ./scripts/install-nav.sh"; fi

step 1 "Installing pyserial + requests"
source "${DRIFTER_DIR}/venv/bin/activate"
pip install --quiet pyserial requests pyyaml
ok "Python deps installed"

step 2 "Deploying nav_engine.py and speed-camera data"
cp "${REPO_DIR}/src/nav_engine.py" "${DRIFTER_DIR}/"
chmod +x "${DRIFTER_DIR}/nav_engine.py"
mkdir -p "${DRIFTER_DIR}/data" "${DRIFTER_DIR}/data/tiles"
if [ -f "${REPO_DIR}/data/speed_cameras_vic.json" ]; then
    cp "${REPO_DIR}/data/speed_cameras_vic.json" "${DRIFTER_DIR}/data/"
    ok "Speed-camera dataset deployed"
fi

step 3 "Deploying nav.yaml"
if [ -f "${DRIFTER_DIR}/nav.yaml" ]; then
    warn "nav.yaml exists — not overwriting"
else
    cp "${REPO_DIR}/config/nav.yaml" "${DRIFTER_DIR}/"
    ok "nav.yaml deployed"
fi

step 4 "Installing drifter-nav systemd service"
cp "${REPO_DIR}/services/drifter-nav.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable drifter-nav
ok "drifter-nav enabled"

echo ""
echo -e "${GREEN}  Navigation installed.${NC}"
echo -e "  Edit GPS device: ${CYAN}${DRIFTER_DIR}/nav.yaml${NC}"
echo -e "  Start:           ${CYAN}sudo systemctl start drifter-nav${NC}"

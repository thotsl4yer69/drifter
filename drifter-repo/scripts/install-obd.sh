#!/bin/bash
# ============================================
# MZ1312 DRIFTER — OBD-II ELM327 Bridge Installer
# UNCAGED TECHNOLOGY — EST 1991
# ============================================
# Usage: sudo ./scripts/install-obd.sh
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

echo -e "${CYAN}  DRIFTER — OBD-II ELM327 Bridge Installer${NC}\n"
if [ "$EUID" -ne 0 ]; then fail "Run as root: sudo ./scripts/install-obd.sh"; fi

step 1 "Installing pyserial + pyyaml"
source "${DRIFTER_DIR}/venv/bin/activate"
pip install --quiet pyserial pyyaml
ok "pyserial installed"

step 2 "Deploying obd_bridge.py"
cp "${REPO_DIR}/src/obd_bridge.py" "${DRIFTER_DIR}/"
chmod +x "${DRIFTER_DIR}/obd_bridge.py"
ok "obd_bridge.py deployed"

step 3 "Deploying obd.yaml"
if [ ! -f "${DRIFTER_DIR}/obd.yaml" ]; then
    cp "${REPO_DIR}/config/obd.yaml" "${DRIFTER_DIR}/"
    ok "obd.yaml deployed"
else
    warn "obd.yaml already present — not overwriting"
fi

step 4 "Installing systemd service"
cp "${REPO_DIR}/services/drifter-obdbridge.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable drifter-obdbridge
ok "drifter-obdbridge enabled"

echo ""
echo -e "${GREEN}  OBD bridge installed.${NC}"
echo -e "  Plug ELM327 into a USB port and run ${CYAN}sudo systemctl start drifter-obdbridge${NC}"
echo -e "  Test: ${CYAN}./scripts/test-obd.sh${NC}"

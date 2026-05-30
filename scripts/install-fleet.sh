#!/bin/bash
# ============================================
# MZ1312 DRIFTER — Fleet Server installer
# UNCAGED TECHNOLOGY — EST 1991
# ============================================
set -e

CYAN='\033[0;36m'; GREEN='\033[0;32m'; AMBER='\033[0;33m'; RED='\033[0;31m'; NC='\033[0m'
DRIFTER_DIR="/opt/drifter"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

step() { echo -e "\n${AMBER}» $1${NC}"; }
ok()   { echo -e "${GREEN}  ✓ $1${NC}"; }
fail() { echo -e "${RED}  ✗ $1${NC}"; exit 1; }

if [ "$EUID" -ne 0 ]; then fail "Run as root: sudo $0"; fi

step "Installing Flask + websocket dependencies"
${DRIFTER_DIR}/venv/bin/pip install --quiet flask flask-sock
ok "Flask + flask-sock installed"

step "Deploying fleet_server.py"
cp "${REPO_DIR}/src/fleet_server.py" "${DRIFTER_DIR}/"
chmod +x "${DRIFTER_DIR}/fleet_server.py"
ok "fleet_server.py deployed"

step "Deploying fleet dashboard"
cp "${REPO_DIR}/src/fleet_dashboard.html" "${DRIFTER_DIR}/"
ok "fleet_dashboard.html deployed"

step "Deploying fleet config"
if [ ! -f "${DRIFTER_DIR}/fleet.yaml" ]; then
    cp "${REPO_DIR}/config/fleet.yaml" "${DRIFTER_DIR}/"
    ok "fleet.yaml deployed"
else
    ok "fleet.yaml already present — not overwriting"
fi

step "Creating fleet data dir"
mkdir -p "${DRIFTER_DIR}/data"
ok "${DRIFTER_DIR}/data/"

step "Installing systemd unit"
cp "${REPO_DIR}/services/drifter-fleet.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable drifter-fleet 2>/dev/null
ok "drifter-fleet enabled"

step "Starting service"
systemctl restart drifter-fleet
sleep 2
if systemctl is-active --quiet drifter-fleet; then
    ok "drifter-fleet running"
    echo -e "  ${CYAN}Open:${NC} http://$(hostname -I | awk '{print $1}'):8420/api/health"
else
    fail "drifter-fleet failed to start — journalctl -u drifter-fleet -n 50"
fi

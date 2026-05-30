#!/bin/bash
# ============================================
# MZ1312 DRIFTER — Mesh networking installer
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

step "Installing zeroconf"
${DRIFTER_DIR}/venv/bin/pip install --quiet zeroconf
ok "zeroconf installed"

step "Allowing mDNS / multicast (UDP 5353)"
if command -v ufw &>/dev/null; then
    ufw allow 5353/udp >/dev/null 2>&1 || true
    ok "ufw allow 5353/udp"
fi
if command -v firewall-cmd &>/dev/null; then
    firewall-cmd --permanent --add-service=mdns >/dev/null 2>&1 || true
    firewall-cmd --reload >/dev/null 2>&1 || true
    ok "firewall-cmd add mdns"
fi

step "Deploying mesh modules"
for f in mesh_discovery.py mesh_coordinator.py mesh_bridge.py; do
    cp "${REPO_DIR}/src/${f}" "${DRIFTER_DIR}/"
    chmod +x "${DRIFTER_DIR}/${f}"
    ok "${f}"
done

step "Deploying mesh config"
if [ ! -f "${DRIFTER_DIR}/mesh.yaml" ]; then
    cp "${REPO_DIR}/config/mesh.yaml" "${DRIFTER_DIR}/"
    ok "mesh.yaml deployed"
else
    ok "mesh.yaml already present — not overwriting"
fi

step "Deploying mesh dashboard"
cp "${REPO_DIR}/src/mesh_dashboard.html" "${DRIFTER_DIR}/"
ok "mesh_dashboard.html deployed"

step "Installing systemd unit"
cp "${REPO_DIR}/services/drifter-mesh.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable drifter-mesh 2>/dev/null
ok "drifter-mesh enabled"

step "Starting service"
systemctl restart drifter-mesh
sleep 2
if systemctl is-active --quiet drifter-mesh; then
    ok "drifter-mesh running"
    echo -e "  ${CYAN}Edit:${NC} /opt/drifter/mesh.yaml to add remote brokers"
else
    fail "drifter-mesh failed to start — journalctl -u drifter-mesh -n 50"
fi

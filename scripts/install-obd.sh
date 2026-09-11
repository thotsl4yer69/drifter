#!/bin/bash
# ============================================
# MZ1312 DRIFTER — OBD-II ELM327 Bridge Installer
# Serial / Bluetooth Classic / Wi-Fi TCP
# ============================================
# Usage: sudo ./scripts/install-obd.sh

set -euo pipefail

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

echo -e "${CYAN}  DRIFTER — ELM327 MULTI-LINK INSTALLER${NC}\n"
if [ "$EUID" -ne 0 ]; then fail "Run as root: sudo ./scripts/install-obd.sh"; fi

step 1 "Installing runtime dependencies"
source "${DRIFTER_DIR}/venv/bin/activate"
pip install --quiet pyserial pyyaml
command -v bluetoothctl >/dev/null 2>&1 || warn "bluetoothctl not present; direct Bluetooth mode needs BlueZ installed"
ok "Python OBD dependencies installed"

step 2 "Deploying ELM327 bridge"
for f in obd_bridge.py obd_bridge_multi.py elm_link.py; do
    cp "${REPO_DIR}/src/${f}" "${DRIFTER_DIR}/${f}"
    chmod +x "${DRIFTER_DIR}/${f}"
done
# The bridge imports these existing shared modules. Keep standalone installer
# usable even when called without the full oneshot deploy.
for f in obd_transport.py obd_pids.py vehicle_profile.py config.py; do
    if [ -f "${REPO_DIR}/src/${f}" ]; then cp "${REPO_DIR}/src/${f}" "${DRIFTER_DIR}/${f}"; fi
done
ok "Serial/Bluetooth/Wi-Fi bridge deployed"

step 3 "Deploying OBD configuration"
if [ ! -f "${DRIFTER_DIR}/obd.yaml" ]; then
    cp "${REPO_DIR}/config/obd.yaml" "${DRIFTER_DIR}/"
    ok "obd.yaml deployed"
else
    warn "obd.yaml already present — preserved"
fi

touch "${DRIFTER_DIR}/.env"
chmod 600 "${DRIFTER_DIR}/.env"

step 4 "Installing systemd service"
cp "${REPO_DIR}/services/drifter-obdbridge.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable drifter-obdbridge
ok "drifter-obdbridge enabled"

cat <<'EOF'

ELM327 link selection lives in /opt/drifter/.env:

  # Bluetooth Classic reader
  DRIFTER_ELM_LINK=bluetooth
  ELM_BT_MAC=AA:BB:CC:DD:EE:FF
  ELM_BT_CHANNEL=1

  # Wi-Fi reader (use the host/port printed by your adapter/manual)
  DRIFTER_ELM_LINK=wifi
  ELM_WIFI_HOST=192.168.0.10
  ELM_WIFI_PORT=35000

  # USB/serial or an rfcomm tty
  DRIFTER_ELM_LINK=serial
  OBD_SERIAL_DEV=/dev/drifter-obd
  OBD_SERIAL_BAUD=38400

  # Or let DRIFTER try configured links in order
  DRIFTER_ELM_LINK=auto

Bluetooth pairing example:
  bluetoothctl
  power on
  scan on
  pair AA:BB:CC:DD:EE:FF
  trust AA:BB:CC:DD:EE:FF
  quit

Then:
  sudo systemctl restart drifter-obdbridge
  journalctl -u drifter-obdbridge -n 60 --no-pager
  drifter diagnose
EOF

ok "OBD bridge installation complete"

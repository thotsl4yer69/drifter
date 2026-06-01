#!/bin/bash
# ============================================
# MZ1312 DRIFTER — OBD Bridge Test Script
# UNCAGED TECHNOLOGY — EST 1991
# ============================================
# Usage: ./scripts/test-obd.sh [--mqtt]
# ============================================

set -e

CYAN='\033[0;36m'
RED='\033[0;31m'
GREEN='\033[0;32m'
AMBER='\033[0;33m'
NC='\033[0m'

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DRIFTER_DIR="/opt/drifter"
VENV_PYTHON="${DRIFTER_DIR}/venv/bin/python3"
TEST_MQTT=false

ok()   { echo -e "${GREEN}  ✓ $1${NC}"; }
warn() { echo -e "${AMBER}  ! $1${NC}"; }
fail() { echo -e "${RED}  ✗ $1${NC}"; exit 1; }
step() { echo -e "\n${AMBER}[TEST] $1${NC}"; }

[ "$1" = "--mqtt" ] && TEST_MQTT=true

echo -e "${CYAN}  DRIFTER — OBD Bridge Test${NC}\n"

step "Syntax check"
python3 -m py_compile "${REPO_DIR}/src/obd_bridge.py" && ok "obd_bridge.py syntax OK" || \
    fail "obd_bridge.py syntax error"

step "Check ELM327 serial device"
DEV="${DRIFTER_OBD_DEV:-/dev/ttyUSB0}"
if [ -e "$DEV" ]; then
    ok "Device present: $DEV"
else
    warn "Device $DEV not present — set DRIFTER_OBD_DEV or plug ELM327 into USB"
fi

step "Check pyserial"
if [ -f "${VENV_PYTHON}" ] && "${VENV_PYTHON}" -c "import serial" 2>/dev/null; then
    ok "pyserial importable in venv"
else
    warn "pyserial not installed in venv — run install-obd.sh first"
fi

if $TEST_MQTT; then
    step "MQTT live publish test (10s)"
    if command -v mosquitto_sub &>/dev/null; then
        mosquitto_sub -h localhost -t 'drifter/obd/#' -W 10 -C 4 || \
            warn "no OBD topics in 10s (is drifter-obdbridge running?)"
    else
        warn "mosquitto_sub not installed — skipping MQTT test"
    fi
fi

echo ""
echo -e "${GREEN}  OBD test complete.${NC}"
echo -e "  Run with --mqtt to wait for live OBD topics on the broker."

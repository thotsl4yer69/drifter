#!/bin/bash
# ============================================
# MZ1312 DRIFTER — Safety Stack Installer
# Tier-1 safety + Tier-2 AI diagnostics + crash + driver-assist + sentry + comms.
# UNCAGED TECHNOLOGY — EST 1991
# ============================================
# Usage: sudo ./scripts/install-safety.sh
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

echo -e "${CYAN}  DRIFTER — Safety Stack Installer${NC}\n"
if [ "$EUID" -ne 0 ]; then fail "Run as root: sudo ./scripts/install-safety.sh"; fi

step 1 "Installing python deps (smbus2, pyserial, pyyaml, requests)"
source "${DRIFTER_DIR}/venv/bin/activate"
pip install --quiet smbus2 pyserial pyyaml requests
ok "Python deps installed"

step 2 "Deploying safety modules"
for f in safety_engine.py ai_diagnostics.py session_reporter.py telemetry_batcher.py \
         adaptive_thresholds.py crash_detect.py driver_assist.py sentry_mode.py \
         comms_bridge.py llm_client_v2.py vehicle_id.py vehicle_kb.py vehicle_learn.py; do
    if [ -f "${REPO_DIR}/src/${f}" ]; then
        cp "${REPO_DIR}/src/${f}" "${DRIFTER_DIR}/"
        chmod +x "${DRIFTER_DIR}/${f}"
    fi
done
ok "Safety modules deployed"

step 3 "Deploying configs + vehicle profiles + workspace dirs"
mkdir -p "${DRIFTER_DIR}/sentry" "${DRIFTER_DIR}/kb" "${DRIFTER_DIR}/memory" "${DRIFTER_DIR}/vehicles"
if [ ! -f "${DRIFTER_DIR}/safety.yaml" ]; then
    cp "${REPO_DIR}/config/safety.yaml" "${DRIFTER_DIR}/"
    ok "safety.yaml deployed"
else
    warn "safety.yaml already present — not overwriting"
fi
cp "${REPO_DIR}/vehicles/default.yaml" "${DRIFTER_DIR}/vehicles/" 2>/dev/null || true
if [ -f "${REPO_DIR}/vehicles/SAJEA51D44XD39283.yaml" ]; then
    cp "${REPO_DIR}/vehicles/SAJEA51D44XD39283.yaml" "${DRIFTER_DIR}/vehicles/"
    ok "X-Type profile deployed"
fi

step 4 "Installing systemd services"
for svc in safety aidiag reporter batcher thresholds vehicleid kb learn \
           crash assist sentry comms; do
    cp "${REPO_DIR}/services/drifter-${svc}.service" /etc/systemd/system/
    systemctl enable "drifter-${svc}"
    ok "drifter-${svc} enabled"
done
systemctl daemon-reload

step 5 "API key reminder"
touch "${DRIFTER_DIR}/.env"
if ! grep -q "ANTHROPIC_API_KEY" "${DRIFTER_DIR}/.env" 2>/dev/null; then
    warn "ANTHROPIC_API_KEY not set in ${DRIFTER_DIR}/.env — Tier-2 AI diagnostics will fall back to Groq/Ollama"
fi

echo ""
echo -e "${GREEN}  Safety stack installed.${NC}"
echo -e "  Set ANTHROPIC_API_KEY / GROQ_API_KEY in ${CYAN}${DRIFTER_DIR}/.env${NC}"
echo -e "  Start:  ${CYAN}sudo systemctl start drifter-batcher drifter-safety drifter-aidiag${NC}"

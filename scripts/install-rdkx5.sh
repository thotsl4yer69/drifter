#!/bin/bash
# ============================================
# MZ1312 DRIFTER — RDK X5 Installer
# Installs the RDK X5 platform profile: native CAN FD bridge (can_native.py),
# ghost protocol counter-surveillance, hardware abstraction, and the X5
# config + CAN FD bring-up.
# UNCAGED TECHNOLOGY — EST 1991
# ============================================
# Usage: sudo ./scripts/install-rdkx5.sh
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

echo -e "${CYAN}  DRIFTER — RDK X5 Installer${NC}\n"
[ "$EUID" -ne 0 ] && fail "Run as root: sudo ./scripts/install-rdkx5.sh"

step 1 "Detecting platform"
MODEL="$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo unknown)"
echo "  device-tree model: ${MODEL}"
case "$(echo "$MODEL" | tr '[:upper:]' '[:lower:]')" in
    *"rdk x5"*|*sunrise*) ok "RDK X5 detected" ;;
    *) warn "model does not look like an RDK X5 — continuing anyway (DRIFTER_PLATFORM will force rdkx5)" ;;
esac

step 2 "Deploying RDK X5 modules"
mkdir -p "${DRIFTER_DIR}"
for f in hardware.py can_native.py ghost_protocol.py config.py can_bridge.py; do
    if [ -f "${REPO_DIR}/src/${f}" ]; then
        cp "${REPO_DIR}/src/${f}" "${DRIFTER_DIR}/"
        chmod +x "${DRIFTER_DIR}/${f}"
        ok "${f}"
    else
        warn "${f} missing in repo — skipped"
    fi
done

step 3 "Deploying RDK X5 config"
if [ ! -f "${DRIFTER_DIR}/rdkx5.yaml" ]; then
    cp "${REPO_DIR}/config/rdkx5.yaml" "${DRIFTER_DIR}/"
    ok "rdkx5.yaml deployed"
else
    warn "rdkx5.yaml already present — not overwriting"
fi

step 4 "Writing platform env"
touch "${DRIFTER_DIR}/.env"
# Force the platform so config.PLATFORM resolves before the device-tree probe.
if ! grep -q "^DRIFTER_PLATFORM=" "${DRIFTER_DIR}/.env"; then
    echo "DRIFTER_PLATFORM=rdkx5" >> "${DRIFTER_DIR}/.env"
    ok "DRIFTER_PLATFORM=rdkx5 written to ${DRIFTER_DIR}/.env"
else
    sed -i 's/^DRIFTER_PLATFORM=.*/DRIFTER_PLATFORM=rdkx5/' "${DRIFTER_DIR}/.env"
    ok "DRIFTER_PLATFORM updated to rdkx5"
fi
if ! grep -q "^CAN_FD_ENABLED=" "${DRIFTER_DIR}/.env"; then
    echo "CAN_FD_ENABLED=false" >> "${DRIFTER_DIR}/.env"
    ok "CAN_FD_ENABLED=false written (flip to true on an FD bus)"
fi

step 5 "Bringing up CAN interface"
if [ -f "${REPO_DIR}/scripts/setup-can-fd.sh" ]; then
    if bash "${REPO_DIR}/scripts/setup-can-fd.sh"; then
        ok "CAN interface configured"
    else
        warn "CAN bring-up failed — plug in / enable the controller, then re-run setup-can-fd.sh"
    fi
else
    warn "setup-can-fd.sh not found — bring CAN up manually"
fi

step 6 "Verifying modules import"
if (cd "${DRIFTER_DIR}" && python3 -c "import hardware; print(hardware.get_platform().as_dict())"); then
    ok "hardware.py imports + detects platform"
else
    warn "hardware.py import check failed — verify python-can is installed in the venv"
fi

echo ""
echo -e "${GREEN}  RDK X5 profile installed.${NC}"
echo -e "  CAN bridge:   ${CYAN}python3 ${DRIFTER_DIR}/can_native.py bridge${NC}"
echo -e "  Ghost watch:  ${CYAN}python3 ${DRIFTER_DIR}/ghost_protocol.py${NC}"
echo -e "  CAN toolkit:  ${CYAN}can_native.py {sniffer|fuzzer|decoder_ai|replay <file>|dbc_gen}${NC}"

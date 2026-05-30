#!/bin/bash
# ============================================
# MZ1312 DRIFTER — CAN FD Interface Setup
# Brings up a native socketcan interface, with CAN FD when supported.
# Targets the RDK X5 on-board CAN controller but works on any native
# socketcan device (MCP251xFD HATs, etc.).
# UNCAGED TECHNOLOGY — EST 1991
# ============================================
# Usage:
#   sudo ./scripts/setup-can-fd.sh [channel] [bitrate] [dbitrate]
# Examples:
#   sudo ./scripts/setup-can-fd.sh                 # can0, 500k nominal, 2M data, FD on
#   sudo ./scripts/setup-can-fd.sh can0 500000     # classic CAN, FD off
#   CAN_FD_ENABLED=false sudo ./scripts/setup-can-fd.sh
# ============================================

set -e

GREEN='\033[0;32m'
RED='\033[0;31m'
AMBER='\033[0;33m'
CYAN='\033[0;36m'
NC='\033[0m'

ok()   { echo -e "${GREEN}  ✓ $1${NC}"; }
warn() { echo -e "${AMBER}  ! $1${NC}"; }
fail() { echo -e "${RED}  ✗ $1${NC}"; exit 1; }

CHANNEL="${1:-${CAN_NATIVE_CHANNEL:-can0}}"
BITRATE="${2:-${CAN_BITRATE:-500000}}"
DBITRATE="${3:-${CAN_FD_DATA_BITRATE:-2000000}}"
FD_ENABLED="${CAN_FD_ENABLED:-true}"

echo -e "${CYAN}  DRIFTER — CAN FD setup (${CHANNEL})${NC}\n"
[ "$EUID" -ne 0 ] && fail "Run as root: sudo ./scripts/setup-can-fd.sh"

# Confirm the netdev exists (native controller present).
if ! ip link show "$CHANNEL" >/dev/null 2>&1; then
    warn "$CHANNEL does not exist — is the CAN controller enabled in the device tree?"
    warn "On the RDK X5 enable the CAN overlay, then reboot before re-running."
    fail "no such interface: $CHANNEL"
fi

# Take the link down first so we can reconfigure it idempotently.
ip link set "$CHANNEL" down 2>/dev/null || true

case "$(echo "$FD_ENABLED" | tr '[:upper:]' '[:lower:]')" in
    1|true|yes|on)
        echo "  Configuring $CHANNEL: CAN FD, bitrate=$BITRATE dbitrate=$DBITRATE"
        if ip link set "$CHANNEL" up type can \
              bitrate "$BITRATE" \
              dbitrate "$DBITRATE" fd on 2>/dev/null; then
            ok "$CHANNEL up (CAN FD: ${BITRATE}/${DBITRATE} bps)"
        else
            warn "CAN FD bring-up failed — controller may not support FD. Falling back to classic CAN."
            ip link set "$CHANNEL" up type can bitrate "$BITRATE" \
                || fail "classic CAN bring-up also failed on $CHANNEL"
            ok "$CHANNEL up (classic CAN: ${BITRATE} bps)"
        fi
        ;;
    *)
        echo "  Configuring $CHANNEL: classic CAN, bitrate=$BITRATE"
        ip link set "$CHANNEL" up type can bitrate "$BITRATE" \
            || fail "classic CAN bring-up failed on $CHANNEL"
        ok "$CHANNEL up (classic CAN: ${BITRATE} bps)"
        ;;
esac

# Show the resulting link state for the operator.
echo ""
ip -details -statistics link show "$CHANNEL" | sed 's/^/    /'
echo ""
ok "CAN interface ready: $CHANNEL"
echo -e "  Verify traffic with: ${CYAN}candump $CHANNEL${NC}"

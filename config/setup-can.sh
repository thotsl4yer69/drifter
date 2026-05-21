#!/bin/bash
# MZ1312 DRIFTER — CAN Interface Auto-Setup
# Detects USB2CANFD and brings up socketcan interface.
# Skips known-not-CAN USB-serial chips (CH340/PL2303) so a mic dongle's
# embedded CH340 doesn't get slcand'd into a phantom slcan0.

set -o pipefail

BITRATE=500000

# Check if can0 already exists and is up
if ip link show can0 &>/dev/null; then
    STATE=$(ip -brief link show can0 | awk '{print $2}')
    if [ "$STATE" = "UP" ]; then
        exit 0
    fi
    # Exists but down — bring it up
    ip link set can0 type can bitrate $BITRATE
    ip link set up can0
    echo "DRIFTER: can0 brought up at ${BITRATE} bps"
    exit 0
fi

# Helper: is this /dev/ttyUSB* device a generic serial chip we know is not CAN?
is_known_not_can() {
    local dev="$1"
    local vid
    vid=$(udevadm info --name "$dev" --query=property 2>/dev/null \
          | awk -F= '/^ID_VENDOR_ID=/{print tolower($2); exit}')
    case "$vid" in
        1a86|067b) return 0 ;;  # QinHeng CH340/CH341, Prolific PL2303
        *)         return 1 ;;
    esac
}

# Check for slcan devices (some USB2CANFD variants use serial).
# nullglob so non-matching patterns expand to nothing (not a literal "*").
shopt -s nullglob
for DEV in /dev/ttyACM* /dev/ttyUSB*; do
    if [ -e "$DEV" ]; then
        if is_known_not_can "$DEV"; then
            echo "DRIFTER: skipping $DEV (generic USB-serial, not a CAN adapter)"
            continue
        fi
        # Try slcand
        slcand -o -s6 -t hw "$DEV" slcan0 2>/dev/null
        sleep 1
        if ip link show slcan0 &>/dev/null; then
            ip link set up slcan0
            echo "DRIFTER: slcan0 created from $DEV at ${BITRATE} bps"
            exit 0
        fi
    fi
done
shopt -u nullglob

echo "DRIFTER: No CAN interface found — will retry on next start"
exit 0

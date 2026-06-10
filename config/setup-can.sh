#!/bin/bash
# MZ1312 DRIFTER — CAN Interface Auto-Setup
# Detects a CAN adapter and brings up a socketcan interface.
#
# SAFETY: this script binds slcand ONLY to a serial device whose USB VID:PID
# is on a POSITIVE allowlist of known CAN-over-serial / gs_usb adapters (see
# CAN_USB_IDS below). The previous version slcand'd ANY ttyUSB/ttyACM that
# wasn't a known-bad CH340/PL2303 — which would hijack the Flipper / Marauder
# / GPS / USB-mic serial ports (several of which use STMicro/SiLabs/FTDI chips
# that a denylist lets through) and create a phantom slcan0 that never sees a
# frame. If no adapter is positively identified, we do NOTHING and exit 0 — we
# never guess. Mirrors CAN_USB_IDS in src/can_bridge.py — keep the two in sync.

set -o pipefail

BITRATE=500000

# Allowlist of known CAN adapters as "vid:pid" (lowercase hex). Add to this
# list (and to src/can_bridge.py CAN_USB_IDS) when qualifying a new adapter.
#   0483:5740  STMicro VCP — CANable / slcan (CANtact-style)
#   1d50:606f  OpenMoko    — candleLight / gs_usb (CANable gs_usb fw)
#   1209:2323  pid.codes   — CANable 2.0 (gs_usb)
#   16d0:117e  MCS         — gs_usb USB2CAN (candleLight-class)
#   1cd2:606f  Geschw.Schn — gs_usb (original CANtact)
CAN_USB_IDS="0483:5740 1d50:606f 1209:2323 16d0:117e 1cd2:606f"

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

# Helper: is this serial device a POSITIVELY-identified CAN adapter?
# Returns 0 (true) only when the device's USB vid:pid is on CAN_USB_IDS.
is_can_adapter() {
    local dev="$1"
    local vid pid id
    vid=$(udevadm info --name "$dev" --query=property 2>/dev/null \
          | awk -F= '/^ID_VENDOR_ID=/{print tolower($2); exit}')
    pid=$(udevadm info --name "$dev" --query=property 2>/dev/null \
          | awk -F= '/^ID_MODEL_ID=/{print tolower($2); exit}')
    [ -z "$vid" ] || [ -z "$pid" ] && return 1
    id="${vid}:${pid}"
    for known in $CAN_USB_IDS; do
        [ "$id" = "$known" ] && return 0
    done
    return 1
}

# Check for serial CAN adapters (CANable/gs_usb appear as ttyACM*/ttyUSB*).
# nullglob so non-matching patterns expand to nothing (not a literal "*").
shopt -s nullglob
for DEV in /dev/ttyACM* /dev/ttyUSB*; do
    if [ -e "$DEV" ]; then
        if ! is_can_adapter "$DEV"; then
            echo "DRIFTER: skipping $DEV (not an allowlisted CAN adapter — "\
"could be Flipper/Marauder/GPS/mic serial)"
            continue
        fi
        # Positively identified — try slcand
        slcand -o -s6 -t hw "$DEV" slcan0 2>/dev/null
        sleep 1
        if ip link show slcan0 &>/dev/null; then
            ip link set up slcan0
            echo "DRIFTER: slcan0 created from $DEV (CAN adapter) at ${BITRATE} bps"
            exit 0
        fi
    fi
done
shopt -u nullglob

echo "DRIFTER: No CAN interface found — will retry on next start"
exit 0

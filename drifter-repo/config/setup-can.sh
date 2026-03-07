#!/bin/bash
# MZ1312 DRIFTER — CAN Interface Auto-Setup
# Detects USB2CANFD and brings up socketcan interface

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

# Check for slcan devices (some USB2CANFD variants use serial)
for DEV in /dev/ttyACM* /dev/ttyUSB*; do
    if [ -e "$DEV" ]; then
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

echo "DRIFTER: No CAN interface found"
exit 1

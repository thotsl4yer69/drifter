#!/usr/bin/env bash
# MZ1312 DRIFTER — ESP32 Marauder firmware flash helper.
# Manual one-shot tool: NOT invoked by any systemd unit. The operator runs
# this when physically connecting a fresh ESP32 board to the Pi via USB.
#
# Usage:
#   tools/flash_marauder.sh <serial-port> [firmware.bin]
#
# Examples:
#   tools/flash_marauder.sh /dev/ttyUSB0
#   tools/flash_marauder.sh /dev/ttyUSB0 /opt/drifter/state/marauder_fw/marauder_devkit_v4.bin
#
# Default firmware path resolves to the most-recent .bin in
# /opt/drifter/state/marauder_fw/ — populated by hand from
# https://github.com/justcallmekoko/ESP32Marauder/releases. Pick the binary
# matching your board: DevKit_v4 is the most-compatible default.
#
# Prerequisites:
#   - esptool.py installed (apt: python3-esptool, pip: esptool)
#   - operator in the dialout/uucp group, or run as root
#   - ESP32 board attached, BOOT button held (some boards auto-bootload)

set -euo pipefail

FW_DIR="${MARAUDER_FW_DIR:-/opt/drifter/state/marauder_fw}"

if [[ $# -lt 1 || "$1" == "-h" || "$1" == "--help" ]]; then
  cat <<EOF
Usage: $0 <serial-port> [firmware.bin]

  <serial-port>  Where the ESP32 enumerated (e.g. /dev/ttyUSB0, /dev/ttyACM1).
                 Hint: \`dmesg | tail\` after plugging in.
  [firmware.bin] Optional. Defaults to newest *.bin in $FW_DIR.

Common DevKit_v4 flash address is 0x10000; full-image releases flash at 0x0.
This helper picks 0x10000 by default — pass MARAUDER_FLASH_ADDR=0x0 to override.

Environment:
  MARAUDER_FW_DIR     Override the firmware directory (default $FW_DIR)
  MARAUDER_FLASH_ADDR Hex address (default 0x10000)
  MARAUDER_BAUD       esptool baud (default 460800)
EOF
  exit 1
fi

PORT="$1"
FW="${2:-}"
ADDR="${MARAUDER_FLASH_ADDR:-0x10000}"
BAUD="${MARAUDER_BAUD:-460800}"

if [[ -z "$FW" ]]; then
  if [[ ! -d "$FW_DIR" ]]; then
    echo "ERROR: $FW_DIR missing. Create it and drop the Marauder .bin in:" >&2
    echo "  sudo mkdir -p $FW_DIR" >&2
    echo "  sudo curl -L -o $FW_DIR/marauder_devkit_v4.bin <release-url>" >&2
    exit 2
  fi
  FW="$(ls -t "$FW_DIR"/*.bin 2>/dev/null | head -1 || true)"
  if [[ -z "$FW" ]]; then
    echo "ERROR: no .bin found in $FW_DIR" >&2
    echo "Download the latest stable release from:" >&2
    echo "  https://github.com/justcallmekoko/ESP32Marauder/releases" >&2
    exit 2
  fi
fi

if [[ ! -e "$PORT" ]]; then
  echo "ERROR: $PORT does not exist (is the ESP32 plugged in?)" >&2
  exit 3
fi
if [[ ! -f "$FW" ]]; then
  echo "ERROR: firmware file $FW not found" >&2
  exit 4
fi

if ! command -v esptool.py >/dev/null 2>&1; then
  echo "ERROR: esptool.py not installed." >&2
  echo "  apt: sudo apt install python3-esptool" >&2
  echo "  pip: pip install esptool" >&2
  exit 5
fi

echo "MARAUDER FLASH"
echo "  port:     $PORT"
echo "  firmware: $FW"
echo "  address:  $ADDR"
echo "  baud:     $BAUD"
echo
read -rp "Continue? (y/N) " ans
[[ "$ans" == "y" || "$ans" == "Y" ]] || { echo "Aborted."; exit 0; }

esptool.py --chip esp32 --port "$PORT" --baud "$BAUD" \
  write_flash -z "$ADDR" "$FW"

echo "Marauder flash complete. Unplug + replug the ESP32, then attach it to"
echo "the Flipper Zero GPIO add-on header. flipper_bridge will detect it"
echo "automatically on its next probe (≤30s)."

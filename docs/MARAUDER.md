# MARAUDER — ESP32 Wi-Fi/BLE Add-on Workflow

DRIFTER's v3sper drawer surfaces passive Wi-Fi and BLE workflows when an
ESP32 Marauder add-on is attached to the Flipper Zero GPIO header. This
file documents the manual flash + integration workflow.

## What ships with the repo

| Path | Purpose |
|---|---|
| `tools/flash_marauder.sh` | Manual one-shot esptool wrapper. NOT auto-invoked. |
| `/opt/drifter/state/marauder_fw/` | Operator-managed firmware cache. Created on first deploy. |
| `src/flipper_bridge.py` | Probes Flipper for Marauder signature every 30s. |
| `ui/cockpit-preview.html` (v3sper drawer) | Renders the WIFI panel buttons. |

The firmware itself is **not** vendored — it's GPL-3.0 and the maintainer
ships per-board binaries. The operator downloads the matching `.bin` once
per board and drops it in `/opt/drifter/state/marauder_fw/` before running
the flash script.

## One-time install

```bash
# 1. Stage the firmware directory.
sudo mkdir -p /opt/drifter/state/marauder_fw
sudo chown "$USER:$USER" /opt/drifter/state/marauder_fw

# 2. Pick the right binary for your board from:
#      https://github.com/justcallmekoko/ESP32Marauder/releases
#    For the operator's default DevKit_v4:
curl -L -o /opt/drifter/state/marauder_fw/marauder_devkit_v4.bin \
  https://github.com/justcallmekoko/ESP32Marauder/releases/latest/download/esp32_marauder_vX.Y.Z_DevKit_v4.bin

# 3. esptool is the underlying flasher.
sudo apt install python3-esptool
```

## Flashing the board

```bash
# Find the port (look at dmesg after plugging in):
dmesg | tail
# → usually /dev/ttyUSB0

# Flash (defaults to newest .bin in marauder_fw/ at address 0x10000):
~/drifter/tools/flash_marauder.sh /dev/ttyUSB0

# To use a specific firmware or address:
MARAUDER_FLASH_ADDR=0x0 ~/drifter/tools/flash_marauder.sh /dev/ttyUSB0 /path/to/full_image.bin
```

The script prints the plan, asks for confirmation, then calls `esptool.py
write_flash`. After it finishes:

1. Unplug + replug the ESP32 to start the new firmware.
2. Attach the ESP32 to the Flipper Zero's GPIO add-on header per the
   Marauder wiring guide.
3. `flipper_bridge` re-probes every 30s. Within one cycle the v3sper
   drawer's `MODULE: WIFI` pill turns on and the WIFI buttons go live.

## What the cockpit exposes

DRIFTER deliberately surfaces ONLY the **passive** Marauder commands. The
firmware can do more, but the cockpit must not surface DEAUTH, BEACON
SPAM, or EVIL TWIN — those are kept out of the operator surface by spec.

| Cockpit button | Marauder CLI | MQTT topic |
|---|---|---|
| WIFI SCAN AP | `scanap` | `drifter/flipper/wifi/aps` |
| WIFI SCAN STA | `scansta` | `drifter/flipper/wifi/stations` |
| BLE SCAN | `blescan` | `drifter/flipper/wifi/ble` |
| PACKET MONITOR | `sniffraw` | `drifter/flipper/wifi/pcaps` |
| PROBE REQUEST CAPTURE | `probescan` | `drifter/flipper/wifi/probes` |
| PWNAGOTCHI (passive) | `evilpwn` | `drifter/flipper/wifi/handshakes` |

PWNAGOTCHI is additionally gated on
`/opt/drifter/etc/audit_targets.yaml` having at least one entry; the
cockpit renders the `(ALLOWLIST EMPTY)` label when the file is missing
or empty. That file is owned by Agent B's wi-fi audit pipeline.

## Honest bench result

Without an ESP32 attached, `drifter/flipper/hardware` retains the payload
`{"module": "none", "capabilities": []}` and every WIFI button is dimmed
in the cockpit. POST `/api/flipper/command` `{"command":"wifi_scan_ap"}`
returns success at the HTTP layer (it just publishes to MQTT) but the
bridge publishes a `wifi module not attached` result on
`drifter/flipper/result` — the cockpit surfaces that on the results ring.

That is the correct behavior on a bench. It only flips when a physical
board is connected.

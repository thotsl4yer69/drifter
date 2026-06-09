# MARAUDER — ESP32 Wi-Fi/BLE Bridge

DRIFTER's foot-mode arsenal includes a Marauder bridge that talks to ESP32
Marauder firmware over USB serial, either directly via a Marauder-flashed
ESP32 dev board or via the Flipper Zero GPIO ESP32 module. The bridge surfaces
**both passive and active** Marauder capabilities; the offensive surface is
gated by the three-layer authorization model described below.

## Change of stance (2026-05-24)

This doc previously stated:

> "DRIFTER deliberately surfaces ONLY the passive Marauder commands. The
> firmware can do more, but the cockpit must not surface DEAUTH, BEACON
> SPAM, or EVIL TWIN — those are kept out of the operator surface by spec."

**That constraint is rescinded.** The new spec surfaces offensive features
(deauth attack, beacon spam, BLE proximity spam, EvilPortal) behind a
three-layer gate:

1. Local-peer network check (127.0.0.1 + 10.42.0.0/24 only).
2. Per-command confirmation token (HIGH-risk commands return a token,
   operator must POST it back within 120 s).
3. Per-target allowlist match in `/opt/drifter/etc/audit_targets.yaml`
   under the `marauder:` top-level key. Empty allowlist → refused.

The gating model is considered sufficient to surface offensive features
safely for authorized pentest engagements.

## How to authorize a target

Edit `/opt/drifter/etc/audit_targets.yaml` and add entries under `marauder:`:

```yaml
marauder:
  wifi:
    - ssid: "ACME-Pentest-Guest"
    - bssid: "aa:bb:cc:dd:ee:ff"
  ble:
    - mac: "11:22:33:44:55:66"
    # OR for indiscriminate-spam areas:
    - area_authorized: true
      area_label: "ACME HQ pen-test lab room 204"
  evilportal:
    - ssid: "ACME-Pentest-Guest"
      template: "acme-guest"
      max_captures: 50
      authorized_use: "ACME contract #1234 valid 2026-05-01 → 2026-06-30"
```

## Firmware flash (operator-managed, unchanged)

The firmware itself is not vendored — operator downloads the matching `.bin`
once per board and drops it in `/opt/drifter/state/marauder_fw/` before
running the flash script:

```bash
sudo mkdir -p /opt/drifter/state/marauder_fw
sudo chown "$USER:$USER" /opt/drifter/state/marauder_fw

# Pick the right binary for your board from:
#   https://github.com/justcallmekoko/ESP32Marauder/releases
# For the operator's default DevKit_v4:
curl -L -o /opt/drifter/state/marauder_fw/marauder_devkit_v4.bin \
  https://github.com/justcallmekoko/ESP32Marauder/releases/latest/download/esp32_marauder_vX.Y.Z_DevKit_v4.bin

sudo apt install python3-esptool

# Flash (defaults to newest .bin in marauder_fw/ at address 0x10000):
~/drifter/tools/flash_marauder.sh /dev/ttyUSB0

# To use a specific firmware or address:
MARAUDER_FLASH_ADDR=0x0 ~/drifter/tools/flash_marauder.sh /dev/ttyUSB0 /path/to/full_image.bin
```

After flashing: unplug + replug the ESP32. The new `drifter-marauder` service
autodetects on next probe (or `POST /api/marauder/probe`).

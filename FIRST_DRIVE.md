# FIRST DRIVE — operator runbook

Plain steps for the first real drive of the MZ1312 DRIFTER (2004 Jaguar X-Type 2.5).
Written against the **actual hardware validated on the bench 2026-06-02**, not the
original spec assumptions. For deeper detail see [`COCKPIT.md`](COCKPIT.md),
[`docs/WIRING.md`](docs/WIRING.md), [`docs/FIELD_DEPLOY.md`](docs/FIELD_DEPLOY.md).

## What's confirmed working (bench)

| Device | Status | Notes |
|---|---|---|
| CAN adapter | ✅ bench-green | It's a **CANable/slcan** type (`0483:5740` → `slcan0` @ 500 kbps), *not* the gs_usb USB2CANFD the old docs assume. `drifter-canbridge` binds it and polls OBD-II correctly. **Untested against the car — see the decision point below.** |
| RTL-SDR Blog V4 | ✅ green | Detected, DVB-T driver blacklisted, real RF received. TPMS/ADS-B prove out while driving. |
| USB audio (C-Media combo) | ✅ green | Speaker **and** mic. Levels set + saved (speaker ~50%, mic gain sane). |
| Location | ✅ via phone | **No GPS dongle.** Your phone's GPS provides the fix (steps below). |
| Flipper Zero | ⬜ not yet tested | Optional; do later. Shares the same USB ID as the CANable — don't run both on one bench test. |
| Vivi | text only | 3D avatar disabled (no GPU). Text chat in the cockpit works. |

## Before you turn the key (parked)

1. **Power the Pi** from the car. Wait ~60 s for all services to come up.
2. **Plug in** (use a powered USB hub if you run out of ports):
   - **CANable → the car's OBD-II port** (under the dash, driver side).
   - **RTL-SDR** (with antenna attached).
   - **USB audio dongle** (with speaker + mic connected).
3. **Phone:** connect to Wi-Fi **`MZ1312_DRIFTER`** (PSK: `sudo nmcli --show-secrets connection show MZ1312_DRIFTER`).
4. Open **`https://10.42.0.1:8443`** in the phone browser → accept the self-signed cert warning **once**. (HTTPS is required so the browser will share GPS.)
5. On the map, tap the **⌖ locate** button → **Allow** location. The map centres on you and the "AWAITING GPS FIX" card clears. (Uses the phone's GPS; only accepted if accurate to ≤100 m — coarse network location is rejected by design.)

## Turn the key — what to watch

6. **Ignition to RUN** (engine running is ideal).
7. **CAN / engine telemetry** — within ~30 s the top-bar **CAN** cell flips `OFF → UP` and the hero gauges (RPM, coolant, voltage, speed) go live.

   > ⚠️ **DECISION POINT — if the gauges stay blank after 30 s with the engine running:**
   > Open the **DRIFTER Diagnose** desktop icon (or run `drifter diagnose`), then check the bus:
   > ```
   > candump slcan0
   > ```
   > - See `7E8` frames (ECU responses)? ✅ It's working — gauges will populate.
   > - Only `7DF` requests, **no `7E8` responses**? → This car uses **K-line OBD** (ISO 9141/KWP2000),
   >   not CAN. The CANable **cannot** read OBD here. You'll need an **ELM327 (K-line)** adapter,
   >   or to tap an internal CAN bus directly. This is the single biggest unknown for a 2004 X-Type.

8. **Audio** — warnings speak through the cabin speaker (preset ~50%; adjust to taste).
9. **Tires (TPMS)** — auto-learn over the first 5–30 min of driving; corners fill in on the **Tires** tab.
10. **Health** — open the **DRIFTER Health** icon → expect `ok` or `ok-hw-pending`.

## One-click desktop launchers

**Cockpit** (HUD) · **Diagnose** · **Health** · **Logs** · **MQTT Monitor** · **OPSEC Console** · **Restart Services**.

## If something looks wrong

- `drifter diagnose` — full hardware + service probe.
- `drifter logs <service> -f` — e.g. `drifter logs canbridge -f`, `drifter logs rf -f`.
- `drifter restart all` — restart every service.
- `curl -fsS http://127.0.0.1:8080/healthz` — JSON health.
- `mosquitto_sub -h localhost -t 'drifter/#' -v` — live telemetry firehose.

## Not-yet / deferred

- **Flipper Zero** — not bench-tested. ⚠️ It and the CANable share USB ID `0483:5740`; unplug the
  CANable before bench-testing the Flipper so the bridges don't grab the wrong device.
- **Display panel / GPU** — `config.txt` has staged edits (`vc4-kms-v3d` enabled; Waveshare DSI
  panel `dtoverlay` typo fixed) that take effect on the **next reboot**. Safe display-driver change;
  backup at `/boot/firmware/config.txt.bak.drifter`.

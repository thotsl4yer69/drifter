# DRIFTER — Vehicle Intelligence Module

```
  ██████  ██████  ██ ███████ ████████ ███████ ██████  
  ██   ██ ██   ██ ██ ██         ██    ██      ██   ██ 
  ██   ██ ██████  ██ █████      ██    █████   ██████  
  ██   ██ ██   ██ ██ ██         ██    ██      ██   ██ 
  ██████  ██   ██ ██ ██         ██    ███████ ██   ██ 
```

**MZ1312 UNCAGED TECHNOLOGY — EST 1991**

Sentient Core vehicle intelligence node for the 2004 Jaguar X-Type 2.5L V6. Turns your car into a self-diagnosing platform using a Raspberry Pi 5, USB CAN adapter, and deterministic diagnostic rules — no cloud, no subscriptions, no bullshit.

## What It Does

- **Reads** the Jaguar's CAN bus via USB2CANFD adapter and OBD-II (Mode 01 live data + Mode 03/07 DTCs)
- **Diagnoses** mechanical issues in real-time using 13 deterministic rules (vacuum leaks, fuel trim drift, coolant, alternator, intake heat soak, DTC detection, stall detection, and more)
- **Displays** live telemetry on your Pioneer head unit via RealDash TCP CAN bridge + Android Auto
- **Speaks** diagnostic alerts through your car speakers via Piper TTS
- **Logs** all telemetry to NVMe with drive session detection and per-drive summaries
- **Calibrates** to your specific engine's baselines after warm-up
- **Monitors** its own health — watchdog restarts failed services automatically
- **Syncs** logs and calibration data to the Sentient Core home network (nanob) when in range

## Hardware

| Component | What You Need |
|-----------|---------------|
| Compute | Raspberry Pi 5 (8GB) + NVMe HAT + SSD |
| CAN Interface | USB2CANFD V1 (or any gs_usb compatible adapter) |
| OBD-II Cable | OBD-II to bare-wire pigtail (Pins 6 & 14) |
| RF (Optional) | RTL-SDR + antenna |
| Display | Your phone (RealDash app) + Pioneer AA head unit |
| Audio | 3.5mm cable + ground loop isolator → Pioneer AUX |
| Power | Whatever works — battery pack, car USB, hardwired 12V |

## Quick Start

```bash
# 1. Flash Kali ARM64 to your NVMe and boot the Pi

# 2. Clone this repo
git clone https://github.com/mz1312/drifter.git
cd drifter

# 3. Run the installer
sudo ./install.sh

# 4. Reboot
sudo reboot

# 5. Connect phone to Wi-Fi: MZ1312_DRIFTER / uncaged1312
# 6. Open RealDash → TCP CAN → 10.42.0.1:35000
#    (or MQTT → 10.42.0.1:1883)
# 7. Plug phone into Pioneer via USB
# 8. Screw OBD-II pigtail CAN-H/CAN-L into USB2CANFD terminals
# 9. After first warm-up drive, run calibration:
#    sudo /opt/drifter/venv/bin/python3 /opt/drifter/calibrate.py --auto
# 10. Done.
```

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    JAGUAR X-TYPE                         │
│  OBD-II Port (Pin 6: CAN-H, Pin 14: CAN-L)            │
└──────────────────────┬──────────────────────────────────┘
                       │ Twisted pair
              ┌────────┴────────┐
              │   USB2CANFD V1  │
              │  (screw terms)  │
              └────────┬────────┘
                       │ USB
┌──────────────────────┴──────────────────────────────────┐
│                 RASPBERRY PI 5                           │
│                                                          │
│  can_bridge.py ──→ MQTT (NanoMQ :1883)                  │
│                       │                                  │
│         ┌─────────────┼─────────────────┐                │
│         ▼             ▼                 ▼                │
│   alert_engine    logger          voice_alerts           │
│   (13 rules)    (sessions)       (Piper TTS)            │
│         │             │                 │                │
│         ▼             ▼                 ▼                │
│    MQTT pub      JSON logs        3.5mm audio            │
│         │        (NVMe)                                  │
│         │                                                │
│   realdash_bridge ──→ TCP :35000 (RealDash 0x44)        │
│   watchdog.py ──→ auto-restarts failed services          │
│   calibrate.py ──→ learns engine baselines               │
│   home_sync ──→ nanob (192.168.1.159) when home          │
│                 (MQTT bridge + rsync logs)                │
│                                                          │
│  Wi-Fi Hotspot: MZ1312_DRIFTER (10.42.0.1)             │
└──────────────────────┬──────────────────────────────────┘
                       │ Wi-Fi
              ┌────────┴────────┐
              │   YOUR PHONE    │
              │   (RealDash)    │
              └────────┬────────┘
                       │ USB (Android Auto)
              ┌────────┴────────┐
              │  PIONEER STEREO │
              │  (touchscreen)  │
              └─────────────────┘
```

## Services

| Service | What It Does | Auto-starts |
|---------|-------------|-------------|
| `nanomq` | MQTT message broker | Yes |
| `drifter-canbridge` | CAN bus → MQTT translator (Mode 01 + DTC reads) | Yes |
| `drifter-alerts` | Diagnostic rule engine (13 rules) | Yes |
| `drifter-logger` | Telemetry → JSON logs with drive session detection | Yes |
| `drifter-voice` | Piper TTS voice alerts | Yes |
| `drifter-hotspot` | Wi-Fi AP for phone | Yes |
| `drifter-homesync` | MQTT bridge + rsync log sync to nanob | Yes |
| `drifter-watchdog` | Service health monitor + auto-restart | Yes |
| `drifter-realdash` | MQTT → TCP CAN frame bridge for RealDash | Yes |

## MQTT Topics

```
# ── Engine Telemetry ──
drifter/engine/rpm          # Engine RPM
drifter/engine/coolant      # Coolant temp (°C)
drifter/engine/stft1        # Short-term fuel trim Bank 1 (%)
drifter/engine/stft2        # Short-term fuel trim Bank 2 (%)
drifter/engine/ltft1        # Long-term fuel trim Bank 1 (%)
drifter/engine/ltft2        # Long-term fuel trim Bank 2 (%)
drifter/engine/load         # Calculated engine load (%)
drifter/engine/throttle     # Throttle position (%)
drifter/engine/iat          # Intake air temperature (°C)
drifter/engine/maf          # Mass air flow (g/s)

# ── Vehicle ──
drifter/vehicle/speed       # Vehicle speed (km/h)
drifter/power/voltage       # Battery/alternator voltage (V)

# ── Diagnostics ──
drifter/alert/level         # Alert level (0=OK, 1=INFO, 2=AMBER, 3=RED)
drifter/alert/message       # Diagnostic message (plain English)
drifter/diag/dtc            # JSON: {stored: [...], pending: [...]}

# ── System ──
drifter/snapshot            # Combined snapshot (all values)
drifter/system/status       # Node status (online/offline)
drifter/system/watchdog     # JSON: service health + system metrics
drifter/session             # JSON: drive session start/stop events
drifter/diag/calibration     # JSON: calibration results
```

## Diagnostic Rules

| # | Rule | Trigger | Alert |
|---|------|---------|-------|
| 1 | Vacuum leak (Bank 1) | STFT1 >12%, RPM <900, STFT2 <5% | AMBER |
| 2 | Vacuum leak (Both) | STFT1 >12%, STFT2 >12%, RPM <900 | AMBER |
| 3 | Coolant critical | Temp ≥108°C or rising >2°C/min over 100°C | RED |
| 4 | Running rich | STFT <-12% sustained 30s+ | AMBER |
| 5 | Alternator failing | Voltage <13.2V at RPM >1500 | AMBER |
| 6 | Idle instability | RPM spread >200 at idle | INFO |
| 7 | Over-rev | RPM >6500 | RED |
| 8 | LTFT drift | LTFT >±20% (maxed) or >±12% (drifted) | RED/AMBER |
| 9 | Bank imbalance | STFT1 vs STFT2 divergence >15% | AMBER |
| 10 | Intake heat soak | IAT >65°C at speed, or >50°C at idle | AMBER |
| 11 | Voltage overcharge | Voltage >15.5V (regulator failure) | RED |
| 12 | Active DTCs | ECU reports stored/pending fault codes | AMBER/RED |
| 13 | Engine stalled | RPM drops to 0 from >300 unexpectedly | RED |

## Calibration

The alert engine uses configurable thresholds from `src/config.py`. For per-vehicle precision, run the calibration tool after a warm-up drive:

```bash
# Auto-calibrate (waits for warm engine, samples 5 minutes at idle)
sudo /opt/drifter/venv/bin/python3 /opt/drifter/calibrate.py --auto

# Check calibration status
/opt/drifter/venv/bin/python3 /opt/drifter/calibrate.py --status
```

Calibration learns: baseline STFT, LTFT, idle RPM, and voltage for your specific engine. The alert engine automatically uses these learned baselines if a calibration file exists.

## Repo Structure

```
drifter/
├── install.sh              # One-command installer
├── README.md               # This file
├── LICENSE                  # MIT
├── src/
│   ├── config.py           # Central configuration (thresholds, paths, topics)
│   ├── can_bridge.py       # CAN → MQTT bridge (Mode 01 + DTC reads)
│   ├── alert_engine.py     # Diagnostic rules (13 rules)
│   ├── logger.py           # Telemetry logger (drive sessions, gzip compression)
│   ├── voice_alerts.py     # TTS voice alerts
│   ├── home_sync.py        # Home network sync (MQTT bridge + rsync)
│   ├── status.py           # CLI status / health check dashboard
│   ├── calibrate.py        # Auto-calibration tool (learns engine baselines)
│   ├── watchdog.py         # Service health monitor + auto-restart
│   └── realdash_bridge.py  # MQTT → RealDash TCP CAN frame bridge
├── services/
│   ├── drifter-canbridge.service
│   ├── drifter-alerts.service
│   ├── drifter-logger.service
│   ├── drifter-voice.service
│   ├── drifter-hotspot.service
│   ├── drifter-homesync.service
│   ├── drifter-watchdog.service
│   └── drifter-realdash.service
├── config/
│   ├── nanomq.conf         # MQTT broker config
│   ├── setup-can.sh        # CAN interface auto-setup
│   ├── 80-can.rules        # udev rules for USB CAN
│   └── boot-config.txt     # Lines to add to /boot/firmware/config.txt
├── realdash/
│   └── drifter_channels.xml # RealDash channel map
└── docs/
    └── WIRING.md           # Physical wiring guide
```

## Checking System Status

```bash
# Human-readable status (services + live telemetry + current alert)
/opt/drifter/venv/bin/python3 /opt/drifter/status.py

# JSON output for scripting
/opt/drifter/venv/bin/python3 /opt/drifter/status.py --json

# Follow alert log
journalctl -u drifter-alerts -f

# Raw MQTT telemetry
mosquitto_sub -h localhost -t "drifter/#" -v
```

## Drive Sessions

The logger automatically detects engine-on/engine-off transitions and records per-drive summaries:

```bash
# View session summaries
ls /opt/drifter/logs/sessions/

# Example session JSON includes:
# - session_id, start/end time, duration
# - max RPM, max speed, max coolant, min voltage
# - alert count, highest alert level
# - estimated distance
```

Sessions are published to `drifter/session` via MQTT and synced to nanob via rsync.

## Troubleshooting

### CAN bridge won't start / no data

```bash
# Check if the interface exists
ip link show can0

# Bring it up manually (replace can0 with your interface)
sudo ip link set can0 type can bitrate 500000
sudo ip link set up can0

# Verify the adapter is seen by the kernel
lsusb | grep -i can
dmesg | grep -i 'gs_usb\|can'
```

### No MQTT data in RealDash

```bash
# Confirm broker is running
systemctl status nanomq || systemctl status mosquitto

# Check canbridge is publishing
mosquitto_sub -h localhost -t "drifter/engine/rpm" -v

# Is the Pi's hotspot up?
nmcli con show "MZ1312_DRIFTER"
nmcli con up "MZ1312_DRIFTER"
```

### Voice alerts not working

```bash
# Test audio directly
aplay /usr/share/sounds/alsa/Front_Left.wav

# Test espeak fallback
espeak-ng "Drifter test" -v en-gb

# Check mixer levels (ALSA)
alsamixer
```

### Log files

```bash
# Service logs
journalctl -u drifter-canbridge -n 50
journalctl -u drifter-alerts -n 50
journalctl -u drifter-watchdog -n 50

# Drive logs (stored as JSONL, compressed to .jsonl.gz after each day)
ls -lh /opt/drifter/logs/
zcat /opt/drifter/logs/drive_2026-01-01.jsonl.gz | tail -20

# Drive session summaries
cat /opt/drifter/logs/sessions/session_*.json | python3 -m json.tool

# System health (from watchdog)
mosquitto_sub -h localhost -t "drifter/system/watchdog" -C 1 | python3 -m json.tool
```

### Service won't stop cleanly

All Python services handle `SIGTERM` properly so `systemctl stop` will trigger
a clean shutdown with MQTT disconnect and log flush. If a service hangs, check:

```bash
systemctl status drifter-canbridge
journalctl -u drifter-canbridge --since "1 min ago"
```

## 1312 — Local Processing — Zero Cloud — Total Sovereignty

No data leaves the vehicle. No subscriptions. No telemetry to manufacturers.
Your car, your data, your rules.

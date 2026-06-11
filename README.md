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

---

## Table of Contents

- [What It Does](#what-it-does)
- [Hardware](#hardware)
- [Quick Start](#quick-start)
- [Architecture](#architecture)
- [Services](#services)
- [MQTT Topics](#mqtt-topics)
- [Diagnostic Rules](#diagnostic-rules)
- [Calibration](#calibration)
- [RTL-SDR / RF Features](#rtl-sdr--rf-features)
- [Repo Structure](#repo-structure)
- [Dependencies](#dependencies)
- [Testing](#testing)
- [Checking System Status](#checking-system-status)
- [Drive Sessions](#drive-sessions)
- [Troubleshooting](#troubleshooting)
- [License](#license)

---

## What It Does

- **Reads** the Jaguar's CAN bus via USB2CANFD adapter and OBD-II (Mode 01 live data + Mode 03/07 DTCs)
- **Diagnoses** mechanical issues in real-time using 23 deterministic rules (vacuum leaks, fuel trim drift, coolant, alternator, intake heat soak, TPMS, DTC detection, stall detection, thermostat failure, coil pack degradation, MAF health, throttle body carbon, cold start monitoring, and more)
- **Scans** the RF spectrum with RTL-SDR — TPMS tire pressure monitoring, 433 MHz signal decoding, emergency band scanning
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
| RF | RTL-SDR v3/v4 dongle + 433 MHz antenna |
| Display | Your phone (RealDash app) + Pioneer AA head unit |
| Audio | 3.5mm cable + ground loop isolator → Pioneer AUX |
| Power | Whatever works — battery pack, car USB, hardwired 12V |

## Quick Start

### Option A: One-command deploy from Windows (easiest)

No git or internet needed on the Pi — just SSH access:

```powershell
# From your Windows machine (PowerShell)
.\deploy.ps1 -PiHost 192.168.1.50
```

This copies everything to the Pi and runs the installer over SSH. Use `-PiUser kali` if your username differs, or `-CopyOnly` to just transfer files without installing.

### Option B: Clone on the Pi

```bash
# On the Pi (needs internet)
git clone https://github.com/thotsl4yer69/drifter.git
cd drifter
sudo ./install.sh
```

### Option C: USB stick (no network at all)

```bash
# On any Linux/Mac machine with the repo
./scripts/make-bundle.sh
# Copy drifter-bundle.tar.gz to a USB stick

# On the Pi
mount /dev/sda1 /mnt
tar xzf /mnt/drifter-bundle.tar.gz
cd drifter && sudo ./install.sh
```

### After install

```bash
# 1. Reboot
sudo reboot

# 2. Connect phone to Wi-Fi: MZ1312_DRIFTER / uncaged1312
# 3. Open RealDash → TCP CAN → 10.42.0.1:35000
#    (or MQTT → 10.42.0.1:1883)
# 4. Plug phone into Pioneer via USB
# 5. Screw OBD-II pigtail CAN-H/CAN-L into USB2CANFD terminals
# 6. After first warm-up drive, run calibration:
sudo /opt/drifter/venv/bin/python3 /opt/drifter/calibrate.py --auto
# 7. Done.
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
│  rf_monitor.py ──→ ┘  │                                 │
│  (RTL-SDR 433MHz)      │                                │
│         ┌───────────────┼──────────────┐                 │
│         ▼               ▼              ▼                 │
│   alert_engine      logger       voice_alerts            │
│   (23 rules)      (sessions)    (Piper TTS)             │
│         │               │              │                 │
│         ▼               ▼              ▼                 │
│    MQTT pub        JSON logs     3.5mm audio             │
│         │          (NVMe)                                │
│         │                                                │
│   realdash_bridge ──→ TCP :35000 (RealDash 0x44)        │
│   watchdog.py ──→ auto-restarts failed services          │
│   calibrate.py ──→ learns engine baselines               │
│   home_sync ──→ nanob (192.168.1.159) when home          │
│                 (MQTT bridge + rsync logs)                │
│                                                          │
│  RTL-SDR dongle ──→ USB  (433 MHz TPMS + spectrum)      │
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
| `drifter-alerts` | Diagnostic rule engine (23 rules) | Yes |
| `drifter-logger` | Telemetry → JSON logs with drive session detection | Yes |
| `drifter-voice` | Piper TTS voice alerts | Yes |
| `drifter-hotspot` | Wi-Fi AP for phone | Yes |
| `drifter-homesync` | MQTT bridge + rsync log sync to nanob | Yes |
| `drifter-watchdog` | Service health monitor + auto-restart | Yes |
| `drifter-realdash` | MQTT → TCP CAN frame bridge for RealDash | Yes |
| `drifter-rf` | RTL-SDR RF monitor — TPMS, spectrum, emergency bands | Yes |
| `drifter-wardrive` | Passive Wi-Fi + Bluetooth scanning per drive | Yes |
| `drifter-dashboard` | Web dashboard (HTTP :8080, WS :8081, audio :8082) | Yes |
| `drifter-anomaly` | Z-score anomaly detection on telemetry | Yes |
| `drifter-analyst` | Drive session analysis and reporting | Yes |
| `drifter-voicein` | Vosk STT + OpenWakeWord voice input | Yes |
| `drifter-fbmirror` | Framebuffer mirror for SPI LCD HUD | Yes |

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

# ── RF / TPMS ──
drifter/rf/tpms/fl          # JSON: front-left tire {pressure_psi, temp_c, sensor_id}
drifter/rf/tpms/fr          # JSON: front-right tire
drifter/rf/tpms/rl          # JSON: rear-left tire
drifter/rf/tpms/rr          # JSON: rear-right tire
drifter/rf/tpms/snapshot    # JSON: all 4 tires combined
drifter/rf/signals          # JSON: decoded 433 MHz signals (all types)
drifter/rf/spectrum         # JSON: spectrum sweep results
drifter/rf/emergency        # JSON: emergency band activity scan
drifter/rf/status           # JSON: RF module status
drifter/rf/command          # JSON: commands (tpms_learn, assign, scan)
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
| 14 | TPMS low pressure | Tire <26 PSI (warn) or <20 PSI (critical) | AMBER/RED |
| 15 | TPMS rapid loss | Pressure drops >3 PSI in 5 min (puncture) | RED |
| 16 | TPMS temp | Tire temp >80°C (warn) or >100°C (critical) | AMBER/RED |
| 17 | X-Type thermostat | Coolant oscillation >8°C/2min or stuck <78°C after 10 min | AMBER |
| 18 | X-Type coil pack | RPM stumble under load + bank STFT divergence | AMBER |
| 19 | X-Type MAF degradation | MAF <2.5 g/s at warm idle (AJ-V6 underreporting) | AMBER |
| 20 | X-Type throttle body | Throttle open but load mismatch (carbon buildup) | AMBER |
| 21 | X-Type cold start | Cold idle monitoring (fast idle normal / too-low warning) | INFO/AMBER |
| 22 | X-Type alternator age | Voltage 12.8-13.5V at >1500 RPM + declining trend | INFO |
| 23 | X-Type warmup progress | Reports warmup completion, enables full diagnostics | INFO |

## Calibration

The alert engine uses configurable thresholds from `src/config.py`. For per-vehicle precision, run the calibration tool after a warm-up drive:

```bash
# Auto-calibrate (waits for warm engine, samples 5 minutes at idle)
sudo /opt/drifter/venv/bin/python3 /opt/drifter/calibrate.py --auto

# Check calibration status
/opt/drifter/venv/bin/python3 /opt/drifter/calibrate.py --status
```

Calibration learns: baseline STFT, LTFT, idle RPM, and voltage for your specific engine. The alert engine automatically uses these learned baselines if a calibration file exists.

## RTL-SDR / RF Features

The RTL-SDR dongle provides three capabilities through a single `rf_monitor` daemon:

### TPMS Tire Monitoring

Decodes 433 MHz TPMS sensors (works with most aftermarket and many factory sensors). The system supports a **learn-and-assign** workflow:

```bash
# Start learn mode — drive a short distance while the system captures sensor IDs
mosquitto_pub -t drifter/rf/command -m '{"cmd": "tpms_learn_start"}'

# Stop learn mode
mosquitto_pub -t drifter/rf/command -m '{"cmd": "tpms_learn_stop"}'

# Auto-assign 4 captured IDs to FL/FR/RL/RR positions
mosquitto_pub -t drifter/rf/command -m '{"cmd": "tpms_auto_assign"}'

# Or manually assign a specific sensor ID to a position
mosquitto_pub -t drifter/rf/command -m '{"cmd": "tpms_assign", "sensor_id": "A1B2C3", "position": "fl"}'
```

Once assigned, each tire publishes live pressure (PSI) and temperature (°C) to MQTT, displays on the RealDash dashboard (frames 0x140 and 0x150), and triggers voice alerts for low pressure, rapid loss (puncture detection), and high temperature.

### RF Spectrum Scanner

Periodic broadband sweeps (24 MHz – 1.7 GHz) using `rtl_power`. Results are classified by band (FM, Airband, TETRA, ISM-433, etc.) and published to `drifter/rf/spectrum`. Useful for general RF awareness and detecting nearby transmitters.

### Emergency Band Monitor

Scans known UK/EU emergency and utility frequencies:

| Band | Frequency | Use |
|------|-----------|-----|
| PMR446 | 446.0 MHz | Licence-free two-way radio |
| Marine VHF Ch16 | 156.8 MHz | Distress / calling |
| Airband Guard | 121.5 MHz | Aviation emergency |
| ISM-433 | 433.92 MHz | Sensors, key fobs, devices |
| TETRA Control | 390.0 MHz | Emergency service control |
| Rail NRN | 454.0 MHz | Network Rail operations |

Detects RF activity above threshold and publishes to `drifter/rf/emergency`. Note: encrypted traffic (TETRA) is detected but not decoded.

> **Single SDR limitation:** The RTL-SDR can only tune to one frequency at a time. `rtl_433` runs continuously for TPMS/sensor decoding. Spectrum and emergency scans run periodically during brief pauses.

## Repo Structure

```
drifter/
├── install.sh              # One-command installer
├── README.md               # This file
├── LICENSE                  # MIT
├── src/
│   ├── config.py                # Central configuration (thresholds, paths, topics)
│   ├── can_bridge.py            # CAN → MQTT bridge (Mode 01 + DTC reads)
│   ├── alert_engine.py          # Diagnostic rules (23 rules)
│   ├── logger.py                # Telemetry logger (drive sessions, gzip compression)
│   ├── voice_alerts.py          # TTS voice alerts
│   ├── voice_input.py           # Wake-word / PTT STT → intent routing
│   ├── vivi.py                  # Vivi voice assistant (STT → Ollama → TTS)
│   ├── home_sync.py             # Home network sync (MQTT bridge + rsync)
│   ├── status.py                # CLI status / health check dashboard
│   ├── calibrate.py             # Auto-calibration tool (learns engine baselines)
│   ├── watchdog.py              # Service health monitor + auto-restart
│   ├── realdash_bridge.py       # MQTT → RealDash TCP CAN frame bridge
│   ├── rf_monitor.py            # RTL-SDR: TPMS, spectrum scan, emergency bands
│   ├── wardrive.py              # Wi-Fi + Bluetooth passive scan logger
│   ├── flipper_bridge.py        # Flipper Zero serial bridge → MQTT
│   ├── anomaly_monitor.py       # Statistical anomaly detection
│   ├── session_analyst.py       # Post-drive session analysis + trend reports
│   ├── db.py                    # SQLite session + telemetry store
│   ├── llm_client.py            # LLM provider abstraction (Ollama / Groq / Anthropic)
│   ├── mechanic.py              # X-Type offline knowledge base + RAG search
│   ├── field_ops_kb.py          # Field operations knowledge base
│   ├── diagnose.py              # Fleet-contract diagnostic probe (drifter diagnose)
│   ├── web_dashboard.py         # HTTP dashboard server (port 8080) + /healthz
│   ├── web_dashboard_handlers.py# Route handlers + healthz payload builder
│   ├── web_dashboard_html.py    # HUD HTML/JS generator
│   ├── web_dashboard_state.py   # Shared dashboard state (telemetry, alerts)
│   ├── web_dashboard_audio.py   # Audio WebSocket bridge (port 8082)
│   ├── web_dashboard_hardware.py# Hardware info helpers
│   └── fbmirror.c               # Framebuffer mirror for SPI LCD
├── services/                    # 18 active systemd units
│   ├── drifter-canbridge.service
│   ├── drifter-alerts.service
│   ├── drifter-logger.service
│   ├── drifter-voice.service
│   ├── drifter-vivi.service
│   ├── drifter-voicein.service
│   ├── drifter-hotspot.service
│   ├── drifter-homesync.service
│   ├── drifter-watchdog.service
│   ├── drifter-realdash.service
│   ├── drifter-rf.service
│   ├── drifter-wardrive.service
│   ├── drifter-flipper.service
│   ├── drifter-anomaly.service
│   ├── drifter-analyst.service
│   ├── drifter-dashboard.service
│   └── drifter-fbmirror.service
├── config/
│   ├── nanomq.conf              # MQTT broker config
│   ├── vivi.yaml                # Vivi voice assistant config
│   ├── setup-can.sh             # CAN interface auto-setup
│   ├── 80-can.rules             # udev rules for USB CAN
│   └── boot-config.txt          # Lines to add to /boot/firmware/config.txt
├── realdash/
│   └── drifter_channels.xml     # RealDash channel map
├── scripts/
│   └── oneshot.sh               # Stage-gated deploy wrapper
├── tests/
│   ├── test_alert_engine.py     # Diagnostic rule unit tests
│   ├── test_alert_hysteresis.py # Alert de-bounce / hysteresis tests
│   ├── test_anomaly_monitor.py
│   ├── test_can_bridge.py
│   ├── test_config.py
│   ├── test_db.py
│   ├── test_diagnose.py
│   ├── test_home_sync.py
│   ├── test_llm_client.py
│   ├── test_mechanic.py
│   ├── test_realdash_bridge.py
│   ├── test_session_analyst.py
│   ├── test_settings.py
│   ├── test_status.py
│   ├── test_vivi.py
│   ├── test_voice_input.py
│   ├── test_wardrive.py
│   ├── test_watchdog.py
│   ├── test_web_dashboard.py
│   └── test_web_dashboard_handlers.py
├── bin/
│   └── drifter                  # Fleet-contract CLI (drifter diagnose)
├── conftest.py                  # pytest path config
└── docs/
    └── WIRING.md                # Physical wiring guide
```

## Dependencies

### System Packages

Installed automatically by `install.sh`:

| Package | Purpose |
|---------|---------|
| `python3-pip`, `python3-venv` | Python environment |
| `can-utils` | CAN bus utilities (`cansend`, `candump`, etc.) |
| `mosquitto-clients` | MQTT CLI tools (`mosquitto_pub`, `mosquitto_sub`) |
| `network-manager` | Wi-Fi hotspot management |
| `alsa-utils` | Audio playback (`aplay`, `alsamixer`) |
| `rsync` | Log sync to home network |
| `librtlsdr-dev`, `rtl-sdr` | RTL-SDR drivers |
| `slcand` | Serial-line CAN adapter |
| `nanomq` or `mosquitto` | MQTT broker |
| `piper` or `espeak-ng` | Text-to-speech engine |
| `rtl_433` | 433 MHz signal decoder |

### Python Packages

Installed in `/opt/drifter/venv`:

| Package | Version | Purpose |
|---------|---------|---------|
| `python-can` | latest | CAN bus communication |
| `paho-mqtt` | <2.0 (v1.x API) | MQTT client |
| `psutil` | latest | System metrics (CPU, memory, disk) |

## Testing

### Unit Tests

```bash
# Run all tests
pytest tests/ -v

# Run with coverage (if pytest-cov is installed)
pytest tests/ -v --cov=src
```

Tests cover all 23 diagnostic rules in the alert engine, including trigger conditions, OK conditions, and edge cases.

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

## License

This project is licensed under the [MIT License](LICENSE).

Copyright © 2026 MZ1312 UNCAGED TECHNOLOGY

---

## 1312 — Local Processing — Zero Cloud — Total Sovereignty

No data leaves the vehicle. No subscriptions. No telemetry to manufacturers.
Your car, your data, your rules.

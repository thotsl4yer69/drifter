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

- **Reads** the Jaguar's CAN bus via USB2CANFD adapter and OBD-II
- **Diagnoses** mechanical issues in real-time using deterministic rules (vacuum leaks, coolant, alternator, misfires, rich/lean conditions)
- **Displays** live telemetry on your Pioneer head unit via RealDash + Android Auto
- **Speaks** diagnostic alerts through your car speakers via Piper TTS
- **Logs** all telemetry to NVMe for post-drive analysis
- **Syncs** to the Sentient Core home network (nanob) when in range

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
# 6. Open RealDash → MQTT → 10.42.0.1:1883
# 7. Plug phone into Pioneer via USB
# 8. Screw OBD-II pigtail CAN-H/CAN-L into USB2CANFD terminals
# 9. Done.
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
│              ┌────────┼────────┐                         │
│              ▼        ▼        ▼                         │
│        alert_engine  logger  voice_alerts                │
│              │        │        │                         │
│              ▼        ▼        ▼                         │
│         MQTT pub   JSON logs  Piper TTS                  │
│              │     (NVMe)    (3.5mm out)                 │
│              │                                           │
│         home_sync ──→ nanob (192.168.1.159)             │
│                      (when home)                         │
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
| `drifter-canbridge` | CAN bus → MQTT translator | Yes |
| `drifter-alerts` | Diagnostic rule engine (7 rules) | Yes |
| `drifter-logger` | Telemetry → JSON log files | Yes |
| `drifter-voice` | Piper TTS voice alerts | Yes |
| `drifter-hotspot` | Wi-Fi AP for phone | Yes |
| `drifter-homesync` | MQTT bridge to nanob | Yes |

## MQTT Topics

```
drifter/engine/rpm          # Engine RPM
drifter/engine/coolant      # Coolant temp (°C)
drifter/engine/stft1        # Short-term fuel trim Bank 1 (%)
drifter/engine/stft2        # Short-term fuel trim Bank 2 (%)
drifter/engine/load         # Calculated engine load (%)
drifter/engine/throttle     # Throttle position (%)
drifter/vehicle/speed       # Vehicle speed (km/h)
drifter/power/voltage       # Battery/alternator voltage (V)
drifter/alert/level         # Alert level (0=OK, 1=INFO, 2=AMBER, 3=RED)
drifter/alert/message       # Diagnostic message (plain English)
drifter/snapshot            # Combined snapshot (all values)
drifter/system/status       # Node status (online/offline)
```

## Diagnostic Rules

| Rule | Trigger | Alert |
|------|---------|-------|
| Vacuum leak (Bank 1) | STFT1 >12%, RPM <900, STFT2 <5% | AMBER |
| Vacuum leak (Both) | STFT1 >12%, STFT2 >12%, RPM <900 | AMBER |
| Coolant critical | Temp ≥108°C or rising >2°C/min over 100°C | RED |
| Running rich | STFT <-12% sustained 30s+ | AMBER |
| Alternator failing | Voltage <13.2V at RPM >1500 | AMBER |
| Idle instability | RPM spread >200 at idle | INFO |
| Over-rev | RPM >6500 | RED |

## Repo Structure

```
drifter/
├── install.sh              # One-command installer
├── README.md               # This file
├── LICENSE                  # MIT
├── src/
│   ├── can_bridge.py       # CAN → MQTT bridge
│   ├── alert_engine.py     # Diagnostic rules
│   ├── logger.py           # Telemetry logger (gzip-compresses old logs)
│   ├── voice_alerts.py     # TTS voice alerts
│   ├── home_sync.py        # Home network sync
│   └── status.py           # CLI status / health check
├── tests/
│   └── test_alert_engine.py # Unit tests for all diagnostic rules
├── services/
│   ├── drifter-canbridge.service
│   ├── drifter-alerts.service
│   ├── drifter-logger.service
│   ├── drifter-voice.service
│   ├── drifter-hotspot.service
│   └── drifter-homesync.service
├── config/
│   ├── nanomq.conf         # MQTT broker config
│   ├── setup-can.sh        # CAN interface auto-setup
│   ├── 80-can.rules        # udev rules for USB CAN
│   └── boot-config.txt     # Lines to add to /boot/firmware/config.txt
├── realdash/
│   └── drifter_channels.xml # RealDash channel map
├── docs/
│   └── WIRING.md           # Physical wiring guide
└── scripts/
    └── test-bench.sh       # Bench test without a car
```

## Checking System Status

```bash
# Human-readable status (services + live telemetry + current alert)
python3 /opt/drifter/status.py

# JSON output for scripting
python3 /opt/drifter/status.py --json

# Follow alert log
journalctl -u drifter-alerts -f

# Raw MQTT telemetry
mosquitto_sub -h localhost -t "drifter/#" -v
```

## Bench Testing (No Car Needed)

```bash
# Create a virtual CAN interface and replay diagnostic scenarios
sudo ./scripts/test-bench.sh

# This simulates a Jaguar with a vacuum leak so you can test
# the full pipeline: CAN → MQTT → Alerts → Voice → RealDash
```

## Running Tests

```bash
cd drifter-repo
pip install pytest paho-mqtt
python -m pytest tests/ -v
```

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

# Drive logs (stored as JSONL, compressed to .jsonl.gz after each day)
ls -lh /opt/drifter/logs/
zcat /opt/drifter/logs/drive_2026-01-01.jsonl.gz | tail -20
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

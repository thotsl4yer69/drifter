
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
@@ -291,14 +314,80 @@ drifter/
│   ├── 80-can.rules        # udev rules for USB CAN
│   └── boot-config.txt     # Lines to add to /boot/firmware/config.txt
├── realdash/
│   └── drifter_channels.xml # RealDash channel map├── scripts/
│   └── drifter_channels.xml # RealDash channel map
├── scripts/
│   └── test-bench.sh       # MQTT test scenarios (idle, vacuum, overheat, alternator, X-Type)
├── tests/
│   └── test_alert_engine.py # Unit tests for diagnostic rules
├── conftest.py             # pytest path config└── docs/
├── conftest.py             # pytest path config
└── docs/
   └── WIRING.md           # Physical wiring guide
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

### Test Bench (Hardware-Free Simulation)

The test bench injects simulated telemetry via MQTT — no CAN hardware or vehicle needed:

```bash
# Run a specific scenario
./scripts/test-bench.sh idle        # Normal warm idle (all OK)
./scripts/test-bench.sh vacuum      # Bank 1 lean (triggers AMBER)
./scripts/test-bench.sh overheat    # Coolant ramp 95→120°C (escalates to RED)
./scripts/test-bench.sh alternator  # Voltage drop (escalates to RED)
./scripts/test-bench.sh coldstart   # Cold engine with fast idle
./scripts/test-bench.sh thermostat  # Coolant oscillation (AMBER)
./scripts/test-bench.sh dtc         # Inject DTCs (P0301, P0420, P0171)

# Run all scenarios
./scripts/test-bench.sh all
```

Requires `mosquitto_pub` (from `mosquitto-clients`).

## Checking System Status

```bash
@@ -405,6 +494,14 @@ systemctl status drifter-canbridge
journalctl -u drifter-canbridge --since "1 min ago"
```

## License

This project is licensed under the [MIT License](LICENSE).

Copyright © 2026 MZ1312 UNCAGED TECHNOLOGY

---

## 1312 — Local Processing — Zero Cloud — Total Sovereignty

No data leaves the vehicle. No subscriptions. No telemetry to manufacturers.

#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Central Configuration
All shared constants, thresholds, and paths in one place.
UNCAGED TECHNOLOGY — EST 1991
"""

from pathlib import Path

# ── Paths ──
DRIFTER_DIR = Path("/opt/drifter")
LOG_DIR = DRIFTER_DIR / "logs"
CALIBRATION_FILE = DRIFTER_DIR / "calibration.json"

# ── MQTT ──
MQTT_HOST = "localhost"
MQTT_PORT = 1883

# ── CAN Bus ──
CAN_BITRATE = 500000
OBD_REQUEST_ID = 0x7DF
OBD_RESPONSE_BASE = 0x7E8
OBD_RESPONSE_END = 0x7EF

# ── Vehicle: 2004 Jaguar X-Type 2.5L V6 ──
VEHICLE = "2004 Jaguar X-Type 2.5L V6"
REDLINE_RPM = 6500
IDLE_RPM_MAX = 1000

# ── Alert Levels ──
LEVEL_OK = 0
LEVEL_INFO = 1
LEVEL_AMBER = 2
LEVEL_RED = 3
LEVEL_NAMES = {0: 'OK', 1: 'INFO', 2: 'AMBER', 3: 'RED'}

# ── Alert Thresholds (defaults — overridden by calibration) ──
THRESHOLDS = {
    # Fuel Trim
    'stft_lean_idle': 12.0,       # % above which = lean at idle
    'stft_rich_sustained': -12.0, # % below which = rich
    'stft_sustained_samples': 150, # ~30s at 5Hz
    'ltft_lean_warn': 15.0,       # LTFT lean warning
    'ltft_lean_crit': 25.0,       # LTFT lean critical (maxed out)
    'ltft_rich_warn': -15.0,      # LTFT rich warning
    'ltft_rich_crit': -25.0,      # LTFT rich critical

    # Coolant
    'coolant_amber': 104,         # °C warning
    'coolant_red': 108,           # °C critical
    'coolant_rise_rate': 2.0,     # °C/min to trigger warning above 100°C

    # Alternator / Battery
    'voltage_undercharge': 13.2,  # V below this at >1500 RPM = alternator issue
    'voltage_critical': 12.0,     # V below this = battery dying
    'voltage_overcharge': 15.0,   # V above this = regulator failure

    # Idle
    'idle_rpm_spread': 200,       # RPM spread at idle = instability
    'idle_rpm_ceiling': 900,      # Max RPM to consider "idle"

    # RPM
    'overrev_rpm': 6500,

    # Catalyst (estimated from STFT/LTFT divergence)
    'catalyst_stft_divergence': 8.0,  # % difference between banks

    # Intake Air Temperature
    'iat_high': 50,               # °C — hot soak warning
    'iat_critical': 65,           # °C — heat soak critical
}

# ── Calibration Defaults ──
CALIBRATION_DEFAULTS = {
    'stft1_baseline': 0.0,
    'stft2_baseline': 0.0,
    'ltft1_baseline': 0.0,
    'ltft2_baseline': 0.0,
    'idle_rpm_baseline': 750.0,
    'voltage_baseline': 14.2,
    'coolant_normal': 92.0,
    'calibrated': False,
    'calibration_date': None,
    'drive_km': 0.0,
}

# ── Home Network (nanob) ──
NANOB_HOST = "192.168.1.159"
NANOB_PORT = 1883
NANOB_USER = "sentient"
HOME_CHECK_INTERVAL = 30

# ── Voice ──
VOICE_COOLDOWN = 15
PIPER_MODEL = "en_GB-alan-medium"

# ── Logger ──
BUFFER_FLUSH_INTERVAL = 30
MAX_LOG_SIZE_MB = 500

# ── Watchdog ──
WATCHDOG_INTERVAL = 30          # Check services every 30s
WATCHDOG_MQTT_TIMEOUT = 60      # No MQTT data for 60s = stale

# ── RealDash ──
REALDASH_TCP_PORT = 35000       # TCP port for RealDash CAN connection

# ── MQTT Topics ──
TOPICS = {
    'rpm': 'drifter/engine/rpm',
    'coolant': 'drifter/engine/coolant',
    'stft1': 'drifter/engine/stft1',
    'stft2': 'drifter/engine/stft2',
    'ltft1': 'drifter/engine/ltft1',
    'ltft2': 'drifter/engine/ltft2',
    'load': 'drifter/engine/load',
    'speed': 'drifter/vehicle/speed',
    'throttle': 'drifter/engine/throttle',
    'voltage': 'drifter/power/voltage',
    'iat': 'drifter/engine/iat',
    'maf': 'drifter/engine/maf',
    'alert_level': 'drifter/alert/level',
    'alert_message': 'drifter/alert/message',
    'snapshot': 'drifter/snapshot',
    'system_status': 'drifter/system/status',
    'dtc': 'drifter/diag/dtc',
    'calibration': 'drifter/diag/calibration',
    'watchdog': 'drifter/system/watchdog',
    'drive_session': 'drifter/session',
}

# ── Services ──
SERVICES = [
    "drifter-canbridge",
    "drifter-alerts",
    "drifter-logger",
    "drifter-voice",
    "drifter-hotspot",
    "drifter-homesync",
    "drifter-watchdog",
    "drifter-realdash",
]

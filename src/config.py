#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Central Configuration
All shared constants, thresholds, and paths in one place.
UNCAGED TECHNOLOGY — EST 1991
"""

import json
import logging
import os
from pathlib import Path

import paho.mqtt.client as _mqtt

# Central API-key registry. Re-exported below so the rest of the fleet
# imports credentials from config (`from config import GOOGLE_MAPS_API_KEY`)
# rather than reaching into api_keys directly. Guarded so a missing
# api_keys.py (older deploy) degrades to env vars instead of bricking every
# service that imports config.
try:
    from api_keys import (
        GOOGLE_EARTH_ENGINE_API_KEY,
        GOOGLE_ELEVATION_API_KEY,
        GOOGLE_MAPS_API_KEY,
        GOOGLE_PLACES_API_KEY,
        OPENWEATHERMAP_API_KEY,
        have_key,
    )
except ImportError:  # pragma: no cover - fallback for partial deploys
    OPENWEATHERMAP_API_KEY = os.getenv("OPENWEATHERMAP_API_KEY", "")
    GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "")
    GOOGLE_ELEVATION_API_KEY = GOOGLE_MAPS_API_KEY
    GOOGLE_PLACES_API_KEY = GOOGLE_MAPS_API_KEY
    GOOGLE_EARTH_ENGINE_API_KEY = os.getenv("GOOGLE_EARTH_ENGINE_API_KEY", "")

    def have_key(key: str | None) -> bool:
        return bool(key and key.strip())

_log = logging.getLogger(__name__)


def make_mqtt_client(client_id: str, **kwargs):
    """Build a paho-mqtt Client on the v2 callback API.

    Centralised so the whole fleet can move API versions with one edit.
    paho-mqtt 2.0 defaults to VERSION1 callbacks (deprecated) unless you
    pass ``callback_api_version`` explicitly — so we always pass VERSION2.

    Extra keyword args are forwarded to paho.mqtt.client.Client().
    """
    if hasattr(_mqtt, 'CallbackAPIVersion'):
        return _mqtt.Client(
            callback_api_version=_mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
            **kwargs,
        )
    # paho-mqtt < 2.0 — the old API is the only option. Keep the fleet
    # running on older installs (e.g. before install.sh is re-run).
    return _mqtt.Client(client_id=client_id, **kwargs)

# ── Paths ──
DRIFTER_DIR = Path("/opt/drifter")
LOG_DIR = DRIFTER_DIR / "logs"
CALIBRATION_FILE = DRIFTER_DIR / "calibration.json"
SETTINGS_FILE = DRIFTER_DIR / "settings.json"

# ── User Settings (runtime-editable via /settings page) ──
SETTINGS_DEFAULTS = {
    # Alert thresholds (mirror THRESHOLDS keys — overrides on load)
    'coolant_amber': 104,
    'coolant_red': 108,
    'voltage_undercharge': 13.2,
    'voltage_critical': 12.0,
    'stft_lean_idle': 12.0,
    'ltft_lean_warn': 15.0,
    'ltft_lean_crit': 25.0,
    # Voice
    'voice_cooldown': 15,
    'tts_engine': 'piper',          # 'piper' or 'espeak'
    'voice_min_level': 2,           # min alert level for voice (0-3)
    # Display
    'temp_unit': 'C',               # 'C' or 'F'
    'pressure_unit': 'PSI',         # 'PSI', 'kPa', or 'bar'
    # LLM
    'llm_model': '',                # empty = use config default
    'llm_max_tokens': 500,
    'llm_tools_enabled': True,
    # Data
    'data_retention_days': 90,
    # Setup
    'setup_complete': False,
}

# Operator-facing settings schema. Drives the cockpit Settings overlay
# render and the POST /api/settings validation. Fields are listed in
# display order; section determines grouping in the UI.
#
# Internal state flags (setup_complete, plus any future onboarding-only
# fields) are intentionally absent — they remain settable via the
# onboarding flow through save_settings (which only checks against
# SETTINGS_DEFAULTS), but they are not surfaced as operator-toggleable
# controls.
SETTINGS_SCHEMA = [
    # ── Thresholds ───────────────────────────────────────────────
    {'key': 'coolant_amber', 'label': 'Coolant amber (°C)',
     'description': 'Warn level for coolant temperature.',
     'type': 'int', 'section': 'thresholds', 'min': 60, 'max': 130},
    {'key': 'coolant_red', 'label': 'Coolant red (°C)',
     'description': 'Critical coolant temperature — triggers voice + alert.',
     'type': 'int', 'section': 'thresholds', 'min': 60, 'max': 140},
    {'key': 'voltage_undercharge', 'label': 'Voltage undercharge (V)',
     'description': 'Alternator output below this is flagged as weak.',
     'type': 'float', 'section': 'thresholds', 'min': 11.0, 'max': 14.5},
    {'key': 'voltage_critical', 'label': 'Voltage critical (V)',
     'description': 'Battery is dropping — critical alert level.',
     'type': 'float', 'section': 'thresholds', 'min': 10.0, 'max': 13.0},
    {'key': 'stft_lean_idle', 'label': 'STFT lean idle (%)',
     'description': 'Short-term fuel trim at idle that flags a lean condition.',
     'type': 'float', 'section': 'thresholds', 'min': 0.0, 'max': 30.0},
    {'key': 'ltft_lean_warn', 'label': 'LTFT lean warn (%)',
     'description': 'Long-term fuel trim warn threshold.',
     'type': 'float', 'section': 'thresholds', 'min': 0.0, 'max': 30.0},
    {'key': 'ltft_lean_crit', 'label': 'LTFT lean critical (%)',
     'description': 'Long-term fuel trim critical threshold.',
     'type': 'float', 'section': 'thresholds', 'min': 0.0, 'max': 50.0},
    # ── Voice ────────────────────────────────────────────────────
    {'key': 'voice_cooldown', 'label': 'Voice cooldown (s)',
     'description': 'Minimum gap between spoken alerts of the same kind.',
     'type': 'int', 'section': 'voice', 'min': 0, 'max': 600},
    {'key': 'tts_engine', 'label': 'TTS engine',
     'description': 'Piper is higher quality; espeak is the lightweight fallback.',
     'type': 'enum', 'section': 'voice', 'enum_options': ['piper', 'espeak']},
    {'key': 'voice_min_level', 'label': 'Voice alert minimum level',
     'description': '0 = chatty, 1 = info, 2 = warn, 3 = critical only.',
     'type': 'int', 'section': 'voice', 'min': 0, 'max': 3},
    # ── Display ──────────────────────────────────────────────────
    {'key': 'temp_unit', 'label': 'Temperature unit',
     'description': 'Used in cockpit gauges and Vivi spoken responses.',
     'type': 'enum', 'section': 'display', 'enum_options': ['C', 'F']},
    {'key': 'pressure_unit', 'label': 'Pressure unit',
     'description': 'TPMS, boost, and oil pressure display unit.',
     'type': 'enum', 'section': 'display', 'enum_options': ['PSI', 'kPa', 'bar']},
    # ── LLM ──────────────────────────────────────────────────────
    {'key': 'llm_model', 'label': 'LLM model override',
     'description': 'Ollama tag, e.g. qwen2.5:1.5b. Empty = use the config default.',
     'type': 'str', 'section': 'llm'},
    {'key': 'llm_max_tokens', 'label': 'LLM max response tokens',
     'description': 'Upper bound on generated tokens per reply.',
     'type': 'int', 'section': 'llm', 'min': 1, 'max': 8192},
    {'key': 'llm_tools_enabled', 'label': 'LLM tool use',
     'description': 'Allow the LLM to call structured tools (DTC lookup, calc).',
     'type': 'bool', 'section': 'llm'},
    # ── Data ─────────────────────────────────────────────────────
    {'key': 'data_retention_days', 'label': 'Data retention (days)',
     'description': 'How long the SQLite telemetry archive is kept before pruning.',
     'type': 'int', 'section': 'data', 'min': 1, 'max': 3650},
]

# Display order + human label for the schema sections. Anything not
# listed here falls into an "other" bucket if added later.
SETTINGS_SECTIONS = [
    {'key': 'thresholds', 'label': 'Alert thresholds'},
    {'key': 'voice',      'label': 'Voice'},
    {'key': 'display',    'label': 'Display'},
    {'key': 'llm',        'label': 'LLM'},
    {'key': 'data',       'label': 'Data'},
]


def validate_settings_payload(payload):
    """Validate a /api/settings POST body against SETTINGS_SCHEMA.

    Returns (cleaned, error). On success, cleaned is the payload dict
    (unchanged — save_settings will still drop unknown keys via the
    SETTINGS_DEFAULTS allowlist). On failure, cleaned is None and error
    is a short string suitable for an HTTP 400 body, naming the field.

    Keys not in SETTINGS_SCHEMA are passed through without schema
    validation — they're either internal-state flags (setup_complete)
    legitimately set by the onboarding flow, or unknown keys that
    save_settings will silently drop. The schema only constrains the
    operator-visible fields it explicitly describes.
    """
    if not isinstance(payload, dict):
        return None, 'body must be a JSON object'
    schema_by_key = {entry['key']: entry for entry in SETTINGS_SCHEMA}
    for k, v in payload.items():
        entry = schema_by_key.get(k)
        if entry is None:
            continue
        t = entry['type']
        if t == 'bool':
            if not isinstance(v, bool):
                return None, f"{k}: expected true or false"
        elif t == 'enum':
            if v not in entry['enum_options']:
                opts = ', '.join(entry['enum_options'])
                return None, f"{k}: must be one of {opts}"
        elif t == 'int':
            # bool is a subclass of int in Python — reject it explicitly
            # so a stray True doesn't sneak through as 1.
            if isinstance(v, bool) or not isinstance(v, int):
                return None, f"{k}: expected an integer"
            lo, hi = entry.get('min'), entry.get('max')
            if lo is not None and v < lo:
                return None, f"{k}: must be >= {lo}"
            if hi is not None and v > hi:
                return None, f"{k}: must be <= {hi}"
        elif t == 'float':
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                return None, f"{k}: expected a number"
            lo, hi = entry.get('min'), entry.get('max')
            if lo is not None and v < lo:
                return None, f"{k}: must be >= {lo}"
            if hi is not None and v > hi:
                return None, f"{k}: must be <= {hi}"
        elif t == 'str':
            if not isinstance(v, str):
                return None, f"{k}: expected a string"
    return payload, None


def load_settings() -> dict:
    """Load user settings from settings.json, merging with defaults."""
    settings = dict(SETTINGS_DEFAULTS)
    try:
        if SETTINGS_FILE.exists():
            with open(SETTINGS_FILE) as f:
                saved = json.load(f)
            settings.update(saved)
    except Exception as e:
        _log.warning(f"Failed to load settings: {e}")
    return settings


def save_settings(settings: dict) -> bool:
    """Persist user settings to settings.json.

    Only keys present in SETTINGS_DEFAULTS are persisted. Unknown keys
    from the request body are silently dropped — the settings file is
    not a key-value bag; new settings must land in SETTINGS_DEFAULTS
    first. This keeps a local-network POST from injecting arbitrary
    fields into the runtime config.
    """
    try:
        if not isinstance(settings, dict):
            return False
        filtered = {k: v for k, v in settings.items() if k in SETTINGS_DEFAULTS}
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(filtered, f, indent=2)
        return True
    except Exception as e:
        _log.warning(f"Failed to save settings: {e}")
        return False

# ── MQTT ──
MQTT_HOST = "localhost"
MQTT_PORT = 1883

# ── CAN Bus ──
CAN_BITRATE = 500000
OBD_REQUEST_ID = 0x7DF
OBD_RESPONSE_BASE = 0x7E8
OBD_RESPONSE_END = 0x7EF

# CAN FD — native socketcan FD bridge (can_native.py / RDK X5).
# The X-Type itself is classic CAN (500 kbps), but the RDK X5 + native
# socketcan stack supports CAN FD with a faster data-phase bitrate. The
# bridge falls back to classic CAN when the interface or controller does
# not advertise FD support.
CAN_FD_ENABLED = os.getenv("CAN_FD_ENABLED", "false").lower() in ("1", "true", "yes")
CAN_FD_DATA_BITRATE = int(os.getenv("CAN_FD_DATA_BITRATE", "2000000"))  # 2 Mbps data phase
CAN_NATIVE_CHANNEL = os.getenv("CAN_NATIVE_CHANNEL", "can0")

# ── Platform Detection ──
# Two supported telemetry nodes share this codebase:
#   pi5    — Raspberry Pi 5 (8 GB), Kali ARM64, slcan/USB2CANFD adapter
#   rdkx5  — D-Robotics RDK X5 (Sunrise X5), native socketcan + CAN FD
# hardware.py owns runtime detection + backend selection; this flag is a
# cheap, import-safe hint other modules can branch on without importing
# hardware.py. Override with DRIFTER_PLATFORM for bench/CI.
def _detect_platform() -> str:
    forced = os.getenv("DRIFTER_PLATFORM", "").strip().lower()
    if forced in ("pi5", "rdkx5"):
        return forced
    try:
        model = Path("/proc/device-tree/model").read_text(errors="ignore").lower()
    except Exception:
        model = ""
    if "rdk x5" in model or "sunrise" in model or "x5" in model:
        return "rdkx5"
    return "pi5"


# PLATFORM / IS_RDKX5 / IS_PI5 are computed LAZILY via the module-level
# __getattr__ (PEP 562) at the bottom of this file. _detect_platform() does a
# /proc/device-tree/model file probe; running it at import would make every
# one of the ~80 modules that `import config` pay that I/O cost up front. By
# resolving on first attribute access instead, `import config` stays pure
# (no file reads / subprocess), while BOTH access patterns keep working
# unchanged: `config.PLATFORM` AND `from config import PLATFORM` (the latter
# also routes through __getattr__). Cached after first access so the probe
# runs at most once per process.
#
# Deliberately NOT assigned at module level — defining them as real
# attributes would shadow __getattr__ (which only fires for *missing* names)
# and reintroduce the import-time probe.
_LAZY_ATTRS_CACHE: dict[str, object] = {}


def __getattr__(name: str):
    """PEP 562 lazy attribute resolver for detected-at-import values.

    Computes PLATFORM/IS_RDKX5/IS_PI5 on first access (and caches them) so
    plain `import config` performs no subprocess / file I/O. Anything else is
    a genuine miss and raises AttributeError as usual.
    """
    if name in _LAZY_ATTRS_CACHE:
        return _LAZY_ATTRS_CACHE[name]
    if name in ("PLATFORM", "IS_RDKX5", "IS_PI5"):
        platform = _detect_platform()
        _LAZY_ATTRS_CACHE["PLATFORM"] = platform
        _LAZY_ATTRS_CACHE["IS_RDKX5"] = platform == "rdkx5"
        _LAZY_ATTRS_CACHE["IS_PI5"] = platform == "pi5"
        return _LAZY_ATTRS_CACHE[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

# ═══════════════════════════════════════════════════════════════════
#  Vehicle: 2004 Jaguar X-Type 2.5L V6 (AJ-V6 / Duratec)
# ═══════════════════════════════════════════════════════════════════
VEHICLE = "2004 Jaguar X-Type 2.5L V6"
VEHICLE_YEAR = 2004
VEHICLE_MODEL = "X-Type"
VEHICLE_ENGINE = "2.5 V6"

# Engine — Ford/Jaguar AJ-V6 (Duratec-derived).
# Full spec sheet (bore/stroke/firing order/etc.) lives in mechanic.py's
# YAML-loaded VEHICLE_SPECS, not here, so we don't duplicate it as
# Python constants that no code reads.

# RPM
IDLE_RPM_MAX = 1000
IDLE_RPM_WARM_LOW = 650    # Normal warm idle floor
IDLE_RPM_WARM_HIGH = 780   # Normal warm idle ceiling

# Thermostat — plastic housing behind timing cover (known failure)
THERMOSTAT_OPEN_C = 88      # Starts opening
COOLANT_NORMAL_LOW = 86     # Normal operating range low
COOLANT_NORMAL_HIGH = 98    # Normal operating range high

# Warmup — suppress lean alerts during cold start
WARMUP_COOLANT_THRESHOLD = 60   # °C — below this, STFT lean is expected
WARMUP_TIME_MAX = 600           # 10 min — if not at 80°C by then, thermostat issue
WARMUP_COOLANT_TARGET = 80      # °C — should reach this within WARMUP_TIME_MAX

# MAF — expected ranges for the AJ-V6
MAF_IDLE_MIN = 2.5     # g/s — below this at warm idle = dirty/failing MAF
MAF_IDLE_MAX = 6.0     # g/s — above this at idle = implausible

# Drivetrain
DRIVETRAIN = "AWD"     # Haldex coupling to rear axle
TRANSMISSION = "5AT"   # Jatco 5-speed auto (JF506E)
FUEL_TYPE = "petrol"
FUEL_OCTANE = 95       # RON — AU spec

# Tire spec — factory 205/55R16
TIRE_SIZE = "205/55R16"
TIRE_PRESSURE_FRONT = 30   # PSI factory spec
TIRE_PRESSURE_REAR = 30    # PSI factory spec

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

    # TPMS (tuned for 205/55R16, factory 30 PSI)
    'tpms_pressure_low': 26.0,    # PSI — X-Type factory min is 30, warn at -4
    'tpms_pressure_crit': 20.0,   # PSI — critically low
    'tpms_pressure_high': 38.0,   # PSI — overinflated for this size
    'tpms_temp_warn': 80,         # °C — tire temp warning
    'tpms_temp_crit': 100,        # °C — tire temp critical
    'tpms_rapid_loss': 3.0,       # PSI drop in 5 min = rapid loss

    # X-Type Specific
    'thermostat_oscillation': 8.0,     # °C swing in 2 min = failing thermostat
    'thermostat_stuck_open_temp': 78,  # °C — below this after 10 min = stuck open
    'maf_idle_low': 2.5,               # g/s — MAF too low at warm idle
    'coil_rpm_drop_threshold': 150,    # RPM — sudden drop under load = misfire
    'throttle_load_mismatch': 15.0,    # % — throttle open but load too low
}

# ═══════════════════════════════════════════════════════════════════
#  DTC Lookup — 2004 Jaguar X-Type / AJ-V6 Specific
#  Plain-English diagnosis with X-Type known causes
# ═══════════════════════════════════════════════════════════════════
# The lookup table itself lives in _config_dtc.py (pure data, extracted to
# keep this module lean). Re-imported here so `config.XTYPE_DTC_LOOKUP` and
# `from config import XTYPE_DTC_LOOKUP` resolve exactly as before.
from _config_dtc import XTYPE_DTC_LOOKUP  # noqa: E402,F401  (re-export)

# ── Calibration Defaults ──
CALIBRATION_DEFAULTS = {
    'stft1_baseline': 0.0,
    'stft2_baseline': 0.0,
    'ltft1_baseline': 0.0,
    'ltft2_baseline': 0.0,
    'idle_rpm_baseline': 720.0,   # AJ-V6 typical warm idle
    'voltage_baseline': 14.2,
    'coolant_normal': 92.0,       # Mid-range for AJ-V6 thermostat
    'maf_idle_baseline': 3.8,     # g/s typical for 2.5L at idle
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
PIPER_MODEL = "en_GB-jenny_dioco-medium"  # female British voice — fits Vivi persona
PIPER_MODEL_DIR = DRIFTER_DIR / "piper-models"

# ── Logger ──
BUFFER_FLUSH_INTERVAL = 30
MAX_LOG_SIZE_MB = 500

# ── Watchdog ──
WATCHDOG_INTERVAL = 30          # Check services every 30s
WATCHDOG_MQTT_TIMEOUT = 60      # No MQTT data for 60s = stale

# ── RealDash ──
REALDASH_TCP_PORT = 35000       # TCP port for RealDash CAN connection

# ── RTL-SDR / RF ──
RTL433_BIN = '/usr/local/bin/rtl_433'
TPMS_SENSOR_FILE = DRIFTER_DIR / 'tpms_sensors.json'
TPMS_POSITIONS = ['fl', 'fr', 'rl', 'rr']  # Front-left, front-right, rear-left, rear-right
TPMS_LEARN_TIMEOUT = 300        # 5 min to learn sensor IDs
TPMS_STALE_TIMEOUT = 1800       # 30 min no reading = sensor offline
SPECTRUM_SCAN_INTERVAL = 300    # Spectrum sweep every 5 min
SPECTRUM_FREQ_START = 24        # MHz — rtl_power start
SPECTRUM_FREQ_END = 1766        # MHz — rtl_power end
EMERGENCY_SCAN_INTERVAL = 60    # Emergency band scan every 60s
EMERGENCY_SCAN_DWELL = 5        # Seconds per frequency
# ADS-B aircraft tracking (1090 MHz, requires dump1090)
ADSB_SCAN_INTERVAL = 300        # ADS-B scan every 5 min (pauses TPMS)
ADSB_SCAN_DURATION = 25         # Seconds to gather aircraft data
ADSB_JSON_DIR = DRIFTER_DIR / 'data' / 'adsb'  # dump1090 write-json target
DUMP1090_BIN = 'readsb'         # Kali ships readsb (modern dump1090 fork). Honours --write-json.

# ── Wardrive ──
WARDRIVE_LOG_DIR = DRIFTER_DIR / 'logs' / 'wardrive'
WIFI_SCAN_INTERVAL = 30         # Seconds between Wi-Fi scans
BT_SCAN_INTERVAL = 60           # Seconds between Bluetooth scans
BT_SCAN_DURATION = 8            # Seconds for BLE lescan window
EMERGENCY_BANDS = [
    # UK / EU emergency and utility bands
    {'name': 'PMR446', 'freq_mhz': 446.0, 'desc': 'Licence-free PMR radios'},
    {'name': 'Marine-VHF-16', 'freq_mhz': 156.8, 'desc': 'Marine distress ch16'},
    {'name': 'Airband-Guard', 'freq_mhz': 121.5, 'desc': 'Aviation emergency'},
    {'name': 'ISM-433', 'freq_mhz': 433.92, 'desc': 'ISM band (sensors, keyfobs)'},
    {'name': 'TETRA-Control', 'freq_mhz': 390.0, 'desc': 'TETRA emergency (encrypted)'},
    {'name': 'Rail-NRN', 'freq_mhz': 454.9, 'desc': 'National Rail Network'},
]

# ── rfaudio: live demodulated emergency-services audio ──
# Frequencies the rfaudio service can tune to (Australia / Bendigo VIC
# defaults — adjust per operating area). 'mode' is the rtl_fm demod:
# 'nfm' = narrowband FM (most land-mobile + UHF CB), 'wfm' = wideband FM
# (commercial broadcast 88–108 MHz), 'am' = AM (aviation/airband). The
# 'family' tag is just a label for grouping in the dashboard later.
EMERGENCY_AUDIO_BANDS = [
    # UHF CB ch 5 (emergency) + ch 35 (emergency repeater input). Channel 5
    # is the only frequency a member of the public must monitor; treat as
    # the default tune-in target.
    {'name': 'UHF-CB-Ch5',      'freq_mhz': 476.525, 'mode': 'nfm', 'family': 'emergency-cb'},
    {'name': 'UHF-CB-Ch35',     'freq_mhz': 476.750, 'mode': 'nfm', 'family': 'emergency-cb'},
    {'name': 'UHF-CB-Ch9-RoadTrain', 'freq_mhz': 476.625, 'mode': 'nfm', 'family': 'cb'},
    {'name': 'Marine-VHF-16',   'freq_mhz': 156.800, 'mode': 'nfm', 'family': 'marine'},
    {'name': 'Airband-Guard',   'freq_mhz': 121.500, 'mode': 'am',  'family': 'aviation'},
    {'name': 'Melbourne-Ctr',   'freq_mhz': 124.700, 'mode': 'am',  'family': 'aviation'},
    {'name': 'CFA-Bendigo',     'freq_mhz':  76.225, 'mode': 'nfm', 'family': 'fire-rescue'},  # one of several legacy analog channels — verify locally
]
RFAUDIO_DEFAULT_FREQ_MHZ = 476.525   # UHF CB ch 5 — the emergency channel
RFAUDIO_DEFAULT_MODE     = 'nfm'
RFAUDIO_DEFAULT_GAIN     = 0          # 0 = automatic
RFAUDIO_SAMPLE_RATE      = 200000     # rtl_fm input rate
RFAUDIO_OUTPUT_RATE      = 48000      # aplay output rate
RFAUDIO_APLAY_DEVICE     = 'plughw:0,0'  # USB Audio Device card 0 (C-Media on this Pi)
RFAUDIO_PAUSE_WAIT_SEC   = 3.0   # MQTT round-trip + drifter-rf scan-kill + USB device release; further retries inside AudioStream
RFAUDIO_OPEN_RETRIES     = 3     # rtl_fm retries on usb_claim_interface error (drifter-rf scan finishing)
RFAUDIO_OPEN_RETRY_BACKOFF_SEC = 2.0  # Between retries; total worst-case latency ≈ 9s

# ── MQTT Topics ──
# Full drifter/* topic map lives in _config_topics.py (pure data, extracted
# to keep this module lean). Re-imported here so `config.TOPICS` and
# `from config import TOPICS` resolve exactly as before.
from _config_topics import TOPICS  # noqa: E402,F401  (re-export)

# ── LLM v2 cascade config ──
LLM_CASCADE_ORDER = os.getenv("LLM_CASCADE_ORDER", "ollama").split(",")
LLM_CLAUDE_TIMEOUT = 60
LLM_GROQ_TIMEOUT = 30
LLM_OLLAMA_TIMEOUT = int(os.getenv("LLM_OLLAMA_TIMEOUT", "300"))
LLM_CACHE_TTL = 3600
LLM_MAX_RETRIES = 2

# ── Telemetry Batcher ──
TELEMETRY_WINDOW_SECONDS = 30
TELEMETRY_PUBLISH_HZ = 1
TELEMETRY_KEEP_SAMPLES = 200

# ── Trip Computer ──
TRIP_FUEL_CURRENCY = os.getenv("TRIP_FUEL_CURRENCY", "AUD")  # ISO-4217; cockpit maps to symbol.
TRIP_FUEL_PRICE_PER_L = float(os.getenv("TRIP_FUEL_PRICE_PER_L", "1.85"))  # AU regular unleaded ~mid-2026.
# Backward-compat alias — drifter-trip historically read GBP_PER_L; keeps the
# import surface stable for any older deploy. Operator overrides via env var
# or config/driver.yaml fuel_price_per_l (no currency suffix).
TRIP_FUEL_PRICE_GBP_PER_L = TRIP_FUEL_PRICE_PER_L
TRIP_FUEL_TANK_LITRES = 60
TRIP_AVG_CONSUMPTION_L_PER_100KM = 12
TRIP_SESSION_GAP_MIN = 15

# ── Adaptive Thresholds ──
ADAPTIVE_LEARN_MIN_SAMPLES = 60
ADAPTIVE_LEARN_SESSIONS = 5
ADAPTIVE_DRIFT_LIMIT = 0.25

# ── Vivi v2 Memory ──
VIVI2_HISTORY_TURNS = 16
VIVI2_MEMORY_MAX_ENTRIES = 256

# ── LLM Analyst ──
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = "claude-sonnet-4-6"

# ── LLM Backend (Ollama — local, offline) ──
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "localhost")
OLLAMA_PORT = int(os.getenv("OLLAMA_PORT", "11434"))
# ONE small model for the whole fleet. Every local LLM consumer resolves to
# this tag: session_analyst, session_reporter, ai_diagnostics (all via
# src/llm_client.py) and Vivi (vivi.yaml ollama_model mirrors this). install.sh
# pulls EXACTLY this tag by default — tests/test_llm_model_strategy.py enforces
# the two stay in sync so the deploy never downloads an unused model again.
# Pi 5 ran qwen2.5:3b at 165% CPU with 60+s stalls; 1.5b responds in ~10s warm.
# The prompt-side NO DATA tags + vivi_grounding.validate() post-hoc check
# together catch the fabrication class 3b was originally chosen to prevent.
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:1.5b")
OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "30m")
# Cap resident models at 1 so analyst + Vivi can't both pin a model and OOM the
# 8GB Pi. Enforced on the ollama DAEMON via the systemd drop-in install.sh
# writes (ollama.service won't read /opt/drifter/.env); surfaced here so callers
# and ops tooling can read the intended limit from one place.
OLLAMA_MAX_LOADED_MODELS = int(os.getenv("OLLAMA_MAX_LOADED_MODELS", "1"))

# ── Voice Input (STT) ──
VOSK_MODEL_DIR = DRIFTER_DIR / "vosk-models" / "vosk-model-small-en-us-0.15"
WAKE_WORD_MODEL = "hey_jarvis_v0.1"  # bundled openwakeword model — closest fit to "hey vivi"
WAKE_WORD_THRESHOLD = 0.85           # bumped from 0.7: default bundle was firing on ambient noise
PTT_GPIO_PIN = 17                    # GPIO pin for push-to-talk button
VOICE_SILENCE_TIMEOUT = 1.5          # seconds of silence to end recording
VOICE_MAX_RECORD = 30                # max seconds per utterance

# ── Anomaly Detection ──
ANOMALY_ROLLING_WINDOW = 60        # readings per sensor
ANOMALY_WARN_Z = 2.5
ANOMALY_HIGH_Z = 3.5
ANOMALY_CRITICAL_Z = 4.5
ANOMALY_IDLE_RPM_STDDEV = 50       # RPM stddev threshold at idle

# ── BLE Passive Scanner (drifter-bleconv) ──
BLE_TARGETS_PATH = DRIFTER_DIR / "ble_targets.yaml"
BLE_HISTORY_PATH = DRIFTER_DIR / "state" / "ble_history.db"
BLE_RAW_PUBLISH = os.getenv("BLE_RAW_PUBLISH", "false").lower() in ("1", "true", "yes")
BLE_LOG_RETENTION_DAYS = int(os.getenv("BLE_LOG_RETENTION_DAYS", "30"))
BLE_RATE_LIMIT_SEC = float(os.getenv("BLE_RATE_LIMIT_SEC", "30"))
BLE_GPS_FRESH_SEC = float(os.getenv("BLE_GPS_FRESH_SEC", "10"))

# Topics that home_sync MUST NEVER bridge to the home node. BLE detection
# data + audio classifier output stay local only.
HOMESYNC_EXCLUDE_TOPICS = [
    'drifter/ble/+',
    'drifter/audio/+',
]

# ── Storage ──
DB_PATH = DRIFTER_DIR / "data" / "drifter.db"
REPORTS_DIR = DRIFTER_DIR / "reports"
ANALYST_BASELINE_SESSIONS = 10

# ── Services ──
# Canonical list of 19 active systemd services.
SERVICES = [
    "drifter-canbridge",
    "drifter-alerts",
    "drifter-logger",
    "drifter-anomaly",
    "drifter-analyst",
    "drifter-voice",
    "drifter-vivi",
    "drifter-hotspot",
    "drifter-homesync",
    "drifter-watchdog",
    "drifter-realdash",
    "drifter-rf",
    "drifter-wardrive",
    "drifter-dashboard",
    "drifter-fbmirror",
    "drifter-voicein",
    "drifter-flipper",
    "drifter-opsec",
    "drifter-bleconv",      # Phase 4.5 — passive BLE scanner
    "drifter-gps",          # Phase 5.2 — gpsd → MQTT GPS publisher
    "drifter-rfaudio",      # on-demand SDR → speaker (emergency listen)
    # v2 services
    "drifter-batcher",      # rolling telemetry window aggregator
    "drifter-trip",         # per-trip distance + fuel computer
    "drifter-thresholds",   # adaptive baseline learner
    "drifter-reporter",     # post-drive markdown report via LLM
    "drifter-weather",      # OpenWeatherMap → drifter/weather/*
    "drifter-location",     # Google Elevation + Places → drifter/location/*
    # Recon / audit expansion (Agent B)
    "drifter-kismet",        # headless Wi-Fi/BLE recon daemon
    "drifter-kismet-bridge", # Kismet REST → MQTT bridge
    "drifter-wifi-audit",    # bettercap PMKID/handshake (allowlist-scoped)
    "drifter-marauder",      # ESP32 Marauder Wi-Fi/BT attack bridge (NEW)
    "drifter-fly-catcher",   # ADS-B ghost detector
    "drifter-feeds",         # ADS-B aircraft producer feeding fly-catcher
    # RF/CAN expansion (Agent A)
    "drifter-can-discovery",  # CaringCaribou UDS / fuzz bridge
    # Arsenal — Rubber Ducky / BadUSB HID injection (foot-only)
    "drifter-hid",           # ARM→CONFIRM→RUN HID injector (Flipper + native)
    # In-car triage console + network resilience
    "drifter-lcd",           # 3.5" SPI LCD framebuffer dashboard (hw-optional)
    "drifter-autoconnect",   # Wi-Fi hotspot auto-connector + AP fallback
    # Counter-surveillance — Ghost Protocol (Shade Core hardware + sw correlator)
    "drifter-ghost",         # ghost_protocol.py — tracker/IMSI/ALPR/RF correlator
    "drifter-ghost-voice",   # speaks drifter/ghost/alert via alert_message
]

# ── Modes ──
# Same hardware, two operator personas:
#   DRIVE — in the vehicle, CAN connected, telemetry meaningful.
#   FOOT  — battery-pack mobile, recon/opsec console.
# Services classified into three buckets; each list is mutually exclusive,
# and the union must equal SERVICES (validated below).
DRIVE_ONLY_SERVICES = [
    "drifter-canbridge",   # CAN bus needs vehicle ECUs present
    "drifter-alerts",      # vehicle alerts
    "drifter-anomaly",     # telemetry anomaly detector
    "drifter-analyst",     # LLM session analyst over driving sessions
    "drifter-voice",       # cabin TTS for vehicle alerts
    "drifter-realdash",    # RealDash app feed
    "drifter-fbmirror",    # SPI LCD mirror for the dash screen
    "drifter-rf",          # RTL-SDR TPMS — passive vehicle telemetry
    "drifter-bleconv",     # passive BLE awareness (axon/tile/airtag)
    "drifter-gps",         # GPS feed for the cockpit map + drive_id geo-tagging
    # v2 drive services
    "drifter-batcher",     # rolling telemetry window aggregator
    "drifter-trip",        # per-trip distance + fuel computer
    "drifter-thresholds",  # adaptive baseline learner
    "drifter-reporter",    # post-drive markdown report via LLM
    # RF/CAN expansion (Agent A)
    "drifter-can-discovery",  # CaringCaribou UDS / fuzz bridge — CAN-only
]
FOOT_ONLY_SERVICES = [
    "drifter-wardrive",    # active Wi-Fi/BT recon
    "drifter-flipper",     # Flipper Zero CLI bridge
    "drifter-opsec",       # OPSEC dashboard on :8090 (Kali aesthetic)
    # Recon / audit expansion (Agent B) — foot-mode only (uses recon dongle)
    "drifter-kismet",
    "drifter-kismet-bridge",
    "drifter-wifi-audit",
    "drifter-marauder",      # NEW
    "drifter-hid",           # Rubber Ducky / BadUSB HID injection (NEW)
]
SHARED_SERVICES = [
    "drifter-dashboard",   # operator HUD (always-on so /healthz stays reachable)
    "drifter-hotspot",     # Wi-Fi AP — phone tethers in either mode
    "drifter-homesync",    # rsync to home node when reachable
    "drifter-watchdog",    # service health monitor
    "drifter-logger",      # telemetry log writer
    "drifter-vivi",        # voice assistant LLM brain
    "drifter-voicein",     # wake-word + STT
    "drifter-rfaudio",     # on-demand SDR → speaker (emergency-band listen)
    "drifter-fly-catcher", # ADS-B ghost detector (passive; runs in both modes)
    "drifter-feeds",       # ADS-B aircraft producer feeding fly-catcher (degrades to idle without a decoder)
    "drifter-weather",     # OpenWeatherMap poller (network-only; runs in both modes)
    "drifter-location",    # Elevation + Places (GPS-aware; runs in both modes)
    "drifter-lcd",         # in-car SPI LCD triage console (runs in both modes)
    "drifter-autoconnect", # Wi-Fi hotspot auto-connect + AP fallback (both modes)
    "drifter-ghost",       # counter-surveillance correlator (runs in both modes)
    "drifter-ghost-voice", # speaks ghost alerts (runs in both modes)
]
# Lean diagnostics floor (RAM safety valve). A curated SUBSET of SERVICES —
# vehicle telemetry + driver-safety only, deliberately excluding every heavy
# RAM consumer (LLM via vivi/analyst/reporter, whisper STT via voicein, the
# fly-catcher ML model, and all recon/offsec). Switch here with
# `sudo drifter mode diag` when the node is memory-pressured; diagnostics and
# the safety pipeline keep running on a fraction of the RAM.
DIAG_SERVICES = [
    "drifter-canbridge",   # CAN telemetry (swap to drifter-obdbridge on K-line cars)
    "drifter-batcher",     # rolling telemetry window
    "drifter-thresholds",  # adaptive baseline learner
    "drifter-anomaly",     # telemetry anomaly detector
    "drifter-alerts",      # driver-safety alert engine
    "drifter-voice",       # cabin TTS for safety alerts (lightweight)
    "drifter-trip",        # trip distance + fuel computer
    "drifter-rf",          # RTL-SDR TPMS (passive vehicle telemetry)
    "drifter-gps",         # GPS feed
    "drifter-realdash",    # RealDash app feed
    "drifter-fbmirror",    # SPI LCD dash mirror
    "drifter-logger",      # telemetry log writer
    "drifter-dashboard",   # operator HUD + /healthz
    "drifter-hotspot",     # Wi-Fi AP
    "drifter-autoconnect", # Wi-Fi uplink / AP fallback
    "drifter-watchdog",    # service health monitor
    "drifter-homesync",    # background rsync to home node
    "drifter-weather",     # OpenWeatherMap poller (network-only, light)
    "drifter-location",    # Elevation + Places (network-only, light)
]

MODES = {
    # Lean diagnostics floor — vehicle telemetry + driver-safety ONLY. No LLM
    # (vivi/analyst/reporter), no STT (voicein), no ML (fly-catcher), no recon.
    # This is the RAM safety valve: `sudo drifter mode diag` stops the heavy
    # services so diagnostics keep working when fuller modes drown the Pi.
    "diag":  set(DIAG_SERVICES),
    "drive": set(DRIVE_ONLY_SERVICES) | set(SHARED_SERVICES),
    "foot":  set(FOOT_ONLY_SERVICES)  | set(SHARED_SERVICES),
    "both":  set(SERVICES),
}
# Sanity: every service must land in exactly one bucket.
_classified = set(DRIVE_ONLY_SERVICES) | set(FOOT_ONLY_SERVICES) | set(SHARED_SERVICES)
assert _classified == set(SERVICES), (
    f"MODES classification drift: missing={set(SERVICES) - _classified}, "
    f"extra={_classified - set(SERVICES)}"
)
assert not (set(DRIVE_ONLY_SERVICES) & set(FOOT_ONLY_SERVICES)), \
    "service cannot be both DRIVE_ONLY and FOOT_ONLY"
# The lean diag mode must be a strict subset of real services.
assert set(DIAG_SERVICES) <= set(SERVICES), \
    f"DIAG_SERVICES not in SERVICES: {set(DIAG_SERVICES) - set(SERVICES)}"

# Persistent mode marker — read by the dashboard and CLI to render which
# persona is currently armed. Updated by `drifter mode <name>`.
MODE_STATE_PATH = DRIFTER_DIR / "mode.state"
DEFAULT_MODE = "drive"

# ── Marauder bridge feature flags ─────────────────────────────────────
# Random-SSID beacon spam is refused outright by the bridge — random
# SSIDs cannot be allowlisted and the firmware-level command is purely
# disruptive. Flip to False + redeploy to enable (deliberate friction).
BEACON_SPAM_RANDOM_REFUSE = True

# Same reasoning for Rick Astley beacon spam. Flip plus add a wildcard
# `marauder.wifi[].ssid: "*"` allowlist entry to enable.
BEACON_SPAM_RICKROLL_REFUSE = True


# ── Arsenal foot-mode control allowlists (BE-4 + command relays) ──────
# The arsenal subset of units the dashboard's POST /api/service/<unit> route
# is permitted to start/stop/restart. This is intersected at the route with
# (FOOT_ONLY_SERVICES ∪ SHARED_SERVICES) so a DRIVE_ONLY unit can NEVER be
# operated even if listed here — fail-closed, defence in depth. The matching
# sudoers drop-in (services/drifter-service.sudoers) enumerates exactly these
# units; keep the two in lock-step.
ARSENAL_SERVICE_UNITS = [
    "drifter-kismet",
    "drifter-kismet-bridge",
    "drifter-marauder",
    "drifter-wardrive",
    "drifter-wifi-audit",
    "drifter-flipper",
    "drifter-rf",
    "drifter-rfaudio",
    "drifter-fly-catcher",
    "drifter-hid",
]

# Marauder command allowlist for POST /api/marauder/command. Mirrors the
# marauder_bridge classifier's action names (LOW ∪ MED ∪ HIGH). HIGH-risk
# ops ARE present so the cockpit can RELAY them WITH the bridge confirm
# token — the dashboard never reimplements the risk tiers or bypasses the
# bridge's ConfirmRegistry; the bridge is the authoritative second gate.
MARAUDER_COMMANDS = [
    # LOW
    "scan_ap", "scan_sta", "scan_probes", "stop",
    "deauth_detect", "ble_scan_all", "ble_scan_airtag", "ble_scan_skim",
    "probe", "status",
    # MED
    "select_ap", "channel_hop", "scan_param",
    # HIGH (relayed only with the bridge's confirm_token round-trip)
    "deauth_attack", "beacon_spam_list", "beacon_spam_random",
    "beacon_spam_rickroll", "probe_flood",
    "ble_spam_swift_pair", "ble_spam_easy_setup",
    "ble_spam_apple_proximity", "ble_spam_all",
    "evilportal_start", "evilportal_stop",
]

# Sentry arm/disarm relay allowlist for POST /api/sentry/command.
SENTRY_COMMANDS = ["arm", "disarm"]



# ═══════════════════════════════════════════════════════════════════
#  v2/v2.1 constants ported from drifter-repo/src/config.py
# ═══════════════════════════════════════════════════════════════════

ALPR_MIN_CONFIDENCE = 0.55
BORE_MM = 82.4
CAN_AI_MIN_SAMPLES = 200           # frames per ID before AI inference
CAN_AI_COLLECT_MAX_SEC = 30        # cap the decoder_ai collection window
CAN_AI_MIN_SATURATED_IDS = 6       # stop once this many IDs reach MIN_SAMPLES
CAN_SNIFF_BUFFER = 5000
CAN_SNIFF_SUMMARY_HZ = 1
COIL_TYPE = "COP"      # Coil-on-plug, 6 individual coils
COMMS_MODEM_DEV = "/dev/ttyUSB2"
COMMS_NOTIFY_BACKENDS = ("ntfy", "telegram", "discord")
COMPRESSION_RATIO = 10.0
CRASH_ACCEL_G_THRESHOLD = 3.0       # peak g over 100ms = crash
CRASH_AIRBAG_GRACE_SEC = 10         # countdown before auto-SOS
CRASH_DECEL_KPH_PER_S = 25          # sudden stop ≥25 km/h/s
CRASH_SOS_NUMBER = ""               # override via crash.yaml -> sos.number
CYLINDER_COUNT = 6
DASHCAM_DIR = DRIFTER_DIR / "dashcam"
DASHCAM_MAX_GB = 32
DASHCAM_SEGMENT_SECONDS = 60
DATA_DIR = DRIFTER_DIR / "data"
DBC_OUTPUT_DIR = DRIFTER_DIR / "data" / "dbc"
DISCORD_COMMAND_PREFIX = "!vivi"
DISCORD_INTENTS = ("messages", "guilds", "message_content")
DISPLACEMENT_CC = 2495
DRIVER_SCORE_WINDOW_KM = 50
ENGINE_CODE = "AJ-V6"
FAST_IDLE_COLD_MAX = 1400  # Cold-start fast idle ceiling
FATIGUE_DRIVE_HOURS = 2.0           # hours behind wheel = nudge
FATIGUE_NIGHT_HOURS = 1.5           # tighter at night
FCW_TTC_CRIT = 1.2                  # time-to-collision critical (s)
FCW_TTC_WARN = 2.5                  # time-to-collision warn (s)
FIRING_ORDER = [1, 4, 2, 5, 3, 6]
FLEET_API_HOST = "0.0.0.0"
FLEET_API_PORT = 8420
FLEET_DB_PATH = DRIFTER_DIR / "data" / "fleet.db"
FLEET_HEARTBEAT_TIMEOUT = 90       # seconds — node considered offline
FLEET_JWT_SECRET_FILE = DRIFTER_DIR / ".fleet_jwt_secret"
FLEET_JWT_TTL = 86400              # 24h tokens
FUZZ_DEFAULT_HZ = 10
FUZZ_DEFAULT_RANGES = {
    'rpm': (650, 6500),
    'speed': (0, 220),
    'coolant': (60, 110),
    'voltage': (11.5, 14.8),
}
HOME_BRIDGE_DISCOVERY = True
HOME_BRIDGE_PREFIX = "homeassistant/drifter"
KB_DIR = DRIFTER_DIR / "kb"
MAF_CRUISE_MIN = 8.0   # g/s — cruising 60-70 km/h typical minimum
MEMORY_DIR = DRIFTER_DIR / "memory"
MESH_BRIDGE_QOS = 1
MESH_DISCOVERY_INTERVAL = 30
MESH_NODE_TTL = 180
MESH_SERVICE_NAME = "_drifter._tcp.local."
NANOB_PASS = os.getenv("NANOB_PASS", "")
NAV_CAMERA_BEARING_TOLERANCE_DEG = 60   # camera must be within ±this of travel bearing
NAV_CAMERA_WARN_METERS = 300
NAV_GEOFENCES_FILE = DATA_DIR / "geofences.json"
NAV_GPS_BAUD = 9600
NAV_GPS_DEVICE = "/dev/ttyACM0"
NAV_OSRM_HOST = "router.project-osrm.org"
NAV_REROUTE_OFF_THRESHOLD = 50      # m off-route triggers reroute
NAV_ROUTE_CACHE_DIR = DATA_DIR / "routes"
NAV_ROUTE_CACHE_TTL_HOURS = 24 * 7
NAV_STATUS_PUBLISH_SEC = 5
NAV_TILE_CACHE_DIR = DATA_DIR / "tiles"
OBD_POLL_HZ = 5
OBD_SERIAL_BAUD = 38400
OBD_SERIAL_DEV = "/dev/ttyUSB0"
PEAK_HP = 194          # bhp @ 6800 RPM
PEAK_TORQUE_NM = 245   # Nm @ 3500 RPM
PRESENCE_DEPARTURE_GRACE = 120     # seconds offline before "departed"
PRESENCE_KNOWN_DEVICES_FILE = DRIFTER_DIR / "data" / "presence_devices.json"
PRESENCE_SCAN_INTERVAL = 30
RECORDER_DIR = DRIFTER_DIR / "recordings"
RECORDER_MAX_GB = 10
RECORDER_SEGMENT_SECONDS = 300     # 5-minute JSONL segments
REDLINE_RPM = 6500
REPLAY_DEFAULT_SPEED = 1.0
REPLAY_DIR = DRIFTER_DIR / "replays"
SATELLITE_DISCOVERY_PORT = 8421
SATELLITE_HEARTBEAT_TIMEOUT = 60
SENTRY_ACCEL_TRIGGER_G = 0.5        # bump threshold
SENTRY_CLIP_SECONDS = 30
SENTRY_DIR = DRIFTER_DIR / "sentry"
SENTRY_MAX_CLIPS = 50
SPEED_CAMERAS_FILE = DATA_DIR / "speed_cameras_vic.json"
SPOTIFY_DEVICE_NAME = "DRIFTER"
SPOTIFY_DUCK_FADE_MS = 400          # fade duration each direction
SPOTIFY_DUCK_LEVEL = 15             # volume during ducking
SPOTIFY_MOODS = {                   # default mood → playlist; overridden by spotify.yaml
    'calm':    'spotify:playlist:37i9dQZF1DWZqd5JICZI0u',
    'focus':   'spotify:playlist:37i9dQZF1DWZeKCadgRdKQ',
    'hype':    'spotify:playlist:37i9dQZF1DXdxcBWuJkbcy',
    'night':   'spotify:playlist:37i9dQZF1DX4SBhb3fqCJd',
    'cruise':  'spotify:playlist:37i9dQZF1DX0XUsuxWHRQd',
}
SPOTIFY_REDIRECT_URI = "http://localhost:8888/callback"
SPOTIFY_SCOPES = "user-modify-playback-state user-read-playback-state user-read-currently-playing streaming"
SPOTIFY_TOKEN_FILE = DRIFTER_DIR / ".spotify_token.json"
STROKE_MM = 79.5
THERMOSTAT_FULL_C = 97      # Fully open
VEHICLES_DIR = DRIFTER_DIR / "vehicles"
VEHICLE_MAKE = "Jaguar"
VEHICLE_DEFAULTS = {
    "make": VEHICLE_MAKE,
    "model": VEHICLE_MODEL,
    "year": VEHICLE_YEAR,
    "engine": VEHICLE_ENGINE,
    "fuel_type": FUEL_TYPE,
    "tank_litres": TRIP_FUEL_TANK_LITRES,
    "avg_consumption_l_per_100km": TRIP_AVG_CONSUMPTION_L_PER_100KM,
    "tire_size": TIRE_SIZE,
    "tire_pressure_front": TIRE_PRESSURE_FRONT,
    "tire_pressure_rear": TIRE_PRESSURE_REAR,
}
VEHICLE_PROFILE_FILE = DRIFTER_DIR / "vehicle.yaml"
VIN_DETECT_RETRIES = 3
VIN_DETECT_TIMEOUT = 2.0
VIN_OBD_MODE = 0x09
VIN_OBD_PID = 0x02
VISION_CLASSES_OF_INTEREST = (
    "person", "bicycle", "car", "motorcycle", "bus", "truck",
    "traffic light", "stop sign",
)
VISION_CONFIDENCE = 0.35
VISION_INPUT_H = 640
VISION_INPUT_W = 640
VISION_MODEL_DIR = DRIFTER_DIR / "vision-models"
VISION_YOLO_MODEL = "yolov8s.hef"
VIVI2_PERSONALITY_FILE = DRIFTER_DIR / "vivi_personality.txt"
VIVI2_PROACTIVE_COOLDOWN_S = 120
VIVI2_STREAMING = True
WEATHER_API_HOST = "api.open-meteo.com"   # legacy: driver_assist fallback fetch

# ═══════════════════════════════════════════════════════════════════
#  External enrichment services — Weather + Location
#  Keys come from api_keys.py (re-exported at the top of this file).
#  weather_service.py and location_service.py are the ONLY modules that
#  call these APIs; everyone else consumes the drifter/weather/* and
#  drifter/location/* MQTT topics, so the real-time/safety path never
#  blocks on the network.
# ═══════════════════════════════════════════════════════════════════

# Fallback position when no GPS fix is available yet. Bendigo, VIC — matches
# the emergency-audio band defaults. Override per operating area.
DEFAULT_LAT = float(os.getenv("DRIFTER_DEFAULT_LAT", "-36.7570"))
DEFAULT_LON = float(os.getenv("DRIFTER_DEFAULT_LON", "144.2794"))

# ── OpenWeatherMap (weather_service.py) ──
OWM_BASE_URL = "https://api.openweathermap.org/data/3.0/onecall"
OWM_FALLBACK_CURRENT_URL = "https://api.openweathermap.org/data/2.5/weather"
OWM_FALLBACK_FORECAST_URL = "https://api.openweathermap.org/data/2.5/forecast"
OWM_UNITS = "metric"                      # °C, m/s, etc.
WEATHER_UPDATE_INTERVAL_SEC = int(os.getenv("WEATHER_UPDATE_INTERVAL_SEC", "900"))  # 15 min
WEATHER_HTTP_TIMEOUT = 10
WEATHER_FOG_VISIBILITY_M = 1000           # below this = fog advisory
WEATHER_ICE_TEMP_C = 3.0                  # at/below this + moisture = ice risk
WEATHER_HIGH_WIND_KPH = 60                # gusty-crosswind advisory
WEATHER_RAIN_SOON_MIN = 30               # "rain within N min" → windows-up nudge

# ── Google Elevation + Places (location_service.py) ──
GOOGLE_ELEVATION_URL = "https://maps.googleapis.com/maps/api/elevation/json"
GOOGLE_PLACES_NEARBY_URL = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
LOCATION_HTTP_TIMEOUT = 10
LOCATION_ELEVATION_INTERVAL_SEC = int(os.getenv("LOCATION_ELEVATION_INTERVAL_SEC", "30"))
LOCATION_ELEVATION_MIN_MOVE_M = 25        # only re-sample grade after moving this far
LOCATION_NEARBY_INTERVAL_SEC = int(os.getenv("LOCATION_NEARBY_INTERVAL_SEC", "300"))  # 5 min
LOCATION_NEARBY_MIN_MOVE_M = 500          # re-poll POIs after moving this far
LOCATION_POI_RADIUS_M = 5000
LOCATION_GRADE_STEEP_PCT = 8.0            # |grade| above this = steep-grade warning
# POI categories the location service keeps warm for Vivi. Keys are the
# spoken-friendly aliases Vivi resolves; values are Google Places types.
LOCATION_POI_TYPES = {
    'fuel': 'gas_station',
    'petrol': 'gas_station',
    'mechanic': 'car_repair',
    'car_wash': 'car_wash',
    'parking': 'parking',
    'charging': 'electric_vehicle_charging_station',
    'rest_stop': 'rest_stop',
    'hospital': 'hospital',
}
# Categories proactively refreshed each poll (the rest are on-demand via
# the location_query topic).
LOCATION_POI_DEFAULT_TYPES = ('gas_station', 'car_repair')

# ═══════════════════════════════════════════════════════════════════
#  In-car 3.5" SPI LCD dashboard (lcd_dashboard.py / drifter-lcd)
#  Framebuffer-rendered triage console so the operator can see node
#  state at the car without an HDMI monitor. Runs directly on /dev/fb1
#  in CLI mode — NO X11/desktop. Distinct from drifter-fbmirror, which
#  mirrors fb0→fb1; this OWNS fb1 with its own menu UI.
# ═══════════════════════════════════════════════════════════════════
LCD_ENABLED = os.getenv("LCD_ENABLED", "true").lower() in ("1", "true", "yes")
LCD_FB_DEVICE = os.getenv("LCD_FB_DEVICE", "/dev/fb1")   # fb0 is HDMI; SPI LCD = fb1
LCD_WIDTH = int(os.getenv("LCD_WIDTH", "480"))           # Waveshare 3.5" landscape
LCD_HEIGHT = int(os.getenv("LCD_HEIGHT", "320"))
# Software rotation applied to the rendered frame (0/90/180/270). Most SPI
# panels are wired so the dtoverlay already rotates; leave at 0 and use the
# overlay's rotate= unless the image lands sideways.
LCD_ROTATE = int(os.getenv("LCD_ROTATE", "0"))
LCD_REFRESH_HZ = float(os.getenv("LCD_REFRESH_HZ", "1.0"))  # status screens are slow-moving
LCD_VEHICLE_REFRESH_HZ = float(os.getenv("LCD_VEHICLE_REFRESH_HZ", "4.0"))  # gauges want faster

# Navigation buttons — active-low, internal pull-up (BCM numbering).
# NOTE: LCD_BTN_PREV defaults to GPIO 17, which is ALSO PTT_GPIO_PIN used by
# voice_input.py. Reading the same pin from two processes (both PUD_UP,
# input-only) is electrically fine, but if you wire a dedicated PTT button
# you MUST move one of them. Override via env on the drifter-lcd unit.
LCD_BTN_PREV = int(os.getenv("LCD_BTN_PREV", "17"))    # previous screen
LCD_BTN_NEXT = int(os.getenv("LCD_BTN_NEXT", "27"))    # next screen
LCD_BTN_ACTION = int(os.getenv("LCD_BTN_ACTION", "22"))  # action / refresh
LCD_BTN_DEBOUNCE_MS = int(os.getenv("LCD_BTN_DEBOUNCE_MS", "200"))

# Screen order. 'vehicle' only renders meaningful data when OBD is connected;
# it stays in the rotation regardless so the operator can confirm "No OBD".
LCD_SCREENS = ("status", "services", "network", "diagnostics", "vehicle")

# Dark "car dashboard" theme — high-contrast monospace, RGB tuples.
LCD_THEME = {
    'bg':       (8, 10, 14),       # near-black
    'panel':    (18, 22, 30),      # slightly lifted panel
    'fg':       (210, 220, 230),   # default text
    'dim':      (120, 130, 140),   # secondary text
    'ok':       (60, 220, 130),    # green
    'warn':     (240, 190, 60),    # amber
    'crit':     (235, 70, 70),     # red
    'accent':   (80, 180, 240),    # MZ1312 cyan accent
    'header_bg': (16, 28, 40),
}
LCD_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/opt/drifter/fonts/DejaVuSansMono.ttf",
)
# journalctl tail depth for the diagnostics screen.
LCD_DIAG_LOG_LINES = int(os.getenv("LCD_DIAG_LOG_LINES", "10"))

# ═══════════════════════════════════════════════════════════════════
#  Wi-Fi hotspot auto-connect (auto_connect.py / drifter-autoconnect)
#  Boots looking for the operator's phone hotspot; if none appears it
#  falls back to bringing up the node's own MZ1312_DRIFTER AP so the
#  operator can always SSH in to fix things.
# ═══════════════════════════════════════════════════════════════════
# Known client SSIDs to join, in priority order. The phone hotspot SSID/PSK
# default to the operator's "Drifter" phone hotspot so the node auto-joins it
# out of the box; both still honour an env override for a different phone.
PHONE_HOTSPOT_SSID = os.getenv("PHONE_HOTSPOT_SSID", "Drifter")
PHONE_HOTSPOT_PSK = os.getenv("PHONE_HOTSPOT_PSK", "54232105")
AUTOCONNECT_KNOWN_SSIDS = [
    s.strip() for s in os.getenv(
        "AUTOCONNECT_KNOWN_SSIDS",
        PHONE_HOTSPOT_SSID,
    ).split(",") if s.strip()
]
AUTOCONNECT_RETRY_SEC = int(os.getenv("AUTOCONNECT_RETRY_SEC", "30"))
AUTOCONNECT_SCAN_TIMEOUT = int(os.getenv("AUTOCONNECT_SCAN_TIMEOUT", "15"))
# After this long with no known SSID joined, bring up our own AP so the
# operator can SSH in. 0 disables the fallback (stay a pure client).
AUTOCONNECT_AP_FALLBACK_SEC = int(os.getenv("AUTOCONNECT_AP_FALLBACK_SEC", "300"))
# The NetworkManager connection name of our own hotspot (install.sh creates it).
AP_FALLBACK_CONNECTION = os.getenv("AP_FALLBACK_CONNECTION", "MZ1312_DRIFTER")
AUTOCONNECT_WIFI_IFACE = os.getenv("AUTOCONNECT_WIFI_IFACE", "wlan0")
# Internet reachability probe (used by auto_connect + the LCD network screen).
PING_HOST = os.getenv("PING_HOST", "8.8.8.8")
PING_TIMEOUT_SEC = int(os.getenv("PING_TIMEOUT_SEC", "3"))

# ═══════════════════════════════════════════════════════════════════
#  Boot sequencer (boot_manager.py / drifter-boot-manager)
#  One-shot orchestrator that paints the LCD splash, brings the network
#  up, confirms the broker, then hands the LCD over to lcd_dashboard.
# ═══════════════════════════════════════════════════════════════════
BOOT_MQTT_WAIT_SEC = int(os.getenv("BOOT_MQTT_WAIT_SEC", "30"))
BOOT_NETWORK_WAIT_SEC = int(os.getenv("BOOT_NETWORK_WAIT_SEC", "45"))
# Core services the boot manager waits on (in dependency order) before it
# declares the node ready and switches the LCD to the live dashboard. These
# are a subset of SERVICES — the safety-critical / always-on spine.
BOOT_CORE_SERVICES = [
    "drifter-dashboard",   # /healthz + cockpit must be up
    "drifter-canbridge",   # telemetry source (hw-optional)
    "drifter-logger",
    "drifter-watchdog",
]

#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Central Configuration
All shared constants, thresholds, and paths in one place.
UNCAGED TECHNOLOGY — EST 1991
"""

import os
import json
import logging
from pathlib import Path

import paho.mqtt.client as _mqtt

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


def load_settings() -> dict:
    """Load user settings from settings.json, merging with defaults."""
    settings = dict(SETTINGS_DEFAULTS)
    try:
        if SETTINGS_FILE.exists():
            with open(SETTINGS_FILE, 'r') as f:
                saved = json.load(f)
            settings.update(saved)
    except Exception as e:
        _log.warning(f"Failed to load settings: {e}")
    return settings


def save_settings(settings: dict) -> bool:
    """Persist user settings to settings.json. Returns True on success."""
    try:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(settings, f, indent=2)
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

# ═══════════════════════════════════════════════════════════════════
#  Vehicle: 2004 Jaguar X-Type 2.5L V6 (AJ-V6 / Duratec)
# ═══════════════════════════════════════════════════════════════════
VEHICLE = "2004 Jaguar X-Type 2.5L V6"
VEHICLE_YEAR = 2004
VEHICLE_MODEL = "X-Type"
VEHICLE_ENGINE = "2.5 V6"

# Engine — Ford/Jaguar AJ-V6 (Duratec-derived)
ENGINE_CODE = "AJ-V6"
DISPLACEMENT_CC = 2495
BORE_MM = 82.4
STROKE_MM = 79.5
COMPRESSION_RATIO = 10.0
PEAK_HP = 194          # bhp @ 6800 RPM
PEAK_TORQUE_NM = 245   # Nm @ 3500 RPM
FIRING_ORDER = [1, 4, 2, 5, 3, 6]
CYLINDER_COUNT = 6
COIL_TYPE = "COP"      # Coil-on-plug, 6 individual coils

# RPM
REDLINE_RPM = 6500
IDLE_RPM_MAX = 1000
IDLE_RPM_WARM_LOW = 650    # Normal warm idle floor
IDLE_RPM_WARM_HIGH = 780   # Normal warm idle ceiling
FAST_IDLE_COLD_MAX = 1400  # Cold-start fast idle ceiling

# Thermostat — plastic housing behind timing cover (known failure)
THERMOSTAT_OPEN_C = 88      # Starts opening
THERMOSTAT_FULL_C = 97      # Fully open
COOLANT_NORMAL_LOW = 86     # Normal operating range low
COOLANT_NORMAL_HIGH = 98    # Normal operating range high

# Warmup — suppress lean alerts during cold start
WARMUP_COOLANT_THRESHOLD = 60   # °C — below this, STFT lean is expected
WARMUP_TIME_MAX = 600           # 10 min — if not at 80°C by then, thermostat issue
WARMUP_COOLANT_TARGET = 80      # °C — should reach this within WARMUP_TIME_MAX

# MAF — expected ranges for the AJ-V6
MAF_IDLE_MIN = 2.5     # g/s — below this at warm idle = dirty/failing MAF
MAF_IDLE_MAX = 6.0     # g/s — above this at idle = implausible
MAF_CRUISE_MIN = 8.0   # g/s — cruising 60-70 km/h typical minimum

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
XTYPE_DTC_LOOKUP = {
    # Fuel system
    'P0171': {
        'desc': 'System Too Lean — Bank 1',
        'cause': 'Intake manifold gasket leak, cracked PCV valve diaphragm, '
                 'dirty MAF sensor, or vacuum hose off the brake booster.',
        'action': 'Smoke test intake, clean MAF with electronics cleaner, '
                  'check PCV valve on top of valve cover Bank 1.',
        'severity': 'AMBER',
    },
    'P0174': {
        'desc': 'System Too Lean — Bank 2',
        'cause': 'Same as P0171 but Bank 2 side. On the X-Type the Bank 2 '
                 'intake runner seals are harder to access.',
        'action': 'Smoke test intake. If both P0171+P0174 appear together, '
                  'suspect the upper intake plenum gasket or large shared vacuum leak.',
        'severity': 'AMBER',
    },
    'P0172': {
        'desc': 'System Too Rich — Bank 1',
        'cause': 'Leaking fuel injector, stuck-open purge valve (common on X-Type), '
                 'or failing upstream O2 sensor Bank 1.',
        'action': 'Check purge valve on firewall side. Pull injector rail and '
                  'look for drippers. Test O2 sensor heater resistance.',
        'severity': 'AMBER',
    },
    'P0175': {
        'desc': 'System Too Rich — Bank 2',
        'cause': 'Same as P0172 but Bank 2. Also check for coolant leaking '
                 'into cylinder (head gasket weep) on the rear bank.',
        'action': 'Inspect spark plugs for fouling. White/steam residue = coolant leak.',
        'severity': 'AMBER',
    },

    # Misfires — often coil packs on this engine
    'P0300': {
        'desc': 'Random/Multiple Cylinder Misfire',
        'cause': 'On the AJ-V6 this is usually failing coil packs (COP), worn plugs, '
                 'or a vacuum leak affecting multiple cylinders.',
        'action': 'Swap coil packs between cylinders and see if misfire follows. '
                  'Replace all 6 plugs if over 30k miles. Check for vacuum leaks.',
        'severity': 'AMBER',
    },
    'P0301': {
        'desc': 'Cylinder 1 Misfire',
        'cause': 'Coil pack failure is the #1 cause on X-Type. Cylinder 1 is '
                 'front-left (Bank 1, nearest radiator).',
        'action': 'Swap coil from Cyl 1 to Cyl 4. If misfire moves → replace coil. '
                  'If not → check plug, compression, injector.',
        'severity': 'AMBER',
    },
    'P0302': {
        'desc': 'Cylinder 2 Misfire',
        'cause': 'Coil pack or spark plug. Cyl 2 is mid Bank 1.',
        'action': 'Swap coil to another cylinder and retest.',
        'severity': 'AMBER',
    },
    'P0303': {
        'desc': 'Cylinder 3 Misfire',
        'cause': 'Coil pack or spark plug. Cyl 3 is rear Bank 1.',
        'action': 'Swap coil to another cylinder and retest.',
        'severity': 'AMBER',
    },
    'P0304': {
        'desc': 'Cylinder 4 Misfire',
        'cause': 'Coil pack or spark plug. Cyl 4 is front Bank 2 (nearest alternator).',
        'action': 'Swap coil to another cylinder and retest.',
        'severity': 'AMBER',
    },
    'P0305': {
        'desc': 'Cylinder 5 Misfire',
        'cause': 'Coil pack or spark plug. Cyl 5 is mid Bank 2.',
        'action': 'Swap coil to another cylinder and retest.',
        'severity': 'AMBER',
    },
    'P0306': {
        'desc': 'Cylinder 6 Misfire',
        'cause': 'Coil pack or spark plug. Cyl 6 is rear Bank 2 (hardest to access).',
        'action': 'Swap coil to another cylinder and retest.',
        'severity': 'AMBER',
    },

    # Sensors
    'P0340': {
        'desc': 'Camshaft Position Sensor A — Bank 1',
        'cause': 'CMP sensor failure or wiring corrosion. Common on ageing X-Types. '
                 'Located behind the timing cover on Bank 1 side.',
        'action': 'Replace CMP sensor (cheap part). Check connector for green corrosion.',
        'severity': 'RED',
    },
    'P0345': {
        'desc': 'Camshaft Position Sensor A — Bank 2',
        'cause': 'Same as P0340 but Bank 2 side.',
        'action': 'Replace CMP sensor Bank 2. Check for coolant contamination '
                  'from thermostat housing leak (they are close together).',
        'severity': 'RED',
    },

    # Catalyst
    'P0420': {
        'desc': 'Catalyst Efficiency Below Threshold — Bank 1',
        'cause': 'Catalytic converter degraded, or downstream O2 sensor lazy. '
                 'UK MOT relevant. On X-Type often caused by prolonged rich running '
                 'from a bad coil pack fouling the cat.',
        'action': 'Fix any upstream fuel trim or misfire codes FIRST. '
                  'Then clear and retest. If cat is truly dead, budget $400-800 for replacement.',
        'severity': 'AMBER',
    },
    'P0430': {
        'desc': 'Catalyst Efficiency Below Threshold — Bank 2',
        'cause': 'Same as P0420 but Bank 2. The X-Type has 2 pre-cats and 1 main cat.',
        'action': 'Same approach — fix fuel/ignition first, then re-evaluate cat health.',
        'severity': 'AMBER',
    },

    # EGR / Purge
    'P0401': {
        'desc': 'EGR Flow Insufficient',
        'cause': 'Carbon buildup in EGR passages (very common on X-Type). '
                 'EGR valve sticking or vacuum actuator leaking.',
        'action': 'Remove and clean EGR valve. Clean EGR passages with carb cleaner. '
                  'Check vacuum hoses to EGR actuator.',
        'severity': 'AMBER',
    },
    'P0443': {
        'desc': 'EVAP Purge Control Valve Circuit',
        'cause': 'Purge valve solenoid failed or wiring fault. Located on the '
                 'firewall side of the engine bay.',
        'action': 'Test purge valve with 12V — should click. Replace if stuck open '
                  '(causes rich condition) or stuck closed (fuel tank pressure).',
        'severity': 'AMBER',
    },

    # Idle / Throttle — X-Type uses electronic throttle body (drive-by-wire)
    'P0507': {
        'desc': 'Idle Air Control RPM Higher Than Expected',
        'cause': 'Vacuum leak, dirty throttle body, or sticking IAC. On X-Type '
                 'the electronic throttle body gets carbon buildup inside.',
        'action': 'Clean throttle body with carb cleaner (remove to clean properly). '
                  'Then do idle relearn: key on 30s, start, idle 2 min, drive 10 min.',
        'severity': 'AMBER',
    },
    'P1000': {
        'desc': 'OBD-II System Readiness Not Complete',
        'cause': 'Not a fault — monitors have not run since last battery disconnect '
                 'or code clear. Normal after work.',
        'action': 'Drive a mixed cycle: cold start, idle 2 min, accelerate to 60 mph, '
                  'cruise 5 min, decelerate with foot off gas. Monitors will complete.',
        'severity': 'INFO',
    },

    # O2 Sensors
    'P0131': {
        'desc': 'O2 Sensor Low Voltage — Bank 1 Sensor 1 (upstream)',
        'cause': 'Upstream O2 sensor degraded or exhaust leak before sensor. '
                 'On X-Type, check the flex joint near the manifold for cracks.',
        'action': 'Check exhaust for leaks at manifold-to-flex joint. '
                  'Test O2 heater fuse. Replace sensor if >80k miles.',
        'severity': 'AMBER',
    },
    'P1131': {
        'desc': 'O2 Sensor Lack of Switching — Bank 1',
        'cause': 'Upstream O2 sensor stuck lean. Common on ageing X-Types. '
                 'Can also be triggered by persistent vacuum leak.',
        'action': 'Fix vacuum leaks first. If still present, replace Bank 1 sensor 1.',
        'severity': 'AMBER',
    },
    'P1151': {
        'desc': 'O2 Sensor Lack of Switching — Bank 2',
        'cause': 'Same as P1131 but Bank 2 side.',
        'action': 'Fix vacuum leaks first, then replace Bank 2 sensor 1 if needed.',
        'severity': 'AMBER',
    },

    # Fuel pump
    'P1235': {
        'desc': 'Fuel Pump Control Out of Range',
        'cause': 'Fuel pump relay failing, wiring fault, or fuel pump itself wearing out. '
                 'On X-Type the pump is in the tank, access via rear seat.',
        'action': 'Check fuel pump relay in engine bay fuse box first (swap with identical relay). '
                  'Listen for pump prime when turning key to ON. '
                  'Check fuel pressure at rail (spec: 3.0-3.5 bar).',
        'severity': 'RED',
    },

    # Throttle body (drive-by-wire specific to X-Type)
    'P1518': {
        'desc': 'Intake Manifold Runner Control Stuck Open',
        'cause': 'IMRC actuator failure or vacuum leak to the runner control. '
                 'Affects power band above 3500 RPM.',
        'action': 'Check vacuum hose to IMRC actuator. Test actuator with vacuum pump. '
                  'Common on high-mileage X-Types.',
        'severity': 'AMBER',
    },
    'P2106': {
        'desc': 'Throttle Actuator — Forced Limited Power',
        'cause': 'PCM has put the engine in limp mode. Usually triggered by another '
                 'fault code. The X-Type throttle body motor can fail internally.',
        'action': 'LIMP MODE. Read ALL codes — fix the root cause. '
                  'If throttle body related, try cleaning first before replacing ($250+ part).',
        'severity': 'RED',
    },
    'P2111': {
        'desc': 'Throttle Actuator Stuck Open',
        'cause': 'Throttle plate sticking or motor failure. Carbon buildup is '
                 'the usual cause on X-Type.',
        'action': 'Remove and clean throttle body. Check for scored bore. '
                  'After refitting: idle relearn procedure required.',
        'severity': 'RED',
    },
    'P2112': {
        'desc': 'Throttle Actuator Stuck Closed',
        'cause': 'Throttle plate jammed shut. Will cause loss of power or no-start.',
        'action': 'Emergency: key off/on may reset. Clean or replace throttle body. '
                  'Do NOT force the plate open with tools.',
        'severity': 'RED',
    },
    'P2135': {
        'desc': 'Throttle Position Sensor Correlation',
        'cause': 'The two TPS signals inside the throttle body disagree. '
                 'Wiring fault or internal throttle body failure.',
        'action': 'Check TPS connector for corrosion. Wiggle-test wiring. '
                  'If intermittent, throttle body replacement likely needed.',
        'severity': 'RED',
    },

    # Communication
    'U0100': {
        'desc': 'Lost Communication with ECM/PCM',
        'cause': 'CAN bus wiring fault, ECM power supply issue, or ECM failure. '
                 'On X-Type, check the main engine fuse box and ECM connector.',
        'action': 'Check battery voltage. Inspect ECM connector (behind glovebox). '
                  'Check CAN bus termination with multimeter (should be ~60 ohms).',
        'severity': 'RED',
    },
    'U0121': {
        'desc': 'Lost Communication with ABS Module',
        'cause': 'ABS module failure or CAN bus fault. The X-Type ABS module '
                 'is known to fail internally (common issue).',
        'action': 'Check ABS fuse first. If module dead, specialist rebuild '
                  '(BBA Reman, ECU Testing) typically $250-400.',
        'severity': 'AMBER',
    },
}

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
DUMP1090_BIN = 'dump1090'       # or full path if needed

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
    'timing': 'drifter/engine/timing',
    'o2_b1s1': 'drifter/engine/o2_b1s1',
    'o2_b2s1': 'drifter/engine/o2_b2s1',
    'run_time': 'drifter/engine/run_time',
    'baro': 'drifter/engine/baro',
    'fuel_lvl': 'drifter/vehicle/fuel_lvl',
    'alert_level': 'drifter/alert/level',
    'alert_message': 'drifter/alert/message',
    'snapshot': 'drifter/snapshot',
    'system_status': 'drifter/system/status',
    'dtc': 'drifter/diag/dtc',
    'calibration': 'drifter/diag/calibration',
    'watchdog': 'drifter/system/watchdog',
    'drive_session': 'drifter/session',
    # RF / TPMS
    'tpms_fl': 'drifter/rf/tpms/fl',
    'tpms_fr': 'drifter/rf/tpms/fr',
    'tpms_rl': 'drifter/rf/tpms/rl',
    'tpms_rr': 'drifter/rf/tpms/rr',
    'tpms_snapshot': 'drifter/rf/tpms/snapshot',
    'rf_signal': 'drifter/rf/signals',
    'rf_spectrum': 'drifter/rf/spectrum',
    'rf_emergency': 'drifter/rf/emergency',
    'rf_status': 'drifter/rf/status',
    'rf_command': 'drifter/rf/command',
    'rf_adsb': 'drifter/rf/adsb',
    # Wardrive
    'wardrive_wifi': 'drifter/wardrive/wifi',
    'wardrive_bt': 'drifter/wardrive/bt',
    'wardrive_status': 'drifter/wardrive/status',
    'wardrive_snapshot': 'drifter/wardrive/snapshot',
    # Analyst
    'analysis_report': 'drifter/analysis/report',
    'analysis_request': 'drifter/analysis/request',
    'anomaly_event': 'drifter/anomaly/event',
    # Voice Input
    'voice_transcript': 'drifter/voice/transcript',
    'voice_command': 'drifter/voice/command',
    'voice_status':  'drifter/voice/status',
    'hud_navigate':  'drifter/hud/navigate',
    # Vivi voice assistant
    'vivi_query': 'drifter/vivi/query',
    'vivi_response': 'drifter/vivi/response',
    'vivi_status': 'drifter/vivi/status',
    # Audio (shared with voice_alerts)
    'audio_wav': 'drifter/audio/wav',
    # Flipper Zero
    'flipper_status': 'drifter/flipper/status',
    'flipper_command': 'drifter/flipper/command',
    'flipper_result': 'drifter/flipper/result',
    'flipper_subghz': 'drifter/flipper/subghz',
    # Tool Executor
    'tool_request': 'drifter/tool/request',
    'tool_result': 'drifter/tool/result',
}

# ── LLM Analyst ──
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = "claude-sonnet-4-6"

# ── LLM Backend (Ollama — local, offline) ──
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "localhost")
OLLAMA_PORT = int(os.getenv("OLLAMA_PORT", "11434"))
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:1.5b")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "300"))
OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "30m")
LLM_PRIMARY = os.getenv("LLM_PRIMARY", "ollama")  # "ollama" = offline-first, "groq" = cloud-first

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

# ── Storage ──
DB_PATH = DRIFTER_DIR / "data" / "drifter.db"
REPORTS_DIR = DRIFTER_DIR / "reports"
ANALYST_BASELINE_SESSIONS = 10

# ── Services ──
# Canonical list of 18 active systemd services.
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
]
FOOT_ONLY_SERVICES = [
    "drifter-wardrive",    # active Wi-Fi/BT recon
    "drifter-flipper",     # Flipper Zero CLI bridge
    "drifter-opsec",       # OPSEC dashboard on :8090 (Kali aesthetic)
]
SHARED_SERVICES = [
    "drifter-dashboard",   # operator HUD (always-on so /healthz stays reachable)
    "drifter-hotspot",     # Wi-Fi AP — phone tethers in either mode
    "drifter-homesync",    # rsync to home node when reachable
    "drifter-watchdog",    # service health monitor
    "drifter-logger",      # telemetry log writer
    "drifter-vivi",        # voice assistant LLM brain
    "drifter-voicein",     # wake-word + STT
]
MODES = {
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

# Persistent mode marker — read by the dashboard and CLI to render which
# persona is currently armed. Updated by `drifter mode <name>`.
MODE_STATE_PATH = DRIFTER_DIR / "mode.state"
DEFAULT_MODE = "drive"

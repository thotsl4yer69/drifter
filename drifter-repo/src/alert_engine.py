#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Alert Engine
Deterministic diagnostic rules for 2004 Jaguar X-Type 2.5L V6.
No LLM needed. If/else runs in microseconds.
UNCAGED TECHNOLOGY — EST 1991
"""

import json
import time
import signal
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Optional
import paho.mqtt.client as mqtt

from config import (
    MQTT_HOST, MQTT_PORT, CALIBRATION_FILE,
    LEVEL_OK, LEVEL_INFO, LEVEL_AMBER, LEVEL_RED, LEVEL_NAMES,
    THRESHOLDS, CALIBRATION_DEFAULTS, TOPICS
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [ALERTS] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

# ── Calibration Baselines (loaded in main(), used by rules) ──
calibration = dict(CALIBRATION_DEFAULTS)

# ── Rolling Buffer (60 seconds of data at ~10Hz) ──
BUFFER_SIZE = 600

@dataclass
class VehicleState:
    """Rolling buffer of vehicle telemetry."""
    rpm: deque = field(default_factory=lambda: deque(maxlen=BUFFER_SIZE))
    coolant: deque = field(default_factory=lambda: deque(maxlen=BUFFER_SIZE))
    stft1: deque = field(default_factory=lambda: deque(maxlen=BUFFER_SIZE))
    stft2: deque = field(default_factory=lambda: deque(maxlen=BUFFER_SIZE))
    ltft1: deque = field(default_factory=lambda: deque(maxlen=BUFFER_SIZE))
    ltft2: deque = field(default_factory=lambda: deque(maxlen=BUFFER_SIZE))
    load: deque = field(default_factory=lambda: deque(maxlen=BUFFER_SIZE))
    speed: deque = field(default_factory=lambda: deque(maxlen=BUFFER_SIZE))
    throttle: deque = field(default_factory=lambda: deque(maxlen=BUFFER_SIZE))
    voltage: deque = field(default_factory=lambda: deque(maxlen=BUFFER_SIZE))
    iat: deque = field(default_factory=lambda: deque(maxlen=BUFFER_SIZE))
    maf: deque = field(default_factory=lambda: deque(maxlen=BUFFER_SIZE))
    timestamps: deque = field(default_factory=lambda: deque(maxlen=BUFFER_SIZE))
    active_dtcs: list = field(default_factory=list)
    pending_dtcs: list = field(default_factory=list)
    # TPMS: {pos: {pressure_psi, temp_c, ts}}
    tpms: dict = field(default_factory=dict)
    tpms_history: dict = field(default_factory=lambda: {
        'fl': deque(maxlen=60), 'fr': deque(maxlen=60),
        'rl': deque(maxlen=60), 'rr': deque(maxlen=60),
    })

    def avg(self, buf, n=50):
        """Average of last n readings."""
        if not buf:
            return None
        samples = list(buf)[-n:]
        return sum(samples) / len(samples)

    def latest(self, buf):
        """Most recent reading."""
        return buf[-1] if buf else None

    def trend(self, buf, window=100):
        """Rate of change per second over window."""
        if len(buf) < 10 or len(self.timestamps) < 10:
            return 0
        samples = list(buf)[-window:]
        times = list(self.timestamps)[-window:]
        if times[-1] == times[0]:
            return 0
        return (samples[-1] - samples[0]) / (times[-1] - times[0])

    def sustained_above(self, buf, threshold, min_samples=50):
        """Check if value has been above threshold for min_samples readings."""
        if len(buf) < min_samples:
            return False
        return all(v > threshold for v in list(buf)[-min_samples:])

    def sustained_below(self, buf, threshold, min_samples=50):
        """Check if value has been below threshold for min_samples readings."""
        if len(buf) < min_samples:
            return False
        return all(v < threshold for v in list(buf)[-min_samples:])


# ── Diagnostic Rules ──
# Each rule returns (level, message) or None

def rule_vacuum_leak_bank1(state: VehicleState):
    """Bank 1 lean at idle = physical vacuum leak on Bank 1 side."""
    stft1 = state.avg(state.stft1, 30)
    stft2 = state.avg(state.stft2, 30)
    rpm = state.avg(state.rpm, 30)

    if stft1 is None or stft2 is None or rpm is None:
        return None

    # Subtract baseline so calibrated engines don't false-trigger
    adj1 = stft1 - calibration.get('stft1_baseline', 0)
    adj2 = stft2 - calibration.get('stft2_baseline', 0)

    if adj1 > THRESHOLDS['stft_lean_idle'] and rpm < THRESHOLDS['idle_rpm_ceiling'] and adj2 < 5:
        return (LEVEL_AMBER,
                f"Vacuum leak — Bank 1 lean at idle (STFT1: {stft1:+.1f}%, "
                f"STFT2: {stft2:+.1f}%). Check brake booster valve, PCV hose, "
                f"intake gaskets on Bank 1 side.")
    return None


def rule_vacuum_leak_both(state: VehicleState):
    """Both banks lean at idle = shared vacuum leak."""
    stft1 = state.avg(state.stft1, 30)
    stft2 = state.avg(state.stft2, 30)
    rpm = state.avg(state.rpm, 30)

    if stft1 is None or stft2 is None or rpm is None:
        return None

    adj1 = stft1 - calibration.get('stft1_baseline', 0)
    adj2 = stft2 - calibration.get('stft2_baseline', 0)

    if adj1 > THRESHOLDS['stft_lean_idle'] and adj2 > THRESHOLDS['stft_lean_idle'] and rpm < THRESHOLDS['idle_rpm_ceiling']:
        return (LEVEL_AMBER,
                f"Vacuum leak — BOTH banks lean at idle (B1: {stft1:+.1f}%, "
                f"B2: {stft2:+.1f}%). Check intake plenum gaskets or large "
                f"shared vacuum line.")
    return None


def rule_coolant_critical(state: VehicleState):
    """Coolant temperature critical."""
    coolant = state.latest(state.coolant)
    trend = state.trend(state.coolant)

    if coolant is None:
        return None

    if coolant >= THRESHOLDS['coolant_red']:
        return (LEVEL_RED,
                f"COOLANT CRITICAL: {coolant}°C. Pull over when safe. "
                f"Check thermostat, fan relay, coolant level.")

    if coolant >= THRESHOLDS['coolant_amber']:
        return (LEVEL_AMBER,
                f"Coolant high: {coolant}°C. Monitor closely. "
                f"Check thermostat, fan relay, coolant level.")

    if coolant > 100 and trend > THRESHOLDS['coolant_rise_rate']:
        return (LEVEL_AMBER,
                f"Coolant rising fast: {coolant}°C (+{trend:.1f}°C/min). "
                f"Monitor closely. May indicate thermostat sticking or fan failure.")
    return None


def rule_running_rich(state: VehicleState):
    """Sustained rich condition = leaking injector or purge valve."""
    stft1 = state.avg(state.stft1, 30)
    stft2 = state.avg(state.stft2, 30)

    if stft1 is None or stft2 is None:
        return None

    # Offset by baseline before checking sustained threshold
    b1 = calibration.get('stft1_baseline', 0)
    b2 = calibration.get('stft2_baseline', 0)
    rich_threshold = THRESHOLDS['stft_rich_sustained']

    if state.sustained_below(state.stft1, rich_threshold + b1, 150) or \
       state.sustained_below(state.stft2, rich_threshold + b2, 150):
        bank = "Bank 1" if (stft1 or 0) < (stft2 or 0) else "Bank 2"
        return (LEVEL_AMBER,
                f"Running rich on {bank} (STFT: {min(stft1, stft2):+.1f}%). "
                f"Possible leaking injector, stuck purge valve, or faulty O2 sensor.")
    return None


def rule_alternator(state: VehicleState):
    """Undercharging alternator."""
    voltage = state.avg(state.voltage, 20)
    rpm = state.avg(state.rpm, 20)

    if voltage is None or rpm is None:
        return None

    if voltage < 13.2 and rpm > 1500:
        return (LEVEL_AMBER,
                f"Alternator undercharging: {voltage:.1f}V at {rpm:.0f} RPM. "
                f"Should be 13.5-14.5V. Check belt tension, voltage regulator.")

    if voltage < 12.0:
        return (LEVEL_RED,
                f"BATTERY VOLTAGE CRITICAL: {voltage:.1f}V. "
                f"Alternator may have failed. Electrical systems at risk.")
    return None


def rule_idle_instability(state: VehicleState):
    """Unstable idle RPM."""
    if len(state.rpm) < 100:
        return None

    rpm = state.avg(state.rpm, 50)
    if rpm is None or rpm > 1000:
        return None  # Only care at idle

    # Check RPM variance
    recent = list(state.rpm)[-100:]
    rpm_min = min(recent)
    rpm_max = max(recent)
    spread = rpm_max - rpm_min

    if spread > 200 and rpm < 900:
        return (LEVEL_INFO,
                f"Idle instability: RPM swinging {rpm_min:.0f}-{rpm_max:.0f} "
                f"(±{spread/2:.0f}). May indicate vacuum leak, dirty IAC valve, "
                f"or failing idle air control.")
    return None


def rule_overrev(state: VehicleState):
    """RPM too high warning."""
    rpm = state.latest(state.rpm)
    if rpm is None:
        return None

    if rpm > THRESHOLDS['overrev_rpm']:
        return (LEVEL_RED, f"HIGH RPM WARNING: {rpm:.0f} RPM. Redline risk.")
    return None


def rule_ltft_drift(state: VehicleState):
    """Long-term fuel trim too far from zero = chronic fueling issue."""
    ltft1 = state.avg(state.ltft1, 20)
    ltft2 = state.avg(state.ltft2, 20)

    if ltft1 is None and ltft2 is None:
        return None

    # Offset LTFT by learned baselines so calibrated deviation is removed
    baselines = {
        "Bank 1": (ltft1, calibration.get('ltft1_baseline', 0)),
        "Bank 2": (ltft2, calibration.get('ltft2_baseline', 0)),
    }

    for label, (val, baseline) in baselines.items():
        if val is None:
            continue
        adj = val - baseline
        if abs(adj) >= abs(THRESHOLDS['ltft_lean_crit']):
            return (LEVEL_RED,
                    f"LTFT {label} maxed at {val:+.1f}%. ECU can't compensate. "
                    f"Major fueling fault — likely large vacuum leak or failing injector.")
        if abs(adj) >= abs(THRESHOLDS['ltft_lean_warn']):
            direction = "lean" if adj > 0 else "rich"
            return (LEVEL_AMBER,
                    f"LTFT {label} drifted {direction}: {val:+.1f}%. "
                    f"Chronic fuel trim issue developing. Check O2 sensors, "
                    f"injectors, vacuum lines.")
    return None


def rule_bank_imbalance(state: VehicleState):
    """Large STFT difference between banks = bank-specific issue."""
    stft1 = state.avg(state.stft1, 30)
    stft2 = state.avg(state.stft2, 30)
    rpm = state.avg(state.rpm, 30)

    if stft1 is None or stft2 is None or rpm is None:
        return None

    # Use baseline-adjusted values for imbalance check
    adj1 = stft1 - calibration.get('stft1_baseline', 0)
    adj2 = stft2 - calibration.get('stft2_baseline', 0)
    divergence = abs(adj1 - adj2)

    if divergence > THRESHOLDS['catalyst_stft_divergence'] and rpm < 2000:
        leaner = "Bank 1" if adj1 > adj2 else "Bank 2"
        return (LEVEL_AMBER,
                f"Fuel trim imbalance: {divergence:.1f}% between banks. "
                f"{leaner} running leaner. Check {leaner.lower()} vacuum lines, "
                f"injector balance, or O2 sensor.")
    return None


def rule_intake_temp(state: VehicleState):
    """Intake air temperature too high = heat soak."""
    iat = state.latest(state.iat)
    if iat is None:
        return None

    if iat >= THRESHOLDS['iat_critical']:
        return (LEVEL_AMBER,
                f"Intake air temp CRITICAL: {iat}°C. Severe heat soak — "
                f"power loss likely. Check intake ducting, hood vents.")
    if iat >= THRESHOLDS['iat_high']:
        return (LEVEL_INFO,
                f"Intake air temp elevated: {iat}°C. Under-hood heat soak "
                f"may reduce power. Consider letting engine bay cool.")
    return None


def rule_voltage_overcharge(state: VehicleState):
    """Voltage too high = regulator failure."""
    voltage = state.avg(state.voltage, 20)
    rpm = state.avg(state.rpm, 20)

    if voltage is None or rpm is None:
        return None

    if voltage > THRESHOLDS['voltage_overcharge'] and rpm > 1000:
        return (LEVEL_RED,
                f"OVERCHARGING: {voltage:.1f}V. Voltage regulator failure. "
                f"Risk of battery damage and electrical component failure. "
                f"Drive to a safe stop.")
    return None


def rule_active_dtcs(state: VehicleState):
    """Report active DTCs read from ECU."""
    if not state.active_dtcs and not state.pending_dtcs:
        return None

    if state.active_dtcs:
        codes = ', '.join(state.active_dtcs[:5])
        extra = f" (+{len(state.active_dtcs) - 5} more)" if len(state.active_dtcs) > 5 else ""
        return (LEVEL_AMBER,
                f"Active DTCs: {codes}{extra}. "
                f"ECU has logged fault codes. Check with full scanner for details.")

    if state.pending_dtcs:
        codes = ', '.join(state.pending_dtcs[:3])
        return (LEVEL_INFO,
                f"Pending DTCs: {codes}. "
                f"Intermittent faults detected — may self-clear or escalate.")
    return None


def rule_stalled(state: VehicleState):
    """Engine stall detection (RPM drops to 0 while voltage present)."""
    rpm = state.latest(state.rpm)
    voltage = state.latest(state.voltage)

    if rpm is None or voltage is None:
        return None

    if rpm == 0 and voltage > 10:
        # Check if RPM was recently > 0 (stall vs key-off)
        if len(state.rpm) >= 10:
            recent = list(state.rpm)[-10:]
            if any(r > 200 for r in recent[:-3]):
                return (LEVEL_RED,
                        f"ENGINE STALL DETECTED. RPM dropped to 0. "
                        f"Battery voltage: {voltage:.1f}V. "
                        f"Check for fuel delivery, ignition, or sensor fault.")
    return None


def rule_tpms_low_pressure(state: VehicleState):
    """Tire pressure below safe threshold."""
    if not state.tpms:
        return None

    names = {'fl': 'Front Left', 'fr': 'Front Right', 'rl': 'Rear Left', 'rr': 'Rear Right'}
    now = time.time()

    for pos, data in state.tpms.items():
        if now - data.get('ts', 0) > 1800:  # Stale reading
            continue
        psi = data.get('pressure_psi')
        if psi is None:
            continue

        if psi < THRESHOLDS['tpms_pressure_crit']:
            return (LEVEL_RED,
                    f"TIRE PRESSURE CRITICAL: {names.get(pos, pos)} at {psi:.0f} PSI. "
                    f"Stop and inspect. Risk of tire failure.")
        if psi < THRESHOLDS['tpms_pressure_low']:
            return (LEVEL_AMBER,
                    f"Low tire pressure: {names.get(pos, pos)} at {psi:.0f} PSI "
                    f"(min {THRESHOLDS['tpms_pressure_low']:.0f}). Inflate when possible.")
        if psi > THRESHOLDS['tpms_pressure_high']:
            return (LEVEL_INFO,
                    f"High tire pressure: {names.get(pos, pos)} at {psi:.0f} PSI. "
                    f"May ride harsh and reduce traction.")
    return None


def rule_tpms_rapid_loss(state: VehicleState):
    """Rapid pressure drop = possible puncture."""
    if not state.tpms:
        return None

    names = {'fl': 'Front Left', 'fr': 'Front Right', 'rl': 'Rear Left', 'rr': 'Rear Right'}

    for pos, history in state.tpms_history.items():
        if len(history) < 2:
            continue
        now = time.time()
        recent = [(t, p) for t, p in history if now - t < 300]
        if len(recent) < 2:
            continue
        drop = recent[0][1] - recent[-1][1]
        if drop >= THRESHOLDS['tpms_rapid_loss']:
            return (LEVEL_RED,
                    f"RAPID PRESSURE LOSS: {names.get(pos, pos)} dropped "
                    f"{drop:.1f} PSI in {(recent[-1][0] - recent[0][0])/60:.0f} min. "
                    f"Possible puncture. Pull over when safe.")
    return None


def rule_tpms_temp(state: VehicleState):
    """Tire temperature too high."""
    if not state.tpms:
        return None

    names = {'fl': 'Front Left', 'fr': 'Front Right', 'rl': 'Rear Left', 'rr': 'Rear Right'}
    now = time.time()

    for pos, data in state.tpms.items():
        if now - data.get('ts', 0) > 1800:
            continue
        temp = data.get('temp_c')
        if temp is None:
            continue

        if temp >= THRESHOLDS['tpms_temp_crit']:
            return (LEVEL_RED,
                    f"TIRE TEMP CRITICAL: {names.get(pos, pos)} at {temp:.0f}\u00b0C. "
                    f"Stop immediately. Risk of blowout.")
        if temp >= THRESHOLDS['tpms_temp_warn']:
            return (LEVEL_AMBER,
                    f"Tire temp high: {names.get(pos, pos)} at {temp:.0f}\u00b0C. "
                    f"Reduce speed. Check for dragging brake or low pressure.")
    return None


# ── All Rules ──
ALL_RULES = [
    rule_vacuum_leak_bank1,
    rule_vacuum_leak_both,
    rule_coolant_critical,
    rule_running_rich,
    rule_alternator,
    rule_idle_instability,
    rule_overrev,
    rule_ltft_drift,
    rule_bank_imbalance,
    rule_intake_temp,
    rule_voltage_overcharge,
    rule_active_dtcs,
    rule_stalled,
    rule_tpms_low_pressure,
    rule_tpms_rapid_loss,
    rule_tpms_temp,
]


# ── MQTT Callbacks ──
state = VehicleState()
current_alert_level = LEVEL_OK
current_alert_msg = "Systems nominal"
last_alert_time = 0
ALERT_COOLDOWN = 5  # Don't spam alerts faster than every 5s


def on_message(client, userdata, msg):
    """Ingest telemetry from CAN bridge."""
    global state
    try:
        data = json.loads(msg.payload)
        topic = msg.topic

        # DTC messages have a different structure
        if topic.endswith('/dtc'):
            state.active_dtcs = data.get('stored', [])
            state.pending_dtcs = data.get('pending', [])
            return

        # TPMS tire data
        if '/rf/tpms/' in topic and not topic.endswith('/snapshot'):
            pos = topic.split('/')[-1]  # fl, fr, rl, rr
            if pos in ('fl', 'fr', 'rl', 'rr'):
                state.tpms[pos] = data
                psi = data.get('pressure_psi')
                if psi is not None:
                    state.tpms_history[pos].append((data.get('ts', time.time()), psi))
            return

        value = data.get('value')
        if value is None:
            return
        ts = data.get('ts', time.time())

        if topic.endswith('/rpm'):
            state.rpm.append(value)
        elif topic.endswith('/coolant'):
            state.coolant.append(value)
        elif topic.endswith('/stft1'):
            state.stft1.append(value)
        elif topic.endswith('/stft2'):
            state.stft2.append(value)
        elif topic.endswith('/ltft1'):
            state.ltft1.append(value)
        elif topic.endswith('/ltft2'):
            state.ltft2.append(value)
        elif topic.endswith('/load'):
            state.load.append(value)
        elif topic.endswith('/speed'):
            state.speed.append(value)
        elif topic.endswith('/throttle'):
            state.throttle.append(value)
        elif topic.endswith('/voltage'):
            state.voltage.append(value)
        elif topic.endswith('/iat'):
            state.iat.append(value)
        elif topic.endswith('/maf'):
            state.maf.append(value)

        state.timestamps.append(ts)

    except (json.JSONDecodeError, KeyError, TypeError) as e:
        log.warning(f"Bad message on {msg.topic}: {e}")


def evaluate_rules(mqtt_client):
    """Run all diagnostic rules and publish the highest-priority alert."""
    global current_alert_level, current_alert_msg, last_alert_time

    now = time.time()
    if now - last_alert_time < ALERT_COOLDOWN:
        return

    highest_level = LEVEL_OK
    highest_msg = "Systems nominal"

    for rule in ALL_RULES:
        result = rule(state)
        if result and result[0] > highest_level:
            highest_level = result[0]
            highest_msg = result[1]

    # Only publish if something changed or it's been a while
    if highest_level != current_alert_level or highest_msg != current_alert_msg:
        current_alert_level = highest_level
        current_alert_msg = highest_msg
        last_alert_time = now

        mqtt_client.publish(TOPICS['alert_level'], json.dumps({
            'level': highest_level,
            'name': LEVEL_NAMES[highest_level],
            'ts': now
        }), retain=True)

        mqtt_client.publish(TOPICS['alert_message'], json.dumps({
            'level': highest_level,
            'name': LEVEL_NAMES[highest_level],
            'message': highest_msg,
            'ts': now
        }), retain=True)

        if highest_level >= LEVEL_AMBER:
            log.warning(f"[{LEVEL_NAMES[highest_level]}] {highest_msg}")
        elif highest_level == LEVEL_INFO:
            log.info(f"[INFO] {highest_msg}")


def main():
    log.info("DRIFTER Alert Engine starting...")
    log.info(f"Loaded {len(ALL_RULES)} diagnostic rules for Jaguar X-Type 2.5L V6")

    # Load calibration if available
    try:
        if CALIBRATION_FILE.exists():
            with open(CALIBRATION_FILE) as f:
                cal = json.load(f)
            if cal.get('calibrated'):
                calibration.update(cal)
                log.info(f"Calibration loaded from {cal.get('calibration_date', 'unknown')}")
                log.info(f"  STFT baselines: B1={cal['stft1_baseline']:+.1f}%, B2={cal['stft2_baseline']:+.1f}%")
                log.info(f"  LTFT baselines: B1={cal.get('ltft1_baseline', 0):+.1f}%, B2={cal.get('ltft2_baseline', 0):+.1f}%")
    except Exception as e:
        log.warning(f"Could not load calibration: {e}")

    running = True

    def _handle_signal(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    # ── MQTT ──
    client = mqtt.Client(client_id="drifter-alerts")
    client.on_message = on_message

    connected = False
    while not connected and running:
        try:
            client.connect(MQTT_HOST, MQTT_PORT, 60)
            connected = True
        except Exception as e:
            log.warning(f"Waiting for MQTT broker... ({e})")
            time.sleep(3)

    if not running:
        return

    # Subscribe to all telemetry
    client.subscribe("drifter/engine/#")
    client.subscribe("drifter/vehicle/#")
    client.subscribe("drifter/power/#")
    client.subscribe("drifter/diag/#")
    client.subscribe("drifter/rf/tpms/#")
    client.loop_start()

    log.info("Alert Engine is LIVE — monitoring telemetry")

    while running:
        evaluate_rules(client)
        time.sleep(0.5)

    client.loop_stop()
    client.disconnect()
    log.info("Alert Engine stopped")


if __name__ == '__main__':
    main()

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

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [ALERTS] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

# ── Alert Levels ──
LEVEL_OK = 0
LEVEL_INFO = 1
LEVEL_AMBER = 2
LEVEL_RED = 3

LEVEL_NAMES = {0: 'OK', 1: 'INFO', 2: 'AMBER', 3: 'RED'}

# ── Rolling Buffer (60 seconds of data at ~10Hz) ──
BUFFER_SIZE = 600

@dataclass
class VehicleState:
    """Rolling buffer of vehicle telemetry."""
    rpm: deque = field(default_factory=lambda: deque(maxlen=BUFFER_SIZE))
    coolant: deque = field(default_factory=lambda: deque(maxlen=BUFFER_SIZE))
    stft1: deque = field(default_factory=lambda: deque(maxlen=BUFFER_SIZE))
    stft2: deque = field(default_factory=lambda: deque(maxlen=BUFFER_SIZE))
    load: deque = field(default_factory=lambda: deque(maxlen=BUFFER_SIZE))
    speed: deque = field(default_factory=lambda: deque(maxlen=BUFFER_SIZE))
    throttle: deque = field(default_factory=lambda: deque(maxlen=BUFFER_SIZE))
    voltage: deque = field(default_factory=lambda: deque(maxlen=BUFFER_SIZE))
    timestamps: deque = field(default_factory=lambda: deque(maxlen=BUFFER_SIZE))

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

    if stft1 > 12 and rpm < 900 and stft2 < 5:
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

    if stft1 > 12 and stft2 > 12 and rpm < 900:
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

    if coolant >= 108:
        return (LEVEL_RED,
                f"COOLANT CRITICAL: {coolant}°C. Pull over when safe. "
                f"Check thermostat, fan relay, coolant level.")

    if coolant > 100 and trend > 2.0:
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

    if state.sustained_below(state.stft1, -12, 150) or \
       state.sustained_below(state.stft2, -12, 150):
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

    if rpm > 6500:
        return (LEVEL_RED, f"HIGH RPM WARNING: {rpm:.0f} RPM. Redline risk.")
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
        value = data.get('value')
        ts = data.get('ts', time.time())

        topic = msg.topic
        if topic.endswith('/rpm'):
            state.rpm.append(value)
        elif topic.endswith('/coolant'):
            state.coolant.append(value)
        elif topic.endswith('/stft1'):
            state.stft1.append(value)
        elif topic.endswith('/stft2'):
            state.stft2.append(value)
        elif topic.endswith('/load'):
            state.load.append(value)
        elif topic.endswith('/speed'):
            state.speed.append(value)
        elif topic.endswith('/throttle'):
            state.throttle.append(value)
        elif topic.endswith('/voltage'):
            state.voltage.append(value)

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

        mqtt_client.publish("drifter/alert/level", json.dumps({
            'level': highest_level,
            'name': LEVEL_NAMES[highest_level],
            'ts': now
        }), retain=True)

        mqtt_client.publish("drifter/alert/message", json.dumps({
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
    while not connected:
        try:
            client.connect("localhost", 1883, 60)
            connected = True
        except Exception as e:
            log.warning(f"Waiting for MQTT broker... ({e})")
            time.sleep(3)

    # Subscribe to all engine telemetry
    client.subscribe("drifter/engine/#")
    client.subscribe("drifter/vehicle/#")
    client.subscribe("drifter/power/#")
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

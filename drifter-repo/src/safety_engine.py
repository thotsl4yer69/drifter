#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Safety Engine (Tier 1)
Local deterministic safety rules. Runs in real time, no network, no LLM.
Watches windowed telemetry from telemetry_batcher and the raw snapshot,
plus crash, fcw, tpms, and driver_fatigue events. Publishes prioritised
safety alerts that supersede the diagnostic alert when life is on the line.
UNCAGED TECHNOLOGY — EST 1991
"""

import json
import logging
import signal
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional

import paho.mqtt.client as mqtt

from pathlib import Path

from config import (
    MQTT_HOST, MQTT_PORT, TOPICS,
    LEVEL_INFO, LEVEL_AMBER, LEVEL_RED, LEVEL_NAMES,
    REDLINE_RPM,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [SAFETY] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

# ── Tunables (overridden by safety.yaml) ──
SAFETY_CFG = {
    'overrev_rpm': REDLINE_RPM,
    'overspeed_kph': 200,
    'hard_brake_kph_per_s': 22,
    'hard_accel_kph_per_s': 14,
    'stall_voltage_min': 10.0,
    'crash_g_threshold': 3.0,
    'fcw_ttc_critical_s': 1.2,
}

ALERT_COOLDOWN_S = 3.0


@dataclass
class SafetyState:
    speed_hist: deque = field(default_factory=lambda: deque(maxlen=30))
    rpm_hist: deque = field(default_factory=lambda: deque(maxlen=30))
    coolant: Optional[float] = None
    voltage: Optional[float] = None
    crash_active: bool = False
    fcw_active: bool = False
    fatigue_active: bool = False
    last_alert_ts: float = 0.0
    last_alert_key: str = ""


_state = SafetyState()


def _load_yaml_config() -> None:
    """Optional safety.yaml overrides on /opt/drifter/safety.yaml."""
    path = Path("/opt/drifter/safety.yaml")
    if not path.exists():
        return
    try:
        import yaml
        cfg = yaml.safe_load(path.read_text()) or {}
        for k, v in cfg.items():
            if k in SAFETY_CFG:
                SAFETY_CFG[k] = v
        log.info(f"Loaded safety.yaml overrides: {sorted(cfg)}")
    except Exception as e:
        log.warning(f"safety.yaml load failed: {e}")


# ── Rule functions: each returns (level, key, message) or None ──

def rule_overrev(s: SafetyState):
    if not s.rpm_hist:
        return None
    rpm = s.rpm_hist[-1]
    if rpm > SAFETY_CFG['overrev_rpm']:
        return (LEVEL_RED, 'overrev',
                f"OVER REV: {rpm:.0f} RPM exceeds {SAFETY_CFG['overrev_rpm']}. Shift up now.")
    return None


def rule_overspeed(s: SafetyState):
    if not s.speed_hist:
        return None
    kph = s.speed_hist[-1]
    if kph > SAFETY_CFG['overspeed_kph']:
        return (LEVEL_RED, 'overspeed',
                f"OVER SPEED: {kph:.0f} km/h. Slow down.")
    return None


def _rate(hist: deque) -> Optional[float]:
    if len(hist) < 2:
        return None
    return hist[-1] - hist[-2]


def rule_hard_brake(s: SafetyState):
    delta = _rate(s.speed_hist)
    if delta is None:
        return None
    decel = -delta  # positive on slowing
    if decel >= SAFETY_CFG['hard_brake_kph_per_s']:
        return (LEVEL_AMBER, 'hard_brake',
                f"HARD BRAKING: -{decel:.0f} km/h/s. Easy on the pedal.")
    return None


def rule_hard_accel(s: SafetyState):
    delta = _rate(s.speed_hist)
    if delta is None:
        return None
    if delta >= SAFETY_CFG['hard_accel_kph_per_s']:
        return (LEVEL_INFO, 'hard_accel',
                f"Hard acceleration: +{delta:.0f} km/h/s.")
    return None


def rule_stall(s: SafetyState):
    if s.voltage is None or not s.rpm_hist:
        return None
    rpm = s.rpm_hist[-1]
    if rpm == 0 and s.voltage > SAFETY_CFG['stall_voltage_min']:
        recent = list(s.rpm_hist)[-6:-1]
        if any(r > 200 for r in recent):
            return (LEVEL_RED, 'stall',
                    f"ENGINE STALL: RPM 0, battery {s.voltage:.1f}V. Restart and pull over.")
    return None


def rule_crash(s: SafetyState):
    if s.crash_active:
        return (LEVEL_RED, 'crash', "CRASH EVENT detected. SOS armed — confirm or cancel.")
    return None


def rule_fcw(s: SafetyState):
    if s.fcw_active:
        return (LEVEL_RED, 'fcw', "FORWARD COLLISION WARNING — brake now.")
    return None


def rule_fatigue(s: SafetyState):
    if s.fatigue_active:
        return (LEVEL_AMBER, 'fatigue',
                "Driver fatigue likely. Pull over for a break when safe.")
    return None


ALL_RULES: list[Callable[[SafetyState], Optional[tuple]]] = [
    rule_crash,
    rule_fcw,
    rule_overrev,
    rule_overspeed,
    rule_stall,
    rule_hard_brake,
    rule_hard_accel,
    rule_fatigue,
]


# ── Ingestion ──

def _on_snapshot(payload: dict) -> None:
    if not isinstance(payload, dict):
        return
    rpm = payload.get('rpm')
    if rpm is not None:
        try:
            _state.rpm_hist.append(float(rpm))
        except (TypeError, ValueError):
            pass
    speed = payload.get('speed')
    if speed is not None:
        try:
            _state.speed_hist.append(float(speed))
        except (TypeError, ValueError):
            pass
    voltage = payload.get('voltage')
    if voltage is not None:
        try:
            _state.voltage = float(voltage)
        except (TypeError, ValueError):
            pass
    coolant = payload.get('coolant')
    if coolant is not None:
        try:
            _state.coolant = float(coolant)
        except (TypeError, ValueError):
            pass


def on_message(client, userdata, msg) -> None:
    topic = msg.topic
    try:
        data = json.loads(msg.payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return

    if topic == TOPICS['snapshot']:
        _on_snapshot(data)
    elif topic == TOPICS['crash_event']:
        _state.crash_active = bool(data.get('active', True))
    elif topic == TOPICS['fcw_warning']:
        ttc = data.get('ttc_s')
        if ttc is None:
            _state.fcw_active = bool(data.get('active', True))
        else:
            try:
                _state.fcw_active = float(ttc) <= SAFETY_CFG['fcw_ttc_critical_s']
            except (TypeError, ValueError):
                _state.fcw_active = False
    elif topic == TOPICS['driver_fatigue']:
        _state.fatigue_active = bool(data.get('active', True))


def evaluate(client: mqtt.Client) -> None:
    now = time.time()
    best = None
    for rule in ALL_RULES:
        result = rule(_state)
        if result is None:
            continue
        if best is None or result[0] > best[0]:
            best = result
    if best is None:
        return

    level, key, message = best
    # Cooldown — don't republish same key faster than ALERT_COOLDOWN_S
    if key == _state.last_alert_key and now - _state.last_alert_ts < ALERT_COOLDOWN_S:
        return
    _state.last_alert_key = key
    _state.last_alert_ts = now

    payload = json.dumps({
        'level': level,
        'name': LEVEL_NAMES.get(level, str(level)),
        'key': key,
        'message': message,
        'ts': now,
    })
    client.publish(TOPICS['safety_alert'], payload, retain=True)
    if level >= LEVEL_AMBER:
        log.warning(f"[{LEVEL_NAMES.get(level)}] {message}")
    else:
        log.info(f"[{LEVEL_NAMES.get(level)}] {message}")


def main() -> None:
    log.info("DRIFTER Safety Engine starting...")
    _load_yaml_config()

    running = True

    def _handle_signal(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    client = mqtt.Client(client_id="drifter-safety")
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

    client.subscribe([
        (TOPICS['snapshot'], 0),
        (TOPICS['crash_event'], 0),
        (TOPICS['fcw_warning'], 0),
        (TOPICS['driver_fatigue'], 0),
    ])
    client.loop_start()
    client.publish(TOPICS['safety_status'], json.dumps({
        'state': 'online',
        'rules': len(ALL_RULES),
        'ts': time.time(),
    }), retain=True)
    log.info(f"Safety Engine LIVE — {len(ALL_RULES)} rules")

    while running:
        evaluate(client)
        time.sleep(0.25)

    client.publish(TOPICS['safety_status'], json.dumps({
        'state': 'offline',
        'ts': time.time(),
    }), retain=True)
    client.loop_stop()
    client.disconnect()
    log.info("Safety Engine stopped")


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Safety Engine (Tier 1)

Local deterministic safety rules. Runs in real time, no network, no LLM.
Watches windowed telemetry from telemetry_batcher and the raw snapshot,
plus crash, fcw, tpms, and driver_fatigue events. Publishes prioritised
safety alerts that supersede the diagnostic alert when life is on the line.

Threading model: MQTT runs its own network thread (loop_start). That thread
mutates _state via on_message; the main thread reads _state in evaluate().
A lock guards non-atomic fields (scalars and flag bools). Deques are used
for windowed numeric histories — append + indexed read are safe in CPython
but we still take the lock when computing rates to prevent torn reads.

UNCAGED TECHNOLOGY — EST 1991
"""

import json
import logging
import os
import signal
import socket
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import paho.mqtt.client as mqtt

from config import (
    LEVEL_AMBER,
    LEVEL_INFO,
    LEVEL_NAMES,
    LEVEL_RED,
    MQTT_HOST,
    MQTT_PORT,
    REDLINE_RPM,
    TOPICS,
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
    # Coolant overheat — local rule, distinct from diagnostic AMBER/RED window
    'coolant_amber_c': 108.0,
    'coolant_red_c': 115.0,
}

ALERT_COOLDOWN_S = 3.0

# Event TTL — if a transient event (crash/fcw/fatigue) hasn't been
# refreshed within this window, treat it as cleared. Stops a missed
# clear-message from latching us in RED forever.
EVENT_TTL_S = 8.0

# Safety publish QoS — life-critical, prefer at-least-once delivery.
SAFETY_QOS = 1


@dataclass
class SafetyState:
    speed_hist: deque = field(default_factory=lambda: deque(maxlen=30))
    rpm_hist: deque = field(default_factory=lambda: deque(maxlen=30))
    coolant: float | None = None
    voltage: float | None = None
    crash_active: bool = False
    crash_ts: float = 0.0
    fcw_active: bool = False
    fcw_ts: float = 0.0
    fatigue_active: bool = False
    fatigue_ts: float = 0.0
    last_alert_ts: float = 0.0
    last_alert_key: str = ""
    mqtt_connected: bool = False


_state = SafetyState()
_state_lock = threading.RLock()


def _load_yaml_config() -> None:
    """Optional safety.yaml overrides on /opt/drifter/safety.yaml.

    Only keys already present in SAFETY_CFG are honoured; unknown keys
    are logged and ignored to fail loud on typos without crashing.
    """
    path = Path("/opt/drifter/safety.yaml")
    if not path.exists():
        return
    try:
        import yaml
        cfg = yaml.safe_load(path.read_text()) or {}
        if not isinstance(cfg, dict):
            log.warning("safety.yaml is not a mapping — ignoring")
            return
        applied, unknown = [], []
        for k, v in cfg.items():
            if k in SAFETY_CFG:
                try:
                    SAFETY_CFG[k] = type(SAFETY_CFG[k])(v)
                    applied.append(k)
                except (TypeError, ValueError):
                    log.warning(f"safety.yaml: bad type for {k}={v!r}")
            else:
                unknown.append(k)
        if applied:
            log.info(f"Loaded safety.yaml overrides: {sorted(applied)}")
        if unknown:
            log.warning(f"safety.yaml: unknown keys ignored: {sorted(unknown)}")
    except Exception as e:
        log.warning(f"safety.yaml load failed: {e}")


# ── Rule functions: each returns (level, key, message) or None ──
# Rules read _state under the lock; callers must hold _state_lock.

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


def _rate(hist: deque) -> float | None:
    if len(hist) < 2:
        return None
    return hist[-1] - hist[-2]


def rule_hard_brake(s: SafetyState):
    delta = _rate(s.speed_hist)
    if delta is None:
        return None
    decel = -delta
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
    """RPM dropped to 0 while battery still healthy and engine was just running.

    Voltage > stall_voltage_min ensures we're not just seeing key-off
    (which also drops RPM). Recent rpm > 200 in the window confirms the
    engine was alive and has died, rather than never having started.
    """
    if s.voltage is None or not s.rpm_hist:
        return None
    rpm = s.rpm_hist[-1]
    if rpm == 0 and s.voltage > SAFETY_CFG['stall_voltage_min']:
        recent = list(s.rpm_hist)[-6:-1]
        if any(r > 200 for r in recent):
            return (LEVEL_RED, 'stall',
                    f"ENGINE STALL: RPM 0, battery {s.voltage:.1f}V. Restart and pull over.")
    return None


def rule_coolant_overheat(s: SafetyState):
    """Coolant overheat — escalates to RED above coolant_red_c."""
    if s.coolant is None:
        return None
    if s.coolant >= SAFETY_CFG['coolant_red_c']:
        return (LEVEL_RED, 'coolant_red',
                f"COOLANT CRITICAL: {s.coolant:.0f}°C. Stop driving when safe.")
    if s.coolant >= SAFETY_CFG['coolant_amber_c']:
        return (LEVEL_AMBER, 'coolant_amber',
                f"COOLANT HIGH: {s.coolant:.0f}°C. Ease off load.")
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


# Order matters for ties — first wins at equal level. Crash/FCW first.
ALL_RULES: list[Callable[[SafetyState], tuple | None]] = [
    rule_crash,
    rule_fcw,
    rule_coolant_overheat,
    rule_overrev,
    rule_overspeed,
    rule_stall,
    rule_hard_brake,
    rule_hard_accel,
    rule_fatigue,
]


# ── Ingestion ──

def _safe_float(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    # Reject NaN/Inf — they break comparisons silently
    if f != f or f in (float('inf'), float('-inf')):
        return None
    return f


def _on_snapshot(payload: dict) -> None:
    """Update telemetry state from a snapshot message. Drops bad values silently."""
    if not isinstance(payload, dict):
        return
    with _state_lock:
        rpm = _safe_float(payload.get('rpm'))
        if rpm is not None:
            _state.rpm_hist.append(rpm)
        speed = _safe_float(payload.get('speed'))
        if speed is not None:
            _state.speed_hist.append(speed)
        voltage = _safe_float(payload.get('voltage'))
        if voltage is not None:
            _state.voltage = voltage
        coolant = _safe_float(payload.get('coolant'))
        if coolant is not None:
            _state.coolant = coolant


def on_message(client, userdata, msg) -> None:
    topic = msg.topic
    try:
        data = json.loads(msg.payload)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
        return

    if topic == TOPICS['snapshot']:
        _on_snapshot(data if isinstance(data, dict) else {})
        return

    if not isinstance(data, dict):
        return

    now = time.time()
    with _state_lock:
        if topic == TOPICS['crash_event']:
            _state.crash_active = bool(data.get('active', True))
            _state.crash_ts = now
        elif topic == TOPICS['fcw_warning']:
            ttc = data.get('ttc_s')
            if ttc is None:
                _state.fcw_active = bool(data.get('active', True))
            else:
                ttc_f = _safe_float(ttc)
                _state.fcw_active = (ttc_f is not None
                                     and ttc_f <= SAFETY_CFG['fcw_ttc_critical_s'])
            _state.fcw_ts = now
        elif topic == TOPICS['driver_fatigue']:
            _state.fatigue_active = bool(data.get('active', True))
            _state.fatigue_ts = now


def on_connect(client, userdata, flags, rc) -> None:
    """Subscribe on (re)connect so we recover from broker bounces."""
    if rc != 0:
        log.warning(f"MQTT connect failed rc={rc}")
        return
    with _state_lock:
        _state.mqtt_connected = True
    client.subscribe([
        (TOPICS['snapshot'], 0),
        (TOPICS['crash_event'], SAFETY_QOS),
        (TOPICS['fcw_warning'], SAFETY_QOS),
        (TOPICS['driver_fatigue'], 0),
    ])
    log.info("MQTT connected — subscriptions active")


def on_disconnect(client, userdata, rc) -> None:
    with _state_lock:
        _state.mqtt_connected = False
    if rc != 0:
        log.warning(f"MQTT disconnected unexpectedly rc={rc} — paho will reconnect")


def _expire_stale_events(now: float) -> None:
    """Auto-clear transient event flags if their publisher fell silent."""
    with _state_lock:
        if _state.crash_active and now - _state.crash_ts > EVENT_TTL_S:
            _state.crash_active = False
        if _state.fcw_active and now - _state.fcw_ts > EVENT_TTL_S:
            _state.fcw_active = False
        if _state.fatigue_active and now - _state.fatigue_ts > EVENT_TTL_S:
            _state.fatigue_active = False


def evaluate(client: mqtt.Client) -> None:
    """Run all rules, pick highest level, publish with cooldown."""
    now = time.time()
    _expire_stale_events(now)

    with _state_lock:
        if not _state.mqtt_connected:
            return
        best = None
        for rule in ALL_RULES:
            try:
                result = rule(_state)
            except Exception as e:
                log.error(f"Rule {rule.__name__} crashed: {e}")
                continue
            if result is None:
                continue
            if best is None or result[0] > best[0]:
                best = result
        if best is None:
            return
        level, key, message = best
        if (key == _state.last_alert_key
                and now - _state.last_alert_ts < ALERT_COOLDOWN_S):
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
    try:
        client.publish(TOPICS['safety_alert'], payload, qos=SAFETY_QOS, retain=True)
    except Exception as e:
        log.error(f"safety_alert publish failed: {e}")
        return

    name = LEVEL_NAMES.get(level, str(level))
    if level >= LEVEL_AMBER:
        log.warning(f"[{name}] {message}")
    else:
        log.info(f"[{name}] {message}")


def _make_client_id() -> str:
    """Unique-ish client_id so two hosts don't kick each other off the broker."""
    return f"drifter-safety-{socket.gethostname()}-{os.getpid()}"


def main() -> None:
    log.info("DRIFTER Safety Engine starting...")
    _load_yaml_config()

    running = True

    def _handle_signal(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    # paho 1.x vs 2.x compatibility — 2.x requires CallbackAPIVersion.
    try:
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION1,  # type: ignore[attr-defined]
            client_id=_make_client_id(),
        )
    except AttributeError:
        client = mqtt.Client(client_id=_make_client_id())

    client.on_message = on_message
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect

    # LWT — broker tells everyone we're offline if we die.
    client.will_set(
        TOPICS['safety_status'],
        json.dumps({'state': 'offline', 'ts': time.time()}),
        qos=SAFETY_QOS,
        retain=True,
    )

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

    client.loop_start()
    try:
        client.publish(TOPICS['safety_status'], json.dumps({
            'state': 'online',
            'rules': len(ALL_RULES),
            'ts': time.time(),
        }), qos=SAFETY_QOS, retain=True)
    except Exception as e:
        log.warning(f"initial status publish failed: {e}")

    log.info(f"Safety Engine LIVE — {len(ALL_RULES)} rules")

    while running:
        try:
            evaluate(client)
        except Exception as e:
            log.error(f"evaluate() crashed: {e}")
        time.sleep(0.25)

    try:
        client.publish(TOPICS['safety_status'], json.dumps({
            'state': 'offline',
            'ts': time.time(),
        }), qos=SAFETY_QOS, retain=True)
    except Exception:
        pass
    client.loop_stop()
    try:
        client.disconnect()
    except Exception:
        pass
    log.info("Safety Engine stopped")


if __name__ == '__main__':
    main()

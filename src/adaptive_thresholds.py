#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Adaptive Thresholds
Learns per-vehicle baselines from warm-running telemetry over multiple
sessions. Publishes a learned-thresholds payload that the alert engine
and safety engine can subscribe to. Bounded drift from defaults so the
system never silently disables protection.
UNCAGED TECHNOLOGY — EST 1991
"""

import json
import logging
import math
import signal
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Dict, Optional

import paho.mqtt.client as mqtt

from config import (
    MQTT_HOST, MQTT_PORT, TOPICS,
    DRIFTER_DIR, THRESHOLDS, WARMUP_COOLANT_THRESHOLD,
    ADAPTIVE_LEARN_MIN_SAMPLES, ADAPTIVE_LEARN_SESSIONS, ADAPTIVE_DRIFT_LIMIT,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [THRESH] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

STATE_FILE = DRIFTER_DIR / "adaptive_thresholds.json"

# Sensors we learn baselines for and the THRESHOLDS key whose default they offset.
LEARNED_KEYS = {
    'stft1': 'stft1_baseline',
    'stft2': 'stft2_baseline',
    'ltft1': 'ltft1_baseline',
    'ltft2': 'ltft2_baseline',
    'rpm':   'idle_rpm_baseline',
    'voltage': 'voltage_baseline',
    'maf':   'maf_idle_baseline',
}

DEFAULT_BASELINES = {
    'stft1_baseline': 0.0,
    'stft2_baseline': 0.0,
    'ltft1_baseline': 0.0,
    'ltft2_baseline': 0.0,
    'idle_rpm_baseline': 720.0,
    'voltage_baseline': 14.2,
    'maf_idle_baseline': 3.8,
}


class Learner:
    def __init__(self) -> None:
        self.samples: Dict[str, deque] = defaultdict(lambda: deque(maxlen=20000))
        self.session_count = 0
        self.baselines = dict(DEFAULT_BASELINES)
        self.current_coolant = 0.0
        self.current_rpm = 0.0
        self.current_speed = 0.0
        self._load()

    def _load(self) -> None:
        if not STATE_FILE.exists():
            return
        try:
            data = json.loads(STATE_FILE.read_text())
            self.baselines.update(data.get('baselines', {}))
            self.session_count = int(data.get('session_count', 0))
            log.info(f"Loaded learner state: session_count={self.session_count}")
        except Exception as e:
            log.warning(f"Could not load learner state: {e}")

    def save(self) -> None:
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            STATE_FILE.write_text(json.dumps({
                'baselines': self.baselines,
                'session_count': self.session_count,
                'updated_ts': time.time(),
            }, indent=2))
        except Exception as e:
            log.warning(f"Save failed: {e}")

    def _eligible(self) -> bool:
        # Only learn during warm idle: coolant warm, RPM in idle range, speed zero.
        return (
            self.current_coolant >= WARMUP_COOLANT_THRESHOLD
            and 500 <= self.current_rpm <= 1000
            and self.current_speed <= 1
        )

    def ingest(self, key: str, value: float) -> None:
        if key not in LEARNED_KEYS:
            return
        # rpm/maf are always eligible at warm idle (matches above gate)
        if not self._eligible():
            return
        self.samples[key].append(value)

    def end_session(self) -> Dict[str, float]:
        """At session end, update baselines if we collected enough."""
        updated = False
        for key, baseline_key in LEARNED_KEYS.items():
            buf = self.samples.get(key)
            if not buf or len(buf) < ADAPTIVE_LEARN_MIN_SAMPLES // len(LEARNED_KEYS):
                continue
            values = list(buf)
            mean = sum(values) / len(values)
            default = DEFAULT_BASELINES[baseline_key]
            # Cap drift relative to default magnitude (or absolute 1.0 for small defaults)
            scale = max(abs(default), 1.0)
            clip = ADAPTIVE_DRIFT_LIMIT * scale
            mean = max(default - clip, min(default + clip, mean))
            # Exponential blend so a single session can't dominate
            blend = 0.4 if self.session_count == 0 else 0.2
            self.baselines[baseline_key] = round(
                (1 - blend) * self.baselines.get(baseline_key, default) + blend * mean,
                3,
            )
            updated = True
        self.samples.clear()
        if updated:
            self.session_count += 1
            self.save()
        return dict(self.baselines)

    def publish(self, client: mqtt.Client) -> None:
        payload = {
            'baselines': self.baselines,
            'session_count': self.session_count,
            'ready': self.session_count >= ADAPTIVE_LEARN_SESSIONS,
            'ts': time.time(),
        }
        client.publish(TOPICS['thresholds_learned'], json.dumps(payload), retain=True)


_learner = Learner()


def on_message(client, userdata, msg) -> None:
    topic = msg.topic
    try:
        data = json.loads(msg.payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return

    if topic == TOPICS['snapshot'] and isinstance(data, dict):
        coolant = data.get('coolant')
        rpm = data.get('rpm')
        speed = data.get('speed')
        if coolant is not None:
            try:
                _learner.current_coolant = float(coolant)
            except (TypeError, ValueError):
                pass
        if rpm is not None:
            try:
                _learner.current_rpm = float(rpm)
            except (TypeError, ValueError):
                pass
        if speed is not None:
            try:
                _learner.current_speed = float(speed)
            except (TypeError, ValueError):
                pass
        for k in ('stft1', 'stft2', 'ltft1', 'ltft2', 'rpm', 'voltage', 'maf'):
            v = data.get(k)
            if v is None:
                continue
            try:
                _learner.ingest(k, float(v))
            except (TypeError, ValueError):
                pass
    elif topic == TOPICS['drive_session'] and isinstance(data, dict):
        if data.get('event') == 'end':
            new_baselines = _learner.end_session()
            log.info(f"Session ended, baselines: {new_baselines}")
            _learner.publish(client)
            client.publish(TOPICS['thresholds_update'], json.dumps({
                'baselines': new_baselines,
                'reason': 'session_end',
                'ts': time.time(),
            }))


def main() -> None:
    log.info("DRIFTER Adaptive Thresholds starting...")

    running = True

    def _handle_signal(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    client = mqtt.Client(client_id="drifter-thresholds")
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
        (TOPICS['drive_session'], 0),
    ])
    client.loop_start()
    _learner.publish(client)
    log.info("Adaptive Thresholds LIVE")

    last_pub = time.time()
    while running:
        # Re-publish baselines periodically for late subscribers
        if time.time() - last_pub > 60:
            _learner.publish(client)
            last_pub = time.time()
        time.sleep(1)

    client.loop_stop()
    client.disconnect()
    log.info("Adaptive Thresholds stopped")


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Telemetry Batcher
Rolling-window aggregator. Subscribes to every drifter/* metric topic and
publishes summary windows (mean/min/max/stddev/last) used by Tier-2 AI
diagnostics, the session reporter, and adaptive thresholds.
UNCAGED TECHNOLOGY — EST 1991
"""

import json
import logging
import math
import signal
import time
from collections import defaultdict, deque
from typing import Dict, Optional, Tuple

import paho.mqtt.client as mqtt

from config import (
    MQTT_HOST, MQTT_PORT, TOPICS,
    TELEMETRY_WINDOW_SECONDS, TELEMETRY_PUBLISH_HZ, TELEMETRY_KEEP_SAMPLES,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [BATCHER] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

# Topics we batch — the keys we care about for downstream consumers.
METRIC_KEYS = (
    'rpm', 'coolant', 'stft1', 'stft2', 'ltft1', 'ltft2',
    'load', 'speed', 'throttle', 'voltage', 'iat', 'maf',
)

# Reverse map: topic string -> metric key
_TOPIC_TO_KEY: Dict[str, str] = {TOPICS[k]: k for k in METRIC_KEYS if k in TOPICS}

# Rolling buffers per metric: (timestamp, value)
_buffers: Dict[str, deque] = defaultdict(lambda: deque(maxlen=TELEMETRY_KEEP_SAMPLES))


def _record(topic: str, payload: bytes) -> None:
    key = _TOPIC_TO_KEY.get(topic)
    if key is None:
        return
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return
    value = data.get('value') if isinstance(data, dict) else None
    if value is None:
        return
    ts = data.get('ts', time.time()) if isinstance(data, dict) else time.time()
    try:
        _buffers[key].append((float(ts), float(value)))
    except (TypeError, ValueError):
        return


def _window_stats(samples: list) -> Optional[dict]:
    if not samples:
        return None
    values = [v for _, v in samples]
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    stddev = math.sqrt(variance) if variance > 0 else 0.0
    return {
        'mean': round(mean, 3),
        'min': round(min(values), 3),
        'max': round(max(values), 3),
        'stddev': round(stddev, 3),
        'last': round(values[-1], 3),
        'count': len(values),
    }


def build_window(now: float, window_seconds: float = TELEMETRY_WINDOW_SECONDS) -> dict:
    """Compute the current rolling-window summary for every metric."""
    cutoff = now - window_seconds
    out: Dict[str, dict] = {}
    for key, buf in _buffers.items():
        recent = [(t, v) for t, v in buf if t >= cutoff]
        stats = _window_stats(recent)
        if stats:
            out[key] = stats
    return {
        'window_seconds': window_seconds,
        'ts': now,
        'metrics': out,
    }


def _publish(client: mqtt.Client, payload: dict) -> None:
    try:
        client.publish(TOPICS['telemetry_window'], json.dumps(payload), retain=True)
    except Exception as e:
        log.warning(f"Publish window failed: {e}")
    # Compact stats topic — mean only, for cheap consumers
    compact = {k: v['mean'] for k, v in payload.get('metrics', {}).items()}
    try:
        client.publish(TOPICS['telemetry_stats'], json.dumps({
            'ts': payload['ts'],
            'means': compact,
        }))
    except Exception as e:
        log.debug(f"Publish stats failed: {e}")


def on_message(client, userdata, msg) -> None:
    _record(msg.topic, msg.payload)


def main() -> None:
    log.info("DRIFTER Telemetry Batcher starting...")
    log.info(f"Window: {TELEMETRY_WINDOW_SECONDS}s, publish @{TELEMETRY_PUBLISH_HZ} Hz")

    running = True

    def _handle_signal(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    client = mqtt.Client(client_id="drifter-batcher")
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

    for topic in _TOPIC_TO_KEY:
        client.subscribe(topic, 0)
    client.loop_start()
    log.info(f"Subscribed to {len(_TOPIC_TO_KEY)} metric topics")

    interval = 1.0 / max(TELEMETRY_PUBLISH_HZ, 0.1)
    next_pub = time.time() + interval

    while running:
        now = time.time()
        if now >= next_pub:
            payload = build_window(now)
            if payload['metrics']:
                _publish(client, payload)
            next_pub = now + interval
        time.sleep(0.1)

    log.info("Telemetry Batcher shutting down...")
    client.loop_stop()
    client.disconnect()
    log.info("Telemetry Batcher stopped")


if __name__ == '__main__':
    main()

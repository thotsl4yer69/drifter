#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Fuzz Engine
Synthetic telemetry generator. Publishes randomised but plausible values
on the standard sensor topics for stress-testing the rest of the stack
without a real vehicle attached.
UNCAGED TECHNOLOGY — EST 1991
"""

import json
import logging
import math
import random
import signal
import threading
import time

import paho.mqtt.client as mqtt

from config import (
    FUZZ_DEFAULT_HZ,
    FUZZ_DEFAULT_RANGES,
    MQTT_HOST,
    MQTT_PORT,
    TOPICS,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [FUZZ] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger(__name__)


def _smooth(prev: float, target: float, alpha: float = 0.1) -> float:
    return prev + alpha * (target - prev)


def _drive_profile(t: float) -> dict[str, float]:
    """Synthesize a slowly-varying drive profile based on wall clock."""
    cycle = (math.sin(t / 30.0) + 1) / 2.0
    return {
        'rpm': 800 + cycle * (5800 - 800) + random.uniform(-50, 50),
        'speed': cycle * 120 + random.uniform(-2, 2),
        'coolant': 88 + cycle * 8 + random.uniform(-1, 1),
        'voltage': 14.2 + math.sin(t / 7.0) * 0.2 + random.uniform(-0.05, 0.05),
        'load': 20 + cycle * 60 + random.uniform(-3, 3),
        'throttle': cycle * 80 + random.uniform(-2, 2),
        'iat': 30 + random.uniform(-3, 3),
        'maf': 3 + cycle * 18 + random.uniform(-0.5, 0.5),
        'stft1': random.uniform(-5, 5),
        'stft2': random.uniform(-5, 5),
    }


def _publish_tick(client: mqtt.Client, ranges: dict) -> None:
    profile = _drive_profile(time.time())
    for key, value in profile.items():
        topic = TOPICS.get(key)
        if not topic:
            continue
        # respect ranges if caller narrowed them
        lo, hi = ranges.get(key, (None, None))
        if lo is not None:
            value = max(lo, value)
        if hi is not None:
            value = min(hi, value)
        client.publish(topic, json.dumps({'value': round(value, 2), 'ts': time.time()}))


def _fuzz_loop(client: mqtt.Client, state: dict) -> None:
    while state['running']:
        if state['active']:
            _publish_tick(client, state['ranges'])
        time.sleep(1.0 / max(state['hz'], 0.1))


def main() -> None:
    log.info("DRIFTER Fuzz Engine starting...")

    state = {
        'running': True,
        'active': False,
        'hz': FUZZ_DEFAULT_HZ,
        'ranges': dict(FUZZ_DEFAULT_RANGES),
    }

    def _handle_signal(sig, frame):
        state['running'] = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    client = mqtt.Client(client_id="drifter-fuzz")

    def on_message(_c, _u, msg) -> None:
        try:
            data = json.loads(msg.payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        if not isinstance(data, dict):
            return
        action = data.get('action', '').lower()
        if action == 'start':
            state['active'] = True
            state['hz'] = float(data.get('hz', FUZZ_DEFAULT_HZ))
            ranges = data.get('ranges')
            if isinstance(ranges, dict):
                state['ranges'] = {k: tuple(v) for k, v in ranges.items()}
            log.info(f"fuzz START hz={state['hz']} ranges={state['ranges']}")
        elif action == 'stop':
            state['active'] = False
            log.info("fuzz STOP")
        client.publish(TOPICS['fuzz_status'], json.dumps({
            'active': state['active'], 'hz': state['hz'], 'ts': time.time(),
        }), retain=True)

    client.on_message = on_message

    connected = False
    while not connected and state['running']:
        try:
            client.connect(MQTT_HOST, MQTT_PORT, 60)
            connected = True
        except Exception as e:
            log.warning(f"Waiting for MQTT broker... ({e})")
            time.sleep(3)

    if not state['running']:
        return

    client.subscribe(TOPICS['fuzz_command'], qos=1)
    client.loop_start()
    log.info("Fuzz Engine LIVE — awaiting commands")

    threading.Thread(target=_fuzz_loop, args=(client, state), daemon=True).start()

    while state['running']:
        time.sleep(1)

    client.publish(TOPICS['fuzz_status'], json.dumps({'active': False, 'ts': time.time()}), retain=True)
    client.loop_stop()
    client.disconnect()
    log.info("Fuzz Engine stopped")


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Sentry Mode
Parked-car monitor. When activated (manually or auto on park):
  - Watches accel events via the crash detector's MPU6050 (subscribes
    to crash_event when accel-triggered),
  - Records short clips from the vision/dashcam node when a bump fires,
  - Persists a tamper log to /opt/drifter/sentry/,
  - Notifies via comms bridge.
UNCAGED TECHNOLOGY — EST 1991
"""

import json
import logging
import signal
import threading
import time
from pathlib import Path
from typing import Optional

import paho.mqtt.client as mqtt

from config import (
    MQTT_HOST, MQTT_PORT, TOPICS,
    SENTRY_DIR, SENTRY_ACCEL_TRIGGER_G, SENTRY_CLIP_SECONDS, SENTRY_MAX_CLIPS,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [SENTRY] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

CONFIG_PATH = Path("/opt/drifter/sentry.yaml")
EVENT_LOG = SENTRY_DIR / "events.jsonl"


def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        import yaml
        return yaml.safe_load(CONFIG_PATH.read_text()) or {}
    except Exception as e:
        log.warning(f"sentry.yaml load failed: {e}")
        return {}


class SentryState:
    def __init__(self) -> None:
        self.armed: bool = False
        self.last_event_ts: float = 0.0
        self.events_logged: int = 0
        self.auto_arm: bool = True
        self.threshold_g: float = SENTRY_ACCEL_TRIGGER_G


def _append_event(state: SentryState, event: dict) -> None:
    try:
        SENTRY_DIR.mkdir(parents=True, exist_ok=True)
        with EVENT_LOG.open('a') as f:
            f.write(json.dumps(event) + '\n')
        state.events_logged += 1
    except Exception as e:
        log.warning(f"sentry log write failed: {e}")


def _request_clip(client: mqtt.Client, reason: str) -> None:
    client.publish(TOPICS['sentry_clip'], json.dumps({
        'request': 'clip',
        'seconds': SENTRY_CLIP_SECONDS,
        'reason': reason,
        'ts': time.time(),
    }))


def _publish_status(client: mqtt.Client, state: SentryState) -> None:
    client.publish(TOPICS['sentry_status'], json.dumps({
        'armed': state.armed,
        'auto_arm': state.auto_arm,
        'threshold_g': state.threshold_g,
        'events_logged': state.events_logged,
        'ts': time.time(),
    }), retain=True)


def main() -> None:
    log.info("DRIFTER Sentry Mode starting...")
    cfg = _load_config()
    state = SentryState()
    state.auto_arm = bool(cfg.get('auto_arm', True))
    state.threshold_g = float(cfg.get('threshold_g', SENTRY_ACCEL_TRIGGER_G))

    running = True

    def _handle_signal(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    client = mqtt.Client(client_id="drifter-sentry")

    def on_message(_c, _u, msg) -> None:
        try:
            data = json.loads(msg.payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        topic = msg.topic
        if topic == TOPICS['sentry_event'] and isinstance(data, dict):
            cmd = data.get('command')
            if cmd == 'arm':
                state.armed = True
                log.info("Armed")
            elif cmd == 'disarm':
                state.armed = False
                log.info("Disarmed")
            _publish_status(client, state)
        elif topic == TOPICS['drive_session'] and isinstance(data, dict):
            # Auto-arm on session end (parked), disarm on session start (driving)
            if state.auto_arm:
                if data.get('event') == 'end':
                    state.armed = True
                    log.info("Auto-armed at session end")
                elif data.get('event') == 'start':
                    state.armed = False
                    log.info("Auto-disarmed at session start")
                _publish_status(client, state)
        elif topic == TOPICS['crash_event'] and isinstance(data, dict):
            if not state.armed:
                return
            magnitude = float(data.get('magnitude_g') or 0.0)
            if magnitude < state.threshold_g:
                return
            event = {
                'type': 'bump',
                'magnitude_g': magnitude,
                'reason': data.get('reason'),
                'ts': time.time(),
            }
            _append_event(state, event)
            client.publish(TOPICS['sentry_event'], json.dumps(event))
            _request_clip(client, f"bump {magnitude:.2f}g")
            client.publish(TOPICS['comms_notify'], json.dumps({
                'title': 'DRIFTER Sentry',
                'message': f"Bump detected ({magnitude:.2f}g) — clip captured",
                'priority': 'high',
                'ts': time.time(),
            }))

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
        (TOPICS['sentry_event'], 0),
        (TOPICS['drive_session'], 0),
        (TOPICS['crash_event'], 0),
    ])
    client.loop_start()
    _publish_status(client, state)
    log.info(f"Sentry Mode LIVE (auto_arm={state.auto_arm}, threshold={state.threshold_g}g)")

    while running:
        time.sleep(1)
        # Cap clip log
        try:
            if EVENT_LOG.exists():
                lines = EVENT_LOG.read_text().splitlines()
                if len(lines) > SENTRY_MAX_CLIPS:
                    EVENT_LOG.write_text('\n'.join(lines[-SENTRY_MAX_CLIPS:]) + '\n')
        except Exception:
            pass

    client.loop_stop()
    client.disconnect()
    log.info("Sentry Mode stopped")


if __name__ == '__main__':
    main()

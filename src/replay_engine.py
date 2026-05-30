#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Replay Engine
Replays recorded drive sessions through MQTT at configurable speed.
Sessions are JSONL files captured by session_recorder.py — each line is
`{"ts": <epoch>, "topic": <str>, "payload": <str>}`. Supports gzip.
UNCAGED TECHNOLOGY — EST 1991
"""

import gzip
import json
import logging
import signal
import threading
import time
from pathlib import Path

import paho.mqtt.client as mqtt

from config import (
    MQTT_HOST,
    MQTT_PORT,
    REPLAY_DEFAULT_SPEED,
    REPLAY_DIR,
    TOPICS,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [REPLAY] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger(__name__)


def _open(path: Path):
    if str(path).endswith('.gz'):
        return gzip.open(path, 'rt')
    return open(path)


def _list_sessions() -> list[dict]:
    p = Path(REPLAY_DIR)
    if not p.exists():
        return []
    out = []
    for f in sorted(p.glob('*.jsonl*')):
        out.append({'name': f.name, 'size': f.stat().st_size, 'mtime': f.stat().st_mtime})
    return out


def _replay_session(client: mqtt.Client, path: Path, speed: float, stop_event: threading.Event) -> None:
    if not path.exists():
        log.warning(f"session not found: {path}")
        client.publish(TOPICS['replay_status'], json.dumps({'status': 'error', 'reason': 'not_found'}))
        return
    log.info(f"replay START {path.name} speed={speed}x")
    client.publish(TOPICS['replay_status'], json.dumps({
        'status': 'running', 'session': path.name, 'speed': speed, 'ts': time.time(),
    }))

    base_real = time.time()
    base_session: float | None = None
    count = 0
    last_progress = 0.0

    try:
        with _open(path) as fh:
            for line in fh:
                if stop_event.is_set():
                    log.info("replay STOPPED by user")
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = rec.get('ts')
                topic = rec.get('topic')
                payload = rec.get('payload', '')
                if ts is None or not topic:
                    continue
                if base_session is None:
                    base_session = ts
                # schedule
                elapsed_session = (ts - base_session) / max(speed, 0.01)
                elapsed_real = time.time() - base_real
                wait = elapsed_session - elapsed_real
                if wait > 0:
                    # chop sleeps so we can stop quickly
                    end = time.time() + wait
                    while time.time() < end and not stop_event.is_set():
                        time.sleep(min(0.2, end - time.time()))
                if stop_event.is_set():
                    break
                if isinstance(payload, (dict, list)):
                    payload = json.dumps(payload)
                client.publish(topic, payload)
                count += 1
                if time.time() - last_progress > 2:
                    client.publish(TOPICS['replay_progress'], json.dumps({
                        'session': path.name, 'frames': count, 'elapsed': elapsed_real,
                    }))
                    last_progress = time.time()
    except Exception as e:
        log.warning(f"replay error: {e}")
        client.publish(TOPICS['replay_status'], json.dumps({'status': 'error', 'reason': str(e)}))
        return

    log.info(f"replay END {path.name} frames={count}")
    client.publish(TOPICS['replay_status'], json.dumps({
        'status': 'complete', 'session': path.name, 'frames': count, 'ts': time.time(),
    }))


def main() -> None:
    log.info("DRIFTER Replay Engine starting...")
    Path(REPLAY_DIR).mkdir(parents=True, exist_ok=True)

    running = [True]
    stop_event = threading.Event()
    worker: list[threading.Thread | None] = [None]

    def _handle_signal(sig, frame):
        running[0] = False
        stop_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    client = mqtt.Client(client_id="drifter-replay")

    def on_message(_c, _u, msg) -> None:
        try:
            data = json.loads(msg.payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        if not isinstance(data, dict):
            return
        action = data.get('action', '').lower()
        if action == 'list':
            client.publish(TOPICS['replay_status'], json.dumps({
                'status': 'list', 'sessions': _list_sessions(),
            }))
            return
        if action == 'stop':
            stop_event.set()
            return
        if action == 'play':
            session = data.get('session')
            speed = float(data.get('speed', REPLAY_DEFAULT_SPEED))
            if not session:
                return
            if worker[0] and worker[0].is_alive():
                stop_event.set()
                worker[0].join(timeout=5)
            stop_event.clear()
            path = Path(REPLAY_DIR) / session
            worker[0] = threading.Thread(
                target=_replay_session,
                args=(client, path, speed, stop_event),
                daemon=True,
            )
            worker[0].start()

    client.on_message = on_message

    connected = False
    while not connected and running[0]:
        try:
            client.connect(MQTT_HOST, MQTT_PORT, 60)
            connected = True
        except Exception as e:
            log.warning(f"Waiting for MQTT broker... ({e})")
            time.sleep(3)

    if not running[0]:
        return

    client.subscribe(TOPICS['replay_command'], qos=1)
    client.loop_start()
    log.info(f"Replay Engine LIVE — sessions dir: {REPLAY_DIR}")

    while running[0]:
        time.sleep(1)

    stop_event.set()
    client.loop_stop()
    client.disconnect()
    log.info("Replay Engine stopped")


if __name__ == '__main__':
    main()

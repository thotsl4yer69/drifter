#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Session Recorder
Captures all MQTT telemetry to gzip-compressed JSONL files for later
replay. Rotates by RECORDER_SEGMENT_SECONDS and prunes the directory
when RECORDER_MAX_GB is exceeded.
UNCAGED TECHNOLOGY — EST 1991
"""

import gzip
import json
import logging
import signal
import threading
import time
from datetime import datetime
from pathlib import Path

import paho.mqtt.client as mqtt

from config import (
    MQTT_HOST,
    MQTT_PORT,
    RECORDER_DIR,
    RECORDER_MAX_GB,
    RECORDER_SEGMENT_SECONDS,
    TOPICS,
    make_mqtt_client,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [RECORDER] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger(__name__)

_lock = threading.Lock()
_state = {
    'recording': False,
    'fh': None,
    'path': None,
    'opened_at': 0.0,
    'frames': 0,
    'topics_filter': '#',
}


def _open_segment() -> Path:
    Path(RECORDER_DIR).mkdir(parents=True, exist_ok=True)
    name = datetime.utcnow().strftime('drifter-%Y%m%dT%H%M%S.jsonl.gz')
    path = Path(RECORDER_DIR) / name
    fh = gzip.open(path, 'wt')
    _state['fh'] = fh
    _state['path'] = path
    _state['opened_at'] = time.time()
    _state['frames'] = 0
    log.info(f"segment opened: {path.name}")
    return path


def _close_segment() -> Path | None:
    fh = _state.get('fh')
    if fh is None:
        return None
    try:
        fh.close()
    except Exception:
        pass
    path = _state.get('path')
    log.info(f"segment closed: {path.name if path else '?'} frames={_state['frames']}")
    _state['fh'] = None
    _state['path'] = None
    return path


def _prune() -> None:
    p = Path(RECORDER_DIR)
    if not p.exists():
        return
    files = sorted(p.glob('*.jsonl.gz'), key=lambda f: f.stat().st_mtime)
    total = sum(f.stat().st_size for f in files)
    limit = RECORDER_MAX_GB * 1024 * 1024 * 1024
    while total > limit and files:
        f = files.pop(0)
        total -= f.stat().st_size
        try:
            f.unlink()
            log.info(f"pruned old segment: {f.name}")
        except Exception as e:
            log.warning(f"prune failed: {e}")


def _write_record(topic: str, payload: bytes) -> None:
    if not _state['recording']:
        return
    fh = _state.get('fh')
    if fh is None:
        return
    try:
        payload_text = payload.decode('utf-8', errors='replace')
    except Exception:
        return
    rec = {'ts': time.time(), 'topic': topic, 'payload': payload_text}
    try:
        fh.write(json.dumps(rec) + '\n')
        _state['frames'] += 1
    except Exception as e:
        log.warning(f"write failed: {e}")


def _on_message(client, _u, msg) -> None:
    topic = msg.topic
    if topic == TOPICS['recorder_command']:
        try:
            data = json.loads(msg.payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        action = data.get('action', '').lower()
        with _lock:
            if action == 'start' and not _state['recording']:
                _state['topics_filter'] = data.get('filter', '#')
                _state['recording'] = True
                _open_segment()
                client.publish(TOPICS['recorder_status'], json.dumps({
                    'status': 'recording', 'filter': _state['topics_filter'], 'ts': time.time(),
                }), retain=True)
            elif action == 'stop' and _state['recording']:
                _state['recording'] = False
                path = _close_segment()
                client.publish(TOPICS['recorder_status'], json.dumps({
                    'status': 'stopped', 'last_segment': path.name if path else None, 'ts': time.time(),
                }), retain=True)
                if path:
                    client.publish(TOPICS['recorder_session'], json.dumps({
                        'segment': path.name, 'frames': _state['frames'], 'ts': time.time(),
                    }))
        return

    with _lock:
        _write_record(topic, msg.payload)


def _rotate_loop(client: mqtt.Client, running: list) -> None:
    while running[0]:
        time.sleep(5)
        with _lock:
            if not _state['recording']:
                continue
            if time.time() - _state['opened_at'] >= RECORDER_SEGMENT_SECONDS:
                _close_segment()
                _open_segment()
                _prune()


def main() -> None:
    log.info("DRIFTER Session Recorder starting...")
    Path(RECORDER_DIR).mkdir(parents=True, exist_ok=True)

    running = [True]

    def _handle_signal(sig, frame):
        running[0] = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    client = make_mqtt_client("drifter-recorder")
    client.on_message = _on_message

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

    # Subscribe to command channel always; wildcard subscribe is added on start
    client.subscribe(TOPICS['recorder_command'], qos=1)
    client.subscribe('drifter/#', qos=0)
    client.loop_start()
    log.info(f"Recorder LIVE — dir: {RECORDER_DIR}")

    threading.Thread(target=_rotate_loop, args=(client, running), daemon=True).start()

    while running[0]:
        time.sleep(1)

    with _lock:
        if _state['recording']:
            _state['recording'] = False
            _close_segment()
    client.loop_stop()
    client.disconnect()
    log.info("Recorder stopped")


if __name__ == '__main__':
    main()

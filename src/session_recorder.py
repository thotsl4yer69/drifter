#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Session Recorder + Playback

Subscribes to drifter/# and writes every message as one JSON line per
topic+payload to /opt/drifter/state/sessions/<session_id>.jsonl, ordered
by ts. Maintains an index file at /opt/drifter/state/sessions/index.json.

Session lifecycle:
  - Start when a CAN frame has been seen in the last 5 min (ignition on)
    OR on /api/session/start (sent via drifter/session/control).
  - Stop on /api/session/stop, CAN silence > 10 min, or SIGTERM.

Playback:
  - Replays a session by re-publishing each line to drifter/replay/<topic>
    so live consumers can't confuse playback with live data.
  - Speed scaling, pause/resume/stop via drifter/session/control.

UNCAGED TECHNOLOGY — EST 1991
"""
from __future__ import annotations

import json
import logging
import os
import signal
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

import paho.mqtt.client as mqtt

from config import MQTT_HOST, MQTT_PORT, TOPICS, make_mqtt_client
import gps_helper

logging.basicConfig(level=logging.INFO, format='%(asctime)s [SESSION-REC] %(message)s',
                    datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

# Persistent state lives next to the SQLite telemetry archive.
SESSIONS_DIR = Path('/opt/drifter/state/sessions')
INDEX_PATH = SESSIONS_DIR / 'index.json'

# Wildcard catch-all for the drifter bus. Replayed topics are explicitly
# prefixed with drifter/replay/ so subscribing here doesn't loop them
# back into the session recorder.
SUBSCRIBE_PATTERN = 'drifter/#'
REPLAY_TOPIC_PREFIX = 'drifter/replay/'

# Topic the dashboard uses to control sessions. Body:
#   {"action": "start"}                   — force a session start
#   {"action": "stop"}                    — end current session
#   {"action": "replay", "session_id":…, "speed": 2.0}
#   {"action": "pause"} | {"action": "resume"} | {"action": "stop_replay"}
CONTROL_TOPIC = 'drifter/session/control'

# Ignition heuristic: a CAN frame within this window means the ECU is up.
CAN_IGNITION_WINDOW_SEC = 300.0   # 5 min
CAN_SILENCE_TIMEOUT_SEC = 600.0   # 10 min — auto-end the session

# CAN frame topics arriving from drifter-canbridge. canbridge publishes the
# decoded OBD-II values under drifter/engine/* and drifter/vehicle/*; the
# heartbeat we use to detect ignition is any of these arriving.
_CAN_HEARTBEAT_TOPIC_PREFIXES = (
    'drifter/engine/',
    'drifter/vehicle/',
)


def _now() -> float:
    return time.time()


class SessionRecorder:
    """Subscribes to drifter/#, writes one JSONL per active session."""

    def __init__(self):
        self.running = True
        # Current session state
        self._session_id: Optional[str] = None
        self._session_start_ts: Optional[float] = None
        self._session_file: Optional = None        # file handle
        self._session_path: Optional[Path] = None
        self._summary: dict = {}
        self._last_can_ts: float = 0.0
        self._lock = threading.Lock()

        # Replay state
        self._replay_thread: Optional[threading.Thread] = None
        self._replay_paused = threading.Event()
        self._replay_stop = threading.Event()

        try:
            SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            log.warning("Could not create sessions dir: %s", e)

        self.client = make_mqtt_client('drifter-session-recorder')
        self.client.on_message = self._on_message
        self.client.on_connect = self._on_connect

    # ── MQTT plumbing ────────────────────────────────────────────────
    def _on_connect(self, client, userdata, flags, rc, properties=None):
        log.info("MQTT connected rc=%s; subscribing to %s + %s",
                 rc, SUBSCRIBE_PATTERN, CONTROL_TOPIC)
        client.subscribe(SUBSCRIBE_PATTERN)
        client.subscribe(CONTROL_TOPIC)

    def _on_message(self, client, userdata, msg):
        topic = msg.topic
        # Never record replayed traffic — that would create a feedback loop.
        if topic.startswith(REPLAY_TOPIC_PREFIX):
            return
        # Control channel.
        if topic == CONTROL_TOPIC:
            self._handle_control(msg.payload)
            return

        # Ignition heuristic — any decoded OBD signal counts as "engine running".
        if any(topic.startswith(p) for p in _CAN_HEARTBEAT_TOPIC_PREFIXES):
            self._last_can_ts = _now()
            if self._session_id is None:
                self._start_session('can_active')

        # Persist if we have an active session.
        if self._session_id is None:
            return
        try:
            payload_text = msg.payload.decode('utf-8', errors='replace')
        except Exception:
            payload_text = ''
        line = {
            'ts': _now(),
            'topic': topic,
            'payload': payload_text,
        }
        # Geo-tag every recorded line per TASK 3.4. gps_helper returns None
        # when no fresh fix is available — we omit the fields rather than
        # fabricate.
        gps_helper.annotate(line)
        # Update lightweight summary counters.
        if topic == TOPICS.get('alert_message', 'drifter/alert/message'):
            self._summary['alerts_count'] = int(self._summary.get('alerts_count', 0)) + 1
        elif topic == TOPICS.get('anomaly_event', 'drifter/anomaly/event'):
            self._summary['anomalies_count'] = int(self._summary.get('anomalies_count', 0)) + 1
        elif topic == TOPICS.get('dtc', 'drifter/diag/dtc'):
            try:
                d = json.loads(payload_text)
                stored = d.get('stored') if isinstance(d, dict) else None
                if isinstance(stored, list):
                    self._summary['dtcs_count'] = len(stored)
            except (json.JSONDecodeError, ValueError):
                pass
        elif topic == TOPICS.get('trip_stats', 'drifter/trip/stats'):
            try:
                d = json.loads(payload_text)
                if isinstance(d, dict) and 'distance_km' in d:
                    self._summary['distance_km'] = float(d['distance_km'])
            except (json.JSONDecodeError, ValueError, TypeError):
                pass

        with self._lock:
            if self._session_file is not None:
                try:
                    self._session_file.write(json.dumps(line, default=str) + '\n')
                except Exception as e:
                    log.warning("session write failed: %s", e)

    # ── Control channel ──────────────────────────────────────────────
    def _handle_control(self, payload):
        try:
            body = json.loads(payload.decode('utf-8') if isinstance(payload, bytes) else payload)
        except (json.JSONDecodeError, ValueError, AttributeError):
            return
        if not isinstance(body, dict):
            return
        action = body.get('action')
        if action == 'start':
            self._start_session('manual')
        elif action == 'stop':
            self._end_session('manual')
        elif action == 'replay':
            sid = body.get('session_id')
            try:
                speed = float(body.get('speed', 1.0))
            except (TypeError, ValueError):
                speed = 1.0
            if isinstance(sid, str) and sid:
                self._start_replay(sid, speed)
        elif action == 'pause':
            self._replay_paused.set()
        elif action == 'resume':
            self._replay_paused.clear()
        elif action == 'stop_replay':
            self._replay_stop.set()

    # ── Session lifecycle ────────────────────────────────────────────
    def _start_session(self, reason: str):
        with self._lock:
            if self._session_id is not None:
                return
            sid = str(uuid.uuid4())
            self._session_id = sid
            self._session_start_ts = _now()
            self._session_path = SESSIONS_DIR / f'{sid}.jsonl'
            self._summary = {
                'distance_km': 0.0,
                'alerts_count': 0,
                'dtcs_count': 0,
                'anomalies_count': 0,
            }
            try:
                self._session_file = open(self._session_path, 'a', encoding='utf-8')
            except OSError as e:
                log.warning("Could not open session file %s: %s", self._session_path, e)
                self._session_id = None
                self._session_start_ts = None
                self._session_path = None
                return
        log.info("Session START id=%s reason=%s file=%s", sid, reason, self._session_path)

    def _end_session(self, reason: str):
        with self._lock:
            if self._session_id is None:
                return
            sid = self._session_id
            start_ts = self._session_start_ts or _now()
            end_ts = _now()
            summary = dict(self._summary)
            try:
                if self._session_file is not None:
                    self._session_file.close()
            except Exception:
                pass
            self._session_file = None
            self._session_id = None
            self._session_start_ts = None
            path = self._session_path
            self._session_path = None
        log.info("Session END id=%s reason=%s duration=%.1fs", sid, reason, end_ts - start_ts)
        try:
            _append_index({
                'session_id': sid,
                'start_ts': start_ts,
                'end_ts': end_ts,
                'duration_s': round(end_ts - start_ts, 2),
                'path': str(path) if path else None,
                'summary': summary,
            })
        except Exception as e:
            log.warning("index append failed: %s", e)

    def _maybe_end_on_can_silence(self):
        if self._session_id is None:
            return
        # Only fire the silence check once we have observed at least one
        # CAN frame in this session. A purely manual session with no CAN
        # bus has _last_can_ts == 0 and we leave it alone.
        if self._last_can_ts <= 0:
            return
        if (_now() - self._last_can_ts) > CAN_SILENCE_TIMEOUT_SEC:
            self._end_session('can_silence')

    # ── Playback ─────────────────────────────────────────────────────
    def _start_replay(self, session_id: str, speed: float):
        if self._replay_thread is not None and self._replay_thread.is_alive():
            log.warning("replay already running; ignoring new request")
            return
        path = SESSIONS_DIR / f'{session_id}.jsonl'
        if not path.exists():
            log.warning("replay path missing: %s", path)
            return
        self._replay_paused.clear()
        self._replay_stop.clear()
        speed = max(0.1, min(speed, 50.0))
        self._replay_thread = threading.Thread(
            target=self._replay_loop, args=(path, speed), daemon=True,
            name='drifter-session-replay')
        self._replay_thread.start()
        log.info("Replay START sid=%s speed=%sx", session_id, speed)

    def _replay_loop(self, path: Path, speed: float):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
        except OSError as e:
            log.warning("replay open failed: %s", e)
            return
        if not lines:
            return
        try:
            first = json.loads(lines[0])
        except (json.JSONDecodeError, ValueError):
            log.warning("replay first-line not JSON")
            return
        origin_ts = float(first.get('ts') or _now())
        wall_origin = _now()
        for raw in lines:
            if self._replay_stop.is_set():
                break
            while self._replay_paused.is_set() and not self._replay_stop.is_set():
                time.sleep(0.1)
            try:
                obj = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                continue
            target_offset = (float(obj.get('ts') or origin_ts) - origin_ts) / speed
            now_offset = _now() - wall_origin
            sleep_for = target_offset - now_offset
            if sleep_for > 0:
                # Sleep in short chunks so pause/stop responds quickly.
                end = _now() + sleep_for
                while _now() < end and not self._replay_stop.is_set():
                    if self._replay_paused.is_set():
                        break
                    time.sleep(min(0.05, end - _now()))
            topic = obj.get('topic') or ''
            if not topic:
                continue
            payload = obj.get('payload') or ''
            try:
                self.client.publish(REPLAY_TOPIC_PREFIX + topic, payload, qos=0, retain=False)
            except Exception as e:
                log.debug("replay publish failed: %s", e)
        log.info("Replay END path=%s", path)

    # ── Main loop ────────────────────────────────────────────────────
    def start(self):
        log.info("Session Recorder starting...")
        connected = False
        while not connected and self.running:
            try:
                self.client.connect(MQTT_HOST, MQTT_PORT, 60)
                connected = True
            except Exception as e:
                log.warning("MQTT connect failed: %s", e)
                time.sleep(3)
        self.client.loop_start()
        log.info("Session Recorder LIVE")
        try:
            while self.running:
                time.sleep(5)
                self._maybe_end_on_can_silence()
        finally:
            self._end_session('shutdown')
            self.client.loop_stop()
            self.client.disconnect()


# ── Index file helpers ───────────────────────────────────────────────
def load_index() -> list:
    """Return the session index, newest first. Empty list on fresh install."""
    if not INDEX_PATH.exists():
        return []
    try:
        data = json.loads(INDEX_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    data.sort(key=lambda e: (e.get('start_ts') or 0), reverse=True)
    return data


def _append_index(entry: dict) -> None:
    rows = load_index()
    # Drop any prior row with the same session_id (re-recordings of the
    # same id are unlikely but we keep the index tidy if it happens).
    rows = [r for r in rows if r.get('session_id') != entry.get('session_id')]
    rows.append(entry)
    rows.sort(key=lambda e: (e.get('start_ts') or 0), reverse=True)
    tmp = INDEX_PATH.with_suffix('.json.tmp')
    try:
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(rows, default=str, indent=2))
        os.replace(str(tmp), str(INDEX_PATH))
    except OSError as e:
        log.warning("index write failed: %s", e)


def main():
    rec = SessionRecorder()

    def _stop(sig, frame):
        rec.running = False

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    rec.start()


if __name__ == '__main__':
    main()

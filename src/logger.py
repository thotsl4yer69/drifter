#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Telemetry Logger + vehicle black box.

Continuously logs all DRIFTER MQTT traffic to timestamped JSONL, detects drive
sessions, and keeps a bounded in-memory pre-fault ring. Deterministic incident
triggers freeze the pre-fault context plus a post-fault tail into an evidence
bundle with a first-change timeline.
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import shutil
import signal
import threading
import time
from datetime import datetime
from pathlib import Path

from blackbox import IncidentBlackBox
from config import (
    BUFFER_FLUSH_INTERVAL,
    LOG_DIR,
    MAX_LOG_SIZE_MB,
    MQTT_HOST,
    MQTT_PORT,
    TOPICS,
    atomic_write_json,
    make_mqtt_client,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [LOGGER] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger(__name__)

buffer = []
_buffer_lock = threading.Lock()
current_file = None
current_date = None
message_count = 0
latest_dtcs: set[str] = set()

SESSION_DIR = LOG_DIR / "sessions"
INCIDENT_DIR = LOG_DIR / "incidents"
INCIDENT_TRIGGER_TOPIC = TOPICS.get("incident_trigger", "drifter/incident/trigger")
INCIDENT_STATUS_TOPIC = TOPICS.get("incident_status", "drifter/incident/status")
INCIDENT_EVENT_TOPIC = TOPICS.get("incident_event", "drifter/incident/event")

blackbox = IncidentBlackBox(
    INCIDENT_DIR,
    pre_seconds=float(os.getenv("DRIFTER_INCIDENT_PRE_SEC", "90")),
    post_seconds=float(os.getenv("DRIFTER_INCIDENT_POST_SEC", "45")),
    max_records=int(os.getenv("DRIFTER_INCIDENT_MAX_RECORDS", "15000")),
    cooldown_seconds=float(os.getenv("DRIFTER_INCIDENT_COOLDOWN_SEC", "90")),
)


def _extract_dtcs(payload: dict) -> set[str]:
    out: set[str] = set()
    if not isinstance(payload, dict):
        return out
    for key in ('stored', 'pending'):
        values = payload.get(key) or []
        if not isinstance(values, (list, tuple, set)):
            continue
        for value in values:
            code = value if isinstance(value, str) else value.get('code') if isinstance(value, dict) else None
            if code:
                out.add(str(code).strip().upper())
    return out


class DriveSession:
    """Tracks one engine-running session from RPM activity."""

    def __init__(self):
        self.active = False
        self.start_time = None
        self.end_time = None
        self.session_id = None
        self.max_rpm = 0
        self.max_speed = 0
        self.max_coolant = 0
        self.min_voltage: float | None = None
        self.distance_km = 0.0
        self.alert_count = 0
        self.highest_alert = 0
        self.dtcs_seen: set[str] = set()
        self.last_speed = 0
        self.last_speed_time = 0
        self.last_running_ts: float | None = None
        self.last_telemetry_ts: float | None = None

    def start(self, now: float | None = None):
        now = time.time() if now is None else float(now)
        self.active = True
        self.start_time = now
        self.session_id = datetime.fromtimestamp(now).strftime("%Y%m%d_%H%M%S")
        self.max_rpm = 0
        self.max_speed = 0
        self.max_coolant = 0
        self.min_voltage = None
        self.distance_km = 0.0
        self.alert_count = 0
        self.highest_alert = 0
        self.dtcs_seen.clear()
        self.last_speed = 0
        self.last_speed_time = now
        self.last_running_ts = now
        self.last_telemetry_ts = now
        log.info("Drive session started: %s", self.session_id)

    def stop(self):
        self.active = False
        self.end_time = time.time()
        log.info(
            "Drive session ended: %s (%s, %.1f km)",
            self.session_id,
            self.duration_str,
            self.distance_km,
        )

    def update_dtcs(self, payload: dict) -> None:
        """Accumulate DTCs observed at any point during the active drive."""
        if self.active:
            self.dtcs_seen.update(_extract_dtcs(payload))

    def update(self, topic, value, ts):
        if not self.active:
            return

        # Any fresh engine/vehicle PID proves the ECU stream is still alive.
        # This is deliberately separate from RPM so a slow K-line polling cycle
        # cannot split a real drive simply because RPM has not come around yet.
        if topic.startswith('drifter/engine/') or topic.startswith('drifter/vehicle/'):
            self.last_telemetry_ts = ts

        if topic.endswith('/rpm'):
            self.max_rpm = max(self.max_rpm, value)
        elif topic.endswith('/speed'):
            self.max_speed = max(self.max_speed, value)
            if self.last_speed_time:
                dt_hours = min(ts - self.last_speed_time, 5.0) / 3600.0
                if dt_hours > 0:
                    avg_speed = (self.last_speed + value) / 2.0
                    self.distance_km += avg_speed * dt_hours
            self.last_speed = value
            self.last_speed_time = ts
        elif topic.endswith('/coolant'):
            self.max_coolant = max(self.max_coolant, value)
        elif topic.endswith('/voltage'):
            if value > 0:
                self.min_voltage = value if self.min_voltage is None else min(self.min_voltage, value)
        elif topic.endswith('/alert/level'):
            level = int(value) if isinstance(value, (int, float)) else 0
            if level >= 2:
                self.alert_count += 1
            self.highest_alert = max(self.highest_alert, level)

    @property
    def duration_seconds(self):
        end = self.end_time or time.time()
        return end - (self.start_time or end)

    @property
    def duration_str(self):
        seconds = int(self.duration_seconds)
        hours, rem = divmod(seconds, 3600)
        minutes, seconds = divmod(rem, 60)
        return f"{hours}h{minutes:02d}m" if hours else f"{minutes}m{seconds:02d}s"

    def summary(self):
        return {
            'session_id': self.session_id,
            'start': self.start_time,
            'end': self.end_time,
            'duration_seconds': self.duration_seconds,
            'distance_km': round(self.distance_km, 1),
            'max_rpm': round(self.max_rpm),
            'max_speed': round(self.max_speed),
            'max_coolant': round(self.max_coolant, 1),
            'min_voltage': round(self.min_voltage, 2) if self.min_voltage is not None else None,
            'alert_count': self.alert_count,
            'highest_alert': self.highest_alert,
            'dtcs_seen': json.dumps(sorted(self.dtcs_seen)),
        }

    def save_summary(self):
        path = SESSION_DIR / f"session_{self.session_id}.json"
        atomic_write_json(path, self.summary())
        log.info("Session summary saved: %s", path.name)


session = DriveSession()
ENGINE_ON_RPM = 300
ENGINE_OFF_SECONDS = float(os.getenv("DRIFTER_ENGINE_OFF_SECONDS", "30"))


def get_log_file():
    global current_file, current_date
    today = datetime.now().strftime("%Y-%m-%d")
    if today != current_date:
        if current_file:
            current_file.close()
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        filepath = LOG_DIR / f"drive_{today}.jsonl"
        current_file = open(filepath, 'a')
        current_date = today
        log.info("Logging to: %s", filepath)
    return current_file


def flush_buffer():
    global buffer, message_count
    with _buffer_lock:
        to_flush = buffer
        buffer = []
    if not to_flush:
        return
    f = get_log_file()
    for entry in to_flush:
        f.write(json.dumps(entry) + '\n')
    f.flush()
    message_count += len(to_flush)


def compress_log(path: Path) -> Path:
    gz_path = Path(str(path) + '.gz')
    with open(path, 'rb') as f_in, gzip.open(gz_path, 'wb') as f_out:
        shutil.copyfileobj(f_in, f_out)
    path.unlink(missing_ok=True)
    log.info("Compressed: %s -> %s", path.name, gz_path.name)
    return gz_path


def cleanup_old_logs():
    today = datetime.now().strftime("%Y-%m-%d")
    for f in LOG_DIR.glob("*.jsonl"):
        if today not in f.name:
            compress_log(f)

    all_logs = sorted(LOG_DIR.glob("*.jsonl.gz"), key=lambda f: f.stat().st_mtime)
    total_size = 0
    for f in all_logs:
        try:
            total_size += f.stat().st_size
        except FileNotFoundError:
            pass
    total_mb = total_size / (1024 * 1024)

    while total_mb > MAX_LOG_SIZE_MB * 0.8 and all_logs:
        oldest = all_logs.pop(0)
        try:
            size = oldest.stat().st_size / (1024 * 1024)
        except FileNotFoundError:
            continue
        oldest.unlink(missing_ok=True)
        total_mb -= size
        log.info("Removed old log: %s (%.1f MB)", oldest.name, size)


def _publish_incident_status(client, event: dict | None = None):
    payload = blackbox.status()
    if event:
        payload["event"] = event
    client.publish(INCIDENT_STATUS_TOPIC, json.dumps(payload), qos=1, retain=True)


def _process_blackbox(client, topic: str, data, ts: float):
    event = blackbox.ingest(topic, data, ts)
    if event:
        log.warning("BLACK BOX trigger: %s", event)
        _publish_incident_status(client, event)


def on_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return

    ts = time.time()
    with _buffer_lock:
        buffer.append({'topic': msg.topic, 'data': data, 'ts': ts})

    if msg.topic == TOPICS['dtc'] and isinstance(data, dict):
        latest_dtcs.clear()
        latest_dtcs.update(_extract_dtcs(data))
        session.update_dtcs(data)

    if msg.topic == TOPICS['alert_level'] and isinstance(data, dict):
        try:
            alert_level = float(data.get('level'))
        except (TypeError, ValueError):
            alert_level = None
        if alert_level is not None:
            session.update(msg.topic, alert_level, ts)

    value = data.get('value') if isinstance(data, dict) else None
    if value is not None:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            numeric = None
        if numeric is not None:
            # Start the session before applying the first running RPM so max RPM
            # and liveness evidence do not discard the sample that created it.
            if msg.topic.endswith('/rpm'):
                detect_session_change(numeric, client, now=ts)
            session.update(msg.topic, numeric, ts)

    _process_blackbox(client, msg.topic, data, ts)


def _finish_session(mqtt_client, *, now: float | None = None):
    """Finalize one active drive session and publish its end event once."""
    global session
    if not session.active:
        return
    now = time.time() if now is None else float(now)
    session.stop()
    session.end_time = now
    session.save_summary()
    incident_status = blackbox.status()
    end_payload = {
        'event': 'end',
        **session.summary(),
        'incident_active': bool(incident_status.get('active')),
    }
    if incident_status.get('active') and incident_status.get('end_at') is not None:
        end_payload['incident_end_at'] = incident_status['end_at']
    try:
        mqtt_client.publish(
            TOPICS.get('drive_session', 'drifter/session'),
            json.dumps(end_payload),
        )
    except Exception:
        pass


def detect_session_change(rpm, mqtt_client, *, now: float | None = None):
    """Track engine-run sessions by elapsed time, independent of PID poll rate."""
    global session
    now = time.time() if now is None else float(now)

    if rpm > ENGINE_ON_RPM:
        if not session.active:
            session.start(now)
            session.dtcs_seen.update(latest_dtcs)
            try:
                mqtt_client.publish(
                    TOPICS.get('drive_session', 'drifter/session'),
                    json.dumps({
                        'event': 'start',
                        'session_id': session.session_id,
                        'ts': now,
                    }),
                )
            except Exception:
                pass
        else:
            session.last_running_ts = now
            session.last_telemetry_ts = now
        return

    if not session.active:
        return
    if session.last_running_ts is None:
        session.last_running_ts = now
        return
    if now - session.last_running_ts < ENGINE_OFF_SECONDS:
        return

    _finish_session(mqtt_client, now=now)


def _finalize_incident_if_ready(client, *, force: bool = False):
    summary = blackbox.finalize(force=force)
    if not summary:
        return
    log.warning(
        "BLACK BOX saved: %s records=%s first=%s",
        summary.get("id"),
        summary.get("record_count"),
        (summary.get("first_change") or {}).get("sensor"),
    )
    client.publish(INCIDENT_EVENT_TOPIC, json.dumps(summary), qos=1, retain=True)
    _publish_incident_status(client)


def main():
    global current_file
    log.info("DRIFTER Telemetry Logger + Black Box starting...")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    INCIDENT_DIR.mkdir(parents=True, exist_ok=True)

    running = True

    def _handle_signal(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    client = make_mqtt_client("drifter-logger")
    client.on_message = on_message

    connected = False
    while not connected and running:
        try:
            client.connect(MQTT_HOST, MQTT_PORT, 60)
            connected = True
        except Exception as exc:
            log.warning("Waiting for MQTT broker... (%s)", exc)
            time.sleep(3)

    if not running:
        return

    client.subscribe("drifter/#")
    client.loop_start()

    log.info("Logging to %s", LOG_DIR)
    log.info(
        "Black Box LIVE — %.0fs pre / %.0fs post -> %s",
        blackbox.pre_seconds,
        blackbox.post_seconds,
        INCIDENT_DIR,
    )
    _publish_incident_status(client)

    last_flush = time.monotonic()
    last_cleanup = time.monotonic()
    last_status = time.monotonic()

    while running:
        now = time.monotonic()
        if now - last_flush >= BUFFER_FLUSH_INTERVAL:
            flush_buffer()
            last_flush = now

        # K-line ECUs can go silent immediately at key-off, with no RPM=0
        # sample. Use absence of *all* engine/vehicle telemetry as the silent
        # fallback instead of absence of RPM alone, so a slow PID cycle cannot
        # incorrectly split a live drive.
        if (
            session.active
            and session.last_telemetry_ts is not None
            and time.time() - session.last_telemetry_ts >= ENGINE_OFF_SECONDS
        ):
            _finish_session(client)

        _finalize_incident_if_ready(client)

        if now - last_status >= 5:
            _publish_incident_status(client)
            last_status = now

        if now - last_cleanup >= 3600:
            cleanup_old_logs()
            last_cleanup = now

        time.sleep(0.25)

    flush_buffer()
    _finalize_incident_if_ready(client, force=True)
    if session.active:
        _finish_session(client)
    if current_file:
        current_file.close()
    client.loop_stop()
    client.disconnect()
    log.info("Logger stopped. Total messages logged: %s", message_count)


if __name__ == '__main__':
    main()

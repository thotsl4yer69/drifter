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

SESSION_DIR = LOG_DIR / "sessions"
INCIDENT_DIR = LOG_DIR / "incidents"
INCIDENT_TRIGGER_TOPIC = TOPICS.get("incident_trigger", "drifter/incident/trigger")
INCIDENT_STATUS_TOPIC = TOPICS.get("incident_status", "drifter/incident/status")
INCIDENT_EVENT_TOPIC = TOPICS.get("incident_event", "drifter/incident/event")
INCIDENT_MIN_KEEP = max(1, int(os.getenv("DRIFTER_INCIDENT_MIN_KEEP", "8")))
STALE_TEMP_SECONDS = max(300.0, float(os.getenv("DRIFTER_STALE_TEMP_SECONDS", "3600")))

blackbox = IncidentBlackBox(
    INCIDENT_DIR,
    pre_seconds=float(os.getenv("DRIFTER_INCIDENT_PRE_SEC", "90")),
    post_seconds=float(os.getenv("DRIFTER_INCIDENT_POST_SEC", "45")),
    max_records=int(os.getenv("DRIFTER_INCIDENT_MAX_RECORDS", "15000")),
    cooldown_seconds=float(os.getenv("DRIFTER_INCIDENT_COOLDOWN_SEC", "90")),
)


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
        self.min_voltage = 99.0
        self.distance_km = 0.0
        self.alert_count = 0
        self.highest_alert = 0
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
        self.min_voltage = 99.0
        self.distance_km = 0.0
        self.alert_count = 0
        self.highest_alert = 0
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

    def update(self, topic, value, ts):
        if not self.active:
            return

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
                self.min_voltage = min(self.min_voltage, value)
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
            'min_voltage': round(self.min_voltage, 2),
            'alert_count': self.alert_count,
            'highest_alert': self.highest_alert,
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


def _safe_stat(path: Path) -> tuple[int, float] | None:
    try:
        stat = path.stat()
        return stat.st_size, stat.st_mtime
    except FileNotFoundError:
        return None


def _incident_bundles() -> list[tuple[float, int, list[Path]]]:
    """Return finalized incident files grouped so data+summary prune together."""
    groups: dict[str, list[Path]] = {}
    for path in INCIDENT_DIR.glob("incident_*"):
        name = path.name
        if ".tmp." in name:
            continue
        if name.endswith(".jsonl.gz"):
            key = name[:-9]
        elif name.endswith(".json"):
            key = name[:-5]
        else:
            continue
        groups.setdefault(key, []).append(path)

    bundles: list[tuple[float, int, list[Path]]] = []
    for paths in groups.values():
        size = 0
        mtimes = []
        live_paths = []
        for path in paths:
            info = _safe_stat(path)
            if info is None:
                continue
            file_size, mtime = info
            size += file_size
            mtimes.append(mtime)
            live_paths.append(path)
        if live_paths:
            bundles.append((max(mtimes), size, live_paths))
    return sorted(bundles, key=lambda item: item[0])


def cleanup_old_logs():
    """Bound telemetry + finalized incident evidence under the logger quota.

    The active daily JSONL is never deleted.  Finalized incident data and its
    JSON summary are treated as one bundle, and the newest incident bundles are
    protected even when old telemetry must be pruned first.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    INCIDENT_DIR.mkdir(parents=True, exist_ok=True)

    for path in LOG_DIR.glob("*.jsonl"):
        if today not in path.name:
            compress_log(path)

    now = time.time()
    for path in INCIDENT_DIR.glob("*.tmp.*"):
        info = _safe_stat(path)
        if info is not None and now - info[1] >= STALE_TEMP_SECONDS:
            path.unlink(missing_ok=True)
            log.info("Removed stale incident temp: %s", path.name)

    bundles = _incident_bundles()
    protected_incidents = bundles[-INCIDENT_MIN_KEEP:] if bundles else []
    protected_paths = {
        path for _mtime, _size, paths in protected_incidents for path in paths
    }

    managed_paths: set[Path] = set(LOG_DIR.glob("*.jsonl"))
    managed_paths.update(LOG_DIR.glob("*.jsonl.gz"))
    managed_paths.update(SESSION_DIR.glob("session_*.json"))
    for _mtime, _size, paths in bundles:
        managed_paths.update(paths)

    total_bytes = 0
    for path in managed_paths:
        info = _safe_stat(path)
        if info is not None:
            total_bytes += info[0]

    target_bytes = int(MAX_LOG_SIZE_MB * 0.8 * 1024 * 1024)
    if total_bytes <= target_bytes:
        return

    deletion_units: list[tuple[float, int, list[Path], str]] = []
    for path in LOG_DIR.glob("*.jsonl.gz"):
        info = _safe_stat(path)
        if info is not None:
            deletion_units.append((info[1], info[0], [path], "telemetry"))

    for mtime, size, paths in bundles:
        if any(path in protected_paths for path in paths):
            continue
        deletion_units.append((mtime, size, paths, "incident"))

    deletion_units.sort(key=lambda item: item[0])
    for _mtime, size, paths, kind in deletion_units:
        if total_bytes <= target_bytes:
            break
        for path in paths:
            path.unlink(missing_ok=True)
        total_bytes -= size
        log.info(
            "Removed old %s evidence: %s (%.1f MB)",
            kind,
            ", ".join(path.name for path in paths),
            size / (1024 * 1024),
        )

    if total_bytes > target_bytes:
        log.warning(
            "Evidence store remains above target after safe pruning: %.1f MB > %.1f MB",
            total_bytes / (1024 * 1024),
            target_bytes / (1024 * 1024),
        )


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

    value = data.get('value') if isinstance(data, dict) else None
    if value is not None:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            numeric = None
        if numeric is not None:
            session.update(msg.topic, numeric, ts)
            if msg.topic.endswith('/rpm'):
                detect_session_change(numeric, client, now=ts)

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
    try:
        mqtt_client.publish(
            TOPICS.get('drive_session', 'drifter/session'),
            json.dumps({'event': 'end', **session.summary()}),
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
    cleanup_old_logs()

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

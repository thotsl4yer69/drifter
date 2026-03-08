#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Telemetry Logger
Logs all vehicle data to timestamped JSONL files.
Detects drive sessions (ignition on/off) and generates per-drive summaries.
Compresses old logs and manages storage.
UNCAGED TECHNOLOGY — EST 1991
"""

import json
import time
import gzip
import shutil
import signal
import logging
from datetime import datetime
from pathlib import Path
from collections import deque
import paho.mqtt.client as mqtt

from config import (
    MQTT_HOST, MQTT_PORT, LOG_DIR, TOPICS,
    BUFFER_FLUSH_INTERVAL, MAX_LOG_SIZE_MB
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [LOGGER] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

buffer = []
current_file = None
current_date = None
message_count = 0

# ── Drive Session Detection ──
SESSION_DIR = LOG_DIR / "sessions"

class DriveSession:
    """Tracks a single drive session from ignition-on to ignition-off."""

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
        self.rpm_history = deque(maxlen=600)  # Last 600 RPM readings (~60s at 10Hz)

    def start(self):
        self.active = True
        self.start_time = time.time()
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.max_rpm = 0
        self.max_speed = 0
        self.max_coolant = 0
        self.min_voltage = 99.0
        self.distance_km = 0.0
        self.alert_count = 0
        self.highest_alert = 0
        self.last_speed = 0
        self.last_speed_time = time.time()
        log.info(f"Drive session started: {self.session_id}")

    def stop(self):
        self.active = False
        self.end_time = time.time()
        log.info(f"Drive session ended: {self.session_id} "
                 f"({self.duration_str}, {self.distance_km:.1f} km)")

    def update(self, topic, value, ts):
        if not self.active:
            return

        if topic.endswith('/rpm'):
            self.max_rpm = max(self.max_rpm, value)
            self.rpm_history.append(value)
        elif topic.endswith('/speed'):
            self.max_speed = max(self.max_speed, value)
            # Estimate distance: speed (km/h) × time (h)
            now = time.time()
            dt_hours = (now - self.last_speed_time) / 3600.0
            avg_speed = (self.last_speed + value) / 2.0
            self.distance_km += avg_speed * dt_hours
            self.last_speed = value
            self.last_speed_time = now
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
        s = int(self.duration_seconds)
        h, m = divmod(s, 3600)
        m, s = divmod(m, 60)
        if h:
            return f"{h}h{m:02d}m"
        return f"{m}m{s:02d}s"

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
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        path = SESSION_DIR / f"session_{self.session_id}.json"
        with open(path, 'w') as f:
            json.dump(self.summary(), f, indent=2)
        log.info(f"Session summary saved: {path.name}")


session = DriveSession()
ENGINE_ON_RPM = 300       # RPM above this = engine running
ENGINE_OFF_SAMPLES = 600  # Samples below threshold = engine off (~60s at 10Hz)


def get_log_file():
    """Get or create today's log file."""
    global current_file, current_date

    today = datetime.now().strftime("%Y-%m-%d")
    if today != current_date:
        if current_file:
            current_file.close()
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        filepath = LOG_DIR / f"drive_{today}.jsonl"
        current_file = open(filepath, 'a')
        current_date = today
        log.info(f"Logging to: {filepath}")

    return current_file


def flush_buffer():
    """Write buffered data to disk."""
    global buffer, message_count

    if not buffer:
        return

    f = get_log_file()
    for entry in buffer:
        f.write(json.dumps(entry) + '\n')
    f.flush()

    count = len(buffer)
    message_count += count
    buffer = []
    log.debug(f"Flushed {count} records (total: {message_count})")


def compress_log(path: Path) -> Path:
    """Gzip-compress a log file and remove the original."""
    gz_path = Path(str(path) + '.gz')
    with open(path, 'rb') as f_in, gzip.open(gz_path, 'wb') as f_out:
        shutil.copyfileobj(f_in, f_out)
    path.unlink()
    log.info(f"Compressed: {path.name} → {gz_path.name}")
    return gz_path


def cleanup_old_logs():
    """Compress yesterday's logs; remove oldest compressed logs if over storage limit."""
    today = datetime.now().strftime("%Y-%m-%d")

    # Compress any uncompressed log files that aren't today's
    for f in LOG_DIR.glob("*.jsonl"):
        if today not in f.name:
            compress_log(f)

    # If still over storage limit, remove oldest compressed logs
    all_logs = sorted(
        LOG_DIR.glob("*.jsonl.gz"), key=lambda f: f.stat().st_mtime
    )
    total_size = sum(f.stat().st_size for f in all_logs)
    total_mb = total_size / (1024 * 1024)

    while total_mb > MAX_LOG_SIZE_MB * 0.8 and all_logs:
        oldest = all_logs.pop(0)
        size = oldest.stat().st_size / (1024 * 1024)
        oldest.unlink()
        total_mb -= size
        log.info(f"Removed old log: {oldest.name} ({size:.1f} MB)")


def on_message(client, userdata, msg):
    """Buffer incoming telemetry and update drive session."""
    try:
        data = json.loads(msg.payload)
        buffer.append({
            'topic': msg.topic,
            'data': data,
            'ts': time.time()
        })

        # Update drive session tracking
        value = data.get('value')
        if value is not None:
            session.update(msg.topic, value, time.time())

            # Drive session detection based on RPM
            if msg.topic.endswith('/rpm'):
                detect_session_change(value, client)

    except json.JSONDecodeError:
        pass


def detect_session_change(rpm, mqtt_client):
    """Detect engine on/off transitions."""
    global session

    if rpm > ENGINE_ON_RPM and not session.active:
        session.start()
        # Publish session start
        try:
            mqtt_client.publish(TOPICS.get('drive_session', 'drifter/session'),
                                json.dumps({
                                    'event': 'start',
                                    'session_id': session.session_id,
                                    'ts': time.time()
                                }))
        except Exception:
            pass

    elif rpm < ENGINE_ON_RPM and session.active:
        # Check if RPM has been low for ENGINE_OFF_SAMPLES
        if session.rpm_history:
            recent = list(session.rpm_history)[-ENGINE_OFF_SAMPLES:]
            if len(recent) >= ENGINE_OFF_SAMPLES and all(r < ENGINE_ON_RPM for r in recent):
                session.stop()
                session.save_summary()
                # Publish session end
                try:
                    mqtt_client.publish(
                        TOPICS.get('drive_session', 'drifter/session'),
                        json.dumps({
                            'event': 'end',
                            **session.summary()
                        }))
                except Exception:
                    pass


def main():
    log.info("DRIFTER Telemetry Logger starting...")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    SESSION_DIR.mkdir(parents=True, exist_ok=True)

    running = True

    def _handle_signal(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    client = mqtt.Client(client_id="drifter-logger")
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

    # Subscribe to everything from DRIFTER
    client.subscribe("drifter/#")
    client.loop_start()

    log.info(f"Logging to {LOG_DIR}")
    log.info(f"Drive sessions to {SESSION_DIR}")
    log.info("Telemetry Logger is LIVE")

    last_flush = time.monotonic()
    last_cleanup = time.monotonic()

    while running:
        now = time.monotonic()

        if now - last_flush >= BUFFER_FLUSH_INTERVAL:
            flush_buffer()
            last_flush = now

        # Cleanup check every hour
        if now - last_cleanup >= 3600:
            cleanup_old_logs()
            last_cleanup = now

        time.sleep(1)

    # Final flush and session save
    flush_buffer()
    if session.active:
        session.stop()
        session.save_summary()
    if current_file:
        current_file.close()
    client.loop_stop()
    client.disconnect()
    log.info(f"Logger stopped. Total messages logged: {message_count}")


if __name__ == '__main__':
    main()

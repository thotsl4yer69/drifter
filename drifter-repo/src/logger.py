#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Telemetry Logger
Logs all vehicle data to timestamped JSON files on NVMe.
Syncs to nanob when home network is available.
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
import paho.mqtt.client as mqtt

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [LOGGER] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

LOG_DIR = Path("/opt/drifter/logs")
BUFFER_FLUSH_INTERVAL = 30  # Write to disk every 30 seconds
MAX_LOG_SIZE_MB = 500       # Max total log storage

buffer = []
current_file = None
current_date = None
message_count = 0


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
    """Buffer incoming telemetry."""
    try:
        data = json.loads(msg.payload)
        buffer.append({
            'topic': msg.topic,
            'data': data,
            'ts': time.time()
        })
    except json.JSONDecodeError:
        pass


def main():
    log.info("DRIFTER Telemetry Logger starting...")
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    running = True

    def _handle_signal(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    client = mqtt.Client(client_id="drifter-logger")
    client.on_message = on_message

    connected = False
    while not connected:
        try:
            client.connect("localhost", 1883, 60)
            connected = True
        except Exception as e:
            log.warning(f"Waiting for MQTT broker... ({e})")
            time.sleep(3)

    # Subscribe to everything from DRIFTER
    client.subscribe("drifter/#")
    client.loop_start()

    log.info(f"Logging to {LOG_DIR}")
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

    flush_buffer()
    if current_file:
        current_file.close()
    client.loop_stop()
    client.disconnect()
    log.info(f"Logger stopped. Total messages logged: {message_count}")


if __name__ == '__main__':
    main()

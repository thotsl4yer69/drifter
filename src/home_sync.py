#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Home Network Sync
Detects home network and bridges telemetry to nanob (192.168.1.159).
Also syncs drive logs and session summaries when home.
UNCAGED TECHNOLOGY — EST 1991
"""

import json
import time
import signal
import subprocess
import logging
from pathlib import Path
import paho.mqtt.client as mqtt

from config import (
    MQTT_HOST, MQTT_PORT, NANOB_HOST, NANOB_PORT, NANOB_USER,
    HOME_CHECK_INTERVAL, LOG_DIR, CALIBRATION_FILE
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [SYNC] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

SESSION_DIR = LOG_DIR / "sessions"
SYNC_STATE_FILE = LOG_DIR / ".sync_state.json"
LOG_SYNC_INTERVAL = 300     # Sync logs every 5 minutes when home
NANOB_LOG_PATH = "/opt/sentient/vehicle/drifter/logs"

home_client = None
is_home = False
last_log_sync = 0


def check_home_network():
    """Check if nanob is reachable."""
    try:
        result = subprocess.run(
            ['ping', '-c', '1', '-W', '2', NANOB_HOST],
            capture_output=True, timeout=5
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, Exception):
        return False


def connect_home():
    """Connect to nanob MQTT broker."""
    global home_client
    try:
        home_client = mqtt.Client(client_id="drifter-sync")
        home_client.username_pw_set(NANOB_USER)
        home_client.connect(NANOB_HOST, NANOB_PORT, 60)
        home_client.loop_start()
        log.info(f"Connected to nanob at {NANOB_HOST}")
        return True
    except Exception as e:
        log.warning(f"Failed to connect to nanob: {e}")
        home_client = None
        return False


def disconnect_home():
    """Disconnect from nanob."""
    global home_client
    if home_client:
        try:
            home_client.loop_stop()
            home_client.disconnect()
        except Exception:
            pass
        home_client = None
        log.info("Disconnected from nanob")


def load_sync_state():
    """Load record of which files have been synced."""
    if SYNC_STATE_FILE.exists():
        try:
            with open(SYNC_STATE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {'synced_files': [], 'last_sync': 0}


def save_sync_state(state):
    """Save sync state to disk."""
    try:
        with open(SYNC_STATE_FILE, 'w') as f:
            json.dump(state, f)
    except IOError as e:
        log.warning(f"Failed to save sync state: {e}")


def sync_logs():
    """Rsync compressed logs and session summaries to nanob."""
    global last_log_sync

    now = time.time()
    if now - last_log_sync < LOG_SYNC_INTERVAL:
        return

    last_log_sync = now
    sync_state = load_sync_state()
    synced = set(sync_state.get('synced_files', []))
    new_synced = []

    # Sync compressed log files (batched into a single rsync call)
    unsynced_logs = [f for f in sorted(LOG_DIR.glob("*.jsonl.gz")) if f.name not in synced]
    if unsynced_logs:
        new_synced.extend(_rsync_batch(unsynced_logs, f"{NANOB_LOG_PATH}/"))

    # Sync session summaries (batched into a single rsync call)
    if SESSION_DIR.exists():
        unsynced_sessions = [f for f in sorted(SESSION_DIR.glob("session_*.json")) if f.name not in synced]
        if unsynced_sessions:
            new_synced.extend(_rsync_batch(unsynced_sessions, f"{NANOB_LOG_PATH}/sessions/"))

    # Sync calibration file
    if CALIBRATION_FILE.exists() and CALIBRATION_FILE.name not in synced:
        if _rsync_file(CALIBRATION_FILE, f"{NANOB_LOG_PATH}/"):
            new_synced.append(CALIBRATION_FILE.name)

    if new_synced:
        synced.update(new_synced)
        sync_state['synced_files'] = list(synced)
        sync_state['last_sync'] = now
        save_sync_state(sync_state)
        log.info(f"Synced {len(new_synced)} files to nanob")


def _rsync_batch(local_files, remote_dir):
    """Rsync multiple files to nanob in one call. Returns list of synced filenames."""
    if not local_files:
        return []
    remote = f"{NANOB_USER}@{NANOB_HOST}:{remote_dir}"
    file_args = [str(f) for f in local_files]
    try:
        result = subprocess.run(
            ['rsync', '-az', '--timeout=30'] + file_args + [remote],
            capture_output=True, timeout=120
        )
        if result.returncode == 0:
            log.debug(f"Batch synced {len(local_files)} files")
            return [f.name for f in local_files]
        else:
            log.debug(f"Batch rsync failed: {result.stderr.decode()[:200]}")
            return []
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        log.debug(f"Batch rsync error: {e}")
        return []


def _rsync_file(local_path, remote_dir):
    """Rsync a single file to nanob. Returns True on success."""
    remote = f"{NANOB_USER}@{NANOB_HOST}:{remote_dir}"
    try:
        result = subprocess.run(
            ['rsync', '-az', '--timeout=10', str(local_path), remote],
            capture_output=True, timeout=30
        )
        if result.returncode == 0:
            log.debug(f"Synced: {local_path.name}")
            return True
        else:
            log.debug(f"Rsync failed for {local_path.name}: {result.stderr.decode()[:100]}")
            return False
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        log.debug(f"Rsync error for {local_path.name}: {e}")
        return False


def on_local_message(client, userdata, msg):
    """Forward drifter messages to nanob."""
    global home_client
    if not (home_client and is_home):
        return
    try:
        topic = msg.topic
        # Only rewrite the leading "drifter/" prefix. str.replace rewrites
        # every occurrence, which would mangle any topic that legitimately
        # contains the substring "drifter/" past the first segment.
        if topic.startswith("drifter/"):
            remote_topic = "sentient/vehicle/drifter/" + topic[len("drifter/"):]
        else:
            remote_topic = topic
        home_client.publish(remote_topic, msg.payload)
    except Exception as e:
        log.debug(f"Forward to nanob failed for {msg.topic}: {e}")


def main():
    global is_home

    log.info("DRIFTER Home Sync starting...")

    running = True

    def _handle_signal(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    # Connect to local broker
    local = mqtt.Client(client_id="drifter-homesync")
    local.on_message = on_local_message

    connected = False
    while not connected and running:
        try:
            local.connect(MQTT_HOST, MQTT_PORT, 60)
            connected = True
        except Exception as e:
            log.warning(f"Waiting for local MQTT... ({e})")
            time.sleep(3)

    if not running:
        return

    local.subscribe("drifter/#")
    local.loop_start()

    log.info("Home Sync is LIVE — checking for home network")

    while running:
        reachable = check_home_network()

        if reachable and not is_home:
            log.info("Home network detected — connecting to nanob")
            if connect_home():
                is_home = True
                # Announce presence
                if home_client:
                    home_client.publish(
                        "sentient/vehicle/drifter/status",
                        json.dumps({"state": "home", "ts": time.time()}),
                        retain=True
                    )
                # Start syncing logs
                sync_logs()

        elif reachable and is_home:
            # Periodically sync logs while home
            sync_logs()

        elif not reachable and is_home:
            log.info("Home network lost — switching to autonomous mode")
            disconnect_home()
            is_home = False

        time.sleep(HOME_CHECK_INTERVAL)

    disconnect_home()
    local.loop_stop()
    local.disconnect()
    log.info("Home Sync stopped")


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Home Network Sync
Detects home network and bridges telemetry to nanob (192.168.1.159).
UNCAGED TECHNOLOGY — EST 1991
"""

import json
import time
import signal
import subprocess
import logging
import paho.mqtt.client as mqtt

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [SYNC] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

NANOB_HOST = "192.168.1.159"
NANOB_PORT = 1883
NANOB_USER = "sentient"
HOME_CHECK_INTERVAL = 30  # Check for home network every 30s

home_client = None
is_home = False


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


def on_local_message(client, userdata, msg):
    """Forward drifter messages to nanob."""
    global home_client
    if home_client and is_home:
        try:
            # Republish under sentient namespace
            remote_topic = msg.topic.replace("drifter/", "sentient/vehicle/drifter/")
            home_client.publish(remote_topic, msg.payload)
        except Exception:
            pass


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
    while not connected:
        try:
            local.connect("localhost", 1883, 60)
            connected = True
        except Exception as e:
            log.warning(f"Waiting for local MQTT... ({e})")
            time.sleep(3)

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

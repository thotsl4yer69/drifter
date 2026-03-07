#!/usr/bin/env python3
"""
MZ1312 DRIFTER — CAN Bridge
Reads OBD-II data from USB2CANFD and publishes to MQTT.
UNCAGED TECHNOLOGY — EST 1991
"""

import can
import json
import time
import struct
import logging
import paho.mqtt.client as mqtt
from collections import deque

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [CANBRIDGE] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

# ── OBD-II PID Definitions ──
# Standard OBD-II PIDs we poll via CAN (Mode 01)
PIDS = {
    0x0C: {'name': 'rpm',      'topic': 'drifter/engine/rpm',      'decode': lambda a, b: ((a * 256) + b) / 4.0,      'unit': 'rpm',  'hz': 10},
    0x05: {'name': 'coolant',  'topic': 'drifter/engine/coolant',  'decode': lambda a, b=0: a - 40,                   'unit': 'C',    'hz': 1},
    0x06: {'name': 'stft1',    'topic': 'drifter/engine/stft1',    'decode': lambda a, b=0: round((a / 1.28) - 100, 2), 'unit': '%', 'hz': 5},
    0x08: {'name': 'stft2',    'topic': 'drifter/engine/stft2',    'decode': lambda a, b=0: round((a / 1.28) - 100, 2), 'unit': '%', 'hz': 5},
    0x04: {'name': 'load',     'topic': 'drifter/engine/load',     'decode': lambda a, b=0: round(a / 2.55, 1),       'unit': '%',    'hz': 5},
    0x0D: {'name': 'speed',    'topic': 'drifter/vehicle/speed',   'decode': lambda a, b=0: a,                         'unit': 'km/h', 'hz': 5},
    0x11: {'name': 'throttle', 'topic': 'drifter/engine/throttle', 'decode': lambda a, b=0: round(a / 2.55, 1),       'unit': '%',    'hz': 10},
    0x42: {'name': 'voltage',  'topic': 'drifter/power/voltage',   'decode': lambda a, b: round(((a * 256) + b) / 1000.0, 2), 'unit': 'V', 'hz': 1},
}

# OBD-II CAN IDs
OBD_REQUEST_ID = 0x7DF   # Broadcast request
OBD_RESPONSE_BASE = 0x7E8  # ECU response range 0x7E8-0x7EF

# ── MQTT Setup ──
MQTT_HOST = "localhost"
MQTT_PORT = 1883

# ── State ──
latest_values = {}
running = True


def find_can_interface():
    """Auto-detect the USB2CANFD interface."""
    # Try common interface names
    for iface in ['can0', 'can1', 'slcan0', 'vcan0']:
        try:
            bus = can.Bus(interface='socketcan', channel=iface, bitrate=500000)
            bus.shutdown()
            log.info(f"Found CAN interface: {iface}")
            return iface
        except (can.CanError, OSError):
            continue

    # If no socketcan found, try the USB serial route (slcan)
    import glob
    usb_devs = glob.glob('/dev/ttyACM*') + glob.glob('/dev/ttyUSB*')
    for dev in usb_devs:
        try:
            import subprocess
            # Try to set up slcan
            subprocess.run(
                ['slcand', '-o', '-s6', '-t', 'hw', dev, 'slcan0'],
                timeout=5, capture_output=True
            )
            subprocess.run(['ip', 'link', 'set', 'up', 'slcan0'],
                           timeout=5, capture_output=True)
            time.sleep(1)
            bus = can.Bus(interface='socketcan', channel='slcan0', bitrate=500000)
            bus.shutdown()
            log.info(f"Created slcan interface from {dev}")
            return 'slcan0'
        except Exception:
            continue

    return None


def send_obd_request(bus, pid):
    """Send a standard OBD-II request for a given PID."""
    # Mode 01 request: [number_of_bytes, mode, pid, padding...]
    data = [0x02, 0x01, pid, 0x00, 0x00, 0x00, 0x00, 0x00]
    msg = can.Message(
        arbitration_id=OBD_REQUEST_ID,
        data=data,
        is_extended_id=False
    )
    try:
        bus.send(msg)
        return True
    except can.CanError as e:
        log.warning(f"CAN send error for PID 0x{pid:02X}: {e}")
        return False


def decode_obd_response(msg):
    """Decode an OBD-II response message."""
    if msg.arbitration_id < OBD_RESPONSE_BASE or msg.arbitration_id > 0x7EF:
        return None

    data = msg.data
    if len(data) < 4:
        return None

    # Check it's a Mode 01 response (0x41)
    if data[1] != 0x41:
        return None

    pid = data[2]
    if pid not in PIDS:
        return None

    pid_def = PIDS[pid]
    try:
        if pid in (0x0C, 0x42):  # Two-byte values
            value = pid_def['decode'](data[3], data[4])
        else:  # Single-byte values
            value = pid_def['decode'](data[3])
        return pid, value
    except (IndexError, ValueError) as e:
        log.warning(f"Decode error for PID 0x{pid:02X}: {e}")
        return None


def main():
    global running, latest_values

    # ── Find CAN Interface ──
    log.info("Searching for CAN interface...")
    iface = None
    while iface is None and running:
        iface = find_can_interface()
        if iface is None:
            log.warning("No CAN interface found. Retrying in 5s...")
            log.warning("Is USB2CANFD plugged in? Run: sudo ip link set can0 up type can bitrate 500000")
            time.sleep(5)

    # ── Connect to CAN Bus ──
    log.info(f"Connecting to {iface} at 500 kbps...")
    bus = can.Bus(interface='socketcan', channel=iface, bitrate=500000)

    # ── Connect to MQTT ──
    mqtt_client = mqtt.Client(client_id="drifter-canbridge")
    mqtt_connected = False
    while not mqtt_connected and running:
        try:
            mqtt_client.connect(MQTT_HOST, MQTT_PORT, 60)
            mqtt_client.loop_start()
            mqtt_connected = True
            log.info("Connected to MQTT broker")
        except Exception as e:
            log.warning(f"MQTT connect failed: {e}. Retrying in 3s...")
            time.sleep(3)

    # ── Publish system status ──
    mqtt_client.publish("drifter/system/status", json.dumps({
        "state": "online",
        "can_interface": iface,
        "timestamp": time.time()
    }), retain=True)

    # ── Polling Loop ──
    # Build schedule: list of (pid, interval_seconds)
    schedule = []
    for pid, info in PIDS.items():
        schedule.append({
            'pid': pid,
            'interval': 1.0 / info['hz'],
            'last_poll': 0,
            'info': info
        })

    log.info(f"Polling {len(schedule)} PIDs from Jaguar X-Type...")
    log.info("DRIFTER CAN Bridge is LIVE")

    poll_index = 0
    while running:
        try:
            now = time.monotonic()

            # Find the next PID that's due
            for entry in schedule:
                if now - entry['last_poll'] >= entry['interval']:
                    send_obd_request(bus, entry['pid'])
                    entry['last_poll'] = now

                    # Wait for response (timeout 50ms)
                    response = bus.recv(timeout=0.05)
                    if response:
                        result = decode_obd_response(response)
                        if result:
                            pid, value = result
                            info = PIDS[pid]
                            latest_values[info['name']] = value

                            # Publish individual value
                            mqtt_client.publish(info['topic'], json.dumps({
                                'value': value,
                                'unit': info['unit'],
                                'ts': time.time()
                            }))

                    break  # Only poll one PID per loop iteration

            # Publish combined snapshot every second
            if latest_values and int(now) % 1 == 0:
                mqtt_client.publish("drifter/snapshot", json.dumps({
                    **latest_values,
                    'ts': time.time()
                }))

            # Small sleep to prevent CPU spin
            time.sleep(0.005)

        except can.CanError as e:
            log.error(f"CAN bus error: {e}")
            time.sleep(1)
        except KeyboardInterrupt:
            running = False

    # ── Cleanup ──
    log.info("Shutting down CAN Bridge...")
    mqtt_client.publish("drifter/system/status", json.dumps({
        "state": "offline",
        "timestamp": time.time()
    }), retain=True)
    mqtt_client.loop_stop()
    mqtt_client.disconnect()
    bus.shutdown()


if __name__ == '__main__':
    main()

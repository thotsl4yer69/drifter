#!/usr/bin/env python3
"""
MZ1312 DRIFTER — CAN Bridge
Reads OBD-II data from USB2CANFD and publishes to MQTT.
Supports Mode 01 (live data), Mode 03 (stored DTCs), Mode 07 (pending DTCs).
UNCAGED TECHNOLOGY — EST 1991
"""

import can
import json
import time
import signal
import logging
import paho.mqtt.client as mqtt
from collections import deque

from config import (
    MQTT_HOST, MQTT_PORT, CAN_BITRATE,
    OBD_REQUEST_ID, OBD_RESPONSE_BASE, OBD_RESPONSE_END, TOPICS
)

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
    0x07: {'name': 'ltft1',    'topic': 'drifter/engine/ltft1',    'decode': lambda a, b=0: round((a / 1.28) - 100, 2), 'unit': '%', 'hz': 1},
    0x08: {'name': 'stft2',    'topic': 'drifter/engine/stft2',    'decode': lambda a, b=0: round((a / 1.28) - 100, 2), 'unit': '%', 'hz': 5},
    0x09: {'name': 'ltft2',    'topic': 'drifter/engine/ltft2',    'decode': lambda a, b=0: round((a / 1.28) - 100, 2), 'unit': '%', 'hz': 1},
    0x04: {'name': 'load',     'topic': 'drifter/engine/load',     'decode': lambda a, b=0: round(a / 2.55, 1),       'unit': '%',    'hz': 5},
    0x0D: {'name': 'speed',    'topic': 'drifter/vehicle/speed',   'decode': lambda a, b=0: a,                         'unit': 'km/h', 'hz': 5},
    0x0F: {'name': 'iat',      'topic': 'drifter/engine/iat',      'decode': lambda a, b=0: a - 40,                   'unit': 'C',    'hz': 1},
    0x10: {'name': 'maf',      'topic': 'drifter/engine/maf',      'decode': lambda a, b: round(((a * 256) + b) / 100.0, 2), 'unit': 'g/s', 'hz': 5},
    0x11: {'name': 'throttle', 'topic': 'drifter/engine/throttle', 'decode': lambda a, b=0: round(a / 2.55, 1),       'unit': '%',    'hz': 10},
    0x42: {'name': 'voltage',  'topic': 'drifter/power/voltage',   'decode': lambda a, b: round(((a * 256) + b) / 1000.0, 2), 'unit': 'V', 'hz': 1},
}

# Two-byte PID set (need both A and B bytes for decode)
TWO_BYTE_PIDS = {0x0C, 0x10, 0x42}

# ── DTC Decoding ──
DTC_PREFIXES = {0: 'P', 1: 'C', 2: 'B', 3: 'U'}

DTC_CHECK_INTERVAL = 60  # Check DTCs every 60 seconds

# ── State ──
latest_values = {}
active_dtcs = []
pending_dtcs = []
running = True


def _handle_signal(sig, frame):
    """Handle SIGTERM and SIGINT for clean systemd shutdown."""
    global running
    running = False


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


def find_can_interface():
    """Auto-detect the USB2CANFD interface."""
    # Try common interface names
    for iface in ['can0', 'can1', 'slcan0']:
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
    if msg.arbitration_id < OBD_RESPONSE_BASE or msg.arbitration_id > OBD_RESPONSE_END:
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
        if pid in TWO_BYTE_PIDS:
            value = pid_def['decode'](data[3], data[4])
        else:
            value = pid_def['decode'](data[3])
        return pid, value
    except (IndexError, ValueError) as e:
        log.warning(f"Decode error for PID 0x{pid:02X}: {e}")
        return None


def decode_dtc(byte1, byte2):
    """Decode a 2-byte DTC into standard format (e.g., P0301)."""
    if byte1 == 0 and byte2 == 0:
        return None
    prefix = DTC_PREFIXES.get((byte1 >> 6) & 0x03, 'P')
    digit2 = (byte1 >> 4) & 0x03
    digit3 = byte1 & 0x0F
    digit4 = (byte2 >> 4) & 0x0F
    digit5 = byte2 & 0x0F
    return f"{prefix}{digit2}{digit3:X}{digit4:X}{digit5:X}"


def request_dtcs(bus, mode=0x03):
    """Request DTCs using Mode 03 (stored) or Mode 07 (pending).
    Returns list of DTC strings."""
    data = [0x01, mode, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]
    msg = can.Message(
        arbitration_id=OBD_REQUEST_ID,
        data=data,
        is_extended_id=False
    )
    try:
        bus.send(msg)
    except can.CanError as e:
        log.warning(f"DTC request error (mode 0x{mode:02X}): {e}")
        return []

    dtcs = []
    # Collect responses (may be multi-frame)
    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline:
        response = bus.recv(timeout=0.1)
        if response is None:
            break
        if response.arbitration_id < OBD_RESPONSE_BASE or response.arbitration_id > OBD_RESPONSE_END:
            continue

        rd = response.data
        response_mode = mode + 0x40  # 0x43 for stored, 0x47 for pending
        if len(rd) >= 2 and rd[1] == response_mode:
            # Parse DTC pairs starting at byte 3
            for i in range(3, len(rd) - 1, 2):
                dtc = decode_dtc(rd[i], rd[i + 1])
                if dtc:
                    dtcs.append(dtc)

    return dtcs


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

    last_snapshot = 0.0
    last_dtc_check = 0.0
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
            if latest_values and now - last_snapshot >= 1.0:
                mqtt_client.publish("drifter/snapshot", json.dumps({
                    **latest_values,
                    'ts': time.time()
                }))
                last_snapshot = now

            # Check DTCs periodically
            if now - last_dtc_check >= DTC_CHECK_INTERVAL:
                stored = request_dtcs(bus, mode=0x03)
                pending = request_dtcs(bus, mode=0x07)

                if stored != active_dtcs or pending != pending_dtcs:
                    active_dtcs.clear()
                    active_dtcs.extend(stored)
                    pending_dtcs.clear()
                    pending_dtcs.extend(pending)

                    mqtt_client.publish(TOPICS['dtc'], json.dumps({
                        'stored': stored,
                        'pending': pending,
                        'count': len(stored) + len(pending),
                        'ts': time.time()
                    }), retain=True)

                    if stored:
                        log.warning(f"Stored DTCs: {', '.join(stored)}")
                    if pending:
                        log.info(f"Pending DTCs: {', '.join(pending)}")

                last_dtc_check = now

            # Small sleep to prevent CPU spin
            time.sleep(0.005)

        except can.CanError as e:
            log.error(f"CAN bus error: {e}")
            time.sleep(1)

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

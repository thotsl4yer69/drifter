#!/usr/bin/env python3
"""
MZ1312 DRIFTER — OBD-II Serial Bridge (ELM327 fallback)
For vehicles or installs that don't have the USB2CANFD hardware, this
service speaks AT/OBD commands to a generic ELM327-compatible adapter on
/dev/ttyUSB0 and publishes the same metric topics as can_bridge.py. Only
runs when the CAN bridge is absent — meant as a compatibility path for
non-Jaguar vehicles after vehicle_id resolves a different profile.
UNCAGED TECHNOLOGY — EST 1991
"""

import json
import logging
import signal
import time
from typing import Callable, Optional

import paho.mqtt.client as mqtt

from config import (
    MQTT_HOST, MQTT_PORT, TOPICS,
    OBD_SERIAL_DEV, OBD_SERIAL_BAUD, OBD_POLL_HZ,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [OBDBRIDGE] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)


# PID definitions matching can_bridge.py's decoding semantics
PID_DEFS = {
    '010C': {'name': 'rpm',      'topic': TOPICS['rpm'],
             'decode': lambda b: ((b[0] * 256) + b[1]) / 4.0, 'unit': 'rpm'},
    '0105': {'name': 'coolant',  'topic': TOPICS['coolant'],
             'decode': lambda b: b[0] - 40, 'unit': 'C'},
    '0106': {'name': 'stft1',    'topic': TOPICS['stft1'],
             'decode': lambda b: round((b[0] / 1.28) - 100, 2), 'unit': '%'},
    '0107': {'name': 'ltft1',    'topic': TOPICS['ltft1'],
             'decode': lambda b: round((b[0] / 1.28) - 100, 2), 'unit': '%'},
    '0108': {'name': 'stft2',    'topic': TOPICS['stft2'],
             'decode': lambda b: round((b[0] / 1.28) - 100, 2), 'unit': '%'},
    '0109': {'name': 'ltft2',    'topic': TOPICS['ltft2'],
             'decode': lambda b: round((b[0] / 1.28) - 100, 2), 'unit': '%'},
    '0104': {'name': 'load',     'topic': TOPICS['load'],
             'decode': lambda b: round(b[0] / 2.55, 1), 'unit': '%'},
    '010D': {'name': 'speed',    'topic': TOPICS['speed'],
             'decode': lambda b: b[0], 'unit': 'km/h'},
    '010F': {'name': 'iat',      'topic': TOPICS['iat'],
             'decode': lambda b: b[0] - 40, 'unit': 'C'},
    '0110': {'name': 'maf',      'topic': TOPICS['maf'],
             'decode': lambda b: round(((b[0] * 256) + b[1]) / 100.0, 2), 'unit': 'g/s'},
    '0111': {'name': 'throttle', 'topic': TOPICS['throttle'],
             'decode': lambda b: round(b[0] / 2.55, 1), 'unit': '%'},
    '0142': {'name': 'voltage',  'topic': TOPICS['voltage'],
             'decode': lambda b: round(((b[0] * 256) + b[1]) / 1000.0, 2), 'unit': 'V'},
}


def _open_elm() -> Optional[object]:
    try:
        import serial
    except ImportError:
        log.warning("pyserial not installed — OBD bridge disabled")
        return None
    try:
        ser = serial.Serial(OBD_SERIAL_DEV, OBD_SERIAL_BAUD, timeout=1)
    except Exception as e:
        log.warning(f"ELM open failed ({OBD_SERIAL_DEV}): {e}")
        return None
    # Initialise ELM327: reset, echo off, headers off, protocol auto
    for cmd in ('ATZ', 'ATE0', 'ATH0', 'ATSP0'):
        try:
            ser.write(f"{cmd}\r".encode())
            time.sleep(0.5)
            ser.read(64)
        except Exception as e:
            log.warning(f"ELM init {cmd} failed: {e}")
            try:
                ser.close()
            except Exception:
                pass
            return None
    log.info(f"ELM327 ready on {OBD_SERIAL_DEV}")
    return ser


def _query_pid(ser, pid: str) -> Optional[list]:
    try:
        ser.reset_input_buffer()
        ser.write(f"{pid}\r".encode())
        time.sleep(0.2)
        raw = ser.read(128).decode('ascii', errors='replace')
        # Strip prompt + whitespace
        line = raw.replace('\r', ' ').replace('>', ' ').strip()
        if not line or 'NO DATA' in line.upper():
            return None
        # Response format: "41 0C 1A F8" — first 2 bytes are mode+0x40 / pid
        tokens = [t for t in line.split() if len(t) == 2]
        if len(tokens) < 3:
            return None
        if tokens[0] != f"{int(pid[:2], 16) + 0x40:02X}":
            return None
        try:
            return [int(t, 16) for t in tokens[2:]]
        except ValueError:
            return None
    except Exception as e:
        log.debug(f"query {pid}: {e}")
        return None


def main() -> None:
    log.info("DRIFTER OBD Bridge starting...")
    ser = _open_elm()

    running = True

    def _handle_signal(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    client = mqtt.Client(client_id="drifter-obdbridge")
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

    client.loop_start()
    client.publish(TOPICS['obd_status'], json.dumps({
        'state': 'online' if ser else 'offline',
        'device': OBD_SERIAL_DEV,
        'ts': time.time(),
    }), retain=True)
    if not ser:
        log.warning("OBD Bridge running without modem — will idle and retry")

    interval = 1.0 / max(OBD_POLL_HZ, 0.5)
    snapshot: dict = {}
    last_snap = 0.0
    while running:
        if ser is None:
            time.sleep(5)
            ser = _open_elm()
            if ser is not None:
                client.publish(TOPICS['obd_status'], json.dumps({
                    'state': 'online', 'device': OBD_SERIAL_DEV, 'ts': time.time(),
                }), retain=True)
            continue
        for pid, info in PID_DEFS.items():
            if not running:
                break
            data = _query_pid(ser, pid)
            if not data:
                continue
            try:
                value = info['decode'](data)
            except Exception:
                continue
            snapshot[info['name']] = value
            client.publish(info['topic'], json.dumps({
                'value': value, 'unit': info['unit'], 'ts': time.time(),
            }))
            client.publish(TOPICS['obd_pid'], json.dumps({
                'pid': pid, 'value': value, 'ts': time.time(),
            }))
            time.sleep(interval)
        now = time.time()
        if snapshot and now - last_snap >= 1.0:
            client.publish(TOPICS['snapshot'], json.dumps({
                **snapshot, 'ts': now, 'source': 'obd_bridge',
            }))
            last_snap = now

    client.publish(TOPICS['obd_status'], json.dumps({
        'state': 'offline', 'ts': time.time(),
    }), retain=True)
    client.loop_stop()
    client.disconnect()
    if ser is not None:
        try:
            ser.close()
        except Exception:
            pass
    log.info("OBD Bridge stopped")


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
MZ1312 DRIFTER — OBD-II Serial Bridge (ELM327 fallback, incl. K-line)
For vehicles or installs that don't have a raw socketcan adapter, this
service speaks AT/OBD commands to a generic ELM327-compatible adapter on
``OBD_SERIAL_DEV`` and publishes the same metric topics as can_bridge.py.

WHY THIS EXISTS — K-LINE FALLBACK
---------------------------------
The 2004 Jaguar X-Type (and many pre-2008 cars) is often NOT CAN on the
OBD-II diagnostic link — it may be ISO 9141-2 or ISO 14230 (KWP2000) on the
K-line. can_bridge.py talks raw socketcan and CANNOT reach a K-line ECU. An
ELM327 abstracts the physical layer (CAN *or* K-line) behind the same AT/PID
text protocol, so this bridge works on either. ``ATSP0`` lets the ELM327
auto-detect the protocol; ``detect_protocol()`` below reports what it landed
on (CAN vs ISO/KWP K-line) for the operator + logs.

OPERATOR: SWITCHING canbridge <-> obdbridge
-------------------------------------------
The two are mutually exclusive telemetry sources (both publish the same
TOPICS['snapshot'] etc). Run exactly one:

  * CAN car, raw socketcan adapter (USB2CANFD / CANable):
        sudo systemctl disable --now drifter-obdbridge
        sudo systemctl enable  --now drifter-canbridge

  * K-line car (ISO 9141 / KWP2000), OR any car via a generic ELM327:
        sudo systemctl disable --now drifter-canbridge
        sudo systemctl enable  --now drifter-obdbridge

Not sure which? Plug in an ELM327, start drifter-obdbridge, and check:
        journalctl -u drifter-obdbridge -n 30
The startup line reports the auto-detected protocol (e.g. "ISO 9141-2
(K-line)" vs "ISO 15765-4 CAN 11/500"). If it reports a CAN protocol and you
have a raw socketcan adapter, prefer drifter-canbridge for the higher poll
rate.

GRACEFUL DEGRADE: like can_bridge, this NEVER exits / crash-loops when no
adapter is present — it idles, publishes a 'hw_pending' status, and retries
opening the modem. A missing telemetry source degrades, never reboots.
UNCAGED TECHNOLOGY — EST 1991
"""

import json
import logging
import signal
import time

from config import (
    MQTT_HOST,
    MQTT_PORT,
    OBD_POLL_HZ,
    OBD_SERIAL_BAUD,
    OBD_SERIAL_DEV,
    TOPICS,
    make_mqtt_client,
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


def _open_elm() -> object | None:
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
    # Initialise ELM327: reset, echo off, headers off, protocol auto.
    # ATSP0 = let the adapter AUTO-DETECT the protocol — this is what makes
    # the bridge work on a K-line car (ISO 9141 / KWP2000) as well as CAN.
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
    proto = detect_protocol(ser)
    log.info(f"ELM327 ready on {OBD_SERIAL_DEV} — protocol: {proto}")
    return ser


# ELM327 'ATDPN' protocol-number → human label. Numbers 6+ are CAN; 1-5 are
# the K-line / J1850 family (this is the path that makes a non-CAN X-Type work).
_ELM_PROTO_NAMES = {
    '0': 'auto (not yet determined)',
    '1': 'SAE J1850 PWM',
    '2': 'SAE J1850 VPW',
    '3': 'ISO 9141-2 (K-line)',
    '4': 'ISO 14230-4 KWP 5-baud (K-line)',
    '5': 'ISO 14230-4 KWP fast (K-line)',
    '6': 'ISO 15765-4 CAN (11-bit, 500k)',
    '7': 'ISO 15765-4 CAN (29-bit, 500k)',
    '8': 'ISO 15765-4 CAN (11-bit, 250k)',
    '9': 'ISO 15765-4 CAN (29-bit, 250k)',
    'A': 'SAE J1939 CAN',
}


def detect_protocol(ser) -> str:
    """Ask the ELM327 which OBD protocol it auto-negotiated (``ATDPN``).

    Returns a human-readable label, e.g. "ISO 9141-2 (K-line)" or
    "ISO 15765-4 CAN (11-bit, 500k)". Lets an operator confirm whether the
    car is CAN or K-line and therefore whether drifter-canbridge is even an
    option (see module docstring). Best-effort — returns 'unknown' on any
    error, never raises. The leading 'A' (auto) prefix from ATDPN is stripped.
    """
    try:
        ser.reset_input_buffer()
        ser.write(b"ATDPN\r")
        time.sleep(0.3)
        raw = ser.read(64).decode('ascii', errors='replace')
        token = raw.replace('\r', ' ').replace('>', ' ').strip().upper()
        # Response looks like "A6" (auto, settled on 6) or just "6".
        token = token.lstrip('A').strip()
        if not token:
            return 'unknown'
        return _ELM_PROTO_NAMES.get(token[:1], f'unknown (ATDPN={token})')
    except Exception as e:
        log.debug(f"ATDPN protocol detect failed: {e}")
        return 'unknown'


def _query_pid(ser, pid: str) -> list | None:
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

    client = make_mqtt_client("drifter-obdbridge")
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
    # 'hw_pending' (not 'offline') while we have no modem yet: the service is
    # alive and healthy, just waiting on the OBD adapter — same hardware-pending
    # semantics as can_bridge so /healthz/cockpit treat it as pending, not failed.
    client.publish(TOPICS['obd_status'], json.dumps({
        'state': 'online' if ser else 'hw_pending',
        'device': OBD_SERIAL_DEV,
        'ts': time.time(),
    }), retain=True)
    if not ser:
        log.warning("OBD Bridge running without modem — will idle and retry "
                    "(degrades, never exits/crash-loops)")

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

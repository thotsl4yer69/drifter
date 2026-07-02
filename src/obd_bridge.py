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

OPERATOR: canbridge <-> obdbridge AUTO-SELECTION
------------------------------------------------
Both drifter-canbridge and drifter-obdbridge are enabled, monitored services,
but they are mutually exclusive telemetry sources (both publish the same
TOPICS['snapshot'] etc). To avoid double-publishing, each self-selects at boot
via obd_transport.select_transport(): the transport that isn't chosen idles and
publishes a hardware-pending status instead of telemetry. Selection prefers a
raw SocketCAN / CANable adapter when present, else an ELM327 serial device.

  * CAN car with a raw socketcan adapter (USB2CANFD / CANable) → drifter-canbridge
    runs, drifter-obdbridge idles (deferring_to_canbridge).
  * K-line car (ISO 9141 / KWP2000), OR any car via a generic ELM327 →
    drifter-obdbridge runs, drifter-canbridge idles (deferring_to_obdbridge).

Force a transport with the DRIFTER_TRANSPORT env var (can | elm327) in
/opt/drifter/.env. To confirm what obdbridge negotiated:
        journalctl -u drifter-obdbridge -n 30
The startup line reports the auto-detected protocol (e.g. "ISO 9141-2
(K-line)" vs "ISO 15765-4 CAN 11/500").

GRACEFUL DEGRADE: like can_bridge, this NEVER exits / crash-loops when no
adapter is present — it idles, publishes a 'hw_pending' status, and retries
opening the modem. A missing telemetry source degrades, never reboots.
UNCAGED TECHNOLOGY — EST 1991
"""

import json
import logging
import signal
import time

import obd_transport
import vehicle_profile
from config import (
    MQTT_HOST,
    MQTT_PORT,
    OBD_POLL_HZ,
    OBD_SERIAL_BAUD,
    OBD_SERIAL_DEV,
    TOPICS,
    make_mqtt_client,
)
from obd_pids import (
    SUPPORT_PROBE_PIDS,
    applies_to,
    obd_pid_defs,
    supported_from_bitmaps,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [OBDBRIDGE] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)


# PID definitions — built from the canonical table in obd_pids.py (the SINGLE
# source of truth shared with the raw-CAN bridge). Keyed by ELM327 command
# string ('010C'); each decode() takes the data bytes (A, B, …) as a list. This
# used to be a hand-copied subset that silently dropped timing / O2 / run-time /
# fuel-level / baro on the K-line path — unifying means the ELM327 transport now
# reports the same metric set as raw CAN.
PID_DEFS = obd_pid_defs()


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


def query_supported_pids(ser):
    """Probe Mode-01 support bitmaps over the ELM327 and return the supported,
    decodable PID set — or ``None`` if the adapter answered nothing (so the
    caller can fall back to a powertrain-appropriate default)."""
    bitmaps: dict[int, int] = {}
    for probe in SUPPORT_PROBE_PIDS:
        data = _query_pid(ser, f"01{probe:02X}")
        if data and len(data) >= 4:
            bitmaps[probe] = (data[0] << 24) | (data[1] << 16) | (data[2] << 8) | data[3]
    if not bitmaps:
        return None
    return supported_from_bitmaps(bitmaps)


def active_pid_defs(ser):
    """Narrow PID_DEFS to what this ECU actually supports (per-car). Falls back
    to the powertrain-default set when discovery is silent — a pure EV drops
    the combustion-only PIDs; an ICE car keeps the full known table."""
    supported = query_supported_pids(ser)
    if supported:
        defs = {c: d for c, d in PID_DEFS.items() if d['pid'] in supported}
        log.info(f"PID discovery: ECU reports {len(defs)}/{len(PID_DEFS)} "
                 f"known PIDs supported")
        return defs
    applicable = applies_to(vehicle_profile.fuel_type())
    defs = {c: d for c, d in PID_DEFS.items() if d['pid'] in applicable}
    log.info(f"PID discovery got no response — polling {len(defs)} "
             f"powertrain-default PIDs")
    return defs


def _idle(running_ref, seconds=5.0):
    """Sleep in short slices so SIGTERM is honoured promptly."""
    for _ in range(int(seconds / 0.25)):
        if not running_ref():
            break
        time.sleep(0.25)


def main() -> None:
    log.info("DRIFTER OBD Bridge starting...")

    running = True

    def _handle_signal(sig, frame):
        nonlocal running
        running = False

    def _running():
        return running

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

    interval = 1.0 / max(OBD_POLL_HZ, 0.5)
    snapshot: dict = {}
    last_snap = 0.0
    ser = None
    active_defs = PID_DEFS
    last_transport_check = 0.0
    transport_ok = False
    deferring = False
    while running:
        # Transport arbitration (throttled): canbridge and obdbridge publish the
        # same topics, so only the auto-selected transport runs. If a raw-CAN
        # adapter is selected, idle here and defer — re-checking so a hot-plug
        # can flip which transport owns telemetry without a restart.
        now_mono = time.monotonic()
        if last_transport_check == 0.0 or now_mono - last_transport_check >= 10.0:
            transport_ok = obd_transport.select_transport() == obd_transport.ELM327
            last_transport_check = now_mono
        if not transport_ok:
            if not deferring:
                log.info("CAN transport selected — deferring to "
                         "drifter-canbridge (obdbridge idle). Force with "
                         "DRIFTER_TRANSPORT=elm327.")
                deferring = True
            if ser is not None:
                try:
                    ser.close()
                except Exception:
                    pass
                ser = None
            client.publish(TOPICS['obd_status'], json.dumps({
                'state': 'hw_pending', 'reason': 'deferring_to_canbridge',
                'ts': time.time(),
            }), retain=True)
            _idle(_running)
            continue
        deferring = False

        if ser is None:
            ser = _open_elm()
            if ser is not None:
                active_defs = active_pid_defs(ser)
                client.publish(TOPICS['obd_status'], json.dumps({
                    'state': 'online', 'device': OBD_SERIAL_DEV, 'ts': time.time(),
                }), retain=True)
            else:
                # No modem yet — alive and healthy, just hardware-pending. Same
                # semantics as can_bridge so /healthz/cockpit treat it as
                # pending, not failed. Degrades, never exits/crash-loops.
                client.publish(TOPICS['obd_status'], json.dumps({
                    'state': 'hw_pending', 'device': OBD_SERIAL_DEV,
                    'ts': time.time(),
                }), retain=True)
                _idle(_running)
            continue
        for pid, info in active_defs.items():
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

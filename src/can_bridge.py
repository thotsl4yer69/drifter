#!/usr/bin/env python3
"""
MZ1312 DRIFTER — CAN Bridge
Reads OBD-II data from USB2CANFD and publishes to MQTT.
Supports Mode 01 (live data), Mode 03 (stored DTCs), Mode 07 (pending DTCs).
UNCAGED TECHNOLOGY — EST 1991
"""

import json
import logging
import signal
import time

import can

import iso_tp
import obd_transport
import vehicle_profile
from config import (
    CAN_BITRATE,
    CAN_USB_IDS,
    MQTT_HOST,
    MQTT_PORT,
    OBD_REQUEST_ID,
    OBD_RESPONSE_BASE,
    OBD_RESPONSE_END,
    TOPICS,
    make_mqtt_client,
)
from obd_pids import (
    PID_TABLE,
    SUPPORT_PROBE_PIDS,
    applies_to,
    can_pids,
    supported_from_bitmaps,
    two_byte_pids,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [CANBRIDGE] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

# ── OBD-II PID Definitions ──
# The canonical PID table lives in obd_pids.py — the SINGLE source of truth
# shared with the ELM327/K-line bridge (obd_bridge.py). `PIDS` is int-keyed with
# a list-decoder (takes the data bytes A,B,… and returns the scaled value);
# `TWO_BYTE_PIDS` is derived (PIDs whose decoder needs both A and B). can_native
# imports `PIDS` from here, so keep the name.
PIDS = can_pids()

# Two-byte PID set (need both A and B bytes for decode) — derived from the table.
TWO_BYTE_PIDS = two_byte_pids()

# ── DTC Decoding ──
DTC_PREFIXES = {0: 'P', 1: 'C', 2: 'B', 3: 'U'}

DTC_CHECK_INTERVAL = 60  # Check DTCs every 60 seconds

# ── Resilience ──
# After this many consecutive send failures we tear down the bus and
# re-discover the interface IN-PROCESS. We never exit the process for a
# missing/dropped CAN source — that is a normal vehicle condition and must
# DEGRADE, not crash-loop (which the old reboot-force unit turned into a
# node-bricking reboot loop). See services/drifter-canbridge.service.
MAX_CONSECUTIVE_FAILURES = 20
ERROR_LOG_INTERVAL = 30         # Only log CAN errors every N seconds
NO_CAN_RETRY_INTERVAL = 5       # Seconds between interface-detection retries
# How often to re-publish the "still waiting for CAN" status while degraded,
# so /healthz and the cockpit see a fresh hw-pending signal (not a stale one).
NO_CAN_STATUS_INTERVAL = 30

# ── CAN-adapter USB allowlist (VID:PID) ──
# The canonical allowlist now lives in config.CAN_USB_IDS (so obd_transport can
# read it without importing python-can). Re-exported here under the historical
# name — find_can_interface + tests reference `can_bridge.CAN_USB_IDS`.

# ── State ──
latest_values = {}
active_dtcs = []
pending_dtcs = []
_consecutive_failures = 0
_last_error_log = 0.0
_suppressed_errors = 0




def _serial_dev_is_can_adapter(dev: str) -> bool:
    """Return True ONLY if udev positively identifies ``dev`` as a known CAN
    adapter by USB VID:PID (see ``CAN_USB_IDS``).

    This is a positive allowlist, not a denylist. Previously can_bridge would
    slcand any ttyUSB/ttyACM that *wasn't* a known-bad CH340/PL2303 — which
    happily hijacked the Flipper / Marauder / GPS / mic serial ports (some of
    which use STMicro/SiLabs/FTDI chips that the denylist let through),
    creating a phantom slcan0 that never sees a frame. We now bind a serial
    CAN interface only when the VID:PID is explicitly an allowlisted CAN
    adapter; an unrecognised serial device is left strictly alone.
    """
    try:
        import subprocess
        r = subprocess.run(
            ['udevadm', 'info', '--name', dev, '--query=property'],
            capture_output=True, text=True, timeout=2,
        )
        if r.returncode != 0:
            return False
        vid = pid = None
        for line in r.stdout.splitlines():
            if line.startswith('ID_VENDOR_ID='):
                vid = line.split('=', 1)[1].strip().lower()
            elif line.startswith('ID_MODEL_ID='):
                pid = line.split('=', 1)[1].strip().lower()
        return (vid, pid) in CAN_USB_IDS
    except Exception:
        return False


def find_can_interface():
    """Auto-detect the USB2CANFD interface."""
    # Try common interface names
    for iface in ['can0', 'can1', 'slcan0']:
        try:
            bus = can.Bus(interface='socketcan', channel=iface, bitrate=CAN_BITRATE)
            bus.shutdown()
            log.info(f"Found CAN interface: {iface}")
            return iface
        except (can.CanError, OSError):
            continue

    # If no socketcan found, try the USB serial route (slcan) — but ONLY for
    # a positively-identified CAN adapter (VID:PID allowlist). Unrecognised
    # serial devices (Flipper / Marauder / GPS / mic) are never touched.
    import glob
    usb_devs = glob.glob('/dev/ttyACM*') + glob.glob('/dev/ttyUSB*')
    for dev in usb_devs:
        if not _serial_dev_is_can_adapter(dev):
            log.info(f"Skipping {dev}: USB VID:PID is not an allowlisted CAN "
                     f"adapter (could be Flipper/Marauder/GPS/mic serial)")
            continue
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
            bus = can.Bus(interface='socketcan', channel='slcan0', bitrate=CAN_BITRATE)
            bus.shutdown()
            log.info(f"Created slcan interface from {dev}")
            return 'slcan0'
        except Exception:
            continue

    return None


def send_obd_request(bus, pid):
    """Send a standard OBD-II request for a given PID."""
    global _consecutive_failures, _last_error_log, _suppressed_errors
    # Mode 01 request: [number_of_bytes, mode, pid, padding...]
    data = [0x02, 0x01, pid, 0x00, 0x00, 0x00, 0x00, 0x00]
    msg = can.Message(
        arbitration_id=OBD_REQUEST_ID,
        data=data,
        is_extended_id=False
    )
    try:
        bus.send(msg)
        if _consecutive_failures > 0:
            log.info(f"CAN interface recovered after {_consecutive_failures} failures")
            _consecutive_failures = 0
            _suppressed_errors = 0
        return True
    except can.CanError as e:
        _consecutive_failures += 1
        now = time.monotonic()
        if now - _last_error_log >= ERROR_LOG_INTERVAL:
            if _suppressed_errors > 0:
                log.warning(f"CAN send failing — {_suppressed_errors} errors suppressed in last {ERROR_LOG_INTERVAL}s")
            log.warning(f"CAN send error for PID 0x{pid:02X}: {e} (failures: {_consecutive_failures})")
            _last_error_log = now
            _suppressed_errors = 0
        else:
            _suppressed_errors += 1
        return False


def decode_obd_response(msg):
    """Decode an OBD-II response message."""
    if msg.arbitration_id < OBD_RESPONSE_BASE or msg.arbitration_id > OBD_RESPONSE_END:
        return None

    data = msg.data
    if len(data) < 4:
        return None

    # Only accept complete ISO-TP single frames; reject stale padding as data.
    if any(getattr(msg, flag, False) is True for flag in ("is_extended_id", "is_remote_frame", "is_error_frame")):
        return None
    length = data[0]
    if not 3 <= length <= 7 or len(data) < length + 1:
        return None

    # Check it's a Mode 01 response (0x41)
    if data[1] != 0x41:
        return None

    pid = data[2]
    if pid not in PIDS:
        return None

    pid_def = PIDS[pid]
    if length < 2 + PID_TABLE[pid].nbytes:
        return None
    try:
        # Decoders take the data bytes (A, B, …) as a sequence, so a raw CAN
        # frame slice and an ELM327 hex line decode identically. The response
        # payload starts at byte 3 (after length + mode-echo + pid-echo).
        value = pid_def['decode'](data[3:])
        return pid, value
    except (IndexError, ValueError) as e:
        log.warning(f"Decode error for PID 0x{pid:02X}: {e}")
        return None


def query_supported_pids(bus, timeout=1.0):
    """Probe Mode-01 support bitmaps (0x00/0x20/0x40/0x60) over the raw bus.

    Returns the set of PIDs the ECU reports supported AND that we can decode,
    or ``None`` when the bus is silent (no probe answered) so the caller can
    fall back to a powertrain-appropriate default rather than polling nothing.
    Each probe answers in a single frame (mode+pid+4 bitmap bytes), so no
    ISO-TP reassembly is needed here.
    """
    bitmaps: dict[int, int] = {}
    for probe in SUPPORT_PROBE_PIDS:
        send_obd_request(bus, probe)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                resp = bus.recv(timeout=0.1)
            except can.CanError:
                break
            if resp is None:
                continue
            if (resp.arbitration_id < OBD_RESPONSE_BASE
                    or resp.arbitration_id > OBD_RESPONSE_END):
                continue
            d = resp.data
            if (len(d) >= 7 and d[0] == 6 and d[1] == 0x41 and d[2] == probe
                    and not any(getattr(resp, flag, False) is True for flag in
                                ('is_extended_id', 'is_remote_frame', 'is_error_frame'))):
                bitmaps[probe] = (d[3] << 24) | (d[4] << 16) | (d[5] << 8) | d[6]
                break
        if probe not in bitmaps or not bitmaps[probe] & 1:
            break
    if not bitmaps:
        return None
    return supported_from_bitmaps(bitmaps)


def _resolve_poll_pids(bus):
    """Which PIDs to poll on this bus, in the table's canonical order.

    Prefer the ECU's live Mode-01 support set (so we only request PIDs the car
    actually answers — per-car, works on anything). If discovery is silent
    (very old ECU, K-line quirk, bench), fall back to the powertrain-default
    set: a pure EV drops the combustion-only PIDs, every ICE car keeps the full
    known table (identical to the pre-discovery behaviour)."""
    supported = query_supported_pids(bus)
    if supported is not None:
        pids = [p for p in PIDS if p in supported]
        log.info(f"PID discovery: ECU reports {len(pids)}/{len(PIDS)} "
                 f"known PIDs supported")
        return pids
    applicable = applies_to(vehicle_profile.fuel_type())
    pids = [p for p in PIDS if p in applicable]
    log.info(f"PID discovery got no response — polling {len(pids)} "
             f"powertrain-default PIDs")
    return pids


def _build_schedule(pids):
    """Build the poll schedule (pid, interval, last_poll) for the given PIDs."""
    schedule = []
    for pid in pids:
        info = PIDS[pid]
        schedule.append({
            'pid': pid,
            'interval': 1.0 / info['hz'],
            'last_poll': 0,
            'info': info,
        })
    return schedule


def poll_pid(bus, pid, timeout=0.2):
    """Ignore unrelated traffic until the requested PID arrives or times out."""
    if not send_obd_request(bus, pid):
        return None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = bus.recv(timeout=min(0.05, max(0, deadline - time.monotonic())))
        result = decode_obd_response(response) if response else None
        if result and result[0] == pid:
            return result
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
    Returns list of DTC strings.

    A car with many DTCs answers over multiple ISO-TP frames; iso_tp.request
    sends the required Flow Control and reassembles them, so this reads every
    code rather than just whatever fit in the first frame."""
    payload = iso_tp.request(bus, [mode], timeout=0.5)
    if not payload:
        return None
    from elm_protocol import dtc_codes
    return dtc_codes(payload.hex(), mode, can_protocol=True)


def _publish_status(mqtt_client, state, **extra):
    """Publish a retained status payload on the system_status topic.

    Centralised so every code path emits a consistent shape. ``state`` is one
    of: 'online', 'hw_pending' (alive, no CAN adapter / no frames yet),
    'can_reconnecting', 'offline'. Best-effort — a publish failure must never
    propagate (we must never crash on a status push)."""
    payload = {"state": state, "timestamp": time.time(), **extra}
    try:
        mqtt_client.publish(TOPICS['system_status'],
                            json.dumps(payload), retain=True)
    except Exception as e:  # pragma: no cover - defensive
        log.debug(f"status publish failed ({state}): {e}")


def _await_can_transport(mqtt_client, running_fn):
    """Block (while alive) until the raw-CAN transport is the selected one.

    Returns True to proceed with CAN telemetry, or False if we were asked to
    stop while deferring. When an ELM327 transport is auto-selected, we idle and
    republish a fresh 'hw_pending' status so /healthz + the cockpit see the node
    as hardware-pending (obdbridge is driving telemetry), not failed. This never
    exits the process — a wrong-transport node degrades, it does not crash-loop.
    """
    last_status = 0.0
    while running_fn():
        if obd_transport.select_transport() == obd_transport.CAN:
            return True
        now = time.monotonic()
        if now - last_status >= NO_CAN_STATUS_INTERVAL or last_status == 0.0:
            log.info("ELM327 transport selected — deferring to drifter-obdbridge "
                     "(canbridge idle). Force with DRIFTER_TRANSPORT=can.")
            _publish_status(mqtt_client, "hw_pending",
                            reason="deferring_to_obdbridge")
            last_status = now
        for _ in range(int(NO_CAN_RETRY_INTERVAL / 0.25)):
            if not running_fn():
                break
            time.sleep(0.25)
    return False


def _acquire_bus(mqtt_client, running_fn):
    """Block (while alive) until a CAN interface is found and a bus opens.

    Returns ``(bus, iface)`` on success, or ``(None, None)`` if we were asked
    to stop while still waiting. CRUCIALLY this NEVER raises and NEVER exits
    the process for a missing CAN source — it keeps retrying and republishes a
    fresh 'hw_pending' status so /healthz + the cockpit see the node as
    hardware-pending (waiting for OBD-II), not failed. This is what stops a
    no-CAN car/bench from crash-looping the service (which the removed
    reboot-force unit escalated into a node-bricking reboot loop)."""
    last_status = 0.0
    while running_fn():
        iface = find_can_interface()
        if iface is not None:
            try:
                bus = can.Bus(interface='socketcan',
                              channel=iface, bitrate=CAN_BITRATE)
                log.info(f"Connected to {iface} at {CAN_BITRATE} bps")
                _publish_status(mqtt_client, "online", can_interface=iface)
                return bus, iface
            except (can.CanError, OSError) as e:
                # Interface name appeared but the bus won't open — treat as
                # still-pending and keep retrying rather than dying.
                log.warning(f"CAN interface {iface} found but bus open failed: "
                            f"{e}. Retrying...")
        now = time.monotonic()
        if now - last_status >= NO_CAN_STATUS_INTERVAL or last_status == 0.0:
            log.warning(
                "No CAN interface — staying alive, will keep retrying. "
                f"Plug in a CAN adapter, or: sudo ip link set can0 up type "
                f"can bitrate {CAN_BITRATE}. (K-line car? use drifter-obdbridge "
                "— see obd_bridge.py.)")
            _publish_status(mqtt_client, "hw_pending", reason="no_can_interface")
            last_status = now
        # Sleep in short slices so SIGTERM is honoured promptly.
        for _ in range(int(NO_CAN_RETRY_INTERVAL / 0.25)):
            if not running_fn():
                break
            time.sleep(0.25)
    return None, None


def main():
    global _consecutive_failures
    from obd_bridge import fresh_snapshot

    running = True

    def stop(_sig, _frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    client = make_mqtt_client('drifter-canbridge')
    client.will_set(TOPICS['system_status'], json.dumps({'state': 'offline'}), retain=True)
    while running:
        try:
            client.connect(MQTT_HOST, MQTT_PORT, 60)
            break
        except OSError:
            time.sleep(1)
    if not running:
        return
    client.loop_start()
    bus = lease = None
    values, sample_ts = {}, {}
    last_snapshot = last_dtc = last_status = 0.0
    try:
        while running:
            try:
                if obd_transport.select_transport() != obd_transport.CAN:
                    if bus is not None:
                        bus.shutdown()
                        bus = None
                    if lease is not None:
                        lease.close()
                        lease = None
                    values.clear()
                    sample_ts.clear()
                    if not _await_can_transport(client, lambda: running):
                        break
                    continue
                if lease is None:
                    lease = obd_transport.acquire_telemetry_lease()
                    if lease is None:
                        time.sleep(0.25)
                        continue
                if bus is None:
                    iface = find_can_interface()
                    if iface is None:
                        _publish_status(client, 'hw_pending', reason='no_can_interface')
                        for _ in range(20):
                            if not running:
                                break
                            time.sleep(0.25)
                        continue
                    bus = can.Bus(interface='socketcan', channel=iface, bitrate=CAN_BITRATE)
                    schedule = _build_schedule(_resolve_poll_pids(bus))
                    vin_pending = True
                    values.clear()
                    sample_ts.clear()
                    _consecutive_failures = 0
                    last_snapshot = last_dtc = 0.0
                if not client.is_connected():
                    raise OSError('MQTT broker disconnected')
                now = time.monotonic()
                received = False
                for entry in sorted(schedule, key=lambda e: (now - e['last_poll']) / e['interval'], reverse=True):
                    if now - entry['last_poll'] < entry['interval']:
                        continue
                    entry['last_poll'] = now
                    result = poll_pid(bus, entry['pid'])
                    if result and result[0] == entry['pid']:
                        pid, value = result
                        info = PIDS[pid]
                        ts = time.time()
                        values[info['name']] = value
                        sample_ts[info['name']] = ts
                        client.publish(info['topic'], json.dumps({
                            'value': value, 'unit': info['unit'], 'ts': ts, 'source': 'can_bridge',
                        }))
                        received = True
                    break  # one transaction per loop; bounded arbitration latency
                if _consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    raise OSError('CAN interface send failures')
                if received and now - last_snapshot >= 1:
                    snapshot = fresh_snapshot(values, sample_ts, time.time())
                    snapshot['source'] = 'can_bridge'
                    client.publish(TOPICS['snapshot'], json.dumps(snapshot))
                    last_snapshot = now
                if now - last_status >= 5:
                    fresh = sample_ts and time.time() - max(sample_ts.values()) <= 15
                    _publish_status(client, 'online' if fresh else 'hw_pending',
                                    reason='' if fresh else 'no_ecu_response', can_interface=iface)
                    last_status = now
                if received and now - last_dtc >= DTC_CHECK_INTERVAL:
                    stored, pending = request_dtcs(bus, 3), request_dtcs(bus, 7)
                    client.publish(TOPICS['dtc'], json.dumps({
                        'stored': stored, 'pending': pending,
                        'stored_available': stored is not None,
                        'pending_available': pending is not None,
                        'count': len(stored or []) + len(pending or []), 'ts': time.time(),
                        'source': 'can_bridge',
                    }), retain=True)
                    last_dtc = now
                if received and vin_pending:
                    payload = iso_tp.request(bus, [0x09, 0x02], timeout=3)
                    vin = None
                    if payload and list(payload[:2]) == [0x49, 0x02]:
                        from elm_protocol import vin_from_reply
                        vin = vin_from_reply(bytes(payload).hex())
                    client.publish(TOPICS['obd_vin'], json.dumps({'vin': vin, 'ts': time.time(), 'source': 'can_bridge'}), retain=True)
                    vin_pending = False
                time.sleep(0.005)
            except (can.CanError, OSError) as exc:
                _publish_status(client, 'can_reconnecting', reason=str(exc))
                if bus is not None:
                    bus.shutdown()
                    bus = None
                values.clear()
                sample_ts.clear()
                time.sleep(1)
    finally:
        if bus is not None:
            bus.shutdown()
        if lease is not None:
            lease.close()
        _publish_status(client, 'offline')
        client.loop_stop()
        client.disconnect()


if __name__ == '__main__':
    main()

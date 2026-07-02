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

import vehicle_profile
from config import (
    CAN_BITRATE,
    MQTT_HOST,
    MQTT_PORT,
    OBD_REQUEST_ID,
    OBD_RESPONSE_BASE,
    OBD_RESPONSE_END,
    TOPICS,
    make_mqtt_client,
)
from obd_pids import (
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
# Positively-identified CAN-over-serial / gs_usb adapters ONLY. We slcand a
# /dev/ttyACM*|ttyUSB* device only when udev says its VID:PID is on THIS list,
# so we never hijack the Flipper / Marauder / GPS / mic serial ports (they are
# generic CH340/PL2303/CP210x serial and are NOT here). Mirrors the allowlist
# in config/setup-can.sh — keep the two in sync.
# TODO(phase2): move to config.py
CAN_USB_IDS = {
    ('0483', '5740'),  # STMicro VCP — CANable / slcan (cantact, CANtact-style)
    ('1d50', '606f'),  # OpenMoko — candleLight / gs_usb (CANable gs_usb fw, CANtact)
    ('1209', '2323'),  # pid.codes — CANable 2.0 (gs_usb)
    ('16d0', '117e'),  # MCS — gs_usb USB2CAN (candleLight-class)
    ('1cd2', '606f'),  # Geschwister Schneider gs_usb (original CANtact)
}

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

    # Check it's a Mode 01 response (0x41)
    if data[1] != 0x41:
        return None

    pid = data[2]
    if pid not in PIDS:
        return None

    pid_def = PIDS[pid]
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
                break
            if (resp.arbitration_id < OBD_RESPONSE_BASE
                    or resp.arbitration_id > OBD_RESPONSE_END):
                continue
            d = resp.data
            if len(d) >= 7 and d[1] == 0x41 and d[2] == probe:
                bitmaps[probe] = (d[3] << 24) | (d[4] << 16) | (d[5] << 8) | d[6]
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
    if supported:
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
    global latest_values, _consecutive_failures

    running = True

    def _handle_signal(sig, frame):
        nonlocal running
        running = False

    def _running():
        return running

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    # ── Connect to MQTT FIRST ──
    # We connect to the broker before touching CAN so we can always publish a
    # status (including 'hw_pending') even when no CAN adapter is present.
    mqtt_client = make_mqtt_client("drifter-canbridge")
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

    if not running:
        log.info("Shutting down before MQTT connected")
        return

    # ── Acquire CAN interface (degrades, never exits) ──
    log.info("Searching for CAN interface...")
    bus, iface = _acquire_bus(mqtt_client, _running)
    if bus is None:
        # Only reached when asked to stop while still waiting — clean exit.
        log.info("Shutting down — stopped while waiting for CAN interface")
        _publish_status(mqtt_client, "offline")
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
        return

    # ── Polling Loop ──
    # Discover which PIDs this ECU actually supports and poll only those (per
    # car — works on anything, not just the X-Type). Falls back to the
    # powertrain-default set on a silent bus.
    schedule = _build_schedule(_resolve_poll_pids(bus))

    log.info(f"Polling {len(schedule)} supported PIDs")
    log.info("DRIFTER CAN Bridge is LIVE")

    last_snapshot = 0.0
    last_dtc_check = 0.0
    while running:
        try:
            now = time.monotonic()

            # ── Interface health check ──
            if _consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                log.error(
                    f"CAN interface lost — {_consecutive_failures} consecutive failures. "
                    f"Attempting in-process reconnection (degrade, never exit)..."
                )
                _publish_status(mqtt_client, "can_reconnecting",
                                can_interface=iface,
                                failures=_consecutive_failures)

                # Tear down old bus
                try:
                    bus.shutdown()
                except Exception:
                    pass

                # Re-acquire the interface. _acquire_bus keeps retrying and
                # publishing 'hw_pending' indefinitely — it returns (None,None)
                # ONLY when we were signalled to stop. We never exit the
                # process just because CAN dropped out.
                _consecutive_failures = 0
                bus, iface = _acquire_bus(mqtt_client, _running)
                if bus is None:
                    break  # asked to stop while waiting
                log.info(f"CAN interface reconnected on {iface}")
                # Re-discover supported PIDs on the fresh bus — a different
                # adapter/vehicle may have been plugged in during the outage.
                schedule = _build_schedule(_resolve_poll_pids(bus))
                last_dtc_check = 0.0  # re-probe DTCs on the fresh bus
                continue

            # Find the next PIDs that are due (up to 4 per loop to prevent blocking)
            polled_count = 0
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

                    polled_count += 1
                    if polled_count >= 4:
                        break  # Limit to 4 PIDs per iteration

            # Publish combined snapshot every second
            if latest_values and now - last_snapshot >= 1.0:
                mqtt_client.publish(TOPICS['snapshot'], json.dumps({
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
    _publish_status(mqtt_client, "offline")
    mqtt_client.loop_stop()
    mqtt_client.disconnect()
    try:
        if bus is not None:
            bus.shutdown()
    except Exception:
        pass


if __name__ == '__main__':
    main()

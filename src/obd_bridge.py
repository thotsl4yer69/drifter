#!/usr/bin/env python3
"""
MZ1312 DRIFTER — OBD-II ELM327 bridge.

Transport-neutral ELM327 telemetry path for serial, Bluetooth and Wi-Fi links.
The bridge deliberately treats adapter reachability, ECU reachability and live
PID flow as separate states. It is hardened for slow ISO 9141/KWP2000 init,
while remaining compatible with CAN-speaking ELM adapters.
"""
from __future__ import annotations

import json
import logging
import os
import re
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
    datefmt='%H:%M:%S',
)
log = logging.getLogger(__name__)

PID_DEFS = obd_pid_defs()

ELM_IO_TIMEOUT = float(os.getenv("ELM_TIMEOUT", "1.5"))
OBD_QUERY_TIMEOUT_SEC = float(os.getenv("OBD_QUERY_TIMEOUT_SEC", "2.5"))
OBD_INIT_TIMEOUT_SEC = float(os.getenv("OBD_INIT_TIMEOUT_SEC", "12"))
OBD_DTC_POLL_SEC = float(os.getenv("OBD_DTC_POLL_SEC", "30"))
OBD_VOLTAGE_POLL_SEC = float(os.getenv("OBD_VOLTAGE_POLL_SEC", "2"))
OBD_REPROBE_AFTER_EMPTY_CYCLES = int(os.getenv("OBD_REPROBE_AFTER_EMPTY_CYCLES", "3"))
OBD_SERIAL_BAUD_EFFECTIVE = int(os.getenv("OBD_SERIAL_BAUD", str(OBD_SERIAL_BAUD)))

_last_elm_meta: dict = {}

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

_FATAL_TEXT = (
    "UNABLE TO CONNECT",
    "BUS INIT: ERROR",
    "CAN ERROR",
    "STOPPED",
    "BUFFER FULL",
)


def _read_until_prompt(ser, timeout_s: float, chunk_size: int = 256) -> str:
    """Read until ELM prompt or deadline; works for serial and SocketStream."""
    deadline = time.monotonic() + max(0.05, timeout_s)
    chunks: list[bytes] = []
    while time.monotonic() < deadline:
        try:
            # pyserial read(N) waits for N bytes or timeout. Reading exactly
            # what is already buffered (or one byte while waiting) lets us
            # return immediately when the ELM prompt arrives instead of paying
            # the serial timeout on every PID. SocketStream has no in_waiting
            # attribute and already returns as soon as it sees `>`.
            if hasattr(ser, "in_waiting"):
                waiting = int(getattr(ser, "in_waiting", 0) or 0)
                part = ser.read(max(1, min(chunk_size, waiting or 1)))
            else:
                part = ser.read(chunk_size)
        except Exception as exc:
            log.debug("ELM read failed: %s", exc)
            break
        if part:
            chunks.append(part)
            if b">" in part:
                break
        else:
            time.sleep(0.02)
    return b"".join(chunks).decode("ascii", errors="replace")


def _exchange(ser, command: str, timeout_s: float = OBD_QUERY_TIMEOUT_SEC) -> str:
    """Send one ELM command and return the complete response up to `>`."""
    try:
        ser.reset_input_buffer()
    except Exception:
        pass
    ser.write(f"{command}\r".encode("ascii"))
    return _read_until_prompt(ser, timeout_s)


def _response_lines(raw: str, command: str | None = None) -> list[str]:
    """Return useful response lines, stripping prompt/noise/command echo."""
    out: list[str] = []
    cmd = (command or "").replace(" ", "").upper()
    for raw_line in raw.replace("\r", "\n").replace(">", "\n").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        upper = line.upper()
        compact = re.sub(r"\s+", "", upper)
        if cmd and compact == cmd:
            continue
        if upper.startswith("SEARCHING"):
            # Some adapters append a real response after SEARCHING... on the
            # same line. Keep the suffix if it contains hex.
            line = re.sub(r"(?i)^SEARCHING(?:\.\.\.)?\s*", "", line).strip()
            if not line:
                continue
            upper = line.upper()
        if upper.startswith("BUS INIT: OK"):
            line = re.sub(r"(?i)^BUS INIT:\s*OK\s*", "", line).strip()
            if not line:
                continue
        out.append(line)
    return out


def _extract_mode_payload(raw: str, request_mode: int, pid: int | None = None) -> list[int] | None:
    """Extract payload bytes from spaced OR compact ELM responses.

    Headers are normally off, but searching for the response mode/PID makes the
    parser tolerant of clone adapters that ignore ATH0 or ATS1.
    """
    response_mode = request_mode + 0x40
    needle = f"{response_mode:02X}" + (f"{pid:02X}" if pid is not None else "")
    for line in _response_lines(raw):
        upper = line.upper()
        if "NO DATA" in upper or any(token in upper for token in _FATAL_TEXT):
            continue
        # Remove all non-hex separators. This intentionally supports both:
        #   41 0C 1A F8
        #   410C1AF8
        compact = re.sub(r"[^0-9A-F]", "", upper)
        idx = compact.find(needle)
        if idx < 0:
            continue
        payload = compact[idx + len(needle):]
        if len(payload) < 2:
            return []
        if len(payload) % 2:
            payload = payload[:-1]
        try:
            return [int(payload[i:i + 2], 16) for i in range(0, len(payload), 2)]
        except ValueError:
            continue
    return None


def _adapter_identity(ser) -> str:
    try:
        raw = _exchange(ser, "ATI", timeout_s=2.0)
    except Exception:
        return ""
    lines = _response_lines(raw, "ATI")
    return " ".join(lines).strip()


def detect_protocol(ser) -> str:
    """Return the ELM-negotiated OBD protocol label."""
    try:
        raw = _exchange(ser, "ATDPN", timeout_s=1.5)
        token = "".join(_response_lines(raw, "ATDPN")).replace(" ", "").upper()
        token = token.lstrip("A").strip()
        if not token:
            return "unknown"
        return _ELM_PROTO_NAMES.get(token[:1], f"unknown (ATDPN={token})")
    except Exception as exc:
        log.debug("ATDPN protocol detect failed: %s", exc)
        return "unknown"


def _configure_adapter(ser, protocol: str = "0") -> bool:
    """Reset and establish parser-safe ELM settings."""
    commands = [
        ("ATZ", 3.0),
        ("ATE0", 1.5),
        ("ATH0", 1.5),
        ("ATL0", 1.5),
        ("ATS1", 1.5),  # parser-safe; never force ATS0 on this path
        ("ATAT1", 1.5),
        (f"ATSP{protocol}", 1.5),
    ]
    for command, timeout_s in commands:
        try:
            raw = _exchange(ser, command, timeout_s=timeout_s)
        except Exception as exc:
            log.debug("ELM init %s failed: %s", command, exc)
            return False
        upper = raw.upper()
        if "?" in upper or "ERROR" in upper:
            # ATAT1 is optional on some clones; don't reject an otherwise
            # functional adapter because that one tuning command is missing.
            if command == "ATAT1":
                continue
            return False
    return True


def _query_pid(ser, pid: str, timeout_s: float = OBD_QUERY_TIMEOUT_SEC) -> list[int] | None:
    """Query an OBD PID; accepts compact or spaced ELM response formatting."""
    try:
        request_mode = int(pid[:2], 16)
        pid_num = int(pid[2:4], 16)
        raw = _exchange(ser, pid, timeout_s=timeout_s)
        return _extract_mode_payload(raw, request_mode, pid_num)
    except Exception as exc:
        log.debug("query %s: %s", pid, exc)
        return None


def initialise_elm(ser) -> dict:
    """Prove adapter then ECU communication, including a K-line protocol ladder."""
    identity = ""
    if not _configure_adapter(ser, "0"):
        return {
            "adapter_ok": False,
            "ecu_ok": False,
            "protocol": "unknown",
            "identity": "",
            "reason": "adapter_init_failed",
        }

    identity = _adapter_identity(ser)
    # A few clones use custom ATI text, so a prompt/identity is enough; we do
    # not require the literal "ELM327" substring.
    adapter_ok = bool(identity)
    if not adapter_ok:
        return {
            "adapter_ok": False,
            "ecu_ok": False,
            "protocol": "unknown",
            "identity": "",
            "reason": "adapter_no_identity",
        }

    # Auto protocol first. ISO9141/KWP can spend several seconds doing 5-baud
    # or fast init, so the first support probe gets a deliberately long window.
    probe = _query_pid(ser, "0100", timeout_s=OBD_INIT_TIMEOUT_SEC)
    protocol = detect_protocol(ser) if probe else "auto (ECU not yet responding)"
    if probe:
        return {
            "adapter_ok": True,
            "ecu_ok": True,
            "protocol": protocol,
            "identity": identity,
            "protocol_attempt": "auto",
            "reason": "online",
        }

    # Deterministic fallback for stubborn clone/K-line combinations.
    # 3 = ISO9141, 4/5 = KWP, 6 = CAN sanity check.
    for proto in ("3", "4", "5", "6"):
        if not _configure_adapter(ser, proto):
            continue
        probe = _query_pid(ser, "0100", timeout_s=min(OBD_INIT_TIMEOUT_SEC, 8.0))
        if probe:
            protocol = detect_protocol(ser)
            return {
                "adapter_ok": True,
                "ecu_ok": True,
                "protocol": protocol,
                "identity": identity,
                "protocol_attempt": proto,
                "reason": "online",
            }

    # Keep the adapter open. Ignition may simply be OFF; the main loop can
    # recover as soon as the ECU begins answering without re-pairing/rejoining.
    _configure_adapter(ser, "0")
    return {
        "adapter_ok": True,
        "ecu_ok": False,
        "protocol": "auto (ECU waiting)",
        "identity": identity,
        "protocol_attempt": "auto+3/4/5/6",
        "reason": "ecu_waiting",
    }


def _open_elm() -> object | None:
    global _last_elm_meta
    try:
        import serial
    except ImportError:
        log.warning("pyserial not installed — OBD bridge disabled")
        return None
    try:
        ser = serial.Serial(
            OBD_SERIAL_DEV,
            OBD_SERIAL_BAUD_EFFECTIVE,
            timeout=ELM_IO_TIMEOUT,
        )
    except Exception as exc:
        log.warning("ELM open failed (%s): %s", OBD_SERIAL_DEV, exc)
        return None

    meta = initialise_elm(ser)
    meta["device"] = OBD_SERIAL_DEV
    _last_elm_meta = meta
    if not meta.get("adapter_ok"):
        log.warning("ELM adapter init failed on %s: %s", OBD_SERIAL_DEV, meta.get("reason"))
        try:
            ser.close()
        except Exception:
            pass
        return None
    log.info(
        "ELM adapter ready on %s — ECU=%s protocol=%s",
        OBD_SERIAL_DEV,
        "online" if meta.get("ecu_ok") else "waiting",
        meta.get("protocol"),
    )
    return ser


def query_supported_pids(ser):
    """Return the supported Mode-01 PID set, or None when ECU is silent."""
    bitmaps: dict[int, int] = {}
    for probe in SUPPORT_PROBE_PIDS:
        data = _query_pid(ser, f"01{probe:02X}")
        if data and len(data) >= 4:
            bitmaps[probe] = (
                (data[0] << 24)
                | (data[1] << 16)
                | (data[2] << 8)
                | data[3]
            )
    if not bitmaps:
        return None
    return supported_from_bitmaps(bitmaps)


def active_pid_defs(ser):
    """Narrow PID polling to what this ECU reports as supported."""
    supported = query_supported_pids(ser)
    if supported:
        defs = {c: d for c, d in PID_DEFS.items() if d["pid"] in supported}
        log.info(
            "PID discovery: ECU reports %d/%d known PIDs supported",
            len(defs),
            len(PID_DEFS),
        )
        return defs
    applicable = applies_to(vehicle_profile.fuel_type())
    defs = {c: d for c, d in PID_DEFS.items() if d["pid"] in applicable}
    log.info(
        "PID discovery got no response — polling %d powertrain-default PIDs",
        len(defs),
    )
    return defs


def _query_voltage(ser) -> float | None:
    """Read adapter supply voltage when Mode-01 PID 0x42 is unavailable."""
    try:
        raw = _exchange(ser, "ATRV", timeout_s=1.5)
    except Exception:
        return None
    text = " ".join(_response_lines(raw, "ATRV")).upper()
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*V", text)
    if not match:
        return None
    try:
        value = float(match.group(1))
    except ValueError:
        return None
    return value if 5.0 <= value <= 30.0 else None


def _decode_dtcs(data: list[int]) -> list[str]:
    """Decode Mode-03 two-byte SAE DTCs."""
    out: list[str] = []
    for i in range(0, len(data) - 1, 2):
        a, b = data[i], data[i + 1]
        if a == 0 and b == 0:
            continue
        prefix = "PCBU"[(a >> 6) & 0x03]
        d1 = (a >> 4) & 0x03
        d2 = a & 0x0F
        code = f"{prefix}{d1:X}{d2:X}{(b >> 4) & 0x0F:X}{b & 0x0F:X}"
        out.append(code)
    return out


def _query_dtcs(ser) -> list[str] | None:
    try:
        raw = _exchange(ser, "03", timeout_s=max(OBD_QUERY_TIMEOUT_SEC, 3.0))
    except Exception:
        return None
    upper = raw.upper()
    if "NO DATA" in upper:
        return []
    data = _extract_mode_payload(raw, 0x03, None)
    if data is None:
        return None
    return _decode_dtcs(data)


def _status_payload(state: str, *, device: str, meta: dict | None = None, **extra) -> dict:
    payload = {
        "state": state,
        "device": device,
        "adapter_ok": bool((meta or {}).get("adapter_ok")),
        "ecu_ok": bool((meta or {}).get("ecu_ok")),
        "protocol": (meta or {}).get("protocol", "unknown"),
        "identity": (meta or {}).get("identity", ""),
        "ts": time.time(),
    }
    payload.update(extra)
    return payload


def _idle(running_ref, seconds: float = 5.0):
    for _ in range(max(1, int(seconds / 0.25))):
        if not running_ref():
            break
        time.sleep(0.25)


def main() -> None:
    global _last_elm_meta
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
        except Exception as exc:
            log.warning("Waiting for MQTT broker... (%s)", exc)
            time.sleep(3)

    if not running:
        return
    client.loop_start()

    interval = 1.0 / max(float(os.getenv("OBD_POLL_HZ", str(OBD_POLL_HZ))), 0.5)
    snapshot: dict = {}
    last_snap = 0.0
    last_dtc = 0.0
    last_voltage = 0.0
    ser = None
    active_defs = PID_DEFS
    last_transport_check = 0.0
    transport_ok = False
    deferring = False
    empty_cycles = 0
    last_online_status = 0.0
    last_metric_ts: dict[str, float] = {}

    while running:
        now_mono = time.monotonic()
        if last_transport_check == 0.0 or now_mono - last_transport_check >= 10.0:
            transport_ok = obd_transport.select_transport() == obd_transport.ELM327
            last_transport_check = now_mono

        if not transport_ok:
            if not deferring:
                log.info(
                    "CAN transport selected — drifter-obdbridge idle "
                    "(set DRIFTER_TRANSPORT=elm327 to force ELM)"
                )
                deferring = True
            if ser is not None:
                try:
                    ser.close()
                except Exception:
                    pass
                ser = None
            client.publish(
                TOPICS["obd_status"],
                json.dumps({
                    "state": "hw_pending",
                    "reason": "deferring_to_canbridge",
                    "adapter_ok": False,
                    "ecu_ok": False,
                    "ts": time.time(),
                }),
                retain=True,
            )
            _idle(_running)
            continue
        deferring = False

        if ser is None:
            ser = _open_elm()
            if ser is None:
                client.publish(
                    TOPICS["obd_status"],
                    json.dumps({
                        "state": "hw_pending",
                        "device": OBD_SERIAL_DEV,
                        "adapter_ok": False,
                        "ecu_ok": False,
                        "reason": "adapter_unreachable",
                        "ts": time.time(),
                    }),
                    retain=True,
                )
                _idle(_running)
                continue

            active_defs = active_pid_defs(ser) if _last_elm_meta.get("ecu_ok") else PID_DEFS
            state = "online" if _last_elm_meta.get("ecu_ok") else "ecu_waiting"
            client.publish(
                TOPICS["obd_status"],
                json.dumps(_status_payload(
                    state,
                    device=str(_last_elm_meta.get("device", OBD_SERIAL_DEV)),
                    meta=_last_elm_meta,
                    reason=_last_elm_meta.get("reason"),
                )),
                retain=True,
            )
            empty_cycles = 0

        cycle_success = 0
        for pid, info in active_defs.items():
            if not running:
                break
            data = _query_pid(ser, pid)
            if data is None:
                continue
            try:
                value = info["decode"](data)
            except Exception:
                continue
            cycle_success += 1
            snapshot[info["name"]] = value
            ts = time.time()
            last_metric_ts[info["name"]] = ts
            client.publish(
                info["topic"],
                json.dumps({"value": value, "unit": info["unit"], "ts": ts}),
            )
            client.publish(
                TOPICS["obd_pid"],
                json.dumps({"pid": pid, "value": value, "ts": ts}),
            )
            time.sleep(interval)

        now = time.time()
        if cycle_success:
            empty_cycles = 0
            if not _last_elm_meta.get("ecu_ok"):
                _last_elm_meta["ecu_ok"] = True
                _last_elm_meta["reason"] = "online"
                _last_elm_meta["protocol"] = detect_protocol(ser)
                active_defs = active_pid_defs(ser)
            if now - last_online_status >= 5.0:
                client.publish(
                    TOPICS["obd_status"],
                    json.dumps(_status_payload(
                        "online",
                        device=str(_last_elm_meta.get("device", OBD_SERIAL_DEV)),
                        meta=_last_elm_meta,
                        pids_flowing=cycle_success,
                    )),
                    retain=True,
                )
                last_online_status = now
        else:
            empty_cycles += 1
            if empty_cycles >= OBD_REPROBE_AFTER_EMPTY_CYCLES:
                identity = _adapter_identity(ser)
                if not identity:
                    client.publish(
                        TOPICS["obd_status"],
                        json.dumps(_status_payload(
                            "adapter_error",
                            device=str(_last_elm_meta.get("device", OBD_SERIAL_DEV)),
                            meta=_last_elm_meta,
                            reason="ELM no longer answers ATI",
                        )),
                        retain=True,
                    )
                    try:
                        ser.close()
                    except Exception:
                        pass
                    ser = None
                    _last_elm_meta = {}
                    _idle(_running, 2.0)
                    continue

                _last_elm_meta.update({
                    "adapter_ok": True,
                    "ecu_ok": False,
                    "identity": identity,
                    "protocol": detect_protocol(ser),
                    "reason": "no_pid_response",
                })
                client.publish(
                    TOPICS["obd_status"],
                    json.dumps(_status_payload(
                        "ecu_waiting",
                        device=str(_last_elm_meta.get("device", OBD_SERIAL_DEV)),
                        meta=_last_elm_meta,
                        no_data_cycles=empty_cycles,
                    )),
                    retain=True,
                )

        # Voltage fallback for older ECUs that do not expose PID 0x42, or
        # adapters/ECUs whose support bitmap is incomplete. Use actual recent
        # voltage flow rather than assuming the definition is supported.
        voltage_stale = now - last_metric_ts.get("voltage", 0.0) >= max(
            OBD_VOLTAGE_POLL_SEC * 2.0, 4.0
        )
        if voltage_stale and now - last_voltage >= OBD_VOLTAGE_POLL_SEC:
            voltage = _query_voltage(ser)
            if voltage is not None:
                snapshot["voltage"] = voltage
                last_metric_ts["voltage"] = now
                client.publish(
                    TOPICS["voltage"],
                    json.dumps({"value": voltage, "unit": "V", "ts": now, "source": "ATRV"}),
                )
            last_voltage = now

        if now - last_dtc >= OBD_DTC_POLL_SEC and _last_elm_meta.get("ecu_ok"):
            dtcs = _query_dtcs(ser)
            if dtcs is not None:
                client.publish(
                    TOPICS["dtc"],
                    json.dumps({"stored": dtcs, "ts": now, "source": "obd_bridge"}),
                    retain=True,
                )
            last_dtc = now

        if snapshot and now - last_snap >= 1.0:
            client.publish(
                TOPICS["snapshot"],
                json.dumps({**snapshot, "ts": now, "source": "obd_bridge"}),
            )
            last_snap = now

        if not cycle_success:
            _idle(_running, 0.5)

    client.publish(
        TOPICS["obd_status"],
        json.dumps({"state": "offline", "adapter_ok": False, "ecu_ok": False, "ts": time.time()}),
        retain=True,
    )
    client.loop_stop()
    client.disconnect()
    if ser is not None:
        try:
            ser.close()
        except Exception:
            pass
    log.info("OBD Bridge stopped")


if __name__ == "__main__":
    main()

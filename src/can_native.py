#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Native CAN FD Bridge + CAN Toolkit
Drives the on-board socketcan controller (RDK X5) or a native CAN HAT,
with optional CAN FD framing. Beyond the OBD-II bridge it bundles a CAN
toolkit selectable by mode:

    bridge      poll OBD-II PIDs → MQTT metric topics (same as can_bridge.py)
    sniffer     capture raw frames → drifter/can/sniff/{frame,summary}
    fuzzer      inject synthetic/randomised frames (bench only)
    decoder_ai  LLM-cascade signal identification on observed IDs
    replay      replay a recorded JSONL capture onto the bus
    dbc_gen     emit a Vector .dbc from observed traffic

Usage: can_native.py [bridge|sniffer|fuzzer|decoder_ai|replay|dbc_gen] [args]
Publishes the same per-PID MQTT topics as can_bridge.py so downstream
services (alerts, anomaly, dashboard) are platform-agnostic.
UNCAGED TECHNOLOGY — EST 1991
"""
from __future__ import annotations

import json
import logging
import random
import signal
import sys
import time
from collections import defaultdict, deque
from pathlib import Path

import can

# Reuse the OBD decode tables + helpers from the classic bridge so PID
# definitions live in exactly one place (AGENTS.md: config/can_bridge are
# the single source of truth for PIDs).
from can_bridge import (
    PIDS,
    decode_dtc,
    decode_obd_response,
)
from config import (
    CAN_AI_COLLECT_MAX_SEC,
    CAN_AI_MIN_SAMPLES,
    CAN_AI_MIN_SATURATED_IDS,
    CAN_SNIFF_BUFFER,
    DBC_OUTPUT_DIR,
    FUZZ_DEFAULT_HZ,
    MQTT_HOST,
    MQTT_PORT,
    OBD_REQUEST_ID,
    TOPICS,
    make_mqtt_client,
)
from hardware import ensure_can_up, get_platform

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [CAN-NATIVE] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger(__name__)

DTC_CHECK_INTERVAL = 60
MAX_CONSECUTIVE_FAILURES = 20


# ═══════════════════════════════════════════════════════════════════
#  Bus helpers
# ═══════════════════════════════════════════════════════════════════
def open_bus(backend, fd: bool | None = None):
    """Open a socketcan bus for the given hardware backend.

    `fd` overrides the backend's FD setting (used by modes that need
    classic framing even on an FD-capable controller). Falls back to
    classic CAN if FD bus creation raises — older kernels / controllers
    that don't advertise FD will reject the fd kwarg.
    """
    want_fd = backend.fd if fd is None else fd
    try:
        if want_fd:
            return can.Bus(interface=backend.interface, channel=backend.channel, fd=True)
        return can.Bus(
            interface=backend.interface, channel=backend.channel, bitrate=backend.bitrate,
        )
    except (can.CanError, OSError, TypeError) as e:
        if want_fd:
            log.warning("FD bus open failed (%s) — retrying classic CAN", e)
            return can.Bus(
                interface=backend.interface, channel=backend.channel, bitrate=backend.bitrate,
            )
        raise


def _connect_mqtt(client_id: str, running: list) -> object | None:
    client = make_mqtt_client(client_id)
    while running[0]:
        try:
            client.connect(MQTT_HOST, MQTT_PORT, 60)
            client.loop_start()
            log.info("connected to MQTT broker")
            return client
        except Exception as e:
            log.warning("MQTT connect failed: %s. Retrying in 3s...", e)
            time.sleep(3)
    return None


def _install_signal_handlers(running: list) -> None:
    def _handle(sig, frame):
        running[0] = False
    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)


def _send_obd_request(bus, pid: int) -> bool:
    data = [0x02, 0x01, pid, 0x00, 0x00, 0x00, 0x00, 0x00]
    try:
        bus.send(can.Message(arbitration_id=OBD_REQUEST_ID, data=data, is_extended_id=False))
        return True
    except can.CanError as e:
        log.debug("OBD send error for PID 0x%02X: %s", pid, e)
        return False


def _request_dtcs(bus, mode: int = 0x03) -> list[str]:
    data = [0x01, mode, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]
    try:
        bus.send(can.Message(arbitration_id=OBD_REQUEST_ID, data=data, is_extended_id=False))
    except can.CanError:
        return []
    dtcs: list[str] = []
    deadline = time.monotonic() + 0.5
    response_mode = mode + 0x40
    while time.monotonic() < deadline:
        resp = bus.recv(timeout=0.1)
        if resp is None:
            break
        rd = resp.data
        if len(rd) >= 2 and rd[1] == response_mode:
            for i in range(3, len(rd) - 1, 2):
                dtc = decode_dtc(rd[i], rd[i + 1])
                if dtc:
                    dtcs.append(dtc)
    return dtcs


# ═══════════════════════════════════════════════════════════════════
#  Mode: bridge — OBD-II polling (same topic contract as can_bridge.py)
# ═══════════════════════════════════════════════════════════════════
def run_bridge() -> None:
    running = [True]
    _install_signal_handlers(running)

    platform = get_platform()
    backend = platform.can
    log.info("platform=%s channel=%s fd=%s", platform.name, backend.channel, backend.fd)
    ensure_can_up(backend)

    client = _connect_mqtt("drifter-canbridge", running)  # same client_id family as classic bridge
    if client is None:
        return

    bus = None
    while bus is None and running[0]:
        try:
            bus = open_bus(backend)
        except Exception as e:
            log.warning("bus open failed (%s) — retry 5s", e)
            time.sleep(5)
    if bus is None:
        return

    client.publish(TOPICS['can_native_status'], json.dumps({
        'status': 'up', 'platform': platform.name, 'channel': backend.channel,
        'fd': backend.fd, 'ts': time.time(),
    }), retain=True)
    client.publish(TOPICS['system_status'], json.dumps({
        'state': 'online', 'can_interface': backend.channel,
        'backend': 'native', 'fd': backend.fd, 'timestamp': time.time(),
    }), retain=True)

    schedule = [
        {'pid': pid, 'interval': 1.0 / info['hz'], 'last_poll': 0.0, 'info': info}
        for pid, info in PIDS.items()
    ]
    latest_values: dict = {}
    active_dtcs: list[str] = []
    pending_dtcs: list[str] = []
    consecutive_failures = 0
    last_snapshot = 0.0
    last_dtc_check = 0.0

    log.info("DRIFTER native CAN bridge LIVE — polling %d PIDs", len(schedule))
    while running[0]:
        try:
            now = time.monotonic()
            polled = 0
            for entry in schedule:
                if now - entry['last_poll'] < entry['interval']:
                    continue
                ok = _send_obd_request(bus, entry['pid'])
                entry['last_poll'] = now
                consecutive_failures = 0 if ok else consecutive_failures + 1
                resp = bus.recv(timeout=0.05)
                if resp:
                    result = decode_obd_response(resp)
                    if result:
                        pid, value = result
                        info = PIDS[pid]
                        latest_values[info['name']] = value
                        client.publish(info['topic'], json.dumps({
                            'value': value, 'unit': info['unit'], 'ts': time.time(),
                        }))
                polled += 1
                if polled >= 4:
                    break

            if latest_values and now - last_snapshot >= 1.0:
                client.publish(TOPICS['snapshot'], json.dumps({**latest_values, 'ts': time.time()}))
                last_snapshot = now

            if now - last_dtc_check >= DTC_CHECK_INTERVAL:
                stored = _request_dtcs(bus, 0x03)
                pending = _request_dtcs(bus, 0x07)
                if stored != active_dtcs or pending != pending_dtcs:
                    active_dtcs[:] = stored
                    pending_dtcs[:] = pending
                    client.publish(TOPICS['dtc'], json.dumps({
                        'stored': stored, 'pending': pending,
                        'count': len(stored) + len(pending), 'ts': time.time(),
                    }), retain=True)
                last_dtc_check = now

            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                log.error("CAN interface lost — %d consecutive failures, exiting for systemd restart",
                          consecutive_failures)
                break

            time.sleep(0.005)
        except can.CanError as e:
            log.error("CAN bus error: %s", e)
            time.sleep(1)

    log.info("shutting down native CAN bridge")
    client.publish(TOPICS['can_native_status'], json.dumps({'status': 'down', 'ts': time.time()}),
                   retain=True)
    client.publish(TOPICS['system_status'], json.dumps({'state': 'offline', 'timestamp': time.time()}),
                   retain=True)
    client.loop_stop()
    client.disconnect()
    try:
        bus.shutdown()
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════
#  Mode: sniffer — raw frame capture
# ═══════════════════════════════════════════════════════════════════
def run_sniffer() -> None:
    running = [True]
    _install_signal_handlers(running)
    backend = get_platform().can
    ensure_can_up(backend)

    client = _connect_mqtt("drifter-can-native-sniff", running)
    if client is None:
        return
    try:
        bus = open_bus(backend)
    except Exception as e:
        log.error("bus open failed: %s", e)
        return

    buf: deque = deque(maxlen=CAN_SNIFF_BUFFER)
    id_stats: dict = defaultdict(lambda: {'count': 0, 'first': 0.0, 'last': 0.0, 'data': ''})
    last_summary = 0.0
    client.publish(TOPICS['can_sniff_status'], json.dumps({
        'status': 'up', 'channel': backend.channel, 'fd': backend.fd, 'ts': time.time(),
    }), retain=True)
    log.info("native CAN sniffer LIVE on %s", backend.channel)

    while running[0]:
        msg = bus.recv(timeout=1.0)
        now = time.time()
        if msg is not None:
            buf.append({'ts': now, 'id': msg.arbitration_id, 'data': bytes(msg.data).hex()})
            s = id_stats[msg.arbitration_id]
            s['count'] += 1
            if s['first'] == 0.0:
                s['first'] = now
            s['last'] = now
            s['data'] = bytes(msg.data).hex()
            client.publish(TOPICS['can_sniff_frame'], json.dumps({
                'id': f"0x{msg.arbitration_id:X}", 'dlc': msg.dlc,
                'data': bytes(msg.data).hex(), 'fd': bool(getattr(msg, 'is_fd', False)),
                'ts': now,
            }))
        if now - last_summary >= 1.0:
            ids = [{
                'id': f"0x{aid:X}", 'count': s['count'],
                'hz': s['count'] / max(s['last'] - s['first'], 0.001),
                'last_data': s['data'],
            } for aid, s in id_stats.items()]
            client.publish(TOPICS['can_sniff_summary'], json.dumps({
                'ts': now, 'buffer': len(buf), 'unique_ids': len(ids), 'ids': ids,
            }))
            last_summary = now

    client.publish(TOPICS['can_sniff_status'], json.dumps({'status': 'down', 'ts': time.time()}),
                   retain=True)
    client.loop_stop()
    client.disconnect()
    try:
        bus.shutdown()
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════
#  Mode: fuzzer — synthetic frame injection (bench only)
# ═══════════════════════════════════════════════════════════════════
def run_fuzzer(hz: float = FUZZ_DEFAULT_HZ) -> None:
    running = [True]
    _install_signal_handlers(running)
    backend = get_platform().can
    ensure_can_up(backend)

    client = _connect_mqtt("drifter-can-native-fuzz", running)
    if client is None:
        return
    try:
        bus = open_bus(backend)
    except Exception as e:
        log.error("bus open failed: %s", e)
        return

    interval = 1.0 / max(hz, 0.1)
    sent = 0
    log.warning("native CAN fuzzer LIVE on %s @ %.1f Hz — BENCH USE ONLY", backend.channel, hz)
    while running[0]:
        arb_id = random.randint(0x100, 0x7FF)
        payload = bytes(random.randint(0, 255) for _ in range(8))
        try:
            bus.send(can.Message(arbitration_id=arb_id, data=payload, is_extended_id=False))
            sent += 1
        except can.CanError as e:
            log.debug("fuzz send error: %s", e)
        if sent % 50 == 0:
            client.publish(TOPICS['can_native_fuzz'], json.dumps({
                'sent': sent, 'hz': hz, 'ts': time.time(),
            }))
        time.sleep(interval)

    client.publish(TOPICS['can_native_fuzz'], json.dumps({
        'status': 'stopped', 'sent': sent, 'ts': time.time(),
    }), retain=True)
    client.loop_stop()
    client.disconnect()
    try:
        bus.shutdown()
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════
#  Mode: decoder_ai — LLM-cascade signal identification
# ═══════════════════════════════════════════════════════════════════
def run_decoder_ai(samples: int = CAN_AI_MIN_SAMPLES) -> None:
    running = [True]
    _install_signal_handlers(running)
    backend = get_platform().can
    ensure_can_up(backend)

    client = _connect_mqtt("drifter-can-native-decoder", running)
    if client is None:
        return
    try:
        bus = open_bus(backend)
    except Exception as e:
        log.error("bus open failed: %s", e)
        return

    log.info("collecting %d frames per ID before AI inference...", samples)
    history: dict = defaultdict(list)
    # Bound the collection window. Requiring EVERY observed ID to reach
    # `samples` never completes on a live bus — new IDs keep appearing and
    # low-rate IDs (DTC, door modules) emit only a few frames/min — so the old
    # min()/all() conditions left this running until SIGTERM with no inference.
    # Stop on a time budget, or early once a healthy spread of IDs is saturated.
    deadline = time.time() + CAN_AI_COLLECT_MAX_SEC
    while running[0] and time.time() < deadline:
        msg = bus.recv(timeout=1.0)
        if msg is not None and len(history[msg.arbitration_id]) < samples:
            history[msg.arbitration_id].append(bytes(msg.data).hex())
        if sum(1 for v in history.values() if len(v) >= samples) >= CAN_AI_MIN_SATURATED_IDS:
            break

    observed = {f"0x{aid:X}": frames[:samples] for aid, frames in history.items()}
    result: dict
    try:
        from llm_client_v2 import query_json
        prompt = (
            "You are a CAN bus reverse-engineering assistant. Given observed "
            "frames per arbitration ID (hex payloads over time), identify likely "
            "signals (name, byte offset, scale, unit). Respond as JSON: "
            '{"signals": [{"id": "0x...", "name": "...", "offset": int, '
            '"scale": float, "unit": "..."}]}.\n\n'
            f"Observed frames: {json.dumps(observed)[:6000]}"
        )
        result = query_json(prompt) or {'signals': []}
    except Exception as e:
        log.warning("AI decode unavailable (%s) — emitting raw observation", e)
        result = {'signals': [], 'error': str(e), 'observed_ids': list(observed.keys())}

    client.publish(TOPICS['can_decode_response'], json.dumps({
        'ts': time.time(), 'unique_ids': len(observed), 'result': result,
    }), retain=True)
    log.info("decoder_ai published %d candidate signals", len(result.get('signals', [])))
    client.loop_stop()
    client.disconnect()
    try:
        bus.shutdown()
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════
#  Mode: replay — replay a recorded JSONL capture onto the bus
# ═══════════════════════════════════════════════════════════════════
def _load_capture(path: Path) -> list[dict]:
    frames: list[dict] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            frames.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return frames


def run_replay(capture_path: str, speed: float = 1.0) -> None:
    running = [True]
    _install_signal_handlers(running)
    path = Path(capture_path)
    if not path.exists():
        log.error("capture file not found: %s", path)
        return
    backend = get_platform().can
    ensure_can_up(backend)

    client = _connect_mqtt("drifter-can-native-replay", running)
    if client is None:
        return
    try:
        bus = open_bus(backend)
    except Exception as e:
        log.error("bus open failed: %s", e)
        return

    frames = _load_capture(path)
    log.info("replaying %d frames from %s at %.1fx", len(frames), path.name, speed)
    prev_ts = None
    played = 0
    for fr in frames:
        if not running[0]:
            break
        ts = fr.get('ts')
        if prev_ts is not None and ts is not None and speed > 0:
            gap = max((ts - prev_ts) / speed, 0.0)
            if gap > 0:
                time.sleep(min(gap, 5.0))
        prev_ts = ts
        try:
            arb_id = int(str(fr.get('id', '0')).replace('0x', ''), 16) if 'id' in fr else 0
            data = bytes.fromhex(fr.get('data', ''))
            bus.send(can.Message(arbitration_id=arb_id, data=data, is_extended_id=False))
            played += 1
        except (ValueError, can.CanError) as e:
            log.debug("replay frame skipped: %s", e)
        if played % 100 == 0:
            client.publish(TOPICS['can_native_replay'], json.dumps({
                'played': played, 'total': len(frames), 'ts': time.time(),
            }))

    client.publish(TOPICS['can_native_replay'], json.dumps({
        'status': 'done', 'played': played, 'total': len(frames), 'ts': time.time(),
    }), retain=True)
    log.info("replay complete: %d/%d frames", played, len(frames))
    client.loop_stop()
    client.disconnect()
    try:
        bus.shutdown()
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════
#  Mode: dbc_gen — emit a Vector .dbc from observed traffic
# ═══════════════════════════════════════════════════════════════════
def run_dbc_gen(duration: float = 30.0) -> None:
    running = [True]
    _install_signal_handlers(running)
    backend = get_platform().can
    ensure_can_up(backend)

    client = _connect_mqtt("drifter-can-native-dbc", running)
    if client is None:
        return
    try:
        bus = open_bus(backend)
    except Exception as e:
        log.error("bus open failed: %s", e)
        return

    log.info("observing CAN traffic for %.0fs to generate .dbc", duration)
    seen: dict = {}
    deadline = time.monotonic() + duration
    while running[0] and time.monotonic() < deadline:
        msg = bus.recv(timeout=1.0)
        if msg is not None:
            seen.setdefault(msg.arbitration_id, msg.dlc)

    DBC_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out = DBC_OUTPUT_DIR / "drifter_observed.dbc"
    lines = [
        'VERSION ""',
        '',
        'NS_ :',
        '',
        'BS_:',
        '',
        'BU_ DRIFTER',
        '',
    ]
    for aid in sorted(seen):
        dlc = seen[aid]
        lines.append(f'BO_ {aid} MSG_{aid:X}: {dlc} DRIFTER')
        lines.append(f' SG_ raw_{aid:X} : 0|{dlc * 8}@1+ (1,0) [0|0] "" DRIFTER')
        lines.append('')
    try:
        out.write_text("\n".join(lines))
        log.info("wrote %s (%d messages)", out, len(seen))
        client.publish(TOPICS['can_dbc_generated'], json.dumps({
            'path': str(out), 'messages': len(seen), 'ts': time.time(),
        }), retain=True)
    except Exception as e:
        log.error("dbc write failed: %s", e)

    client.loop_stop()
    client.disconnect()
    try:
        bus.shutdown()
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════
#  Entry point
# ═══════════════════════════════════════════════════════════════════
MODES = {
    'bridge': run_bridge,
    'sniffer': run_sniffer,
    'fuzzer': run_fuzzer,
    'decoder_ai': run_decoder_ai,
    'replay': run_replay,
    'dbc_gen': run_dbc_gen,
}


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else 'bridge'
    if mode not in MODES:
        log.error("unknown mode '%s' — choose from: %s", mode, ", ".join(MODES))
        sys.exit(2)
    args = sys.argv[2:]
    fn = MODES[mode]
    if mode == 'replay':
        if not args:
            log.error("replay mode requires a capture file path")
            sys.exit(2)
        speed = float(args[1]) if len(args) > 1 else 1.0
        fn(args[0], speed)
    elif mode == 'fuzzer':
        fn(float(args[0]) if args else FUZZ_DEFAULT_HZ)
    elif mode == 'decoder_ai':
        fn(int(args[0]) if args else CAN_AI_MIN_SAMPLES)
    elif mode == 'dbc_gen':
        fn(float(args[0]) if args else 30.0)
    else:
        fn()


if __name__ == '__main__':
    main()

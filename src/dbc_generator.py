#!/usr/bin/env python3
"""
MZ1312 DRIFTER — DBC Generator
Builds a CAN DBC file from observations: sniffer summaries identify
arbitration IDs and frequencies, can_decoder_ai responses tag them with
signal names, and this module emits a Vector .dbc that can be loaded
into CAN tools (SavvyCAN, BusMaster, etc).
UNCAGED TECHNOLOGY — EST 1991
"""

import json
import logging
import signal
import threading
import time
from pathlib import Path

import paho.mqtt.client as mqtt

from config import (
    DBC_OUTPUT_DIR,
    MQTT_HOST,
    MQTT_PORT,
    TOPICS,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [DBC] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger(__name__)

_lock = threading.Lock()
# Keyed by canonical int arbitration id so the two feeds join regardless of
# whether a producer emits "0x7E8", "2024", or 2024.
_observed: dict[int, dict] = {}     # arb_id -> {hz, dlc, last_data}
_classified: dict[int, dict] = {}   # arb_id -> {signal_name, byte_layout, ...}


def _parse_id(arb_id) -> int | None:
    """Normalise an arbitration id to int. Returns None on garbage rather than
    raising — an unparseable id from a malformed payload must not kill the emit
    thread (this runs as a sort key over live MQTT data)."""
    if isinstance(arb_id, int):
        return arb_id
    if not isinstance(arb_id, str):
        return None
    s = arb_id.strip()
    try:
        if s.lower().startswith('0x'):
            return int(s, 16)
        return int(s)
    except ValueError:
        try:
            return int(s, 16)
        except ValueError:
            return None


def _emit_dbc(path: Path) -> None:
    """Emit a minimal but well-formed DBC."""
    lines = [
        'VERSION ""',
        '',
        'NS_ :',
        '',
        'BS_:',
        '',
        'BU_: DRIFTER',
        '',
    ]
    with _lock:
        ids = sorted(_observed.keys())  # keys are already canonical ints
        for id_int in ids:
            obs = _observed[id_int]
            cls = _classified.get(id_int, {})
            signal_name = (cls.get('signal_name') or 'UNKNOWN').upper()
            dlc = obs.get('dlc') or 8
            msg_name = f"DRIFTER_{signal_name}_{id_int:X}"
            lines.append(f"BO_ {id_int} {msg_name}: {dlc} DRIFTER")
            # crude: one signal across the first 2 bytes, little-endian (@1) to
            # match can_native's generator and load in strict DBC parsers.
            lines.append(f" SG_ {signal_name} : 0|16@1+ (1,0) [0|65535] \"\" Vector__XXX")
            lines.append('')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))
    log.info(f"DBC written: {path} ({len(_observed)} IDs)")


def _on_message(client: mqtt.Client, _u, msg) -> None:
    try:
        data = json.loads(msg.payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return
    if not isinstance(data, dict):
        return
    if msg.topic == TOPICS['can_sniff_summary']:
        with _lock:
            for entry in data.get('ids', []):
                key = _parse_id(entry.get('id'))
                if key is None:
                    continue
                _observed[key] = {
                    'hz': entry.get('hz', 0),
                    'count': entry.get('count', 0),
                    'last_data': entry.get('last_data', ''),
                    'dlc': len(entry.get('last_data', '')) // 2,
                }
    elif msg.topic == TOPICS['can_decode_response']:
        key = _parse_id(data.get('id'))
        if key is not None:
            with _lock:
                _classified[key] = data


def _emit_loop(client: mqtt.Client, running: list) -> None:
    Path(DBC_OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    last_count = 0
    while running[0]:
        time.sleep(30)
        with _lock:
            count = len(_observed)
        if count > last_count:
            path = Path(DBC_OUTPUT_DIR) / "drifter_observed.dbc"
            _emit_dbc(path)
            client.publish(TOPICS['can_dbc_generated'], json.dumps({
                'path': str(path), 'ids': count, 'ts': time.time(),
            }), retain=True)
            last_count = count


def main() -> None:
    log.info("DRIFTER DBC Generator starting...")

    running = [True]

    def _handle_signal(sig, frame):
        running[0] = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    client = mqtt.Client(client_id="drifter-dbc-gen")

    def cb(c, u, msg):
        _on_message(client, u, msg)

    client.on_message = cb

    connected = False
    while not connected and running[0]:
        try:
            client.connect(MQTT_HOST, MQTT_PORT, 60)
            connected = True
        except Exception as e:
            log.warning(f"Waiting for MQTT broker... ({e})")
            time.sleep(3)

    if not running[0]:
        return

    client.subscribe([
        (TOPICS['can_sniff_summary'], 0),
        (TOPICS['can_decode_response'], 1),
    ])
    client.loop_start()
    log.info(f"DBC Generator LIVE — output: {DBC_OUTPUT_DIR}")

    threading.Thread(target=_emit_loop, args=(client, running), daemon=True).start()

    while running[0]:
        time.sleep(1)

    client.loop_stop()
    client.disconnect()
    log.info("DBC Generator stopped")


if __name__ == '__main__':
    main()

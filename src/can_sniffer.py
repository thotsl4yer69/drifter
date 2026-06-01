#!/usr/bin/env python3
"""
MZ1312 DRIFTER — CAN Sniffer
Captures raw CAN bus traffic into a rolling buffer, publishes frames and
periodic summaries (count / hz / unique IDs). Designed for offline
reverse engineering of unknown ECUs alongside can_decoder_ai.py.
UNCAGED TECHNOLOGY — EST 1991
"""

import json
import logging
import signal
import threading
import time
from collections import defaultdict, deque

import paho.mqtt.client as mqtt

from config import (
    CAN_BITRATE,
    CAN_SNIFF_BUFFER,
    CAN_SNIFF_SUMMARY_HZ,
    MQTT_HOST,
    MQTT_PORT,
    TOPICS,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [CAN-SNIFF] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger(__name__)

_lock = threading.Lock()
_buf: deque = deque(maxlen=CAN_SNIFF_BUFFER)
_id_stats: dict[int, dict] = defaultdict(lambda: {'count': 0, 'first_ts': 0.0, 'last_ts': 0.0, 'data': b''})


def _record(arb_id: int, data: bytes) -> None:
    now = time.time()
    with _lock:
        _buf.append({'ts': now, 'id': arb_id, 'data': data.hex()})
        s = _id_stats[arb_id]
        s['count'] += 1
        if s['first_ts'] == 0.0:
            s['first_ts'] = now
        s['last_ts'] = now
        s['data'] = data


def _summary_loop(client: mqtt.Client, running: list) -> None:
    interval = 1.0 / max(CAN_SNIFF_SUMMARY_HZ, 0.1)
    while running[0]:
        time.sleep(interval)
        with _lock:
            total = len(_buf)
            ids = []
            for arb_id, s in _id_stats.items():
                ids.append({
                    'id': f"0x{arb_id:X}",
                    'count': s['count'],
                    'hz': s['count'] / max(s['last_ts'] - s['first_ts'], 0.001),
                    'last_data': s['data'].hex() if isinstance(s['data'], bytes) else str(s['data']),
                })
        client.publish(TOPICS['can_sniff_summary'], json.dumps({
            'ts': time.time(),
            'buffer': total,
            'unique_ids': len(ids),
            'ids': ids,
        }))


def _capture_loop(client: mqtt.Client, channel: str, running: list) -> None:
    try:
        import can
    except ImportError:
        log.error("python-can not installed — CAN sniffer disabled")
        return
    while running[0]:
        try:
            bus = can.interface.Bus(channel=channel, bustype='socketcan', bitrate=CAN_BITRATE)
        except Exception as e:
            log.warning(f"bus open failed ({e}) — retry 5s")
            time.sleep(5)
            continue
        log.info(f"sniffing on {channel}")
        while running[0]:
            try:
                msg = bus.recv(timeout=1.0)
            except Exception as e:
                log.warning(f"recv error: {e}")
                break
            if msg is None:
                continue
            _record(msg.arbitration_id, bytes(msg.data))
            client.publish(TOPICS['can_sniff_frame'], json.dumps({
                'id': f"0x{msg.arbitration_id:X}",
                'dlc': msg.dlc,
                'data': bytes(msg.data).hex(),
                'ts': time.time(),
            }))
        try:
            bus.shutdown()
        except Exception:
            pass


def main() -> None:
    log.info("DRIFTER CAN Sniffer starting...")
    channel = 'can0'

    running = [True]

    def _handle_signal(sig, frame):
        running[0] = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    client = mqtt.Client(client_id="drifter-can-sniffer")
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

    client.loop_start()
    log.info(f"CAN Sniffer LIVE — channel: {channel}")
    client.publish(TOPICS['can_sniff_status'], json.dumps({
        'status': 'up', 'channel': channel, 'ts': time.time(),
    }), retain=True)

    threading.Thread(target=_summary_loop, args=(client, running), daemon=True).start()
    threading.Thread(target=_capture_loop, args=(client, channel, running), daemon=True).start()

    while running[0]:
        time.sleep(1)

    client.publish(TOPICS['can_sniff_status'], json.dumps({
        'status': 'down', 'ts': time.time(),
    }), retain=True)
    client.loop_stop()
    client.disconnect()
    log.info("CAN Sniffer stopped")


if __name__ == '__main__':
    main()

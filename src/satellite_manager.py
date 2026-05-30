#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Satellite Manager
ESP32 satellite-node discovery + management. Listens on a UDP broadcast
port for `DRIFTER_SAT_ANNOUNCE` packets from ESP32s (e.g. tire-pressure
peripherals, remote sensors), tracks their state, and surfaces them on
the MQTT bus so other services can subscribe.
UNCAGED TECHNOLOGY — EST 1991
"""

import json
import logging
import signal
import socket
import threading
import time

import paho.mqtt.client as mqtt

from config import (
    MQTT_HOST,
    MQTT_PORT,
    SATELLITE_DISCOVERY_PORT,
    SATELLITE_HEARTBEAT_TIMEOUT,
    TOPICS,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [SAT] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger(__name__)

_lock = threading.Lock()
_satellites: dict[str, dict] = {}


def _on_announce(client: mqtt.Client, payload: dict, addr: tuple) -> None:
    sat_id = payload.get('id') or payload.get('mac') or f"{addr[0]}:{addr[1]}"
    now = time.time()
    with _lock:
        existing = _satellites.get(sat_id)
        is_new = existing is None
        _satellites[sat_id] = {
            'id': sat_id,
            'address': addr[0],
            'port': addr[1],
            'type': payload.get('type', 'unknown'),
            'caps': payload.get('caps', []),
            'rssi': payload.get('rssi'),
            'firmware': payload.get('firmware'),
            'last_seen': now,
        }
    if is_new:
        log.info(f"+ satellite {sat_id} type={payload.get('type')} addr={addr[0]}")
    client.publish(TOPICS['satellite_announce'], json.dumps({
        **payload, 'address': addr[0], 'port': addr[1], 'ts': now,
    }), qos=1)


def _on_telemetry(client: mqtt.Client, payload: dict, addr: tuple) -> None:
    sat_id = payload.get('id') or payload.get('mac')
    if not sat_id:
        return
    with _lock:
        if sat_id in _satellites:
            _satellites[sat_id]['last_seen'] = time.time()
    client.publish(TOPICS['satellite_telemetry'], json.dumps({
        **payload, 'address': addr[0], 'ts': time.time(),
    }))


def _udp_loop(client: mqtt.Client, running: list) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    except Exception:
        pass
    try:
        sock.bind(('0.0.0.0', SATELLITE_DISCOVERY_PORT))
    except OSError as e:
        log.error(f"bind {SATELLITE_DISCOVERY_PORT} failed: {e}")
        return
    sock.settimeout(1.0)
    log.info(f"UDP listening on :{SATELLITE_DISCOVERY_PORT}")

    while running[0]:
        try:
            data, addr = sock.recvfrom(2048)
        except TimeoutError:
            continue
        except Exception as e:
            log.warning(f"recv error: {e}")
            continue
        try:
            payload = json.loads(data.decode('utf-8', errors='replace'))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        kind = payload.get('kind', 'telemetry').lower()
        if kind in ('announce', 'hello'):
            _on_announce(client, payload, addr)
        else:
            _on_telemetry(client, payload, addr)

    try:
        sock.close()
    except Exception:
        pass


def _expire_loop(client: mqtt.Client, running: list) -> None:
    while running[0]:
        time.sleep(15)
        now = time.time()
        with _lock:
            stale = [s for s, v in _satellites.items()
                     if (now - v.get('last_seen', 0)) > SATELLITE_HEARTBEAT_TIMEOUT]
            for s in stale:
                _satellites.pop(s, None)
                log.info(f"- satellite {s} aged out")
                client.publish(TOPICS['satellite_status'], json.dumps({
                    'event': 'offline', 'id': s, 'ts': now,
                }))


def _command_loop(client: mqtt.Client) -> None:
    # forward MQTT commands back to satellites via UDP unicast
    pass  # placeholder: future ESP32 firmware will define an inbound port


def _status_loop(client: mqtt.Client, running: list) -> None:
    while running[0]:
        time.sleep(10)
        with _lock:
            snapshot = list(_satellites.values())
        client.publish(TOPICS['satellite_status'], json.dumps({
            'count': len(snapshot), 'satellites': snapshot, 'ts': time.time(),
        }), retain=True)


def main() -> None:
    log.info("DRIFTER Satellite Manager starting...")

    running = [True]

    def _handle_signal(sig, frame):
        running[0] = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    client = mqtt.Client(client_id="drifter-satellite")
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

    client.subscribe(TOPICS['satellite_command'], qos=1)
    client.loop_start()
    log.info(f"Satellite Manager LIVE — UDP :{SATELLITE_DISCOVERY_PORT}")

    threading.Thread(target=_udp_loop, args=(client, running), daemon=True).start()
    threading.Thread(target=_expire_loop, args=(client, running), daemon=True).start()
    threading.Thread(target=_status_loop, args=(client, running), daemon=True).start()

    while running[0]:
        time.sleep(1)

    client.loop_stop()
    client.disconnect()
    log.info("Satellite Manager stopped")


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Mesh Coordinator
Tracks the current mesh topology by listening to discovery announcements,
ages out stale nodes, and publishes a coherent topology snapshot on a
periodic cadence. Other services subscribe to topology to know who is up.
UNCAGED TECHNOLOGY — EST 1991
"""

import json
import logging
import signal
import threading
import time
from typing import Optional

import paho.mqtt.client as mqtt

from config import (
    MQTT_HOST, MQTT_PORT, TOPICS,
    MESH_NODE_TTL,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [MESH-CO] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger(__name__)

_lock = threading.Lock()
_nodes: dict[str, dict] = {}


def _on_message(client, _u, msg) -> None:
    try:
        data = json.loads(msg.payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return
    if not isinstance(data, dict):
        return
    topic = msg.topic
    now = time.time()
    if topic == TOPICS['mesh_announce']:
        name = data.get('name')
        action = data.get('action')
        if not name:
            return
        with _lock:
            if action == 'removed':
                _nodes.pop(name, None)
                log.info(f"node removed: {name}")
            else:
                _nodes[name] = {
                    'name': name,
                    'info': data.get('info', {}),
                    'last_seen': now,
                }
                log.info(f"node up: {name}")
    elif topic == TOPICS['mesh_node']:
        hostname = data.get('hostname')
        if not hostname:
            return
        with _lock:
            entry = _nodes.setdefault(hostname, {'name': hostname})
            entry['last_seen'] = now
            entry['info'] = data


def _expire_loop(client: mqtt.Client, running: list) -> None:
    while running[0]:
        time.sleep(5)
        now = time.time()
        with _lock:
            stale = [n for n, v in _nodes.items()
                     if (now - v.get('last_seen', 0)) > MESH_NODE_TTL]
            for n in stale:
                _nodes.pop(n, None)
                log.info(f"node aged out: {n}")


def _topology_loop(client: mqtt.Client, running: list) -> None:
    while running[0]:
        with _lock:
            snapshot = {
                'ts': time.time(),
                'count': len(_nodes),
                'nodes': list(_nodes.values()),
            }
        client.publish(
            TOPICS['mesh_topology'],
            json.dumps(snapshot),
            qos=1,
            retain=True,
        )
        time.sleep(10)


def main() -> None:
    log.info("DRIFTER Mesh Coordinator starting...")

    running = [True]

    def _handle_signal(sig, frame):
        running[0] = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    client = mqtt.Client(client_id="drifter-mesh-coordinator")
    client.on_message = _on_message

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
        (TOPICS['mesh_announce'], 1),
        (TOPICS['mesh_node'], 0),
    ])
    client.loop_start()
    log.info("Mesh Coordinator LIVE")

    threading.Thread(target=_expire_loop, args=(client, running), daemon=True).start()
    threading.Thread(target=_topology_loop, args=(client, running), daemon=True).start()

    while running[0]:
        time.sleep(1)

    client.publish(TOPICS['mesh_status'], json.dumps({'status': 'down', 'ts': time.time()}), retain=True)
    client.loop_stop()
    client.disconnect()
    log.info("Mesh Coordinator stopped")


if __name__ == '__main__':
    main()

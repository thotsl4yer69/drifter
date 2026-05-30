#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Mesh Discovery
mDNS (zeroconf) auto-discovery of Sentient Core / Drifter nodes on the
local network. Publishes discovered peers to MQTT for the coordinator.
UNCAGED TECHNOLOGY — EST 1991
"""

import json
import logging
import signal
import socket
import time
from typing import Optional

import paho.mqtt.client as mqtt

from config import (
    MQTT_HOST, MQTT_PORT, TOPICS,
    MESH_SERVICE_NAME, MESH_DISCOVERY_INTERVAL,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [MESH] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger(__name__)


def _local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        return s.getsockname()[0]
    except Exception:
        return '127.0.0.1'
    finally:
        s.close()


def _build_node_info() -> dict:
    return {
        'hostname': socket.gethostname(),
        'ip': _local_ip(),
        'service': MESH_SERVICE_NAME,
        'port': 1883,
        'ts': time.time(),
    }


class DiscoveryListener:
    """zeroconf listener that forwards updates to MQTT."""

    def __init__(self, client: mqtt.Client) -> None:
        self.client = client
        self.peers: dict[str, dict] = {}

    def _publish(self, action: str, name: str, info: dict) -> None:
        self.peers[name] = info if action != 'removed' else {}
        self.client.publish(
            TOPICS['mesh_announce'],
            json.dumps({'action': action, 'name': name, 'info': info, 'ts': time.time()}),
            qos=1,
        )

    def add_service(self, zc, type_, name) -> None:
        info = zc.get_service_info(type_, name)
        if not info:
            return
        record = {
            'addresses': [socket.inet_ntoa(a) for a in info.addresses],
            'port': info.port,
            'properties': {
                k.decode(errors='replace'): v.decode(errors='replace') if isinstance(v, bytes) else v
                for k, v in (info.properties or {}).items()
            },
        }
        log.info(f"+ peer {name} @ {record['addresses']}")
        self._publish('added', name, record)

    def update_service(self, zc, type_, name) -> None:
        self.add_service(zc, type_, name)

    def remove_service(self, zc, type_, name) -> None:
        log.info(f"- peer {name}")
        self._publish('removed', name, {})


def main() -> None:
    log.info("DRIFTER Mesh Discovery starting...")
    try:
        from zeroconf import Zeroconf, ServiceBrowser, ServiceInfo
    except ImportError:
        log.error("zeroconf not installed — install: pip install zeroconf")
        return

    running = [True]

    def _handle_signal(sig, frame):
        running[0] = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    client = mqtt.Client(client_id="drifter-mesh-discovery")
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

    zc = Zeroconf()
    info = _build_node_info()
    service_info = ServiceInfo(
        MESH_SERVICE_NAME,
        f"{info['hostname']}.{MESH_SERVICE_NAME}",
        addresses=[socket.inet_aton(info['ip'])],
        port=info['port'],
        properties={'hostname': info['hostname'], 'role': 'drifter-node'},
    )
    zc.register_service(service_info)
    listener = DiscoveryListener(client)
    browser = ServiceBrowser(zc, MESH_SERVICE_NAME, listener)

    log.info(f"Discovery LIVE — service={MESH_SERVICE_NAME} ip={info['ip']}")

    last_publish = 0
    while running[0]:
        if time.time() - last_publish > MESH_DISCOVERY_INTERVAL:
            client.publish(
                TOPICS['mesh_node'],
                json.dumps({**info, 'peer_count': len(listener.peers)}),
                qos=0,
            )
            last_publish = time.time()
        time.sleep(1)

    zc.unregister_service(service_info)
    zc.close()
    client.loop_stop()
    client.disconnect()
    log.info("Mesh Discovery stopped")


if __name__ == '__main__':
    main()

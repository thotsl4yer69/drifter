#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Mesh Bridge
MQTT-to-MQTT bridge that selectively forwards topics between the local
broker and one or more remote brokers (other Drifter nodes / Sentient
Core). Loop prevention via a topic prefix on bridged messages.
UNCAGED TECHNOLOGY — EST 1991
"""

import json
import logging
import signal
import threading
import time

import paho.mqtt.client as mqtt

from config import (
    DRIFTER_DIR,
    MESH_BRIDGE_QOS,
    MQTT_HOST,
    MQTT_PORT,
    TOPICS,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [MESH-BR] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger(__name__)

CONFIG_PATH = DRIFTER_DIR / "mesh.yaml"
LOOP_MARKER = "__mesh_origin__"


def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {'remotes': [], 'forward_topics': ['drifter/fleet/#', 'drifter/mesh/#']}
    try:
        import yaml
        return yaml.safe_load(CONFIG_PATH.read_text()) or {}
    except Exception as e:
        log.warning(f"mesh.yaml load failed: {e}")
        return {}


class Bridge:
    def __init__(self, local: mqtt.Client, remote_cfg: dict, forward_topics: list[str]) -> None:
        self.local = local
        self.cfg = remote_cfg
        self.name = remote_cfg.get('name', remote_cfg.get('host', 'remote'))
        self.forward_topics = forward_topics
        self.remote = mqtt.Client(client_id=f"drifter-bridge-{self.name}")
        self.connected = False
        if remote_cfg.get('username'):
            self.remote.username_pw_set(remote_cfg['username'], remote_cfg.get('password', ''))
        self.remote.on_message = self._on_remote_msg
        self.remote.on_connect = self._on_remote_connect

    def _on_remote_connect(self, c, u, f, rc) -> None:
        if rc == 0:
            log.info(f"[{self.name}] remote connected")
            for t in self.forward_topics:
                c.subscribe(t, qos=MESH_BRIDGE_QOS)
            self.connected = True

    def _on_remote_msg(self, c, _u, msg) -> None:
        # incoming from remote → republish locally with origin marker
        payload = msg.payload
        try:
            data = json.loads(payload)
            if isinstance(data, dict) and data.get(LOOP_MARKER) == 'local':
                return  # we sent this
            if isinstance(data, dict):
                data[LOOP_MARKER] = self.name
                payload = json.dumps(data).encode()
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
        self.local.publish(msg.topic, payload, qos=MESH_BRIDGE_QOS)

    def forward_local(self, topic: str, payload: bytes) -> None:
        if not self.connected:
            return
        try:
            data = json.loads(payload)
            if isinstance(data, dict) and data.get(LOOP_MARKER):
                return  # came from another bridge — don't loop back
            if isinstance(data, dict):
                data[LOOP_MARKER] = 'local'
                payload = json.dumps(data).encode()
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
        self.remote.publish(topic, payload, qos=MESH_BRIDGE_QOS)

    def connect(self) -> None:
        host = self.cfg.get('host', 'localhost')
        port = int(self.cfg.get('port', 1883))
        while True:
            try:
                self.remote.connect(host, port, 60)
                self.remote.loop_start()
                return
            except Exception as e:
                log.warning(f"[{self.name}] connect {host}:{port} failed ({e}) — retry 5s")
                time.sleep(5)

    def disconnect(self) -> None:
        try:
            self.remote.loop_stop()
            self.remote.disconnect()
        except Exception:
            pass


def main() -> None:
    log.info("DRIFTER Mesh Bridge starting...")
    cfg = _load_config()
    remotes = cfg.get('remotes', [])
    forward_topics = cfg.get('forward_topics', ['drifter/fleet/#', 'drifter/mesh/#'])

    if not remotes:
        log.warning("No remotes configured — bridge will idle. Edit /opt/drifter/mesh.yaml.")

    running = [True]

    def _handle_signal(sig, frame):
        running[0] = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    local = mqtt.Client(client_id="drifter-mesh-bridge-local")
    bridges: list[Bridge] = []

    def on_local_msg(_c, _u, msg) -> None:
        for b in bridges:
            b.forward_local(msg.topic, msg.payload)

    local.on_message = on_local_msg
    connected = False
    while not connected and running[0]:
        try:
            local.connect(MQTT_HOST, MQTT_PORT, 60)
            connected = True
        except Exception as e:
            log.warning(f"Waiting for MQTT broker... ({e})")
            time.sleep(3)

    if not running[0]:
        return

    for t in forward_topics:
        local.subscribe(t, qos=MESH_BRIDGE_QOS)
    local.loop_start()

    for r in remotes:
        b = Bridge(local, r, forward_topics)
        bridges.append(b)
        threading.Thread(target=b.connect, daemon=True).start()

    log.info(f"Mesh Bridge LIVE — {len(bridges)} remote(s), topics={forward_topics}")
    local.publish(TOPICS['mesh_bridge'], json.dumps({
        'status': 'up', 'remotes': len(bridges), 'ts': time.time(),
    }), retain=True)

    while running[0]:
        time.sleep(1)

    for b in bridges:
        b.disconnect()
    local.loop_stop()
    local.disconnect()
    log.info("Mesh Bridge stopped")


if __name__ == '__main__':
    main()

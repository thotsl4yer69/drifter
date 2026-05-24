"""Thin client used by opsec_dashboard.py to talk to drifter-marauder
over MQTT. Subscribes to status (retained) so /api/marauder/status is
near-instant; publishes commands on /api/marauder/cmd.
"""

import collections
import json
import threading
import uuid

_STATUS = {"state": "unknown", "transport": "unknown", "ts": 0}
_LOCK = threading.Lock()

_RING_CAP = 200
_RINGS: dict[str, collections.deque] = {
    "ap":    collections.deque(maxlen=_RING_CAP),
    "sta":   collections.deque(maxlen=_RING_CAP),
    "probe": collections.deque(maxlen=_RING_CAP),
}


def _install_scan_rings(mqtt_client) -> None:
    """Subscribe to scan streams and keep rolling rings for /scan/recent."""
    mqtt_client.subscribe("drifter/marauder/scan/ap", qos=0)
    mqtt_client.subscribe("drifter/marauder/scan/sta", qos=0)
    mqtt_client.subscribe("drifter/marauder/scan/probe", qos=0)
    prev = mqtt_client.on_message

    def on_message(client, userdata, msg):
        try:
            if msg.topic == "drifter/marauder/scan/ap":
                _RINGS["ap"].append(json.loads(msg.payload.decode()))
            elif msg.topic == "drifter/marauder/scan/sta":
                _RINGS["sta"].append(json.loads(msg.payload.decode()))
            elif msg.topic == "drifter/marauder/scan/probe":
                _RINGS["probe"].append(json.loads(msg.payload.decode()))
        except Exception:
            pass
        if prev:
            prev(client, userdata, msg)
    mqtt_client.on_message = on_message


def get_scan_recent(stream: str, n: int = 200) -> list[dict]:
    ring = _RINGS.get(stream)
    if ring is None:
        return []
    n = max(1, min(int(n), _RING_CAP))
    return list(ring)[-n:]


def install(mqtt_client) -> None:
    """Hook the existing mqtt_client to keep _STATUS fresh."""
    mqtt_client.subscribe("drifter/marauder/status", qos=0)

    prev_on_message = mqtt_client.on_message

    def on_message(client, userdata, msg):
        if msg.topic == "drifter/marauder/status":
            try:
                with _LOCK:
                    _STATUS.update(json.loads(msg.payload.decode()))
            except Exception:
                pass
        if prev_on_message:
            prev_on_message(client, userdata, msg)
    mqtt_client.on_message = on_message

    _install_scan_rings(mqtt_client)


def get_status() -> dict:
    with _LOCK:
        return dict(_STATUS)


def publish_cmd(mqtt_client, command: str, args: dict | None = None,
                confirm_token: str | None = None) -> str:
    op_id = uuid.uuid4().hex
    payload = {"id": op_id, "command": command, "args": args or {}}
    if confirm_token:
        payload["confirm_token"] = confirm_token
    mqtt_client.publish("drifter/marauder/cmd",
                        json.dumps(payload, separators=(",", ":")),
                        qos=0, retain=False)
    return op_id

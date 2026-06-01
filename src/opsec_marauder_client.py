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


import hmac
import os
import secrets
import time as _time
from pathlib import Path as _Path

_PORTAL_STATE_ROOT = _Path(os.environ.get(
    "MARAUDER_STATE_ROOT", "/opt/drifter/state/marauder"))
_REVEAL_TOKENS: dict[str, tuple[float, str]] = {}  # token → (expiry_ts, session_id)
_REVEAL_TTL_S = 60


def list_portal_sessions() -> list[dict]:
    out = []
    pdir = _PORTAL_STATE_ROOT / "evilportal"
    if not pdir.exists():
        return out
    for f in sorted(pdir.glob("*.json")):
        try:
            data = json.loads(f.read_text())
        except Exception:
            continue
        out.append({"id": data.get("id"), "ssid": data.get("ssid"),
                    "template_name": data.get("template_name"),
                    "started_ts": data.get("started_ts"),
                    "ended_ts": data.get("ended_ts"),
                    "captures_count": data.get("captures_count", 0)})
    return out


def issue_reveal_token(session_id: str) -> str:
    token = secrets.token_urlsafe(32)
    _REVEAL_TOKENS[token] = (_time.time() + _REVEAL_TTL_S, session_id)
    return token


def consume_reveal_token(token: str, session_id: str) -> bool:
    entry = _REVEAL_TOKENS.pop(token, None)
    if not entry:
        return False
    expiry, sid = entry
    if _time.time() > expiry:
        return False
    return hmac.compare_digest(sid, session_id)


def portal_capture_path(session_id: str) -> _Path:
    return _PORTAL_STATE_ROOT / "evilportal" / f"captures-{session_id}.jsonl"

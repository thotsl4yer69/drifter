#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Perception Fusion
Fuses Hailo vision events with live OBD/GPS telemetry into conservative,
structured driving-context events. Vision is optional: loss of the camera or
accelerator never blocks the vehicle telemetry spine.
UNCAGED TECHNOLOGY — EST 1991
"""

from __future__ import annotations

import json
import logging
import signal
import time
from dataclasses import dataclass, field

from config import MQTT_HOST, MQTT_PORT, TOPICS, make_mqtt_client

logging.basicConfig(level=logging.INFO, format='%(asctime)s [FUSION] %(message)s', datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

VEHICLE_CLASSES = {"car", "truck", "bus", "motorcycle"}
VULNERABLE_CLASSES = {"person", "bicycle"}
EVENT_COOLDOWN_S = 2.0


@dataclass
class FusionState:
    speed_kph: float = 0.0
    throttle_pct: float = 0.0
    gps: dict = field(default_factory=dict)
    vision_state: str = "offline"
    last_event: dict[str, float] = field(default_factory=dict)


def _bbox(obj: dict) -> dict:
    return obj.get("bbox") if isinstance(obj.get("bbox"), dict) else {}


def _central(obj: dict) -> bool:
    """Return whether a detection is in the central half of the frame.

    vision_engine publishes pixel-space bbox coordinates (including cx) after
    inference. Older fixtures/other producers may publish normalised 0..1
    coordinates, so normalize either representation before comparing.
    """
    box = _bbox(obj)
    cx = box.get("cx")
    try:
        if cx is None and box.get("x1") is not None and box.get("x2") is not None:
            cx = (float(box["x1"]) + float(box["x2"])) / 2.0
        if cx is None:
            return True
        cx = float(cx)
        if cx > 1.0:
            width = float(obj.get("frame_width") or 0.0)
            if width <= 0:
                return False
            cx /= width
        return 0.25 <= cx <= 0.75
    except (TypeError, ValueError):
        return False


def _event(kind: str, severity: str, obj: dict, state: FusionState, now: float) -> dict:
    return {
        "kind": kind,
        "severity": severity,
        "object_class": obj.get("class"),
        "confidence": obj.get("confidence"),
        "speed_kph": round(state.speed_kph, 1),
        "throttle_pct": round(state.throttle_pct, 1),
        "gps": state.gps or None,
        "ts": now,
    }


def derive_events(objects: list[dict], state: FusionState, now: float | None = None) -> list[dict]:
    """Pure event derivation seam; intentionally conservative and testable."""
    now = time.time() if now is None else now
    out: list[dict] = []
    for obj in objects:
        cls = obj.get("class")
        if not cls or not _central(obj):
            continue
        conf = float(obj.get("confidence") or 0.0)
        if conf < 0.45:
            continue
        box = _bbox(obj)
        h = float(box.get("height") or 0.0)
        # Accept normalised or pixel-space boxes. Large central objects are the
        # only ones promoted to context events; FCW remains the TTC authority.
        near = h >= 0.22 if h <= 1.0 else h >= 140.0
        if cls in VULNERABLE_CLASSES and state.speed_kph >= 8 and near:
            out.append(_event("vulnerable_road_user_ahead", "warn", obj, state, now))
        elif cls in VEHICLE_CLASSES and state.speed_kph >= 15 and near:
            out.append(_event("vehicle_ahead", "info", obj, state, now))
        elif cls == "stop sign" and state.speed_kph >= 10:
            out.append(_event("stop_sign_seen", "info", obj, state, now))
    return out


def main() -> None:
    log.info("DRIFTER Perception Fusion starting...")
    state = FusionState()
    running = True

    def _handle_signal(_sig, _frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    client = make_mqtt_client("drifter-perception-fusion")

    def publish_status(status: str) -> None:
        client.publish(TOPICS["perception_status"], json.dumps({
            "state": status,
            "vision": state.vision_state,
            "ts": time.time(),
        }), retain=True)

    def on_message(_c, _u, msg):
        try:
            data = json.loads(msg.payload)
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
            return
        if msg.topic == TOPICS["snapshot"] and isinstance(data, dict):
            try:
                state.speed_kph = float(data.get("speed", state.speed_kph) or 0)
                state.throttle_pct = float(data.get("throttle", state.throttle_pct) or 0)
            except (TypeError, ValueError):
                pass
        elif msg.topic == TOPICS["gps_fix"] and isinstance(data, dict):
            state.gps = {k: data.get(k) for k in ("lat", "lon", "accuracy") if data.get(k) is not None}
        elif msg.topic == TOPICS["vision_status"] and isinstance(data, dict):
            state.vision_state = str(data.get("state") or "unknown")
            publish_status("online")
        elif msg.topic == TOPICS["vision_object"] and isinstance(data, dict):
            now = time.time()
            for event in derive_events(data.get("objects") or [], state, now):
                key = event["kind"]
                if now - state.last_event.get(key, 0.0) < EVENT_COOLDOWN_S:
                    continue
                state.last_event[key] = now
                client.publish(TOPICS["perception_event"], json.dumps(event))

    client.on_message = on_message
    while running:
        try:
            client.connect(MQTT_HOST, MQTT_PORT, 60)
            break
        except Exception as exc:
            log.warning("Waiting for MQTT broker... (%s)", exc)
            time.sleep(3)
    if not running:
        return

    client.subscribe([
        (TOPICS["snapshot"], 0),
        (TOPICS["gps_fix"], 0),
        (TOPICS["vision_status"], 0),
        (TOPICS["vision_object"], 0),
    ])
    client.loop_start()
    publish_status("online")
    while running:
        time.sleep(1)
    publish_status("offline")
    client.loop_stop()
    client.disconnect()


if __name__ == "__main__":
    main()

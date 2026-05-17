#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Forward Collision Warning
Consumes vehicle detections from vision_engine and the current OBD speed
to compute a rough time-to-collision. Publishes warnings on
TOPICS['fcw_warning'] when TTC drops below configured thresholds. This is
an assist-only system — never a substitute for the driver's attention.
UNCAGED TECHNOLOGY — EST 1991
"""

import json
import logging
import signal
import time
from collections import deque
from typing import Optional

import paho.mqtt.client as mqtt

from config import (
    MQTT_HOST, MQTT_PORT, TOPICS,
    FCW_TTC_WARN, FCW_TTC_CRIT, VISION_INPUT_H,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [FCW] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

# Crude calibration — pixel-height of a vehicle bounding box -> distance metres.
# Tuned for a 1.5m tall reference vehicle and a 640x640 frame.
REF_VEHICLE_HEIGHT_M = 1.5
FOCAL_PX = 600.0


def _estimate_distance_m(bbox: dict) -> Optional[float]:
    height_px = bbox.get('height') or (bbox.get('y2', 0) - bbox.get('y1', 0))
    if not height_px or height_px <= 0:
        return None
    return REF_VEHICLE_HEIGHT_M * FOCAL_PX / float(height_px)


class FCWState:
    def __init__(self) -> None:
        self.speed_kph: float = 0.0
        self.distance_hist: deque = deque(maxlen=8)
        self.last_warn_ts: float = 0.0
        self.warn_active: bool = False


def _evaluate(state: FCWState) -> Optional[dict]:
    if not state.distance_hist or state.speed_kph <= 5:
        return None
    distance = state.distance_hist[-1]
    speed_ms = state.speed_kph / 3.6
    ttc = distance / speed_ms if speed_ms > 0.5 else None
    if ttc is None:
        return None
    if ttc <= FCW_TTC_CRIT:
        return {'level': 'critical', 'ttc_s': round(ttc, 2), 'distance_m': round(distance, 1)}
    if ttc <= FCW_TTC_WARN:
        return {'level': 'warn', 'ttc_s': round(ttc, 2), 'distance_m': round(distance, 1)}
    return None


def main() -> None:
    log.info("DRIFTER Forward Collision Warning starting...")
    state = FCWState()

    running = True

    def _handle_signal(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    client = mqtt.Client(client_id="drifter-fcw")

    def on_message(_c, _u, msg) -> None:
        try:
            data = json.loads(msg.payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        topic = msg.topic
        if topic == TOPICS['snapshot'] and isinstance(data, dict):
            speed = data.get('speed')
            if speed is not None:
                try:
                    state.speed_kph = float(speed)
                except (TypeError, ValueError):
                    pass
        elif topic == TOPICS['vision_object'] and isinstance(data, dict):
            best_distance = None
            for obj in data.get('objects', []):
                if obj.get('class') not in ('car', 'truck', 'bus'):
                    continue
                # Prefer central detections (y-axis low half = ahead)
                bbox = obj.get('bbox') or {}
                cx = bbox.get('cx')
                if cx is not None and not (0.3 <= cx <= 0.7):
                    continue
                d = _estimate_distance_m(bbox)
                if d is None:
                    continue
                if best_distance is None or d < best_distance:
                    best_distance = d
            if best_distance is not None:
                state.distance_hist.append(best_distance)

    client.on_message = on_message

    connected = False
    while not connected and running:
        try:
            client.connect(MQTT_HOST, MQTT_PORT, 60)
            connected = True
        except Exception as e:
            log.warning(f"Waiting for MQTT broker... ({e})")
            time.sleep(3)

    if not running:
        return

    client.subscribe([(TOPICS['snapshot'], 0), (TOPICS['vision_object'], 0)])
    client.loop_start()
    client.publish(TOPICS['fcw_status'], json.dumps({
        'state': 'online',
        'ttc_warn': FCW_TTC_WARN,
        'ttc_critical': FCW_TTC_CRIT,
        'ts': time.time(),
    }), retain=True)
    log.info(f"FCW LIVE (warn<{FCW_TTC_WARN}s, crit<{FCW_TTC_CRIT}s)")

    while running:
        warning = _evaluate(state)
        now = time.time()
        if warning:
            state.warn_active = True
            if now - state.last_warn_ts >= 1.0:
                state.last_warn_ts = now
                client.publish(TOPICS['fcw_warning'], json.dumps({
                    **warning,
                    'speed_kph': state.speed_kph,
                    'active': True,
                    'ts': now,
                }), retain=True)
                log.warning(f"FCW [{warning['level']}] TTC={warning['ttc_s']}s "
                            f"dist={warning['distance_m']}m")
        elif state.warn_active:
            state.warn_active = False
            client.publish(TOPICS['fcw_warning'], json.dumps({
                'active': False, 'ts': now,
            }), retain=True)
        time.sleep(0.2)

    client.publish(TOPICS['fcw_status'], json.dumps({
        'state': 'offline', 'ts': time.time(),
    }), retain=True)
    client.loop_stop()
    client.disconnect()
    log.info("FCW stopped")


if __name__ == '__main__':
    main()

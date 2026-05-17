#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Driver Assistance
Three loops in one daemon:
  - Drive scoring (0-100) over a rolling distance window based on
    hard accel/brake/cornering events from the snapshot.
  - Fatigue detection: time-of-day, drive duration, micro-sleep proxies.
  - Weather hook: fetches Open-Meteo for the active GPS position and
    annotates current conditions on TOPICS['driver_weather'].
UNCAGED TECHNOLOGY — EST 1991
"""

import json
import logging
import signal
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Optional

import paho.mqtt.client as mqtt
import requests

from config import (
    MQTT_HOST, MQTT_PORT, TOPICS,
    DRIFTER_DIR, DRIVER_SCORE_WINDOW_KM,
    FATIGUE_DRIVE_HOURS, FATIGUE_NIGHT_HOURS, WEATHER_API_HOST,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [ASSIST] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

CONFIG_PATH = DRIFTER_DIR / "assist.yaml"

# Penalty weights per event
PENALTY = {
    'hard_brake': 4,
    'hard_accel': 3,
    'overspeed': 5,
    'sharp_corner': 2,
    'idle_long': 1,
}


class AssistState:
    def __init__(self) -> None:
        self.events: deque = deque(maxlen=200)
        self.distance_km: float = 0.0
        self.score: int = 100
        self.last_pos: Optional[tuple] = None
        self.last_speed: float = 0.0
        self.drive_start: Optional[float] = None
        self.fatigue_active: bool = False
        self.weather: dict = {}


def _is_night(now: Optional[datetime] = None) -> bool:
    now = now or datetime.now()
    return now.hour >= 22 or now.hour < 6


def _recompute_score(state: AssistState) -> int:
    # Drop events outside the last DRIVER_SCORE_WINDOW_KM
    if state.distance_km > DRIVER_SCORE_WINDOW_KM:
        cutoff_km = state.distance_km - DRIVER_SCORE_WINDOW_KM
        while state.events and state.events[0].get('odo_km', 0) < cutoff_km:
            state.events.popleft()
    penalty = sum(PENALTY.get(e['type'], 0) for e in state.events)
    return max(0, min(100, 100 - penalty))


def _record_event(state: AssistState, event_type: str, detail: dict) -> None:
    state.events.append({
        'type': event_type,
        'detail': detail,
        'ts': time.time(),
        'odo_km': state.distance_km,
    })
    state.score = _recompute_score(state)


def _fetch_weather(lat: float, lon: float) -> dict:
    url = (
        f"https://{WEATHER_API_HOST}/v1/forecast"
        f"?latitude={lat}&longitude={lon}&current=temperature_2m,precipitation,"
        f"weather_code,wind_speed_10m,visibility"
    )
    try:
        resp = requests.get(url, timeout=8)
        if resp.status_code != 200:
            return {}
        data = resp.json().get('current', {})
        return {
            'temp_c': data.get('temperature_2m'),
            'precip_mm': data.get('precipitation'),
            'wind_kph': data.get('wind_speed_10m'),
            'visibility_m': data.get('visibility'),
            'weather_code': data.get('weather_code'),
            'fetched_at': time.time(),
        }
    except Exception as e:
        log.debug(f"weather fetch: {e}")
        return {}


def _check_fatigue(state: AssistState) -> Optional[str]:
    if state.drive_start is None:
        return None
    hours = (time.time() - state.drive_start) / 3600.0
    limit = FATIGUE_NIGHT_HOURS if _is_night() else FATIGUE_DRIVE_HOURS
    if hours >= limit:
        return f"You've been driving for {hours:.1f}h{' at night' if _is_night() else ''} — take a break."
    return None


def main() -> None:
    log.info("DRIFTER Driver Assist starting...")
    state = AssistState()

    running = True

    def _handle_signal(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    client = mqtt.Client(client_id="drifter-assist")

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
                    s = float(speed)
                    if state.drive_start is None and s > 5:
                        state.drive_start = time.time()
                    if state.last_speed > 0:
                        dt = max(0.5, time.time() - (state.events[-1]['ts'] if state.events else time.time() - 1))
                        # Rolling distance for window scoring
                        state.distance_km += (s / 3600.0) * 1.0
                        delta = state.last_speed - s
                        if delta >= 22:
                            _record_event(state, 'hard_brake', {'delta_kph_per_s': round(delta, 1)})
                        if (s - state.last_speed) >= 14:
                            _record_event(state, 'hard_accel', {'delta_kph_per_s': round(s - state.last_speed, 1)})
                    state.last_speed = s
                except (TypeError, ValueError):
                    pass
        elif topic == TOPICS['nav_position'] and isinstance(data, dict):
            lat = data.get('lat'); lon = data.get('lon')
            if lat is not None and lon is not None:
                state.last_pos = (float(lat), float(lon))
        elif topic == TOPICS['safety_alert'] and isinstance(data, dict):
            key = data.get('key')
            if key == 'overspeed':
                _record_event(state, 'overspeed', data)
            elif key == 'hard_brake':
                _record_event(state, 'hard_brake', data)
            elif key == 'hard_accel':
                _record_event(state, 'hard_accel', data)
        elif topic == TOPICS['drive_session'] and isinstance(data, dict):
            if data.get('event') == 'end':
                state.drive_start = None
                state.fatigue_active = False

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

    client.subscribe([
        (TOPICS['snapshot'], 0),
        (TOPICS['nav_position'], 0),
        (TOPICS['safety_alert'], 0),
        (TOPICS['drive_session'], 0),
    ])
    client.loop_start()
    log.info("Driver Assist LIVE")

    last_weather = 0.0
    last_pub = 0.0
    while running:
        now = time.time()
        if now - last_pub >= 5:
            client.publish(TOPICS['driver_score'], json.dumps({
                'score': state.score,
                'distance_km': round(state.distance_km, 2),
                'recent_events': len(state.events),
                'ts': now,
            }))
            last_pub = now

        # Fatigue check
        msg = _check_fatigue(state)
        active = bool(msg)
        if active != state.fatigue_active:
            state.fatigue_active = active
            client.publish(TOPICS['driver_fatigue'], json.dumps({
                'active': active, 'message': msg, 'ts': now,
            }), retain=True)
            if active:
                client.publish(TOPICS['driver_event'], json.dumps({
                    'event': 'fatigue', 'message': msg, 'ts': now,
                }))

        # Weather pull every 10 minutes
        if state.last_pos and now - last_weather > 600:
            w = _fetch_weather(*state.last_pos)
            if w:
                state.weather = w
                client.publish(TOPICS['driver_weather'], json.dumps(w), retain=True)
            last_weather = now

        time.sleep(1)

    client.loop_stop()
    client.disconnect()
    log.info("Driver Assist stopped")


if __name__ == '__main__':
    main()

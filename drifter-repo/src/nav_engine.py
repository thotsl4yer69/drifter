#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Navigation Engine
Reads NMEA from a USB GPS, publishes position, computes distance to
known fixed speed cameras (Victoria dataset shipped in data/), and
optionally requests routes from a public OSRM endpoint when online.
UNCAGED TECHNOLOGY — EST 1991
"""

import json
import logging
import math
import signal
import threading
import time
from pathlib import Path
from typing import Iterable, Optional

import paho.mqtt.client as mqtt
import requests

from config import (
    MQTT_HOST, MQTT_PORT, TOPICS,
    DRIFTER_DIR, SPEED_CAMERAS_FILE,
    NAV_GPS_DEVICE, NAV_GPS_BAUD,
    NAV_CAMERA_WARN_METERS, NAV_REROUTE_OFF_THRESHOLD, NAV_OSRM_HOST,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [NAV] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

CONFIG_PATH = DRIFTER_DIR / "nav.yaml"

EARTH_RADIUS_M = 6_371_000.0


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def _load_cameras() -> list:
    if not SPEED_CAMERAS_FILE.exists():
        log.warning(f"Speed camera dataset missing: {SPEED_CAMERAS_FILE}")
        return []
    try:
        data = json.loads(SPEED_CAMERAS_FILE.read_text())
    except Exception as e:
        log.warning(f"Speed camera load failed: {e}")
        return []
    cameras = []
    for entry in data.get('cameras', []):
        try:
            cameras.append({
                'id': entry.get('id'),
                'lat': float(entry['lat']),
                'lon': float(entry['lon']),
                'limit_kph': entry.get('limit_kph'),
                'type': entry.get('type', 'fixed'),
                'description': entry.get('description', ''),
            })
        except (KeyError, TypeError, ValueError):
            continue
    log.info(f"Loaded {len(cameras)} speed cameras")
    return cameras


def _parse_nmea_gga(line: str) -> Optional[dict]:
    parts = line.strip().split(',')
    if len(parts) < 10 or not parts[0].endswith('GGA'):
        return None
    try:
        lat_raw = parts[2]
        lat_dir = parts[3]
        lon_raw = parts[4]
        lon_dir = parts[5]
        if not lat_raw or not lon_raw:
            return None
        lat_deg = int(lat_raw[:2])
        lat_min = float(lat_raw[2:])
        lat = lat_deg + lat_min / 60.0
        if lat_dir == 'S':
            lat = -lat
        lon_deg = int(lon_raw[:3])
        lon_min = float(lon_raw[3:])
        lon = lon_deg + lon_min / 60.0
        if lon_dir == 'W':
            lon = -lon
        fix = int(parts[6] or 0)
        sats = int(parts[7] or 0)
        alt = float(parts[9] or 0.0)
        return {'lat': lat, 'lon': lon, 'fix': fix, 'sats': sats, 'alt_m': alt}
    except (ValueError, IndexError):
        return None


def _parse_nmea_rmc(line: str) -> Optional[dict]:
    parts = line.strip().split(',')
    if len(parts) < 10 or not parts[0].endswith('RMC'):
        return None
    try:
        speed_knots = float(parts[7] or 0.0)
        bearing = float(parts[8] or 0.0)
        return {
            'speed_kph': round(speed_knots * 1.852, 2),
            'bearing': bearing,
        }
    except (ValueError, IndexError):
        return None


class NavState:
    def __init__(self) -> None:
        self.lat: Optional[float] = None
        self.lon: Optional[float] = None
        self.speed_kph: float = 0.0
        self.bearing: float = 0.0
        self.fix: int = 0
        self.sats: int = 0
        self.last_camera_id: Optional[str] = None
        self.last_camera_ts: float = 0.0
        self.route_target: Optional[tuple] = None


def _nearest_camera(state: NavState, cameras: Iterable[dict]) -> Optional[tuple]:
    if state.lat is None:
        return None
    best = None
    for cam in cameras:
        d = haversine(state.lat, state.lon, cam['lat'], cam['lon'])
        if best is None or d < best[1]:
            best = (cam, d)
    return best


def _emit_camera_warning(client: mqtt.Client, state: NavState, cam: dict, distance: float) -> None:
    if cam['id'] == state.last_camera_id and time.time() - state.last_camera_ts < 30:
        return
    state.last_camera_id = cam['id']
    state.last_camera_ts = time.time()
    client.publish(TOPICS['nav_camera'], json.dumps({
        'id': cam['id'],
        'lat': cam['lat'],
        'lon': cam['lon'],
        'limit_kph': cam.get('limit_kph'),
        'type': cam.get('type'),
        'description': cam.get('description'),
        'distance_m': round(distance, 1),
        'speed_kph': state.speed_kph,
        'ts': time.time(),
    }))
    log.info(f"Camera ahead: {cam['id']} @ {distance:.0f}m "
             f"limit={cam.get('limit_kph')}kph current={state.speed_kph}kph")


def _request_route(origin: tuple, destination: tuple) -> Optional[dict]:
    url = (
        f"https://{NAV_OSRM_HOST}/route/v1/driving/"
        f"{origin[1]},{origin[0]};{destination[1]},{destination[0]}"
        f"?overview=simplified&steps=true"
    )
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200:
            return None
        return resp.json()
    except Exception as e:
        log.warning(f"OSRM request failed: {e}")
        return None


def _gps_loop(state: NavState, running_ref: list) -> None:
    """Read NMEA from the GPS device. Falls back to stdin lines if unavailable."""
    try:
        import serial
        ser = serial.Serial(NAV_GPS_DEVICE, NAV_GPS_BAUD, timeout=1)
        log.info(f"GPS open: {NAV_GPS_DEVICE} @ {NAV_GPS_BAUD}")
    except Exception as e:
        log.warning(f"GPS open failed: {e} — running in offline stub mode")
        ser = None

    while running_ref[0]:
        try:
            line: Optional[str] = None
            if ser:
                raw = ser.readline().decode('ascii', errors='replace')
                line = raw.strip() if raw else None
            if not line:
                time.sleep(0.2)
                continue
            gga = _parse_nmea_gga(line)
            if gga and gga.get('fix', 0) > 0:
                state.lat = gga['lat']
                state.lon = gga['lon']
                state.fix = gga['fix']
                state.sats = gga['sats']
            rmc = _parse_nmea_rmc(line)
            if rmc:
                state.speed_kph = rmc['speed_kph']
                state.bearing = rmc['bearing']
        except Exception as e:
            log.debug(f"gps loop: {e}")
            time.sleep(0.5)


def main() -> None:
    log.info("DRIFTER Navigation starting...")
    cameras = _load_cameras()
    state = NavState()

    running = [True]

    def _handle_signal(sig, frame):
        running[0] = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    client = mqtt.Client(client_id="drifter-nav")

    def on_message(_c, _u, msg) -> None:
        if msg.topic == TOPICS['nav_route']:
            try:
                req = json.loads(msg.payload)
                if state.lat is None:
                    return
                dest = (float(req['lat']), float(req['lon']))
                route = _request_route((state.lat, state.lon), dest)
                if route:
                    state.route_target = dest
                    client.publish(TOPICS['nav_route'], json.dumps({
                        'origin': {'lat': state.lat, 'lon': state.lon},
                        'destination': {'lat': dest[0], 'lon': dest[1]},
                        'route': route.get('routes', [{}])[0] if route else None,
                        'ts': time.time(),
                    }))
            except Exception as e:
                log.warning(f"route handler: {e}")

    client.on_message = on_message

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

    client.subscribe(TOPICS['nav_route'], 0)
    client.loop_start()

    gps_thread = threading.Thread(target=_gps_loop, args=(state, running), daemon=True)
    gps_thread.start()
    log.info("Navigation LIVE")

    last_pos_pub = 0.0
    while running[0]:
        now = time.time()
        if state.lat is not None and now - last_pos_pub >= 1:
            client.publish(TOPICS['nav_position'], json.dumps({
                'lat': state.lat, 'lon': state.lon,
                'speed_kph': state.speed_kph,
                'bearing': state.bearing,
                'fix': state.fix,
                'sats': state.sats,
                'ts': now,
            }))
            last_pos_pub = now
            if cameras:
                nearest = _nearest_camera(state, cameras)
                if nearest:
                    cam, dist = nearest
                    if dist <= NAV_CAMERA_WARN_METERS:
                        _emit_camera_warning(client, state, cam, dist)
        time.sleep(0.25)

    client.loop_stop()
    client.disconnect()
    log.info("Navigation stopped")


if __name__ == '__main__':
    main()

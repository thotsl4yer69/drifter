#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Navigation Engine
Reads NMEA from a USB GPS, publishes position, computes distance to
known fixed speed cameras (Victoria dataset shipped in data/), supports
offline route caching, geofence enter/exit detection, and optionally
requests routes from a public OSRM endpoint when online.
UNCAGED TECHNOLOGY — EST 1991
"""

import functools
import hashlib
import json
import logging
import math
import operator
import signal
import threading
import time
from collections.abc import Iterable

import paho.mqtt.client as mqtt
import requests

from config import (
    DRIFTER_DIR,
    LOCATION_GRADE_STEEP_PCT,
    MQTT_HOST,
    MQTT_PORT,
    NAV_CAMERA_BEARING_TOLERANCE_DEG,
    NAV_CAMERA_WARN_METERS,
    NAV_GEOFENCES_FILE,
    NAV_GPS_BAUD,
    NAV_GPS_DEVICE,
    NAV_OSRM_HOST,
    NAV_ROUTE_CACHE_DIR,
    NAV_ROUTE_CACHE_TTL_HOURS,
    NAV_STATUS_PUBLISH_SEC,
    SPEED_CAMERAS_FILE,
    TOPICS,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [NAV] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

CONFIG_PATH = DRIFTER_DIR / "nav.yaml"

EARTH_RADIUS_M = 6_371_000.0

GPS_REOPEN_BACKOFF_SEC = 5.0
ROUTE_CACHE_QUANT_M = 50  # round endpoints to ~50m grid for cache keys

# Throttle for enrichment-driven nav alerts so a hill or a standing weather
# warning doesn't spam Vivi.
GRADE_ALERT_COOLDOWN_SEC = 60
WEATHER_ALERT_COOLDOWN_SEC = 300


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def initial_bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Bearing in degrees (0-360) from point 1 to point 2."""
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def bearing_delta(a: float, b: float) -> float:
    """Smallest absolute angle between two bearings (0-180)."""
    d = abs((a - b) % 360.0)
    return d if d <= 180.0 else 360.0 - d


def verify_nmea_checksum(line: str) -> bool:
    """Validate a $...*XX NMEA sentence's XOR checksum."""
    if not line.startswith('$') or '*' not in line:
        return False
    try:
        body, csum = line[1:].split('*', 1)
        csum = csum.strip()[:2]
        x = functools.reduce(operator.xor, (ord(c) for c in body), 0)
        return f"{x:02X}" == csum.upper()
    except (ValueError, IndexError):
        return False


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


def _load_geofences() -> list:
    if not NAV_GEOFENCES_FILE.exists():
        return []
    try:
        data = json.loads(NAV_GEOFENCES_FILE.read_text())
    except Exception as e:
        log.warning(f"Geofence load failed: {e}")
        return []
    fences = []
    for f in data.get('fences', []):
        try:
            fences.append({
                'id': str(f['id']),
                'name': f.get('name', f['id']),
                'lat': float(f['lat']),
                'lon': float(f['lon']),
                'radius_m': float(f.get('radius_m', 100)),
            })
        except (KeyError, TypeError, ValueError):
            continue
    log.info(f"Loaded {len(fences)} geofences")
    return fences


def _parse_nmea_gga(line: str) -> dict | None:
    parts = line.strip().split(',')
    if len(parts) < 10 or not parts[0].endswith('GGA'):
        return None
    try:
        lat_raw = parts[2]
        lat_dir = parts[3]
        lon_raw = parts[4]
        lon_dir = parts[5]
        if not lat_raw or not lon_raw or len(lat_raw) < 4 or len(lon_raw) < 5:
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
        # parts[9] may carry a checksum suffix on the last field; strip it
        alt_raw = parts[9].split('*')[0] if parts[9] else '0'
        alt = float(alt_raw or 0.0)
        return {'lat': lat, 'lon': lon, 'fix': fix, 'sats': sats, 'alt_m': alt}
    except (ValueError, IndexError):
        return None


def _parse_nmea_rmc(line: str) -> dict | None:
    parts = line.strip().split(',')
    if len(parts) < 10 or not parts[0].endswith('RMC'):
        return None
    try:
        status = parts[2]
        if status and status != 'A':  # 'V' = void / no fix
            return None
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
        self.lat: float | None = None
        self.lon: float | None = None
        self.speed_kph: float = 0.0
        self.bearing: float = 0.0
        self.fix: int = 0
        self.sats: int = 0
        self.last_fix_ts: float = 0.0
        self.last_camera_id: str | None = None
        self.last_camera_ts: float = 0.0
        self.route_target: tuple | None = None
        self.inside_fences: set = set()
        # Enrichment from weather_service / location_service.
        self.grade_pct: float | None = None
        self.elevation_m: float | None = None
        self.last_grade_alert_ts: float = 0.0
        self.last_weather_alert_ts: dict = {}   # alert kind -> last emit ts


def _nearest_camera(state: NavState, cameras: Iterable[dict]) -> tuple | None:
    if state.lat is None:
        return None
    best = None
    for cam in cameras:
        d = haversine(state.lat, state.lon, cam['lat'], cam['lon'])
        if best is None or d < best[1]:
            best = (cam, d)
    return best


def _camera_in_front(state: NavState, cam: dict) -> bool:
    """Cheap directional gate: only warn when camera lies within the travel cone.

    Skipped when speed is too low to trust the GPS-derived bearing.
    """
    if state.speed_kph < 5.0:
        return True
    cam_bearing = initial_bearing(state.lat, state.lon, cam['lat'], cam['lon'])
    return bearing_delta(state.bearing, cam_bearing) <= NAV_CAMERA_BEARING_TOLERANCE_DEG


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


def _check_geofences(client: mqtt.Client, state: NavState, fences: Iterable[dict]) -> None:
    if state.lat is None:
        return
    current = set()
    for f in fences:
        d = haversine(state.lat, state.lon, f['lat'], f['lon'])
        if d <= f['radius_m']:
            current.add(f['id'])
    entered = current - state.inside_fences
    exited = state.inside_fences - current
    for fid in entered:
        f = next((x for x in fences if x['id'] == fid), None)
        if not f:
            continue
        client.publish(TOPICS['nav_geofence'], json.dumps({
            'event': 'enter', 'id': fid, 'name': f['name'],
            'lat': state.lat, 'lon': state.lon, 'ts': time.time(),
        }))
        log.info(f"Geofence ENTER: {f['name']}")
    for fid in exited:
        f = next((x for x in fences if x['id'] == fid), None)
        if not f:
            continue
        client.publish(TOPICS['nav_geofence'], json.dumps({
            'event': 'exit', 'id': fid, 'name': f['name'],
            'lat': state.lat, 'lon': state.lon, 'ts': time.time(),
        }))
        log.info(f"Geofence EXIT: {f['name']}")
    state.inside_fences = current


def _route_cache_key(origin: tuple, destination: tuple) -> str:
    """Quantise endpoints to ~50m and hash; identical short trips reuse the cache."""
    step = ROUTE_CACHE_QUANT_M / 111_000.0
    quant = (
        round(origin[0] / step) * step,
        round(origin[1] / step) * step,
        round(destination[0] / step) * step,
        round(destination[1] / step) * step,
    )
    return hashlib.sha1(json.dumps(quant).encode()).hexdigest()[:16]


def _route_from_cache(key: str) -> dict | None:
    path = NAV_ROUTE_CACHE_DIR / f"{key}.json"
    if not path.exists():
        return None
    try:
        age_h = (time.time() - path.stat().st_mtime) / 3600.0
        if age_h > NAV_ROUTE_CACHE_TTL_HOURS:
            return None
        return json.loads(path.read_text())
    except Exception:
        return None


def _route_to_cache(key: str, route: dict) -> None:
    try:
        NAV_ROUTE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (NAV_ROUTE_CACHE_DIR / f"{key}.json").write_text(json.dumps(route))
    except Exception as e:
        log.debug(f"route cache write failed: {e}")


def _request_route(origin: tuple, destination: tuple) -> dict | None:
    key = _route_cache_key(origin, destination)
    cached = _route_from_cache(key)
    if cached:
        log.info(f"Route cache hit ({key})")
        return cached
    url = (
        f"https://{NAV_OSRM_HOST}/route/v1/driving/"
        f"{origin[1]},{origin[0]};{destination[1]},{destination[0]}"
        f"?overview=simplified&steps=true"
    )
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200:
            log.warning(f"OSRM HTTP {resp.status_code}")
            return None
        data = resp.json()
        _route_to_cache(key, data)
        return data
    except Exception as e:
        log.warning(f"OSRM request failed: {e}")
        return None


def _open_serial():
    try:
        import serial
        ser = serial.Serial(NAV_GPS_DEVICE, NAV_GPS_BAUD, timeout=1)
        log.info(f"GPS open: {NAV_GPS_DEVICE} @ {NAV_GPS_BAUD}")
        return ser
    except Exception as e:
        log.warning(f"GPS open failed: {e}")
        return None


def _gps_loop(state: NavState, running_ref: list) -> None:
    """Read NMEA from the GPS device. Auto-recovers on disconnect."""
    ser = _open_serial()
    next_reopen = 0.0

    while running_ref[0]:
        try:
            if ser is None:
                now = time.time()
                if now >= next_reopen:
                    ser = _open_serial()
                    next_reopen = now + GPS_REOPEN_BACKOFF_SEC
                else:
                    time.sleep(0.5)
                continue

            raw = ser.readline().decode('ascii', errors='replace')
            line = raw.strip() if raw else None
            if not line:
                continue
            if not verify_nmea_checksum(line):
                continue
            gga = _parse_nmea_gga(line)
            if gga and gga.get('fix', 0) > 0:
                state.lat = gga['lat']
                state.lon = gga['lon']
                state.fix = gga['fix']
                state.sats = gga['sats']
                state.last_fix_ts = time.time()
            elif gga:
                state.fix = 0
                state.sats = gga['sats']
            rmc = _parse_nmea_rmc(line)
            if rmc:
                state.speed_kph = rmc['speed_kph']
                state.bearing = rmc['bearing']
        except Exception as e:
            log.warning(f"GPS read error, reopening: {e}")
            try:
                ser.close()
            except Exception:
                pass
            ser = None
            next_reopen = time.time() + GPS_REOPEN_BACKOFF_SEC


def _handle_elevation(client: mqtt.Client, state: NavState, payload: bytes) -> None:
    """Grade-aware routing: surface a nav alert on steep terrain ahead.

    Hill grade drives fuel economy and engine/brake load, so the cockpit + Vivi
    get a heads-up. Throttled by GRADE_ALERT_COOLDOWN_SEC.
    """
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return
    if not isinstance(data, dict):
        return
    grade = data.get('grade_pct')
    elev = data.get('elevation_m')
    if isinstance(elev, (int, float)):
        state.elevation_m = elev
    if not isinstance(grade, (int, float)):
        return
    state.grade_pct = grade
    if abs(grade) < LOCATION_GRADE_STEEP_PCT:
        return
    now = time.time()
    if now - state.last_grade_alert_ts < GRADE_ALERT_COOLDOWN_SEC:
        return
    state.last_grade_alert_ts = now
    if grade < 0:
        msg = f"Steep descent {grade:.0f}% ahead — engine-brake, save the pads."
    else:
        msg = f"Steep climb {grade:.0f}% ahead — expect higher fuel burn and load."
    client.publish(TOPICS['nav_alert'], json.dumps({
        'event': 'steep_grade', 'grade_pct': grade, 'elevation_m': elev,
        'message': msg, 'urgent': False, 'ts': now,
    }))
    log.info(msg)


def _handle_weather_alerts(client: mqtt.Client, state: NavState, payload: bytes) -> None:
    """Re-surface actionable weather hazards as urgent nav alerts.

    weather_service derives rain_soon / fog / ice / high_wind advisories; nav
    forwards the hazardous ones as nav_alert so Vivi speaks "rain/fog/ice
    ahead". Per-kind cooldown stops a standing warning from repeating.
    """
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return
    if not isinstance(data, dict):
        return
    now = time.time()
    urgent_kinds = {'rain_soon', 'fog', 'ice', 'gov'}
    for alert in data.get('alerts', []):
        if not isinstance(alert, dict):
            continue
        kind = alert.get('kind')
        if kind not in urgent_kinds:
            continue
        last = state.last_weather_alert_ts.get(kind, 0.0)
        if now - last < WEATHER_ALERT_COOLDOWN_SEC:
            continue
        state.last_weather_alert_ts[kind] = now
        client.publish(TOPICS['nav_alert'], json.dumps({
            'event': 'weather', 'kind': kind,
            'message': alert.get('message') or alert.get('event') or 'Weather hazard ahead',
            'urgent': kind in ('fog', 'ice'),
            'ts': now,
        }))
        log.info(f"Weather alert forwarded: {kind}")


def main() -> None:
    log.info("DRIFTER Navigation starting...")
    cameras = _load_cameras()
    fences = _load_geofences()
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
                    client.publish(TOPICS['nav_alert'], json.dumps({
                        'event': 'route_error', 'reason': 'no_gps_fix', 'ts': time.time(),
                    }))
                    return
                dest = (float(req['lat']), float(req['lon']))
                route = _request_route((state.lat, state.lon), dest)
                if route:
                    state.route_target = dest
                    client.publish(TOPICS['nav_route'], json.dumps({
                        'origin': {'lat': state.lat, 'lon': state.lon},
                        'destination': {'lat': dest[0], 'lon': dest[1]},
                        'route': route.get('routes', [{}])[0],
                        'ts': time.time(),
                    }))
                else:
                    client.publish(TOPICS['nav_alert'], json.dumps({
                        'event': 'route_error', 'reason': 'osrm_unavailable',
                        'destination': {'lat': dest[0], 'lon': dest[1]},
                        'ts': time.time(),
                    }))
            except Exception as e:
                log.warning(f"route handler: {e}")
                client.publish(TOPICS['nav_alert'], json.dumps({
                    'event': 'route_error', 'reason': str(e), 'ts': time.time(),
                }))
        elif msg.topic == TOPICS['location_elevation']:
            _handle_elevation(client, state, msg.payload)
        elif msg.topic == TOPICS['weather_alerts']:
            _handle_weather_alerts(client, state, msg.payload)

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

    client.subscribe([
        (TOPICS['nav_route'], 0),
        (TOPICS['location_elevation'], 0),
        (TOPICS['weather_alerts'], 0),
    ])
    client.loop_start()

    gps_thread = threading.Thread(target=_gps_loop, args=(state, running), daemon=True)
    gps_thread.start()
    log.info("Navigation LIVE")

    last_pos_pub = 0.0
    last_status_pub = 0.0
    while running[0]:
        now = time.time()
        fix_age = now - state.last_fix_ts if state.last_fix_ts else None
        if now - last_status_pub >= NAV_STATUS_PUBLISH_SEC:
            client.publish(TOPICS['nav_status'], json.dumps({
                'has_fix': state.fix > 0 and (fix_age is None or fix_age < 5),
                'fix': state.fix,
                'sats': state.sats,
                'fix_age_s': round(fix_age, 1) if fix_age is not None else None,
                'ts': now,
            }), retain=True)
            last_status_pub = now

        if state.lat is not None and state.fix > 0 and now - last_pos_pub >= 1:
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
                    if dist <= NAV_CAMERA_WARN_METERS and _camera_in_front(state, cam):
                        _emit_camera_warning(client, state, cam, dist)
            if fences:
                _check_geofences(client, state, fences)
        time.sleep(0.25)

    client.loop_stop()
    client.disconnect()
    log.info("Navigation stopped")


if __name__ == '__main__':
    main()

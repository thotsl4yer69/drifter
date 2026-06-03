#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Location Service
UNCAGED TECHNOLOGY — EST 1991

Position-aware enrichment via the Google Maps Platform. Tracks the live GPS
fix (nav_position / gps_fix) and publishes:

  drifter/location/elevation — elevation (m) + instantaneous road grade (%)
                               from consecutive Elevation samples
  drifter/location/nearby    — nearby POIs (fuel, mechanic, ...) via Places

Other modules consume these topics instead of calling Google directly:
  - safety_engine    → steep-grade warning
  - nav_engine       → grade + POI awareness along route
  - trip_computer    → elevation profile overlay
  - vivi_v2          → "find me the nearest petrol station"

On-demand POI lookups: publish {"type": "car_wash"} (or a Vivi alias like
"mechanic") to drifter/location/query and the matching nearby result comes
back on drifter/location/nearby with `query=True`.

paho-mqtt v1.x callback API, flat main() pattern, imports from config.
"""

import json
import logging
import math
import signal
import threading
import time

import paho.mqtt.client as mqtt
import requests

from config import (
    DEFAULT_LAT,
    DEFAULT_LON,
    GOOGLE_ELEVATION_API_KEY,
    GOOGLE_ELEVATION_URL,
    GOOGLE_PLACES_API_KEY,
    GOOGLE_PLACES_NEARBY_URL,
    LOCATION_ELEVATION_INTERVAL_SEC,
    LOCATION_ELEVATION_MIN_MOVE_M,
    LOCATION_GRADE_STEEP_PCT,
    LOCATION_HTTP_TIMEOUT,
    LOCATION_NEARBY_INTERVAL_SEC,
    LOCATION_NEARBY_MIN_MOVE_M,
    LOCATION_POI_DEFAULT_TYPES,
    LOCATION_POI_RADIUS_M,
    LOCATION_POI_TYPES,
    MQTT_HOST,
    MQTT_PORT,
    TOPICS,
    have_key,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [LOCATION] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

EARTH_RADIUS_M = 6_371_000.0

# Live position + last-sampled anchors (guarded by _lock).
_lock = threading.Lock()
_pos = {'lat': DEFAULT_LAT, 'lon': DEFAULT_LON, 'have_fix': False, 'ts': 0.0}
_last_elev = {'lat': None, 'lon': None, 'elev_m': None}
_last_nearby_pos = {'lat': None, 'lon': None}


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


# ───────────────────────── Google fetch ─────────────────────────

def fetch_elevation(lat: float, lon: float) -> float | None:
    """Ground elevation in metres for a point, or None on failure."""
    if not have_key(GOOGLE_ELEVATION_API_KEY):
        return None
    try:
        resp = requests.get(GOOGLE_ELEVATION_URL, params={
            'locations': f"{lat},{lon}",
            'key': GOOGLE_ELEVATION_API_KEY,
        }, timeout=LOCATION_HTTP_TIMEOUT)
    except requests.RequestException as e:
        log.warning(f"Elevation request failed: {e}")
        return None
    if resp.status_code != 200:
        log.warning(f"Elevation HTTP {resp.status_code}")
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    if data.get('status') != 'OK':
        log.warning(f"Elevation status {data.get('status')}: {data.get('error_message', '')}")
        return None
    results = data.get('results') or []
    if not results:
        return None
    try:
        return float(results[0]['elevation'])
    except (KeyError, TypeError, ValueError):
        return None


def fetch_nearby(lat: float, lon: float, place_type: str,
                 radius: int = LOCATION_POI_RADIUS_M) -> list[dict]:
    """Nearby POIs of a Google Places type, sorted by distance."""
    if not have_key(GOOGLE_PLACES_API_KEY):
        return []
    try:
        resp = requests.get(GOOGLE_PLACES_NEARBY_URL, params={
            'location': f"{lat},{lon}",
            'radius': radius,
            'type': place_type,
            'key': GOOGLE_PLACES_API_KEY,
        }, timeout=LOCATION_HTTP_TIMEOUT)
    except requests.RequestException as e:
        log.warning(f"Places request failed: {e}")
        return []
    if resp.status_code != 200:
        log.warning(f"Places HTTP {resp.status_code}")
        return []
    try:
        data = resp.json()
    except ValueError:
        return []
    status = data.get('status')
    if status not in ('OK', 'ZERO_RESULTS'):
        log.warning(f"Places status {status}: {data.get('error_message', '')}")
        return []

    pois = []
    for r in data.get('results') or []:
        loc = (r.get('geometry') or {}).get('location') or {}
        plat, plon = loc.get('lat'), loc.get('lng')
        dist = None
        if plat is not None and plon is not None:
            try:
                dist = round(haversine(lat, lon, float(plat), float(plon)))
            except (TypeError, ValueError):
                dist = None
        pois.append({
            'name': r.get('name'),
            'type': place_type,
            'lat': plat,
            'lon': plon,
            'vicinity': r.get('vicinity'),
            'distance_m': dist,
            'open_now': ((r.get('opening_hours') or {}).get('open_now')),
            'rating': r.get('rating'),
        })
    pois.sort(key=lambda p: (p['distance_m'] is None, p['distance_m'] or 0))
    return pois[:10]


def resolve_place_type(alias: str) -> str:
    """Map a spoken alias ('petrol', 'mechanic') to a Google Places type.

    Pass-through for anything already a Places type so callers can send
    either form.
    """
    alias = (alias or '').strip().lower().replace(' ', '_')
    if alias in LOCATION_POI_TYPES:
        return LOCATION_POI_TYPES[alias]
    return alias or 'gas_station'


# ───────────────────────── Compute + publish ─────────────────────────

def compute_grade(prev_lat, prev_lon, prev_elev, lat, lon, elev) -> float | None:
    """Road grade (%) = rise / horizontal run, between two samples."""
    if None in (prev_lat, prev_lon, prev_elev, elev):
        return None
    run = haversine(prev_lat, prev_lon, lat, lon)
    if run < 1.0:  # too little movement to trust
        return None
    return round((elev - prev_elev) / run * 100.0, 1)


def _publish_elevation(client: mqtt.Client) -> None:
    with _lock:
        lat, lon, have_fix = _pos['lat'], _pos['lon'], _pos['have_fix']
        prev = dict(_last_elev)

    # Only re-sample after meaningful movement (saves Elevation quota).
    if prev['lat'] is not None:
        moved = haversine(prev['lat'], prev['lon'], lat, lon)
        if moved < LOCATION_ELEVATION_MIN_MOVE_M:
            return

    elev = fetch_elevation(lat, lon)
    if elev is None:
        return
    grade = compute_grade(prev['lat'], prev['lon'], prev['elev_m'], lat, lon, elev)

    with _lock:
        _last_elev.update({'lat': lat, 'lon': lon, 'elev_m': elev})

    payload = {
        'lat': lat,
        'lon': lon,
        'elevation_m': round(elev, 1),
        'grade_pct': grade,
        'steep': bool(grade is not None and abs(grade) >= LOCATION_GRADE_STEEP_PCT),
        'gps_fix': have_fix,
        'ts': time.time(),
    }
    try:
        client.publish(TOPICS['location_elevation'], json.dumps(payload), retain=True)
    except Exception as e:
        log.warning(f"elevation publish failed: {e}")
        return
    if payload['steep']:
        log.info(f"Steep grade {grade:+.1f}% @ {elev:.0f} m")


def _publish_nearby(client: mqtt.Client, place_types, query: bool = False) -> None:
    with _lock:
        lat, lon, have_fix = _pos['lat'], _pos['lon'], _pos['have_fix']

    results: dict[str, list] = {}
    for pt in place_types:
        pois = fetch_nearby(lat, lon, pt)
        if pois:
            results[pt] = pois
    if not results and not query:
        return

    try:
        client.publish(TOPICS['location_nearby'], json.dumps({
            'lat': lat,
            'lon': lon,
            'gps_fix': have_fix,
            'query': query,
            'results': results,
            'ts': time.time(),
        }), retain=not query)
    except Exception as e:
        log.warning(f"nearby publish failed: {e}")
        return

    if not query:
        with _lock:
            _last_nearby_pos.update({'lat': lat, 'lon': lon})
    counts = ', '.join(f"{k}={len(v)}" for k, v in results.items()) or 'none'
    log.info(f"Nearby{' [query]' if query else ''}: {counts}")


# ───────────────────────── MQTT plumbing ─────────────────────────

def _on_position(payload: bytes) -> None:
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return
    if not isinstance(data, dict):
        return
    lat, lon = data.get('lat'), data.get('lon')
    if lat is None or lon is None:
        return
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return
    with _lock:
        _pos.update({'lat': lat, 'lon': lon, 'have_fix': True, 'ts': time.time()})


def make_on_message(client: mqtt.Client):
    def on_message(_c, _u, msg) -> None:
        topic = msg.topic
        if topic in (TOPICS['nav_position'], TOPICS['gps_fix']):
            _on_position(msg.payload)
        elif topic == TOPICS['location_query']:
            try:
                data = json.loads(msg.payload)
            except (json.JSONDecodeError, UnicodeDecodeError):
                return
            alias = ''
            if isinstance(data, dict):
                alias = str(data.get('type') or data.get('alias') or '')
            elif isinstance(data, str):
                alias = data
            place_type = resolve_place_type(alias)
            # Run the on-demand lookup off the MQTT thread.
            threading.Thread(
                target=_publish_nearby, args=(client, [place_type], True),
                daemon=True,
            ).start()
    return on_message


def main() -> None:
    log.info("DRIFTER Location Service starting...")
    if not have_key(GOOGLE_PLACES_API_KEY):
        log.warning("GOOGLE_MAPS_API_KEY missing — elevation/POI lookups disabled")

    running = True

    def _handle_signal(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    client = mqtt.Client(client_id="drifter-location")
    client.on_message = make_on_message(client)

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
        (TOPICS['nav_position'], 0),
        (TOPICS['gps_fix'], 0),
        (TOPICS['location_query'], 0),
    ])
    client.loop_start()
    log.info(
        f"Location Service LIVE — elevation @{LOCATION_ELEVATION_INTERVAL_SEC}s, "
        f"POIs @{LOCATION_NEARBY_INTERVAL_SEC}s"
    )

    next_elev = 0.0
    next_nearby = 0.0
    while running:
        now = time.time()
        if now >= next_elev:
            try:
                _publish_elevation(client)
            except Exception as e:
                log.error(f"elevation loop crashed: {e}")
            next_elev = now + LOCATION_ELEVATION_INTERVAL_SEC
        if now >= next_nearby:
            try:
                # Skip the periodic POI poll if we haven't moved far since the
                # last one (POIs don't change; saves Places quota).
                with _lock:
                    lat, lon = _pos['lat'], _pos['lon']
                    plat, plon = _last_nearby_pos['lat'], _last_nearby_pos['lon']
                moved_enough = (
                    plat is None
                    or haversine(plat, plon, lat, lon) >= LOCATION_NEARBY_MIN_MOVE_M
                )
                if moved_enough:
                    _publish_nearby(client, LOCATION_POI_DEFAULT_TYPES)
            except Exception as e:
                log.error(f"nearby loop crashed: {e}")
            next_nearby = now + LOCATION_NEARBY_INTERVAL_SEC
        time.sleep(1)

    client.loop_stop()
    client.disconnect()
    log.info("Location Service stopped")


if __name__ == '__main__':
    main()

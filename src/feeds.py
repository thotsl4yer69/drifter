"""
MZ1312 DRIFTER — Public-data feeds aggregator (drifter-feeds)

Polls a curated set of public sources, publishes structured snapshots
to MQTT, and writes /opt/drifter/state/feeds_summary.json + radar.gif
for the dashboard / Vivi to consume.

Sources:
  EMV          incidents (60s)    drifter/feeds/emv/{snapshot,event}
  BOM          warnings  (300s)   drifter/feeds/bom/warnings
  BOM          radar gif (600s)   drifter/feeds/bom/radar
  Open-Meteo   weather   (600s)   drifter/feeds/weather/current
  ADS-B        aircraft  (30s)    drifter/feeds/aircraft/snapshot
                                    readsb local first, ADSB.lol fallback
  Overpass     POIs      (3600s)  drifter/feeds/poi/stations
  Summary      (30s)              drifter/feeds/summary

Origin resolution:
  /opt/drifter/state/gps.json with fix=true and ts<120s old → 'gps'
  Otherwise DRIFTER_HOME_LAT/LON env (defaults Long Gully) → 'home'

Config (env, all optional):
  DRIFTER_MQTT_HOST/PORT/USER/PASS
  DRIFTER_HOME_LAT, DRIFTER_HOME_LON
  DRIFTER_FEED_RADIUS_KM           default 50
  DRIFTER_STATE_DIR                default /opt/drifter/state
  DRIFTER_READSB_AIRCRAFT          default /run/readsb/aircraft.json

UNCAGED TECHNOLOGY — EST 1991
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any, Optional

try:
    import aiohttp
except ImportError:
    print('FATAL: aiohttp not installed', file=sys.stderr); sys.exit(1)

try:
    from lxml import etree as LET
except ImportError:
    LET = None  # we degrade gracefully if lxml is missing — BOM warnings then 0

import paho.mqtt.client as mqtt


# ── Config ─────────────────────────────────────────────────────────

MQTT_HOST = os.environ.get('DRIFTER_MQTT_HOST', 'localhost')
MQTT_PORT = int(os.environ.get('DRIFTER_MQTT_PORT', '1883'))
MQTT_USER = os.environ.get('DRIFTER_MQTT_USER') or None
MQTT_PASS = os.environ.get('DRIFTER_MQTT_PASS') or None

HOME_LAT = float(os.environ.get('DRIFTER_HOME_LAT', '-36.7596'))
HOME_LON = float(os.environ.get('DRIFTER_HOME_LON', '144.2531'))
RADIUS_KM = float(os.environ.get('DRIFTER_FEED_RADIUS_KM', '50'))

STATE_DIR = Path(os.environ.get('DRIFTER_STATE_DIR', '/opt/drifter/state'))
GPS_PATH  = STATE_DIR / 'gps.json'
SUMMARY_PATH = STATE_DIR / 'feeds_summary.json'
RADAR_PATH   = STATE_DIR / 'radar.gif'
READSB_PATH  = Path(os.environ.get('DRIFTER_READSB_AIRCRAFT', '/run/readsb/aircraft.json'))

UA = 'Mozilla/5.0 (X11; Linux aarch64) DRIFTER/1.0 +mz1312'
# Overpass blocks the spec UA; use a tool-style UA for it specifically
# (deviation from spec — documented in deploy notes).
UA_OVERPASS = 'drifter-feeds/1.0 (+mz1312)'

EMV_URL     = 'https://data.emergency.vic.gov.au/Show?pageId=getIncidentJSON'
BOM_WARN_URL = 'https://www.bom.gov.au/fwo/IDZ00056.warnings_vic.xml'
BOM_RADAR_URL = 'https://www.bom.gov.au/radar/IDR023.gif'
OPEN_METEO_URL = (
    'https://api.open-meteo.com/v1/forecast'
    '?latitude={lat}&longitude={lon}'
    '&current=temperature_2m,relative_humidity_2m,apparent_temperature,'
    'is_day,precipitation,rain,weather_code,wind_speed_10m,'
    'wind_direction_10m,wind_gusts_10m'
    '&hourly=temperature_2m,precipitation_probability,precipitation,wind_speed_10m'
    '&forecast_hours=24&timezone=Australia%2FMelbourne'
)
ADSBLOL_URL = 'https://api.adsb.lol/v2/lat/{lat}/lon/{lon}/dist/{nm}'
OVERPASS_URL = 'https://overpass-api.de/api/interpreter'

# Topics
T_EMV_SNAP   = 'drifter/feeds/emv/snapshot'
T_EMV_EVENT  = 'drifter/feeds/emv/event'
T_BOM_WARN   = 'drifter/feeds/bom/warnings'
T_BOM_RADAR  = 'drifter/feeds/bom/radar'
T_WEATHER    = 'drifter/feeds/weather/current'
T_AIRCRAFT   = 'drifter/feeds/aircraft/snapshot'
T_POI        = 'drifter/feeds/poi/stations'
T_SUMMARY    = 'drifter/feeds/summary'


# ── Logging ────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [FEEDS] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger('drifter.feeds')


# ── Helpers ────────────────────────────────────────────────────────

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def origin() -> dict:
    """Resolve origin: live GPS fix (≤120s old) or env home."""
    try:
        j = json.loads(GPS_PATH.read_text())
        if j.get('fix') and time.time() - float(j.get('ts', 0)) <= 120:
            return {'lat': float(j['lat']), 'lon': float(j['lon']), 'source': 'gps'}
    except (FileNotFoundError, json.JSONDecodeError, KeyError, ValueError, TypeError):
        pass
    return {'lat': HOME_LAT, 'lon': HOME_LON, 'source': 'home'}


def atomic_write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, default=str))
    os.replace(str(tmp), str(path))


def atomic_write_bytes(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_bytes(data)
    os.replace(str(tmp), str(path))


def stable_id_from(*parts: Any) -> str:
    return hashlib.sha1('|'.join(str(p) for p in parts).encode()).hexdigest()[:16]


# ── EMV ────────────────────────────────────────────────────────────

def parse_emv_geometry(feature: dict) -> tuple[Optional[float], Optional[float]]:
    """EMV publishes a mix of geometries: GeoJSON Point, GeoJSON Polygon
    (we use the first ring's first vertex), and flat lat/lon properties."""
    g = feature.get('geometry') or {}
    coords = g.get('coordinates')
    gt = (g.get('type') or '').lower()
    if coords:
        if gt == 'point' and len(coords) >= 2:
            return (float(coords[1]), float(coords[0]))
        if gt == 'polygon' and coords and coords[0] and len(coords[0][0]) >= 2:
            return (float(coords[0][0][1]), float(coords[0][0][0]))
    props = feature.get('properties') or feature
    lat = props.get('lat') or props.get('latitude')
    lon = props.get('lon') or props.get('lng') or props.get('longitude')
    try:
        if lat is not None and lon is not None:
            return (float(lat), float(lon))
    except (TypeError, ValueError):
        pass
    return (None, None)


def normalise_emv(feature: dict, ox: float, oy: float, radius: float) -> Optional[dict]:
    lat, lon = parse_emv_geometry(feature)
    if lat is None or lon is None:
        return None
    dist = haversine_km(ox, oy, lat, lon)
    if dist > radius:
        return None
    p = feature.get('properties') or feature
    raw_id = (p.get('id') or p.get('guid') or p.get('eventId')
              or stable_id_from(lat, lon, p.get('category1'), p.get('location')))
    return {
        'id':         str(raw_id),
        'category1':  p.get('category1'),
        'category2':  p.get('category2'),
        'status':     p.get('status'),
        'size':       p.get('size'),
        'resources':  p.get('resources'),
        'location':   p.get('location'),
        'sourceOrg':  p.get('sourceOrg'),
        'updated':    p.get('updated') or p.get('time'),
        'lat':        lat,
        'lon':        lon,
        'distance_km': round(dist, 2),
    }


# ── Weather ────────────────────────────────────────────────────────

def shape_weather(om: dict) -> dict:
    """Compact shape that matches the dashboard's WEATHER panel needs."""
    cur = om.get('current', {}) or {}
    return {
        'ts': time.time(),
        'temp_c':    cur.get('temperature_2m'),
        'feels_c':   cur.get('apparent_temperature'),
        'humidity':  cur.get('relative_humidity_2m'),
        'wind_kmh':  cur.get('wind_speed_10m'),
        'wind_dir':  cur.get('wind_direction_10m'),
        'gust_kmh':  cur.get('wind_gusts_10m'),
        'rain_mm':   cur.get('rain') or cur.get('precipitation'),
        'is_day':    cur.get('is_day'),
        'code':      cur.get('weather_code'),
        'hourly':    om.get('hourly', {}),
    }


# ── Aircraft ───────────────────────────────────────────────────────

INTERESTING_PREFIXES = ('VHPOL', 'POLAIR', 'RESCU', 'HEMS', 'LIFE')
INTERESTING_SQUAWKS = {'7700', '7600', '7500'}


def shape_aircraft(a: dict, ox: float, oy: float) -> Optional[dict]:
    lat, lon = a.get('lat'), a.get('lon')
    try:
        if lat is None or lon is None:
            return None
        lat = float(lat); lon = float(lon)
    except (TypeError, ValueError):
        return None
    dist = haversine_km(ox, oy, lat, lon)
    flight = (a.get('flight') or '').strip().upper()
    cat = a.get('category')
    sq  = str(a.get('squawk') or '').strip()
    interesting = bool(
        cat == 'A7'
        or sq in INTERESTING_SQUAWKS
        or any(flight.startswith(p) for p in INTERESTING_PREFIXES)
    )
    return {
        'hex':      (a.get('hex') or '').lower(),
        'flight':   flight or None,
        'lat':      lat,
        'lon':      lon,
        'alt_baro': a.get('alt_baro'),
        'track':    a.get('track'),
        'gs':       a.get('gs'),
        'squawk':   sq or None,
        'category': cat,
        'type':     a.get('t') or a.get('type'),
        'registration': a.get('r') or a.get('registration'),
        'distance_km':  round(dist, 2),
        'interesting':  interesting,
    }


def read_local_readsb() -> tuple[Optional[list[dict]], Optional[float]]:
    """Return (raw aircraft list, mtime) or (None, None) if stale/missing."""
    try:
        st = READSB_PATH.stat()
        if time.time() - st.st_mtime > 30:
            return (None, None)
        j = json.loads(READSB_PATH.read_text())
        return (j.get('aircraft') or [], st.st_mtime)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return (None, None)


# ── Overpass POIs ──────────────────────────────────────────────────

def overpass_query(lat: float, lon: float, radius_m: int) -> str:
    return f"""
[out:json][timeout:25];
(
  node["amenity"~"^(police|fire_station|hospital|ambulance_station)$"](around:{radius_m},{lat},{lon});
  way["amenity"~"^(police|fire_station|hospital|ambulance_station)$"](around:{radius_m},{lat},{lon});
  node["emergency"~"^(ambulance_station|fire_station)$"](around:{radius_m},{lat},{lon});
);
out center tags;
""".strip()


def shape_poi(elem: dict, ox: float, oy: float) -> Optional[dict]:
    if elem.get('type') == 'way':
        c = elem.get('center') or {}
        lat, lon = c.get('lat'), c.get('lon')
    else:
        lat, lon = elem.get('lat'), elem.get('lon')
    try:
        if lat is None or lon is None:
            return None
        lat = float(lat); lon = float(lon)
    except (TypeError, ValueError):
        return None
    tags = elem.get('tags') or {}
    kind = tags.get('amenity') or tags.get('emergency') or 'unknown'
    return {
        'id':       f"{elem.get('type','?')}/{elem.get('id','?')}",
        'kind':     kind,
        'name':     tags.get('name'),
        'operator': tags.get('operator'),
        'lat':      lat,
        'lon':      lon,
        'distance_km': round(haversine_km(ox, oy, lat, lon), 2),
    }


# ── BOM warnings ───────────────────────────────────────────────────

def parse_bom_warnings(xml_bytes: bytes) -> list[dict]:
    if LET is None or not xml_bytes:
        return []
    try:
        root = LET.fromstring(xml_bytes)
    except Exception:
        return []
    out: list[dict] = []
    # Try ATOM/RSS shape first (entry/item), then BOM warning shape.
    for tag in ('{http://www.w3.org/2005/Atom}entry', 'item', 'warning'):
        for el in root.iter(tag):
            def child(name):
                for c in el:
                    if c.tag.endswith('}' + name) or c.tag == name:
                        return (c.text or '').strip()
                return None
            out.append({
                'title':   child('title'),
                'summary': child('summary') or child('description'),
                'link':    child('link') or child('id'),
                'updated': child('updated') or child('pubDate') or child('issued'),
            })
        if out:
            break
    return [w for w in out if w.get('title')]


# ── Daemon ─────────────────────────────────────────────────────────

class Feeds:
    def __init__(self) -> None:
        self.session: Optional[aiohttp.ClientSession] = None
        self.mqtt: Optional[mqtt.Client] = None
        self.stop_event = asyncio.Event()

        # Cached per-source state.
        self.emv_seen: set[str] = set()
        self.last_emv: list[dict] = []
        self.last_warn: list[dict] = []
        self.last_weather: dict = {}
        self.last_aircraft: list[dict] = []
        self.last_aircraft_source: str = 'none'
        self.last_radar_meta: dict = {}
        self.last_pois: list[dict] = []

    # ── HTTP ────────────────────────────────────────────────────────

    async def _get_json(self, url: str, *, ua: str = UA, **kw) -> Optional[dict]:
        try:
            async with self.session.get(url, headers={'User-Agent': ua}, **kw) as r:
                if r.status != 200:
                    log.warning('%s → HTTP %s', url[:80], r.status)
                    return None
                return await r.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError) as e:
            log.warning('%s → %s', url[:80], e)
            return None

    async def _get_bytes(self, url: str, *, ua: str = UA, **kw) -> Optional[bytes]:
        try:
            async with self.session.get(url, headers={'User-Agent': ua}, **kw) as r:
                if r.status != 200:
                    log.warning('%s → HTTP %s', url[:80], r.status)
                    return None
                return await r.read()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            log.warning('%s → %s', url[:80], e)
            return None

    # ── EMV ────────────────────────────────────────────────────────

    async def loop_emv(self) -> None:
        while not self.stop_event.is_set():
            o = origin()
            j = await self._get_json(EMV_URL)
            if j is not None:
                feats = j.get('features') or []
                items = [
                    n for n in (
                        normalise_emv(f, o['lat'], o['lon'], RADIUS_KM)
                        for f in feats
                    ) if n
                ]
                items.sort(key=lambda x: x['distance_km'])
                self.last_emv = items
                snap = {'ts': time.time(), 'origin': o, 'count': len(items),
                        'items': items}
                self.mqtt.publish(T_EMV_SNAP, json.dumps(snap), qos=1, retain=True)
                # Per-new-item events (only fire for IDs we haven't seen).
                new_ids: list[str] = []
                for it in items:
                    if it['id'] not in self.emv_seen:
                        self.emv_seen.add(it['id'])
                        self.mqtt.publish(T_EMV_EVENT, json.dumps(it), qos=1)
                        new_ids.append(it['id'])
                log.info('EMV: %d items (%d new)', len(items), len(new_ids))
            await asyncio.wait([asyncio.create_task(self.stop_event.wait())], timeout=60)

    # ── BOM ────────────────────────────────────────────────────────

    async def loop_bom_warnings(self) -> None:
        while not self.stop_event.is_set():
            data = await self._get_bytes(BOM_WARN_URL)
            warnings = parse_bom_warnings(data or b'')
            self.last_warn = warnings
            self.mqtt.publish(
                T_BOM_WARN,
                json.dumps({'ts': time.time(), 'count': len(warnings),
                            'items': warnings}),
                qos=1, retain=True,
            )
            log.info('BOM warnings: %d', len(warnings))
            await asyncio.wait([asyncio.create_task(self.stop_event.wait())], timeout=300)

    async def loop_bom_radar(self) -> None:
        while not self.stop_event.is_set():
            blob = await self._get_bytes(BOM_RADAR_URL)
            if blob and blob[:4] in (b'GIF8', b'GIF9') or (blob and blob[:6].startswith(b'GIF8')):
                atomic_write_bytes(RADAR_PATH, blob)
                meta = {
                    'ts': time.time(), 'path': str(RADAR_PATH),
                    'bytes': len(blob),
                    'image_url': '/api/radar.gif',
                    'bbox': [[-39.0, 143.5], [-36.5, 146.5]],
                }
                self.last_radar_meta = meta
                self.mqtt.publish(T_BOM_RADAR, json.dumps(meta), qos=1, retain=True)
                log.info('BOM radar: %d bytes', len(blob))
            else:
                log.warning('BOM radar: not a GIF (or empty) — skipping write')
            await asyncio.wait([asyncio.create_task(self.stop_event.wait())], timeout=600)

    # ── Open-Meteo ─────────────────────────────────────────────────

    async def loop_weather(self) -> None:
        while not self.stop_event.is_set():
            o = origin()
            j = await self._get_json(OPEN_METEO_URL.format(lat=o['lat'], lon=o['lon']))
            if j is not None:
                shaped = shape_weather(j)
                self.last_weather = shaped
                self.mqtt.publish(T_WEATHER, json.dumps(shaped), qos=1, retain=True)
                log.info('weather: %s°C feels %s°C wind %s km/h',
                         shaped.get('temp_c'), shaped.get('feels_c'),
                         shaped.get('wind_kmh'))
            await asyncio.wait([asyncio.create_task(self.stop_event.wait())], timeout=600)

    # ── ADS-B ──────────────────────────────────────────────────────

    async def loop_aircraft(self) -> None:
        while not self.stop_event.is_set():
            o = origin()
            ac, source = await self._get_aircraft(o)
            self.last_aircraft = ac
            self.last_aircraft_source = source
            payload = {
                'ts': time.time(), 'source': source, 'origin': o,
                'count': len(ac),
                'interesting_count': sum(1 for x in ac if x.get('interesting')),
                'aircraft': ac,
            }
            self.mqtt.publish(T_AIRCRAFT, json.dumps(payload), qos=1, retain=True)
            log.info('ADS-B: %d aircraft (source=%s)', len(ac), source)
            await asyncio.wait([asyncio.create_task(self.stop_event.wait())], timeout=30)

    async def _get_aircraft(self, o: dict) -> tuple[list[dict], str]:
        """Local readsb first; fall back to ADSB.lol if local empty/stale.
        Spec calls for this two-source path."""
        local, _mtime = read_local_readsb()
        if local:
            shaped = [s for s in (shape_aircraft(a, o['lat'], o['lon'])
                                  for a in local) if s]
            shaped.sort(key=lambda x: x['distance_km'])
            if shaped:
                return shaped, 'readsb'
        nm = max(1, int(RADIUS_KM * 0.54))
        url = ADSBLOL_URL.format(lat=o['lat'], lon=o['lon'], nm=nm)
        j = await self._get_json(url)
        if not j:
            return ([], 'none')
        raw = j.get('ac') or j.get('aircraft') or []
        shaped = [s for s in (shape_aircraft(a, o['lat'], o['lon'])
                              for a in raw) if s]
        shaped.sort(key=lambda x: x['distance_km'])
        return shaped, 'adsblol'

    # ── Overpass ───────────────────────────────────────────────────

    async def loop_pois(self) -> None:
        while not self.stop_event.is_set():
            o = origin()
            radius_m = int(RADIUS_KM * 1000)
            q = overpass_query(o['lat'], o['lon'], radius_m)
            try:
                async with self.session.post(
                    OVERPASS_URL,
                    data={'data': q},
                    headers={'User-Agent': UA_OVERPASS},
                ) as r:
                    if r.status != 200:
                        log.warning('overpass → HTTP %s', r.status)
                        await asyncio.wait(
                            [asyncio.create_task(self.stop_event.wait())], timeout=3600)
                        continue
                    j = await r.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError,
                    json.JSONDecodeError) as e:
                log.warning('overpass → %s', e)
                await asyncio.wait(
                    [asyncio.create_task(self.stop_event.wait())], timeout=3600)
                continue
            elements = j.get('elements') or []
            shaped = [s for s in (shape_poi(e, o['lat'], o['lon'])
                                  for e in elements) if s]
            shaped.sort(key=lambda x: x['distance_km'])
            self.last_pois = shaped
            self.mqtt.publish(
                T_POI,
                json.dumps({'ts': time.time(), 'origin': o, 'count': len(shaped),
                            'items': shaped}),
                qos=1, retain=True,
            )
            log.info('POIs: %d police/fire/hospital/ambo', len(shaped))
            await asyncio.wait([asyncio.create_task(self.stop_event.wait())], timeout=3600)

    # ── Summary ────────────────────────────────────────────────────

    async def loop_summary(self) -> None:
        # First publish slightly after pollers warm up.
        await asyncio.wait(
            [asyncio.create_task(self.stop_event.wait())], timeout=10)
        while not self.stop_event.is_set():
            o = origin()
            w = self.last_weather or {}
            payload = {
                'ts': time.time(),
                'origin': o,
                'radius_km': RADIUS_KM,
                'weather': {
                    'temp_c':   w.get('temp_c'),
                    'feels_c':  w.get('feels_c'),
                    'wind_kmh': w.get('wind_kmh'),
                    'gust_kmh': w.get('gust_kmh'),
                    'rain_mm':  w.get('rain_mm'),
                    'code':     w.get('code'),
                },
                'incidents_nearby': len(self.last_emv),
                'incidents_top':    self.last_emv[:5],
                'warnings_count':   len(self.last_warn),
                'warnings_top':     self.last_warn[:3],
                'aircraft_total':   len(self.last_aircraft),
                'aircraft_source':  self.last_aircraft_source,
                'aircraft_interesting': [a for a in self.last_aircraft
                                         if a.get('interesting')][:5],
                'radar_image':      self.last_radar_meta.get('path'),
                'pois_total':       len(self.last_pois),
            }
            try:
                atomic_write_json(SUMMARY_PATH, payload)
            except OSError as e:
                log.warning('summary write failed: %s', e)
            self.mqtt.publish(T_SUMMARY, json.dumps(payload, default=str),
                              qos=1, retain=True)
            await asyncio.wait(
                [asyncio.create_task(self.stop_event.wait())], timeout=30)

    # ── Lifecycle ──────────────────────────────────────────────────

    async def run(self) -> None:
        connector = aiohttp.TCPConnector(limit=8, ttl_dns_cache=300)
        timeout = aiohttp.ClientTimeout(total=20, connect=8)
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as s:
            self.session = s
            tasks = [
                asyncio.create_task(self.loop_emv()),
                asyncio.create_task(self.loop_bom_warnings()),
                asyncio.create_task(self.loop_bom_radar()),
                asyncio.create_task(self.loop_weather()),
                asyncio.create_task(self.loop_aircraft()),
                asyncio.create_task(self.loop_pois()),
                asyncio.create_task(self.loop_summary()),
            ]
            log.info('DRIFTER feeds online — origin=%s radius=%dkm', origin(), RADIUS_KM)
            try:
                await self.stop_event.wait()
            finally:
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)


# ── MQTT ───────────────────────────────────────────────────────────

def make_mqtt() -> mqtt.Client:
    try:
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                        client_id='drifter-feeds')
    except AttributeError:
        c = mqtt.Client(client_id='drifter-feeds')
    if MQTT_USER:
        c.username_pw_set(MQTT_USER, MQTT_PASS or '')
    c.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
    c.loop_start()
    return c


# ── Entry ──────────────────────────────────────────────────────────

def main() -> int:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    feeds = Feeds()
    feeds.mqtt = make_mqtt()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _shutdown(*_a):
        log.info('signal received — stopping')
        loop.call_soon_threadsafe(feeds.stop_event.set)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    try:
        loop.run_until_complete(feeds.run())
    except Exception:
        log.exception('feeds crashed')
        return 1
    finally:
        try:
            feeds.mqtt.loop_stop()
            feeds.mqtt.disconnect()
        except Exception:
            pass
        log.info('feeds stopped')
    return 0


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Weather Service
UNCAGED TECHNOLOGY — EST 1991

The single source of weather truth for the node. Polls OpenWeatherMap for
the active GPS position (falling back to DEFAULT_LAT/LON before the first
fix) and fan-publishes to MQTT:

  drifter/weather/current   — temp, humidity, wind, visibility, condition
  drifter/weather/forecast  — next ~12h hourly outlook
  drifter/weather/alerts    — government alerts + DRIFTER-derived advisories
                              (rain_soon / fog / ice / high_wind)

Every other module (nav_engine, safety_engine, trip_computer, ai_diagnostics,
driver_assist, vivi_v2) consumes these topics rather than calling the API —
so the real-time + safety path never blocks on the network, and the API is
hit from exactly one place at one rate (every WEATHER_UPDATE_INTERVAL_SEC).

Uses One Call 3.0 when the key is subscribed to it (gives minutely rain for
the "should I put the windows up?" nudge); transparently degrades to the
2.5 current+forecast endpoints on 401/403.

paho-mqtt v1.x callback API, flat main() pattern, imports from config.
"""

import json
import logging
import signal
import threading
import time

import paho.mqtt.client as mqtt
import requests

from config import (
    DEFAULT_LAT,
    DEFAULT_LON,
    MQTT_HOST,
    MQTT_PORT,
    OPENWEATHERMAP_API_KEY,
    OWM_BASE_URL,
    OWM_FALLBACK_CURRENT_URL,
    OWM_FALLBACK_FORECAST_URL,
    OWM_UNITS,
    TOPICS,
    WEATHER_FOG_VISIBILITY_M,
    WEATHER_HIGH_WIND_KPH,
    WEATHER_HTTP_TIMEOUT,
    WEATHER_ICE_TEMP_C,
    WEATHER_RAIN_SOON_MIN,
    WEATHER_UPDATE_INTERVAL_SEC,
    have_key,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [WEATHER] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

# Live position cache, updated from nav/gps topics by the MQTT thread.
_pos_lock = threading.Lock()
_pos = {'lat': DEFAULT_LAT, 'lon': DEFAULT_LON, 'have_fix': False}


def _ms_to_kph(v) -> float | None:
    try:
        return round(float(v) * 3.6, 1)
    except (TypeError, ValueError):
        return None


# ───────────────────────── Fetch ─────────────────────────

def fetch_onecall(lat: float, lon: float) -> dict | None:
    """One Call 3.0 — current + minutely + hourly + alerts. None on failure.

    Returns the raw OWM payload so callers can parse exactly the slices they
    need. Logs (does not raise) on HTTP/network error so the service loop
    keeps ticking.
    """
    if not have_key(OPENWEATHERMAP_API_KEY):
        log.warning("No OpenWeatherMap key configured — weather disabled")
        return None
    try:
        resp = requests.get(OWM_BASE_URL, params={
            'lat': lat, 'lon': lon,
            'units': OWM_UNITS,
            'exclude': 'daily',
            'appid': OPENWEATHERMAP_API_KEY,
        }, timeout=WEATHER_HTTP_TIMEOUT)
    except requests.RequestException as e:
        log.warning(f"One Call request failed: {e}")
        return None
    if resp.status_code == 200:
        try:
            return resp.json()
        except ValueError:
            return None
    if resp.status_code in (401, 403):
        # Key not subscribed to One Call 3.0 — caller should use 2.5 path.
        log.info(f"One Call 3.0 not available (HTTP {resp.status_code}) — using 2.5 endpoints")
        return None
    log.warning(f"One Call HTTP {resp.status_code}")
    return None


def fetch_legacy(lat: float, lon: float) -> dict | None:
    """2.5 current + 5-day/3h forecast, reshaped to look like a One Call slice.

    Fallback when the key lacks One Call 3.0. No minutely data, so rain
    prediction degrades to "rain in the current/next forecast block".
    """
    if not have_key(OPENWEATHERMAP_API_KEY):
        return None
    params = {'lat': lat, 'lon': lon, 'units': OWM_UNITS, 'appid': OPENWEATHERMAP_API_KEY}
    try:
        cur = requests.get(OWM_FALLBACK_CURRENT_URL, params=params, timeout=WEATHER_HTTP_TIMEOUT)
        fc = requests.get(OWM_FALLBACK_FORECAST_URL, params=params, timeout=WEATHER_HTTP_TIMEOUT)
    except requests.RequestException as e:
        log.warning(f"2.5 request failed: {e}")
        return None
    if cur.status_code != 200:
        log.warning(f"2.5 current HTTP {cur.status_code}")
        return None
    try:
        cj = cur.json()
        fj = fc.json() if fc.status_code == 200 else {}
    except ValueError:
        return None

    # Reshape into a One-Call-ish dict so the parsers below are uniform.
    now = int(time.time())
    current = {
        'dt': cj.get('dt', now),
        'temp': (cj.get('main') or {}).get('temp'),
        'feels_like': (cj.get('main') or {}).get('feels_like'),
        'humidity': (cj.get('main') or {}).get('humidity'),
        'pressure': (cj.get('main') or {}).get('pressure'),
        'visibility': cj.get('visibility'),
        'wind_speed': (cj.get('wind') or {}).get('speed'),
        'wind_deg': (cj.get('wind') or {}).get('deg'),
        'clouds': (cj.get('clouds') or {}).get('all'),
        'weather': cj.get('weather') or [],
        'rain': cj.get('rain') or {},
    }
    hourly = []
    for block in (fj.get('list') or [])[:6]:  # 6 blocks ≈ 18h at 3h steps
        hourly.append({
            'dt': block.get('dt'),
            'temp': (block.get('main') or {}).get('temp'),
            'humidity': (block.get('main') or {}).get('humidity'),
            'wind_speed': (block.get('wind') or {}).get('speed'),
            'pop': block.get('pop'),
            'weather': block.get('weather') or [],
            'rain': block.get('rain') or {},
        })
    return {'current': current, 'hourly': hourly, 'minutely': [], 'alerts': []}


# ───────────────────────── Parse ─────────────────────────

def parse_current(oc: dict, lat: float, lon: float) -> dict:
    """Flatten the One Call `current` block into a compact MQTT payload."""
    cur = oc.get('current') or {}
    weather0 = (cur.get('weather') or [{}])[0]
    rain = cur.get('rain') or {}
    rain_1h = rain.get('1h') if isinstance(rain, dict) else None
    cond = str(weather0.get('main') or '').lower()
    return {
        'lat': lat,
        'lon': lon,
        'temp_c': cur.get('temp'),
        'feels_like_c': cur.get('feels_like'),
        'humidity': cur.get('humidity'),
        'pressure_hpa': cur.get('pressure'),
        'visibility_m': cur.get('visibility'),
        'wind_kph': _ms_to_kph(cur.get('wind_speed')),
        'wind_deg': cur.get('wind_deg'),
        'clouds_pct': cur.get('clouds'),
        'condition': weather0.get('main'),
        'description': weather0.get('description'),
        'weather_id': weather0.get('id'),
        'rain_1h_mm': rain_1h,
        'is_raining': cond in ('rain', 'drizzle', 'thunderstorm') or bool(rain_1h),
        'is_foggy': cond in ('fog', 'mist', 'haze', 'smoke'),
        'is_snowing': cond == 'snow',
        'ts': time.time(),
    }


def parse_forecast(oc: dict) -> list[dict]:
    out = []
    for h in (oc.get('hourly') or [])[:12]:
        w = (h.get('weather') or [{}])[0]
        out.append({
            'dt': h.get('dt'),
            'temp_c': h.get('temp'),
            'humidity': h.get('humidity'),
            'wind_kph': _ms_to_kph(h.get('wind_speed')),
            'pop': h.get('pop'),
            'condition': w.get('main'),
            'description': w.get('description'),
        })
    return out


def rain_next_hour(oc: dict) -> dict:
    """Minutely-precision rain prediction for the windows-up nudge.

    Returns {'rain_expected': bool, 'minutes_until_rain': int|None,
             'peak_mm': float}. Uses One Call `minutely`; if absent (2.5
     fallback), infers from the nearest forecast block's probability.
    """
    minutely = oc.get('minutely') or []
    now = time.time()
    if minutely:
        peak = 0.0
        first_wet = None
        for m in minutely:
            precip = m.get('precipitation') or 0.0
            try:
                precip = float(precip)
            except (TypeError, ValueError):
                precip = 0.0
            peak = max(peak, precip)
            if precip > 0 and first_wet is None:
                dt = m.get('dt')
                if dt:
                    first_wet = max(0, int((dt - now) / 60))
        return {
            'rain_expected': first_wet is not None,
            'minutes_until_rain': first_wet,
            'peak_mm': round(peak, 2),
            'source': 'minutely',
        }
    # 2.5 fallback — use the next forecast block's probability of precip.
    hourly = oc.get('hourly') or []
    if hourly:
        nxt = hourly[0]
        pop = nxt.get('pop') or 0.0
        try:
            pop = float(pop)
        except (TypeError, ValueError):
            pop = 0.0
        rain = (nxt.get('rain') or {}).get('3h') if isinstance(nxt.get('rain'), dict) else None
        if pop >= 0.4 or rain:
            dt = nxt.get('dt')
            mins = max(0, int((dt - now) / 60)) if dt else None
            return {'rain_expected': True, 'minutes_until_rain': mins,
                    'peak_mm': float(rain or 0.0), 'source': 'forecast_pop'}
    return {'rain_expected': False, 'minutes_until_rain': None, 'peak_mm': 0.0,
            'source': 'minutely' if minutely else 'forecast_pop'}


def derive_alerts(oc: dict, current: dict, rain: dict) -> list[dict]:
    """Government alerts plus DRIFTER-derived driving advisories.

    Derived advisories are the actionable bits the rest of the fleet reacts
    to (Vivi speaks them, safety_engine tightens thresholds): rain_soon, fog,
    ice, high_wind.
    """
    alerts: list[dict] = []
    now = time.time()

    for gov in (oc.get('alerts') or []):
        alerts.append({
            'kind': 'gov',
            'event': gov.get('event'),
            'sender': gov.get('sender_name'),
            'description': str(gov.get('description', ''))[:400],
            'start': gov.get('start'),
            'end': gov.get('end'),
            'severity': 'amber',
            'ts': now,
        })

    if rain.get('rain_expected'):
        mins = rain.get('minutes_until_rain')
        if mins is not None and mins <= WEATHER_RAIN_SOON_MIN:
            alerts.append({
                'kind': 'rain_soon',
                'event': 'Rain incoming',
                'minutes_until_rain': mins,
                'peak_mm': rain.get('peak_mm'),
                'message': (f"Rain in about {mins} min" if mins
                            else "Rain starting now")
                           + " — windows up, ease off in the wet.",
                'severity': 'info',
                'ts': now,
            })

    vis = current.get('visibility_m')
    if isinstance(vis, (int, float)) and vis < WEATHER_FOG_VISIBILITY_M:
        alerts.append({
            'kind': 'fog',
            'event': 'Low visibility',
            'visibility_m': vis,
            'message': f"Fog — visibility {int(vis)} m. Slow down, lights on.",
            'severity': 'amber',
            'ts': now,
        })

    temp = current.get('temp_c')
    moisture = current.get('is_raining') or current.get('is_snowing') or (
        (current.get('humidity') or 0) >= 90)
    if isinstance(temp, (int, float)) and temp <= WEATHER_ICE_TEMP_C and moisture:
        alerts.append({
            'kind': 'ice',
            'event': 'Ice risk',
            'temp_c': temp,
            'message': f"Ice risk — {temp:.0f}°C and damp. Gentle inputs, longer gaps.",
            'severity': 'amber',
            'ts': now,
        })

    wind = current.get('wind_kph')
    if isinstance(wind, (int, float)) and wind >= WEATHER_HIGH_WIND_KPH:
        alerts.append({
            'kind': 'high_wind',
            'event': 'High wind',
            'wind_kph': wind,
            'message': f"Strong wind {wind:.0f} km/h — watch for crosswind gusts.",
            'severity': 'info',
            'ts': now,
        })

    return alerts


# ───────────────────────── MQTT plumbing ─────────────────────────

def _on_position(payload: bytes) -> None:
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return
    if not isinstance(data, dict):
        return
    lat = data.get('lat')
    lon = data.get('lon')
    if lat is None or lon is None:
        return
    try:
        lat = float(lat)
        lon = float(lon)
    except (TypeError, ValueError):
        return
    with _pos_lock:
        _pos['lat'] = lat
        _pos['lon'] = lon
        _pos['have_fix'] = True


def on_message(client, userdata, msg) -> None:
    if msg.topic in (TOPICS['nav_position'], TOPICS['gps_fix']):
        _on_position(msg.payload)


def _poll_and_publish(client: mqtt.Client) -> None:
    with _pos_lock:
        lat, lon, have_fix = _pos['lat'], _pos['lon'], _pos['have_fix']

    oc = fetch_onecall(lat, lon)
    if oc is None:
        oc = fetch_legacy(lat, lon)
    if oc is None:
        return

    current = parse_current(oc, lat, lon)
    current['gps_fix'] = have_fix
    forecast = parse_forecast(oc)
    rain = rain_next_hour(oc)
    current['rain_next_hour'] = rain
    alerts = derive_alerts(oc, current, rain)

    try:
        client.publish(TOPICS['weather_current'], json.dumps(current), retain=True)
        client.publish(TOPICS['weather_forecast'], json.dumps({
            'hourly': forecast, 'ts': time.time(),
        }), retain=True)
        client.publish(TOPICS['weather_alerts'], json.dumps({
            'alerts': alerts, 'count': len(alerts), 'ts': time.time(),
        }), retain=True)
    except Exception as e:
        log.warning(f"publish failed: {e}")
        return

    cond = current.get('description') or current.get('condition') or '?'
    log.info(
        f"{cond}, {current.get('temp_c')}°C, vis={current.get('visibility_m')}m, "
        f"alerts={len(alerts)} ({'fix' if have_fix else 'default-pos'})"
    )


def main() -> None:
    log.info("DRIFTER Weather Service starting...")
    if not have_key(OPENWEATHERMAP_API_KEY):
        log.warning("OPENWEATHERMAP_API_KEY missing — service will idle until configured")

    running = True

    def _handle_signal(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    client = mqtt.Client(client_id="drifter-weather")
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
        (TOPICS['nav_position'], 0),
        (TOPICS['gps_fix'], 0),
    ])
    client.loop_start()
    log.info(f"Weather Service LIVE — polling every {WEATHER_UPDATE_INTERVAL_SEC}s")

    next_poll = 0.0  # fire immediately on boot
    while running:
        now = time.time()
        if now >= next_poll:
            try:
                _poll_and_publish(client)
            except Exception as e:
                log.error(f"poll crashed: {e}")
            next_poll = now + WEATHER_UPDATE_INTERVAL_SEC
        time.sleep(1)

    client.loop_stop()
    client.disconnect()
    log.info("Weather Service stopped")


if __name__ == '__main__':
    main()

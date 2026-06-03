"""Offline tests for the weather + location enrichment services.

No network: every test feeds synthetic OpenWeatherMap / Google payloads
through the pure parse/derive helpers. Covers the windows-up rain nudge,
derived hazard advisories, road-grade maths, and POI alias resolution.
"""
import sys
import time

sys.path.insert(0, 'src')

import pytest

import location_service as ls
import weather_service as ws

# ───────────────────────── weather_service ─────────────────────────

def _onecall(**over):
    now = time.time()
    base = {
        'current': {
            'temp': 12.0, 'feels_like': 11.0, 'humidity': 60, 'pressure': 1015,
            'visibility': 10000, 'wind_speed': 5.0, 'wind_deg': 200, 'clouds': 20,
            'weather': [{'id': 800, 'main': 'Clear', 'description': 'clear sky'}],
            'rain': {},
        },
        'minutely': [],
        'hourly': [],
        'alerts': [],
    }
    base['current'].update(over.pop('current', {}))
    base.update(over)
    base['_now'] = now
    return base


def test_parse_current_flags_rain_and_fog():
    oc = _onecall(current={
        'weather': [{'id': 500, 'main': 'Rain', 'description': 'light rain'}],
        'rain': {'1h': 0.4}, 'visibility': 600,
    })
    cur = ws.parse_current(oc, -36.75, 144.27)
    assert cur['is_raining'] is True
    assert cur['rain_1h_mm'] == 0.4
    # 'Rain' main is not fog; visibility handled separately by derive_alerts.
    assert cur['is_foggy'] is False


def test_rain_next_hour_from_minutely():
    now = time.time()
    oc = _onecall(minutely=[
        {'dt': int(now + 300), 'precipitation': 0.0},
        {'dt': int(now + 900), 'precipitation': 0.6},
    ])
    rain = ws.rain_next_hour(oc)
    assert rain['rain_expected'] is True
    assert rain['minutes_until_rain'] == pytest.approx(15, abs=1)
    assert rain['peak_mm'] == 0.6
    assert rain['source'] == 'minutely'


def test_rain_next_hour_dry():
    now = time.time()
    oc = _onecall(minutely=[{'dt': int(now + 600), 'precipitation': 0.0}])
    rain = ws.rain_next_hour(oc)
    assert rain['rain_expected'] is False
    assert rain['minutes_until_rain'] is None


def test_derive_alerts_fog_ice_wind_and_rainsoon():
    now = time.time()
    oc = _onecall(
        current={
            'temp': 1.0, 'humidity': 95, 'visibility': 400, 'wind_speed': 20.0,
            'weather': [{'id': 500, 'main': 'Rain', 'description': 'light rain'}],
            'rain': {'1h': 0.3},
        },
        minutely=[{'dt': int(now + 600), 'precipitation': 0.5}],
        alerts=[{'event': 'Flood Watch', 'sender_name': 'BOM',
                 'description': 'x', 'start': 1, 'end': 2}],
    )
    cur = ws.parse_current(oc, 0, 0)
    rain = ws.rain_next_hour(oc)
    kinds = {a['kind'] for a in ws.derive_alerts(oc, cur, rain)}
    assert {'fog', 'ice', 'high_wind', 'rain_soon', 'gov'} <= kinds


def test_legacy_reshape_is_parseable(monkeypatch):
    """The 2.5 fallback should reshape into a One-Call-ish dict the parsers eat."""
    now = int(time.time())

    class _Resp:
        status_code = 200

        def __init__(self, payload):
            self._p = payload

        def json(self):
            return self._p

    cur_payload = {
        'dt': now, 'main': {'temp': 9.0, 'humidity': 80, 'pressure': 1008},
        'visibility': 5000, 'wind': {'speed': 3.0, 'deg': 90},
        'clouds': {'all': 75}, 'weather': [{'id': 803, 'main': 'Clouds',
                                            'description': 'broken clouds'}],
    }
    fc_payload = {'list': [{
        'dt': now + 3600, 'main': {'temp': 8, 'humidity': 82},
        'wind': {'speed': 4}, 'pop': 0.7, 'weather': [{'main': 'Rain'}],
        'rain': {'3h': 1.2},
    }]}

    def fake_get(url, params=None, timeout=None):
        # NB: 'weather' is a substring of 'openweathermap.org', so key off
        # the path segment instead.
        return _Resp(fc_payload if url.endswith('forecast') else cur_payload)

    monkeypatch.setattr(ws.requests, 'get', fake_get)
    monkeypatch.setattr(ws, 'OPENWEATHERMAP_API_KEY', 'x')
    oc = ws.fetch_legacy(-36.0, 144.0)
    assert oc is not None
    cur = ws.parse_current(oc, -36.0, 144.0)
    assert cur['temp_c'] == 9.0
    assert cur['condition'] == 'Clouds'
    rain = ws.rain_next_hour(oc)
    assert rain['rain_expected'] is True
    assert rain['source'] == 'forecast_pop'


# ───────────────────────── location_service ─────────────────────────

def test_compute_grade_positive_and_none_on_no_move():
    # ~111 m east at this latitude; +10 m rise → ~9% grade.
    g = ls.compute_grade(-36.7500, 144.2700, 100.0, -36.7500, 144.2710, 110.0)
    assert g is not None and g > 0
    # No movement → grade undefined (avoids divide-by-zero noise).
    assert ls.compute_grade(-36.75, 144.27, 100.0, -36.75, 144.27, 105.0) is None


def test_resolve_place_type_aliases_and_passthrough():
    assert ls.resolve_place_type('petrol') == 'gas_station'
    assert ls.resolve_place_type('Mechanic') == 'car_repair'
    assert ls.resolve_place_type('car wash') == 'car_wash'
    # Already a Places type → passthrough.
    assert ls.resolve_place_type('gas_station') == 'gas_station'
    # Empty → safe default.
    assert ls.resolve_place_type('') == 'gas_station'


def test_fetch_nearby_sorts_by_distance(monkeypatch):
    class _Resp:
        status_code = 200

        def json(self):
            return {'status': 'OK', 'results': [
                {'name': 'Far', 'geometry': {'location': {'lat': -36.80, 'lng': 144.30}},
                 'vicinity': 'far st'},
                {'name': 'Near', 'geometry': {'location': {'lat': -36.7505, 'lng': 144.2705}},
                 'vicinity': 'near st', 'opening_hours': {'open_now': True}},
            ]}

    monkeypatch.setattr(ls.requests, 'get', lambda *a, **k: _Resp())
    monkeypatch.setattr(ls, 'GOOGLE_PLACES_API_KEY', 'x')
    pois = ls.fetch_nearby(-36.7500, 144.2700, 'gas_station')
    assert [p['name'] for p in pois] == ['Near', 'Far']
    assert pois[0]['distance_m'] < pois[1]['distance_m']

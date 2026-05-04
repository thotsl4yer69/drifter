# tests/test_web_dashboard_handlers.py
"""Tests for the refactored dispatch table in web_dashboard_handlers."""
import sys
sys.path.insert(0, 'src')

import web_dashboard_handlers as h
import web_dashboard_state as state


def test_exact_get_route_table_covers_key_endpoints():
    routes = h.DashboardHandler._EXACT_GET_ROUTES
    # Spot-check a handful of routes that absolutely must exist.
    for path in ['/', '/index.html', '/mechanic', '/settings',
                 '/api/state', '/api/hardware', '/api/settings',
                 '/api/mechanic/search', '/api/mechanic/advice',
                 '/api/mechanic/specs', '/api/mechanic/problems',
                 '/api/mechanic/service', '/api/mechanic/torque']:
        assert path in routes, f"missing route {path}"


def test_all_routes_point_to_callables():
    for path, handler in h.DashboardHandler._EXACT_GET_ROUTES.items():
        assert callable(handler), f"{path} -> {handler!r} is not callable"


def test_build_query_context_includes_telemetry(monkeypatch):
    state.latest_state.clear()
    state.latest_state.update({
        'engine_rpm':     {'value': 850},
        'engine_coolant': {'value': 92.5},
        'power_voltage':  {'value': 14.1},
    })
    prompt = h.build_query_context('rough idle')
    assert 'RPM: 850' in prompt
    assert 'Coolant: 92.5°C' in prompt
    assert 'Battery: 14.1V' in prompt
    assert 'rough idle' in prompt


def test_build_query_context_with_no_telemetry():
    state.latest_state.clear()
    prompt = h.build_query_context('anything')
    assert 'No live telemetry' in prompt


def test_build_query_context_includes_dtcs():
    state.latest_state.clear()
    state.latest_state['diag_dtc'] = {
        'stored': ['P0301', 'P0302'],
        'pending': ['P0171'],
    }
    prompt = h.build_query_context('misfire')
    assert 'Active DTCs: P0301, P0302' in prompt
    assert 'Pending DTCs: P0171' in prompt


def test_build_query_context_skips_nominal_alert():
    state.latest_state.clear()
    state.latest_state['alert_message'] = {'message': 'Systems nominal'}
    prompt = h.build_query_context('status')
    assert 'Systems nominal' not in prompt


def test_dtc_regex_still_enforced():
    assert h._DTC_RE.match('P0301')
    assert not h._DTC_RE.match('../etc/passwd')


# ── /healthz contract ──────────────────────────────────────────────────

import pytest


@pytest.fixture(autouse=True)
def _reset_healthz_state():
    """Clear cached /healthz state + dashboard state between tests."""
    h._healthz_cache.update(ts=0.0, payload=None, http_status=200)
    state.mqtt_client = None
    state.latest_state.clear()
    yield
    state.mqtt_client = None


def _all_active(monkeypatch, failed: set[str] = frozenset()):
    monkeypatch.setattr(
        h, '_systemctl_active_map',
        lambda units: {u: u not in failed for u in units},
    )


def test_healthz_route_registered():
    assert '/healthz' in h.DashboardHandler._EXACT_GET_ROUTES
    assert h.DashboardHandler._EXACT_GET_ROUTES['/healthz'] is \
        h.DashboardHandler._get_healthz


def test_healthz_payload_all_active(monkeypatch):
    _all_active(monkeypatch)
    payload, status = h._healthz_payload()
    assert status == 200
    assert payload['status'] == 'ok'
    assert payload['services_failed'] == []
    assert all(payload['services'].values())
    assert {'ts', 'mqtt_connected', 'telemetry_fresh'} <= payload.keys()


def test_healthz_payload_one_failed(monkeypatch):
    _all_active(monkeypatch, failed={'drifter-canbridge'})
    payload, status = h._healthz_payload()
    assert status == 503
    assert payload['status'] == 'degraded'
    assert 'drifter-canbridge' in payload['services_failed']


def test_healthz_payload_caches(monkeypatch):
    calls = {'n': 0}

    def fake_map(units):
        calls['n'] += 1
        return {u: True for u in units}

    monkeypatch.setattr(h, '_systemctl_active_map', fake_map)
    h._healthz_payload()
    n_first = calls['n']
    h._healthz_payload()  # within TTL — must hit cache
    assert calls['n'] == n_first


def test_healthz_payload_telemetry_fresh(monkeypatch):
    import time as _time
    _all_active(monkeypatch)
    state.latest_state['_last_update'] = _time.time()
    payload, _ = h._healthz_payload()
    assert payload['telemetry_fresh'] is True


def test_healthz_payload_mqtt_shim_works_without_is_connected(monkeypatch):
    """Old paho clients lack is_connected() — must not raise."""
    _all_active(monkeypatch)

    class FakeOldPaho:
        pass

    state.mqtt_client = FakeOldPaho()
    payload, _ = h._healthz_payload()
    assert payload['mqtt_connected'] is False
    state.mqtt_client = None

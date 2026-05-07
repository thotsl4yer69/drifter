# tests/test_web_dashboard_handlers.py
"""Tests for the refactored dispatch table in web_dashboard_handlers."""
import sys

import pytest
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

def _reset_healthz_cache():
    h._healthz_cache.update(ts=0.0, payload=None, http_status=200)


@pytest.fixture(autouse=True)
def _fresh_heartbeats(monkeypatch):
    """Default to fresh heartbeats so unrelated tests don't trip the
    capability override — the bug-fix test below opts back into a stale one."""
    monkeypatch.setattr(h, '_heartbeat_fresh', lambda *_a, **_kw: True)


def test_healthz_route_registered():
    """Fleet contract: /healthz must dispatch via the exact-match table."""
    assert '/healthz' in h.DashboardHandler._EXACT_GET_ROUTES
    assert h.DashboardHandler._EXACT_GET_ROUTES['/healthz'] is \
        h.DashboardHandler._get_healthz


def test_healthz_payload_all_active(monkeypatch):
    """Every service active → status=ok, http=200."""
    _reset_healthz_cache()
    monkeypatch.setattr(h, '_systemctl_active', lambda _u: True)
    state.mqtt_client = None
    state.latest_state.clear()
    payload, status = h._healthz_payload()
    assert status == 200
    assert payload['status'] == 'ok'
    assert payload['services_failed'] == []
    assert all(payload['services'].values())
    assert 'ts' in payload
    assert 'mqtt_connected' in payload
    assert 'telemetry_fresh' in payload


def test_healthz_payload_one_failed(monkeypatch):
    """One failed unit → status=degraded, http=503, listed in services_failed."""
    _reset_healthz_cache()
    monkeypatch.setattr(
        h, '_systemctl_active',
        lambda u: u != 'drifter-canbridge',
    )
    state.mqtt_client = None
    state.latest_state.clear()
    payload, status = h._healthz_payload()
    assert status == 503
    assert payload['status'] == 'degraded'
    assert 'drifter-canbridge' in payload['services_failed']


def test_healthz_payload_caches(monkeypatch):
    """Within TTL, repeat calls don't re-poke systemctl."""
    _reset_healthz_cache()
    calls = {'n': 0}

    def fake_active(_u):
        calls['n'] += 1
        return True

    monkeypatch.setattr(h, '_systemctl_active', fake_active)
    state.mqtt_client = None

    h._healthz_payload()
    n_first = calls['n']
    h._healthz_payload()  # second call within TTL — should hit cache
    assert calls['n'] == n_first


def test_healthz_payload_telemetry_fresh(monkeypatch):
    """Recent _last_update flips telemetry_fresh to True."""
    import time as _time
    _reset_healthz_cache()
    monkeypatch.setattr(h, '_systemctl_active', lambda _u: True)
    state.mqtt_client = None
    state.latest_state.clear()
    state.latest_state['_last_update'] = _time.time()
    payload, _ = h._healthz_payload()
    assert payload['telemetry_fresh'] is True


def test_healthz_payload_mqtt_shim_works_without_is_connected(monkeypatch):
    """Old paho clients lack is_connected() — must not raise."""
    _reset_healthz_cache()
    monkeypatch.setattr(h, '_systemctl_active', lambda _u: True)

    class FakeOldPaho:
        pass  # no is_connected attribute

    state.mqtt_client = FakeOldPaho()
    payload, _ = h._healthz_payload()
    assert payload['mqtt_connected'] is False
    state.mqtt_client = None


def test_healthz_voicein_stale_heartbeat_marks_degraded(monkeypatch):
    """systemd reports voicein active, but its mic loop has stalled — the
    capability override must mark it failed and flip status to 503. This is
    the regression that masked a broken mic as a healthy node."""
    _reset_healthz_cache()
    monkeypatch.setattr(h, '_systemctl_active', lambda _u: True)
    monkeypatch.setattr(h, '_heartbeat_fresh', lambda *_a, **_kw: False)
    state.mqtt_client = None
    state.latest_state.clear()
    payload, status = h._healthz_payload()
    assert status == 503
    assert payload['status'] == 'degraded'
    assert payload['services']['drifter-voicein'] is False
    assert 'drifter-voicein' in payload['services_failed']

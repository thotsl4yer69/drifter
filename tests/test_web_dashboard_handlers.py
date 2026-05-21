# tests/test_web_dashboard_handlers.py
"""Tests for the refactored dispatch table in web_dashboard_handlers."""
import sys
from unittest.mock import MagicMock

import pytest
sys.path.insert(0, 'src')

import web_dashboard_handlers as h
import web_dashboard_state as state


def test_exact_get_route_table_covers_key_endpoints():
    routes = h.DashboardHandler._EXACT_GET_ROUTES
    for path in ['/', '/index.html', '/settings', '/healthz',
                 '/api/state', '/api/hardware', '/api/settings',
                 '/api/mechanic/advice', '/api/mode']:
        assert path in routes, f"missing route {path}"


def test_root_route_serves_cockpit():
    """The front door is the cockpit. The old DASHBOARD_HTML and
    /legacy route are gone (Phase 5 of the cutover)."""
    routes = h.DashboardHandler._EXACT_GET_ROUTES
    assert routes['/'] is h.DashboardHandler._serve_dashboard_page
    assert '/legacy' not in routes
    assert not hasattr(h.DashboardHandler, '_serve_legacy_dashboard')


def test_legacy_urls_redirect_to_root():
    """/preview/cockpit (cockpit's old URL) and /settings (replaced by
    the inline overlay) both 301 to / so bookmarks survive."""
    routes = h.DashboardHandler._EXACT_GET_ROUTES
    assert routes['/preview/cockpit'] is h.DashboardHandler._redirect_to_root
    assert routes['/settings']        is h.DashboardHandler._redirect_to_root
    handler = h.DashboardHandler.__new__(h.DashboardHandler)
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()
    h.DashboardHandler._redirect_to_root(handler, None)
    handler.send_response.assert_called_once_with(301)
    location_header = [c for c in handler.send_header.call_args_list
                       if c[0][0] == 'Location']
    assert location_header and location_header[0][0][1] == '/'


def test_static_panel_routes_are_gone():
    """Phase 2: every static-content panel route deleted; ASK + corpus
    are the only path to that knowledge now."""
    routes = h.DashboardHandler._EXACT_GET_ROUTES
    for path in ['/mechanic', '/api/mechanic/search', '/api/mechanic/specs',
                 '/api/mechanic/problems', '/api/mechanic/service',
                 '/api/mechanic/emergency', '/api/mechanic/torque',
                 '/api/mechanic/fuses', '/api/mechanic/training',
                 '/api/mechanic/tsb']:
        assert path not in routes, f"stale route {path} still in table"


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
    """Empty latest_state → prompt emits explicit NO DATA per sensor +
    a no-invention reminder. Phase 5.3 grounding fix replaces the old
    collapsed 'No live telemetry — car may be off' line that the model
    was happy to ignore."""
    state.latest_state.clear()
    prompt = h.build_query_context('anything')
    assert 'RPM: NO DATA' in prompt
    assert 'Coolant: NO DATA' in prompt
    assert 'do NOT invent' in prompt or 'Never estimate' in prompt


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
def _fresh_heartbeats(monkeypatch, tmp_path):
    """Default to fresh heartbeats AND a tmp-isolated mode state so unrelated
    tests don't trip the capability override or the mode-aware failure filter.
    Tests that exercise those code paths opt back in explicitly."""
    monkeypatch.setattr(h, '_heartbeat_fresh', lambda *_a, **_kw: True)
    # MODE_STATE_PATH points to a non-existent file → DEFAULT_MODE ('drive')
    # is used, which expects every drive+shared service running. Mirrors the
    # original assumption these tests were written under.
    monkeypatch.setattr(h, 'MODE_STATE_PATH', tmp_path / 'mode.state')


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
    """A failed non-hardware service → status=degraded, http=503."""
    _reset_healthz_cache()
    # drifter-watchdog is SHARED + not hardware-optional → real failure path.
    monkeypatch.setattr(
        h, '_systemctl_active',
        lambda u: u != 'drifter-watchdog',
    )
    state.mqtt_client = None
    state.latest_state.clear()
    payload, status = h._healthz_payload()
    assert status == 503
    assert payload['status'] == 'degraded'
    assert 'drifter-watchdog' in payload['services_failed']


def test_healthz_payload_hw_optional_inactive(monkeypatch):
    """A hardware-optional service down → status=ok-hw-pending, http=200,
    surfaced in services_hw_pending. Bench units without OBD-II/RTL-SDR
    must still pass the deploy contract."""
    _reset_healthz_cache()
    monkeypatch.setattr(
        h, '_systemctl_active',
        lambda u: u != 'drifter-canbridge',
    )
    state.mqtt_client = None
    state.latest_state.clear()
    payload, status = h._healthz_payload()
    assert status == 200
    assert payload['status'] == 'ok-hw-pending'
    assert 'drifter-canbridge' in payload['services_hw_pending']
    assert payload['services_failed'] == []


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


def test_healthz_foot_mode_ignores_drive_only_inactive(monkeypatch, tmp_path):
    """In FOOT mode, drive-only services being inactive is the *expected*
    state — the contract must report status=ok and not list them as failed."""
    state_path = tmp_path / 'mode.state'
    state_path.write_text('foot\n')
    monkeypatch.setattr(h, 'MODE_STATE_PATH', state_path)
    _reset_healthz_cache()
    # All drive-only services down, all foot+shared up.
    drive_only = {'drifter-canbridge', 'drifter-alerts', 'drifter-anomaly',
                  'drifter-analyst', 'drifter-voice', 'drifter-realdash',
                  'drifter-fbmirror', 'drifter-rf'}
    monkeypatch.setattr(h, '_systemctl_active', lambda u: u not in drive_only)
    state.mqtt_client = None
    state.latest_state.clear()
    payload, status = h._healthz_payload()
    assert status == 200
    assert payload['status'] == 'ok'
    assert payload['mode'] == 'foot'
    assert payload['services_failed'] == []
    # Underlying services dict still reflects truth — drive-only ARE inactive.
    assert payload['services']['drifter-canbridge'] is False


def test_healthz_voicein_stale_heartbeat_marks_hw_pending(monkeypatch):
    """systemd reports voicein active, but its mic loop has stalled — the
    capability override marks it inactive and surfaces it on
    services_hw_pending. Voicein is hardware-optional (mic might not be
    plugged in on the bench), so this is HTTP 200 with status=ok-hw-pending,
    not a fatal 503 — but the broken-mic state IS visible to operators."""
    _reset_healthz_cache()
    monkeypatch.setattr(h, '_systemctl_active', lambda _u: True)
    monkeypatch.setattr(h, '_heartbeat_fresh', lambda *_a, **_kw: False)
    state.mqtt_client = None
    state.latest_state.clear()
    payload, status = h._healthz_payload()
    assert status == 200
    assert payload['status'] == 'ok-hw-pending'
    assert payload['services']['drifter-voicein'] is False
    assert 'drifter-voicein' in payload['services_hw_pending']
    assert payload['services_failed'] == []


# ── /api/rfaudio/command ──────────────────────────────────────────────

import io
import json as _json
from unittest.mock import MagicMock


def _build_post_handler(body: bytes, peer: str = '127.0.0.1'):
    """Wire up a DashboardHandler instance enough to call a _post_ method."""
    handler = h.DashboardHandler.__new__(h.DashboardHandler)
    handler.rfile = io.BytesIO(body)
    handler.wfile = io.BytesIO()
    handler.headers = {'Content-Length': str(len(body))}
    handler.client_address = (peer, 0)
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()
    handler.send_error = MagicMock()
    return handler


def test_post_rfaudio_command_forwards_to_mqtt(monkeypatch):
    """A valid POST must publish the body verbatim to drifter/rfaudio/command."""
    state.mqtt_client = MagicMock()
    handler = _build_post_handler(b'{"action":"list_bands"}')
    handler._post_rfaudio_command()
    state.mqtt_client.publish.assert_called_once()
    topic, payload = state.mqtt_client.publish.call_args[0]
    assert topic == 'drifter/rfaudio/command'
    assert _json.loads(payload) == {'action': 'list_bands'}


def test_post_rfaudio_command_rejects_missing_action(monkeypatch):
    state.mqtt_client = MagicMock()
    handler = _build_post_handler(b'{"freq_mhz": 476.525}')
    handler._post_rfaudio_command()
    handler.send_error.assert_called_once()
    args = handler.send_error.call_args[0]
    assert args[0] == 400
    state.mqtt_client.publish.assert_not_called()


def test_post_rfaudio_command_rejects_non_json(monkeypatch):
    state.mqtt_client = MagicMock()
    handler = _build_post_handler(b'this is not json')
    handler._post_rfaudio_command()
    handler.send_error.assert_called_once()
    args = handler.send_error.call_args[0]
    assert args[0] == 400


def test_post_rfaudio_command_forwards_full_start_payload(monkeypatch):
    """The handler must not strip fields — freq_mhz/mode/gain reach rfaudio.py."""
    state.mqtt_client = MagicMock()
    body = b'{"action":"start","freq_mhz":476.525,"mode":"nfm","gain":0}'
    handler = _build_post_handler(body)
    handler._post_rfaudio_command()
    topic, payload = state.mqtt_client.publish.call_args[0]
    parsed = _json.loads(payload)
    assert parsed['action'] == 'start'
    assert parsed['freq_mhz'] == 476.525
    assert parsed['mode'] == 'nfm'
    assert parsed['gain'] == 0


def test_post_rfaudio_command_rejects_remote_peer():
    """Hotspot ACL — anything outside 127.0.0.1 / 10.42.0.0/24 must 403."""
    state.mqtt_client = MagicMock()
    handler = _build_post_handler(b'{"action":"list_bands"}', peer='192.168.1.50')
    handler._post_rfaudio_command()
    handler.send_error.assert_called_once()
    assert handler.send_error.call_args[0][0] == 403
    state.mqtt_client.publish.assert_not_called()


def test_post_rfaudio_command_rejects_unknown_action():
    """Action allowlist — only start/stop/scan/test_tone/list_bands."""
    state.mqtt_client = MagicMock()
    handler = _build_post_handler(b'{"action":"shutdown"}')
    handler._post_rfaudio_command()
    handler.send_error.assert_called_once()
    assert handler.send_error.call_args[0][0] == 400
    state.mqtt_client.publish.assert_not_called()


def test_get_rfaudio_status_route_registered():
    routes = h.DashboardHandler._EXACT_GET_ROUTES
    assert '/api/rfaudio/status' in routes
    assert callable(routes['/api/rfaudio/status'])


def test_get_rfaudio_status_serves_latest(monkeypatch):
    """The handler must surface latest_state['rfaudio_status'] verbatim."""
    state.latest_state.clear()
    state.latest_state['rfaudio_status'] = {
        'state': 'playing', 'freq_mhz': 476.525, 'mode': 'nfm',
        'bands': [{'name': 'UHF-CB-Ch5', 'freq_mhz': 476.525, 'mode': 'nfm'}],
        'ts': 0,
    }
    handler = _build_post_handler(b'')
    handler._serve_json = MagicMock()
    h.DashboardHandler._get_rfaudio_status(handler, None)
    handler._serve_json.assert_called_once()
    payload = handler._serve_json.call_args[0][0]
    assert payload['state'] == 'playing'
    assert payload['freq_mhz'] == 476.525
    assert len(payload['bands']) == 1


def test_get_rfaudio_status_serves_empty_when_no_publish_yet():
    state.latest_state.clear()
    handler = _build_post_handler(b'')
    handler._serve_json = MagicMock()
    h.DashboardHandler._get_rfaudio_status(handler, None)
    handler._serve_json.assert_called_once_with({})


# ── /api/alerts/recent ────────────────────────────────────────────────

def test_get_recent_alerts_route_registered():
    routes = h.DashboardHandler._EXACT_GET_ROUTES
    assert '/api/alerts/recent' in routes
    assert callable(routes['/api/alerts/recent'])


def test_get_recent_alerts_returns_newest_first():
    state.recent_alerts.clear()
    state.recent_alerts.append({'ts': 1.0, 'level': 1, 'name': 'info',     'message': 'old'})
    state.recent_alerts.append({'ts': 2.0, 'level': 2, 'name': 'warn',     'message': 'mid'})
    state.recent_alerts.append({'ts': 3.0, 'level': 3, 'name': 'critical', 'message': 'new'})
    handler = _build_post_handler(b'')
    handler._serve_json = MagicMock()
    h.DashboardHandler._get_recent_alerts(handler, None)
    payload = handler._serve_json.call_args[0][0]
    assert [a['message'] for a in payload['alerts']] == ['new', 'mid', 'old']


def test_get_recent_alerts_empty_when_none():
    state.recent_alerts.clear()
    handler = _build_post_handler(b'')
    handler._serve_json = MagicMock()
    h.DashboardHandler._get_recent_alerts(handler, None)
    handler._serve_json.assert_called_once_with({'alerts': []})


# ── /api/aircraft/recent ──────────────────────────────────────────────

def test_get_recent_aircraft_route_registered():
    routes = h.DashboardHandler._EXACT_GET_ROUTES
    assert '/api/aircraft/recent' in routes
    assert callable(routes['/api/aircraft/recent'])


def test_get_recent_aircraft_returns_snapshot():
    state.latest_state.clear()
    snap = {
        'ts': 100.0,
        'origin': {'lat': -37.85, 'lon': 145.12, 'source': 'gps'},
        'count': 2,
        'aircraft': [
            {'hex': 'abc', 'flight': 'QFA1', 'distance_km': 3.1, 'interesting': False},
            {'hex': 'def', 'flight': 'JST2', 'distance_km': 8.4, 'interesting': True},
        ],
    }
    state.latest_state['feeds_aircraft_snapshot'] = snap
    handler = _build_post_handler(b'')
    handler._serve_json = MagicMock()
    h.DashboardHandler._get_recent_aircraft(handler, None)
    payload = handler._serve_json.call_args[0][0]
    assert payload['count'] == 2
    assert len(payload['aircraft']) == 2
    assert payload['aircraft'][1]['interesting'] is True


def test_get_recent_aircraft_empty_when_no_snapshot():
    state.latest_state.clear()
    handler = _build_post_handler(b'')
    handler._serve_json = MagicMock()
    h.DashboardHandler._get_recent_aircraft(handler, None)
    handler._serve_json.assert_called_once_with({})


# ── on_message → recent_alerts capture ────────────────────────────────

class _FakeMsg:
    def __init__(self, topic, payload):
        self.topic = topic
        self.payload = payload if isinstance(payload, (bytes, bytearray)) else _json.dumps(payload).encode()


def test_on_message_captures_alert_to_ring_buffer():
    state.recent_alerts.clear()
    state.latest_state.clear()
    msg = _FakeMsg('drifter/alert/message',
                   {'level': 2, 'name': 'warn', 'message': 'coolant rising', 'ts': 42.0})
    state.on_message(None, None, msg)
    assert len(state.recent_alerts) == 1
    captured = state.recent_alerts[0]
    assert captured['message'] == 'coolant rising'
    assert captured['level'] == 2
    assert captured['ts'] == 42.0


def test_on_message_dedupes_identical_consecutive_alerts():
    state.recent_alerts.clear()
    payload = {'level': 1, 'name': 'info', 'message': 'idle', 'ts': 1.0}
    for _ in range(5):
        state.on_message(None, None, _FakeMsg('drifter/alert/message', payload))
    assert len(state.recent_alerts) == 1


def test_on_message_appends_when_message_changes():
    state.recent_alerts.clear()
    state.on_message(None, None, _FakeMsg('drifter/alert/message',
                    {'level': 1, 'name': 'info', 'message': 'first',  'ts': 1.0}))
    state.on_message(None, None, _FakeMsg('drifter/alert/message',
                    {'level': 2, 'name': 'warn', 'message': 'second', 'ts': 2.0}))
    assert len(state.recent_alerts) == 2
    assert state.recent_alerts[-1]['message'] == 'second'


def test_on_message_ignores_non_alert_topics_for_ring_buffer():
    state.recent_alerts.clear()
    state.on_message(None, None, _FakeMsg('drifter/engine/rpm', {'rpm': 850}))
    state.on_message(None, None, _FakeMsg('drifter/alert/level', {'level': 2}))
    assert len(state.recent_alerts) == 0


def test_on_message_skips_alert_with_empty_message():
    state.recent_alerts.clear()
    state.on_message(None, None, _FakeMsg('drifter/alert/message',
                    {'level': 0, 'name': 'nominal', 'message': '', 'ts': 1.0}))
    assert len(state.recent_alerts) == 0


# ── POST /api/gps/manual — accuracy gate ──────────────────────────────
# Background: the handler was accepting any browser-geolocation payload
# as an authoritative fix. A laptop without GPS hardware reports IP-based
# geolocation with 1-50km accuracy, which then poisoned feeds.origin()
# and produced a phantom vehicle position. The accuracy gate rejects
# anything coarser than GPS_MAX_ACCURACY_M (100m).

def _gps_manual_handler(payload: dict, peer: str = '127.0.0.1', tmp_path=None,
                        monkeypatch=None):
    handler = _build_post_handler(_json.dumps(payload).encode(), peer=peer)
    handler._serve_json = MagicMock()
    if tmp_path is not None and monkeypatch is not None:
        monkeypatch.setattr(h, '_GPS_STATE_PATH', tmp_path / 'gps.json')
    state.mqtt_client = MagicMock()
    handler._post_gps_manual()
    return handler


def test_post_gps_manual_rejects_missing_accuracy(monkeypatch, tmp_path):
    handler = _gps_manual_handler({'lat': -37.85, 'lng': 145.12},
                                  tmp_path=tmp_path, monkeypatch=monkeypatch)
    handler.send_error.assert_called_once()
    code = handler.send_error.call_args[0][0]
    assert code == 400


def test_post_gps_manual_rejects_25km_ip_geolocation(monkeypatch, tmp_path):
    handler = _gps_manual_handler(
        {'lat': -37.85, 'lng': 145.12, 'accuracy_m': 25000},
        tmp_path=tmp_path, monkeypatch=monkeypatch)
    handler.send_error.assert_called_once()
    code, msg = handler.send_error.call_args[0]
    assert code == 400
    assert '25000m' in msg or '25000.' in msg


def test_post_gps_manual_rejects_zero_accuracy(monkeypatch, tmp_path):
    handler = _gps_manual_handler(
        {'lat': -37.85, 'lng': 145.12, 'accuracy_m': 0.0},
        tmp_path=tmp_path, monkeypatch=monkeypatch)
    handler.send_error.assert_called_once()
    assert handler.send_error.call_args[0][0] == 400


def test_post_gps_manual_accepts_real_gps_accuracy(monkeypatch, tmp_path):
    handler = _gps_manual_handler(
        {'lat': -37.85, 'lng': 145.12, 'accuracy_m': 8.5},
        tmp_path=tmp_path, monkeypatch=monkeypatch)
    handler.send_error.assert_not_called()
    handler._serve_json.assert_called_once()
    state.mqtt_client.publish.assert_called_once()
    topic, payload = state.mqtt_client.publish.call_args[0]
    assert topic == 'drifter/gps/fix'
    fix = _json.loads(payload)
    assert fix['accuracy_m'] == 8.5
    assert fix['source'] == 'browser'


def test_post_gps_manual_threshold_boundary(monkeypatch, tmp_path):
    # Exactly at GPS_MAX_ACCURACY_M (100m) must pass; one over must fail.
    pass_h = _gps_manual_handler(
        {'lat': 0.0, 'lng': 0.0, 'accuracy_m': h.GPS_MAX_ACCURACY_M},
        tmp_path=tmp_path, monkeypatch=monkeypatch)
    pass_h.send_error.assert_not_called()

    fail_h = _gps_manual_handler(
        {'lat': 0.0, 'lng': 0.0, 'accuracy_m': h.GPS_MAX_ACCURACY_M + 0.1},
        tmp_path=tmp_path, monkeypatch=monkeypatch)
    fail_h.send_error.assert_called_once()


# ── Settings schema + cockpit overlay ────────────────────────────────

def test_settings_schema_route_registered():
    routes = h.DashboardHandler._EXACT_GET_ROUTES
    assert '/api/settings/schema' in routes
    assert routes['/api/settings/schema'] is h.DashboardHandler._get_settings_schema


def test_settings_schema_handler_emits_schema_payload():
    handler = h.DashboardHandler.__new__(h.DashboardHandler)
    handler._serve_json = MagicMock()
    h.DashboardHandler._get_settings_schema(handler, None)
    handler._serve_json.assert_called_once()
    payload = handler._serve_json.call_args[0][0]
    assert 'fields' in payload and 'sections' in payload
    keys = {f['key'] for f in payload['fields']}
    assert 'setup_complete' not in keys, \
        "setup_complete must not appear in the operator-facing schema"
    # Sanity: known operator fields are present.
    for k in ('tts_engine', 'temp_unit', 'pressure_unit',
              'voice_min_level', 'llm_max_tokens', 'data_retention_days'):
        assert k in keys, f"schema missing operator field {k}"


def test_cockpit_html_does_not_render_setup_complete():
    """The served cockpit must not surface setup_complete as a
    user-toggleable control. Test reads the deployed-or-source HTML
    directly so a regression in the template is caught here."""
    from pathlib import Path
    candidates = [
        Path('/opt/drifter/ui/cockpit-preview.html'),
        Path('ui/cockpit-preview.html'),
    ]
    html_path = next((p for p in candidates if p.exists()), None)
    assert html_path is not None, "cockpit HTML not found in known locations"
    html = html_path.read_text(encoding='utf-8')
    assert 'setup_complete' not in html, \
        f"'setup_complete' must not appear in cockpit HTML ({html_path})"
    assert 'data-key="setup_complete"' not in html


def _settings_post_handler(body_dict, monkeypatch, tmp_path):
    """Build a DashboardHandler stub that runs _post_settings on the
    supplied body dict. Returns the stub so the test can inspect
    send_error / _serve_json calls."""
    import json as _json
    import config as cfg
    monkeypatch.setattr(cfg, 'SETTINGS_FILE', tmp_path / 'settings.json')

    handler = h.DashboardHandler.__new__(h.DashboardHandler)
    raw = _json.dumps(body_dict).encode()

    class _Stream:
        def __init__(self, data): self.data = data
        def read(self, n): return self.data[:n]

    handler.rfile = _Stream(raw)
    handler.headers = {'Content-Length': str(len(raw))}
    handler.send_error = MagicMock()
    handler._serve_json = MagicMock()
    h.DashboardHandler._post_settings(handler)
    return handler


def test_post_settings_rejects_invalid_enum(monkeypatch, tmp_path):
    handler = _settings_post_handler({'temp_unit': 'K'},
                                     monkeypatch=monkeypatch, tmp_path=tmp_path)
    handler.send_error.assert_called_once()
    code, msg = handler.send_error.call_args[0]
    assert code == 400
    assert 'temp_unit' in msg


def test_post_settings_rejects_out_of_range_int(monkeypatch, tmp_path):
    handler = _settings_post_handler({'voice_min_level': 99},
                                     monkeypatch=monkeypatch, tmp_path=tmp_path)
    handler.send_error.assert_called_once()
    code, _ = handler.send_error.call_args[0]
    assert code == 400


def test_post_settings_accepts_valid_payload(monkeypatch, tmp_path):
    handler = _settings_post_handler({'temp_unit': 'F', 'voice_min_level': 1},
                                     monkeypatch=monkeypatch, tmp_path=tmp_path)
    handler.send_error.assert_not_called()
    handler._serve_json.assert_called_once()
    body = handler._serve_json.call_args[0][0]
    assert body.get('ok') is True


def test_post_settings_passes_through_setup_complete(monkeypatch, tmp_path):
    # Onboarding flow must still be able to set this even though it's
    # excluded from the schema.
    handler = _settings_post_handler({'setup_complete': True},
                                     monkeypatch=monkeypatch, tmp_path=tmp_path)
    handler.send_error.assert_not_called()
    handler._serve_json.assert_called_once()


def test_post_settings_drops_unknown_keys_via_save_settings(monkeypatch, tmp_path):
    # Unknown key passes the schema validator (it doesn't know about
    # it) but save_settings drops it via the SETTINGS_DEFAULTS allowlist.
    import config as cfg
    _settings_post_handler({'totally_unknown_key': 42},
                           monkeypatch=monkeypatch, tmp_path=tmp_path)
    persisted = (tmp_path / 'settings.json').read_text()
    assert 'totally_unknown_key' not in persisted

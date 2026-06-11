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
    # MODE_STATE_PATH points to a non-existent file → DEFAULT_MODE ('diag', the
    # lean floor) is used; tests that need a wider expected set pin their own
    # mode.state. Mirrors the
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


def test_healthz_payload_lcd_inactive_is_hw_pending(monkeypatch):
    """The in-car SPI LCD triage console (drifter-lcd) needs /dev/fb1; on a
    bench without the panel it exits hw-pending. It must classify as
    hardware-optional so the deploy gate stays HTTP 200 rather than degraded.
    (drifter-lcd replaced drifter-fbmirror as the sole SPI dash service.)
    """
    _reset_healthz_cache()
    monkeypatch.setattr(
        h, '_systemctl_active',
        lambda u: u != 'drifter-lcd',
    )
    state.mqtt_client = None
    state.latest_state.clear()
    payload, status = h._healthz_payload()
    assert status == 200
    assert payload['status'] == 'ok-hw-pending'
    assert 'drifter-lcd' in payload['services_hw_pending']
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
                  'drifter-rf'}
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


def test_healthz_voicein_stale_heartbeat_marks_hw_pending(monkeypatch, tmp_path):
    """systemd reports voicein active, but its mic loop has stalled — the
    capability override marks it inactive and surfaces it on
    services_hw_pending. Voicein is hardware-optional (mic might not be
    plugged in on the bench), so this is HTTP 200 with status=ok-hw-pending,
    not a fatal 503 — but the broken-mic state IS visible to operators."""
    # voicein only exists in drive/foot (not the lean diag default), so pin a
    # persona that expects it.
    state_path = tmp_path / 'mode.state'
    state_path.write_text('drive\n')
    monkeypatch.setattr(h, 'MODE_STATE_PATH', state_path)
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


def _patch_rfaudio_probes(monkeypatch, sdr=True, speaker=True):
    """Stub hw_probe probes so rfaudio-status tests don't shell out."""
    monkeypatch.setattr(h, 'probe_rtl_sdr', lambda: {
        'connected': sdr, 'action': '' if sdr else 'Plug in RTL-SDR dongle'})
    monkeypatch.setattr(h, 'probe_speaker', lambda: {
        'connected': speaker,
        'action': '' if speaker else 'Plug in USB audio dongle (plughw:0,0)'})


def test_get_rfaudio_status_serves_latest(monkeypatch):
    """The handler must surface latest_state['rfaudio_status'] verbatim,
    plus the merged hardware-presence echo (BE-2)."""
    _patch_rfaudio_probes(monkeypatch, sdr=True, speaker=True)
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
    assert payload['sdr_present'] is True
    assert payload['speaker_present'] is True


def test_get_rfaudio_status_serves_empty_when_no_publish_yet(monkeypatch):
    """Even with no retained status, the presence echo (BE-2) is always
    present so the cockpit can gate honestly from a cold start."""
    _patch_rfaudio_probes(monkeypatch, sdr=False, speaker=False)
    state.latest_state.clear()
    handler = _build_post_handler(b'')
    handler._serve_json = MagicMock()
    h.DashboardHandler._get_rfaudio_status(handler, None)
    handler._serve_json.assert_called_once()
    payload = handler._serve_json.call_args[0][0]
    assert payload['sdr_present'] is False
    assert payload['speaker_present'] is False
    assert payload['sdr_action']
    assert payload['speaker_action']


def test_rfaudio_status_includes_presence(monkeypatch):
    """BE-2 regression: monkeypatch probes, assert sdr_present/speaker_present
    keys are present and latest_state is NOT mutated by building the echo."""
    _patch_rfaudio_probes(monkeypatch, sdr=False, speaker=True)
    state.latest_state.clear()
    base = {'state': 'idle', 'freq_mhz': None, 'mode': None,
            'error': 'rtl_fm exited', 'ts': 12.0}
    state.latest_state['rfaudio_status'] = base
    handler = _build_post_handler(b'')
    handler._serve_json = MagicMock()
    h.DashboardHandler._get_rfaudio_status(handler, None)
    payload = handler._serve_json.call_args[0][0]
    assert 'sdr_present' in payload and 'speaker_present' in payload
    assert payload['sdr_present'] is False
    assert payload['speaker_present'] is True
    assert payload['sdr_action']
    # error passes through verbatim
    assert payload['error'] == 'rtl_fm exited'
    # latest_state untouched — no presence keys leaked into the retained copy.
    assert 'sdr_present' not in state.latest_state['rfaudio_status']
    assert state.latest_state['rfaudio_status'] is base


def test_dump1090_not_mirrored_from_rtl433():
    """BE-4 regression: the RF status capture path must NOT derive
    dump1090_active from rtl_433. An incoming rf/status with no independent
    dump1090 flag yields dump1090_active=None (FE renders '—'), never a
    mirror of the rtl_433 state."""
    # Behavioural check: rtl_433 up, no independent dump1090 flag →
    # dump1090_active is None, NOT mirrored from rtl_433_active=True.
    state.latest_state.clear()
    msg = _FakeMsg('drifter/rf/status', {
        'state': 'online', 'sdr_detected': True,
        'rtl433_installed': True, 'dump1090_installed': True,
        'rtl_433_active': True, 'ts': 1.0,
    })
    state.on_message(None, None, msg)
    captured = state.latest_state['rf_status']
    assert captured['dump1090_active'] is None
    assert captured['dump1090_active'] != captured['rtl_433_active']

    # When the publisher DOES supply an independent flag, it is respected
    # verbatim (not overwritten).
    state.latest_state.clear()
    msg2 = _FakeMsg('drifter/rf/status', {
        'rtl_433_active': True, 'dump1090_active': False, 'ts': 2.0,
    })
    state.on_message(None, None, msg2)
    assert state.latest_state['rf_status']['dump1090_active'] is False


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
    _settings_post_handler({'totally_unknown_key': 42},
                           monkeypatch=monkeypatch, tmp_path=tmp_path)
    persisted = (tmp_path / 'settings.json').read_text()
    assert 'totally_unknown_key' not in persisted


# ── /api/rf/command (cockpit preset buttons → drifter/rf/command) ─────

def test_post_rf_command_forwards_pause(monkeypatch):
    state.mqtt_client = MagicMock()
    handler = _build_post_handler(b'{"command":"pause_rtl_433"}')
    handler._post_rf_command()
    state.mqtt_client.publish.assert_called_once()
    topic, payload = state.mqtt_client.publish.call_args[0]
    assert topic == 'drifter/rf/command'
    assert _json.loads(payload) == {'command': 'pause_rtl_433'}


def test_post_rf_command_forwards_resume(monkeypatch):
    state.mqtt_client = MagicMock()
    handler = _build_post_handler(b'{"command":"resume_rtl_433"}')
    handler._post_rf_command()
    handler.send_error.assert_not_called()
    state.mqtt_client.publish.assert_called_once()


def test_post_rf_command_forwards_tpms_learn_start(monkeypatch):
    state.mqtt_client = MagicMock()
    handler = _build_post_handler(b'{"command":"tpms_learn_start"}')
    handler._post_rf_command()
    state.mqtt_client.publish.assert_called_once()
    _, payload = state.mqtt_client.publish.call_args[0]
    assert _json.loads(payload)['command'] == 'tpms_learn_start'


def test_post_rf_command_forwards_tpms_assign_with_assignments(monkeypatch):
    state.mqtt_client = MagicMock()
    body = b'{"command":"tpms_assign","assignments":{"123":"fl","456":"fr"}}'
    handler = _build_post_handler(body)
    handler._post_rf_command()
    state.mqtt_client.publish.assert_called_once()
    _, payload = state.mqtt_client.publish.call_args[0]
    parsed = _json.loads(payload)
    assert parsed['assignments']['123'] == 'fl'


def test_post_rf_command_rejects_tpms_assign_missing_assignments(monkeypatch):
    state.mqtt_client = MagicMock()
    handler = _build_post_handler(b'{"command":"tpms_assign"}')
    handler._post_rf_command()
    handler.send_error.assert_called_once()
    code, field = handler.send_error.call_args[0]
    assert code == 400
    assert field == 'assignments'
    state.mqtt_client.publish.assert_not_called()


def test_post_rf_command_rejects_unknown_command(monkeypatch):
    state.mqtt_client = MagicMock()
    handler = _build_post_handler(b'{"command":"shutdown_everything"}')
    handler._post_rf_command()
    handler.send_error.assert_called_once()
    code, field = handler.send_error.call_args[0]
    assert code == 400
    assert field == 'command'
    state.mqtt_client.publish.assert_not_called()


def test_post_rf_command_rejects_missing_command(monkeypatch):
    state.mqtt_client = MagicMock()
    handler = _build_post_handler(b'{}')
    handler._post_rf_command()
    handler.send_error.assert_called_once()
    code, field = handler.send_error.call_args[0]
    assert code == 400
    assert field == 'command'


def test_post_rf_command_rejects_non_json(monkeypatch):
    state.mqtt_client = MagicMock()
    handler = _build_post_handler(b'not-json')
    handler._post_rf_command()
    handler.send_error.assert_called_once()
    assert handler.send_error.call_args[0][0] == 400


def test_post_rf_command_rejects_remote_peer():
    """Hotspot ACL — anything outside 127.0.0.1 / 10.42.0.0/24 must 403."""
    state.mqtt_client = MagicMock()
    handler = _build_post_handler(b'{"command":"pause_rtl_433"}',
                                  peer='192.168.1.50')
    handler._post_rf_command()
    handler.send_error.assert_called_once()
    assert handler.send_error.call_args[0][0] == 403
    state.mqtt_client.publish.assert_not_called()


def test_rf_command_allowlist_matches_rf_monitor_handler():
    """The handler-side allowlist must mirror the rf_monitor.on_message
    if/elif chain so the cockpit can't fail-open on a real backend
    command. If somebody adds a command in rf_monitor and forgets here,
    this test still passes (the bridge still consumes the new command),
    but the cockpit can't reach it — which is the intended fail-closed
    posture. The reverse (allowlist has a command rf_monitor ignores)
    is what we guard here."""
    expected = {'tpms_learn_start', 'tpms_learn_stop', 'tpms_auto_assign',
                'tpms_assign', 'pause_rtl_433', 'resume_rtl_433',
                'force_spectrum',
                'tpms_harvest_start', 'tpms_harvest_stop',
                'tpms_assign_corner', 'tpms_clear_assignments',
                'tpms_delta_capture'}
    assert expected == h._RF_COMMANDS


# ── /api/rf/command — new commands ────────────────────────────────────

def test_post_rf_command_forwards_force_spectrum(monkeypatch):
    state.mqtt_client = MagicMock()
    handler = _build_post_handler(b'{"command":"force_spectrum"}')
    handler._post_rf_command()
    state.mqtt_client.publish.assert_called_once()
    _, payload = state.mqtt_client.publish.call_args[0]
    assert _json.loads(payload)['command'] == 'force_spectrum'


def test_post_rf_command_forwards_tpms_harvest_start(monkeypatch):
    state.mqtt_client = MagicMock()
    handler = _build_post_handler(b'{"command":"tpms_harvest_start"}')
    handler._post_rf_command()
    state.mqtt_client.publish.assert_called_once()


def test_post_rf_command_forwards_tpms_assign_corner(monkeypatch):
    state.mqtt_client = MagicMock()
    body = b'{"command":"tpms_assign_corner","sensor_id":"deadbeef","corner":"FL"}'
    handler = _build_post_handler(body)
    handler._post_rf_command()
    state.mqtt_client.publish.assert_called_once()
    _, payload = state.mqtt_client.publish.call_args[0]
    parsed = _json.loads(payload)
    assert parsed['sensor_id'] == 'deadbeef'
    assert parsed['corner'] == 'FL'


def test_post_rf_command_rejects_tpms_assign_corner_bad_corner(monkeypatch):
    state.mqtt_client = MagicMock()
    body = b'{"command":"tpms_assign_corner","sensor_id":"a","corner":"XY"}'
    handler = _build_post_handler(body)
    handler._post_rf_command()
    handler.send_error.assert_called_once()
    code, field = handler.send_error.call_args[0]
    assert code == 400
    assert field == 'corner'
    state.mqtt_client.publish.assert_not_called()


def test_post_rf_command_rejects_tpms_assign_corner_missing_sensor_id(monkeypatch):
    state.mqtt_client = MagicMock()
    body = b'{"command":"tpms_assign_corner","corner":"FL"}'
    handler = _build_post_handler(body)
    handler._post_rf_command()
    handler.send_error.assert_called_once()
    code, field = handler.send_error.call_args[0]
    assert code == 400
    assert field == 'sensor_id'


# ── /api/tpms/assignments ─────────────────────────────────────────────

def test_get_tpms_assignments_route_registered():
    assert '/api/tpms/assignments' in h.DashboardHandler._EXACT_GET_ROUTES


def test_get_tpms_assignments_returns_json(tmp_path, monkeypatch):
    monkeypatch.setattr(h, '_TPMS_ASSIGNMENTS_PATH',
                         tmp_path / 'tpms_assignments.json')
    (tmp_path / 'tpms_assignments.json').write_text(
        _json.dumps({'FL': 'deadbeef'}))
    handler = _build_post_handler(b'')
    handler._serve_json = MagicMock()
    h.DashboardHandler._get_tpms_assignments(handler, None)
    payload = handler._serve_json.call_args[0][0]
    assert payload == {'FL': 'deadbeef'}


def test_get_tpms_assignments_empty_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(h, '_TPMS_ASSIGNMENTS_PATH',
                         tmp_path / 'missing.json')
    handler = _build_post_handler(b'')
    handler._serve_json = MagicMock()
    h.DashboardHandler._get_tpms_assignments(handler, None)
    assert handler._serve_json.call_args[0][0] == {}


# ── /api/flipper/command — subghz_replay ──────────────────────────────

def test_post_flipper_command_subghz_replay_forwarded(monkeypatch):
    state.mqtt_client = MagicMock()
    body = b'{"command":"subghz_replay","capture_id":"drifter-42"}'
    handler = _build_post_handler(body)
    handler._post_flipper_command()
    state.mqtt_client.publish.assert_called_once()
    topic, payload = state.mqtt_client.publish.call_args[0]
    assert topic == 'drifter/flipper/command'
    parsed = _json.loads(payload)
    assert parsed['command'] == 'subghz_replay'
    assert parsed['capture_id'] == 'drifter-42'


def test_post_flipper_command_subghz_replay_requires_capture_id(monkeypatch):
    state.mqtt_client = MagicMock()
    handler = _build_post_handler(b'{"command":"subghz_replay"}')
    handler._post_flipper_command()
    handler.send_error.assert_called_once()
    code, field = handler.send_error.call_args[0]
    assert code == 400
    assert field == 'capture_id'
    state.mqtt_client.publish.assert_not_called()


def test_post_flipper_command_passes_through_cli_strings(monkeypatch):
    """Bare CLI commands (e.g. 'hw info') still pass through; only structured
    workflow commands are rejected when not in the allowlist."""
    state.mqtt_client = MagicMock()
    handler = _build_post_handler(b'{"command":"hw info"}')
    handler._post_flipper_command()
    state.mqtt_client.publish.assert_called_once()


def test_post_flipper_command_rejects_unknown_workflow_command(monkeypatch):
    state.mqtt_client = MagicMock()
    handler = _build_post_handler(b'{"command":"reformat_everything"}')
    handler._post_flipper_command()
    handler.send_error.assert_called_once()
    assert handler.send_error.call_args[0][0] == 400
    state.mqtt_client.publish.assert_not_called()


# ── /api/flipper/captures augmentation ────────────────────────────────

def test_get_flipper_captures_merges_persisted_sub_paths(tmp_path, monkeypatch):
    """Live ring rows with a matching .sub on disk get local_sub_path added."""
    import flipper_bridge
    monkeypatch.setattr(flipper_bridge, 'FLIPPER_CAPTURE_DIR', tmp_path)
    body = flipper_bridge.build_sub_file(433920000, [244, -732])
    (tmp_path / 'drifter-1700.sub').write_text(body)
    state.recent_flipper_captures.clear()
    state.recent_flipper_captures.append({
        'id': 'drifter-1700', 'raw': '244 -732', 'freq_hz': 433920000,
        'ts': 1700,
    })
    handler = _build_post_handler(b'')
    handler._serve_json = MagicMock()
    h.DashboardHandler._get_flipper_captures(handler, None)
    payload = handler._serve_json.call_args[0][0]
    rows = payload['captures']
    assert any('local_sub_path' in r and r['id'] == 'drifter-1700' for r in rows)


# ── Cockpit HTML — new operator surfaces ──────────────────────────────

def test_cockpit_has_force_spectrum_button():
    html = _cockpit_html()
    assert 'FORCE SPECTRUM' in html
    assert 'btn-force-spectrum' in html


def test_cockpit_has_harvest_button():
    html = _cockpit_html()
    assert 'HARVEST' in html
    assert 'tpms-harvest' in html


def test_cockpit_has_replay_confirm_row():
    html = _cockpit_html()
    # The new inline confirm uses data-confirm-replay; the legacy
    # aria-disabled REPLAY hint must be gone.
    assert 'data-confirm-replay' in html
    assert 'REPLAY' in html


# ── Cockpit HTML invariants (preset-button surface) ────────────────────

def _cockpit_html() -> str:
    from pathlib import Path
    candidates = [
        Path('/opt/drifter/ui/cockpit-preview.html'),
        Path('ui/cockpit-preview.html'),
    ]
    html_path = next((p for p in candidates if p.exists()), None)
    assert html_path is not None, "cockpit HTML not found in known locations"
    return html_path.read_text(encoding='utf-8')


def test_cockpit_has_no_flipper_free_text_input():
    """The raw Flipper CLI input has been removed — preset buttons only."""
    html = _cockpit_html()
    assert 'rf-cmd-input' not in html, "Free-text Flipper CLI input must be gone"
    assert 'rf-cmd-send' not in html


def test_cockpit_exposes_preset_button_ids():
    """Each operator surface must register at least its lead button id."""
    html = _cockpit_html()
    for marker in ['tpms-learn-start', 'flipper-monitor-start',
                   'btn-rtl433-toggle', 'btn-rfaudio-scan',
                   'rfaudio-band-stop', 'data-rfaudio-band']:
        assert marker in html, f"cockpit missing preset surface: {marker}"


def test_cockpit_titles_v3sper_panel():
    """Operator callsign for the Flipper unit is 'v3sper' — must be surfaced."""
    html = _cockpit_html()
    assert 'V3SPER' in html or 'v3sper' in html


# ── /api/voice/listen_now ─────────────────────────────────────────────

def test_post_voice_listen_now_route_registered():
    """do_POST must dispatch /api/voice/listen_now to the new handler."""
    import inspect
    src = inspect.getsource(h.DashboardHandler.do_POST)
    assert '/api/voice/listen_now' in src
    assert '_post_voice_listen_now' in src
    assert hasattr(h.DashboardHandler, '_post_voice_listen_now')


def test_post_voice_listen_now_publishes_topic(monkeypatch):
    """A POST from the hotspot publishes {ts} to drifter/voice/listen_now."""
    state.mqtt_client = MagicMock()
    handler = _build_post_handler(b'{}')
    handler._post_voice_listen_now()
    state.mqtt_client.publish.assert_called_once()
    args, kwargs = state.mqtt_client.publish.call_args
    topic, payload = args[0], args[1]
    assert topic == 'drifter/voice/listen_now'
    parsed = _json.loads(payload)
    assert isinstance(parsed.get('ts'), (int, float))
    assert kwargs.get('retain') is False
    assert kwargs.get('qos') == 0
    handler.send_error.assert_not_called()


def test_post_voice_listen_now_rejects_remote_peer():
    """Hotspot ACL — anything outside 127.0.0.1 / 10.42.0.0/24 must 403."""
    state.mqtt_client = MagicMock()
    handler = _build_post_handler(b'{}', peer='192.168.1.50')
    handler._post_voice_listen_now()
    handler.send_error.assert_called_once()
    assert handler.send_error.call_args[0][0] == 403
    state.mqtt_client.publish.assert_not_called()


def test_post_voice_listen_now_503_when_mqtt_offline():
    """No silent success — operator UI must see 503 to flash failure."""
    state.mqtt_client = None
    handler = _build_post_handler(b'{}')
    handler._post_voice_listen_now()
    handler.send_error.assert_called_once()
    assert handler.send_error.call_args[0][0] == 503


# ── /api/driver ───────────────────────────────────────────────────────

def test_get_driver_route_registered():
    routes = h.DashboardHandler._EXACT_GET_ROUTES
    assert '/api/driver' in routes
    assert routes['/api/driver'] is h.DashboardHandler._get_driver


def test_get_driver_returns_only_whitelisted_fields(monkeypatch, tmp_path):
    """Whitelist: only preferred_name, name, registration_plate may leak.
    Other fields (postcode/address/etc.) must NOT appear in the payload."""
    yaml_path = tmp_path / 'driver.yaml'
    yaml_path.write_text(
        'name: Jack Smith\n'
        'preferred_name: Jack\n'
        'registration_plate: ABC123\n'
        'home_postcode: SW1A 1AA\n'
        'phone: "+447700900000"\n'
        'address: 10 Downing Street\n'
        'units: metric\n',
        encoding='utf-8',
    )
    monkeypatch.setattr(h, '_DRIVER_YAML_PATH', yaml_path)
    handler = h.DashboardHandler.__new__(h.DashboardHandler)
    captured: dict = {}
    handler._serve_json = lambda payload: captured.update({'payload': payload})
    handler._get_driver(None)
    body = captured['payload']
    assert set(body.keys()) == {'preferred_name', 'name', 'registration_plate'}
    assert body['preferred_name'] == 'Jack'
    assert body['name'] == 'Jack Smith'
    assert body['registration_plate'] == 'ABC123'
    # Negative assertions — guard against future regressions adding more keys.
    for leak_key in ('home_postcode', 'phone', 'address', 'units'):
        assert leak_key not in body


def test_get_driver_returns_nulls_when_missing(monkeypatch, tmp_path):
    """Missing file must NOT 500 the cockpit; returns null fields at 200."""
    monkeypatch.setattr(h, '_DRIVER_YAML_PATH', tmp_path / 'no_such.yaml')
    handler = h.DashboardHandler.__new__(h.DashboardHandler)
    captured: dict = {}
    handler._serve_json = lambda payload: captured.update({'payload': payload})
    handler._get_driver(None)
    body = captured['payload']
    assert body == {'preferred_name': None, 'name': None,
                    'registration_plate': None}


# ── Mechanic chat history ring (Task B3) ──────────────────────────────

@pytest.fixture(autouse=False)
def _reset_mech_history():
    h._mechanic_history_reset()
    yield
    h._mechanic_history_reset()


def test_mechanic_history_appends_user_and_assistant_turns(_reset_mech_history):
    h._mechanic_history_append('user', 'why is coolant high')
    h._mechanic_history_append('assistant', 'reading is 95C')
    turns = h._mechanic_history_snapshot()
    assert len(turns) == 2
    assert turns[0]['role'] == 'user'
    assert turns[0]['content'] == 'why is coolant high'
    assert turns[1]['role'] == 'assistant'


def test_mechanic_history_grows_on_consecutive_calls(_reset_mech_history):
    """Append N times, length should grow up to ring max."""
    for i in range(3):
        h._mechanic_history_append('user', f'q{i}')
        h._mechanic_history_append('assistant', f'a{i}')
    turns = h._mechanic_history_snapshot()
    assert len(turns) == 6


def test_mechanic_history_bounded_by_max_turns(_reset_mech_history):
    """More than MECHANIC_HISTORY_TURNS appends → ring drops oldest."""
    for i in range(20):
        h._mechanic_history_append('user', f'short{i}')
    turns = h._mechanic_history_snapshot()
    assert len(turns) == h.MECHANIC_HISTORY_TURNS
    # Newest preserved, oldest dropped.
    assert turns[-1]['content'] == 'short19'
    assert all(t['content'] != 'short0' for t in turns)


def test_mechanic_history_char_budget_trims_long_turns(_reset_mech_history):
    """A turn that exceeds the char budget triggers a trim of older turns."""
    h._mechanic_history_append('user', 'tiny')
    # A single 9000-char message pushes total over the 8000 budget.
    big = 'x' * 9000
    h._mechanic_history_append('assistant', big)
    turns = h._mechanic_history_snapshot()
    # The 'tiny' turn should have been evicted; only the big turn remains.
    assert len(turns) == 1
    assert turns[0]['content'] == big


def test_mechanic_history_reset_clears_ring(_reset_mech_history):
    h._mechanic_history_append('user', 'one')
    h._mechanic_history_append('assistant', 'two')
    assert len(h._mechanic_history_snapshot()) == 2
    h._mechanic_history_reset()
    assert h._mechanic_history_snapshot() == []


def test_mechanic_history_endpoint_returns_empty_on_fresh_start(
        _reset_mech_history):
    handler = h.DashboardHandler.__new__(h.DashboardHandler)
    captured: dict = {}
    handler._serve_json = lambda payload: captured.update({'payload': payload})
    handler._get_mechanic_history(None)
    body = captured['payload']
    assert body['turns'] == []
    assert body['max_turns'] == h.MECHANIC_HISTORY_TURNS


def test_mechanic_history_endpoint_reflects_appended_turns(
        _reset_mech_history):
    h._mechanic_history_append('user', 'q')
    h._mechanic_history_append('assistant', 'a')
    handler = h.DashboardHandler.__new__(h.DashboardHandler)
    captured: dict = {}
    handler._serve_json = lambda payload: captured.update({'payload': payload})
    handler._get_mechanic_history(None)
    body = captured['payload']
    assert len(body['turns']) == 2
    assert body['turns'][0]['role'] == 'user'


def test_build_query_context_includes_history_block(monkeypatch,
                                                    _reset_mech_history):
    """When the ring has turns, the prompt must show a CONVERSATION HISTORY block."""
    state.latest_state.clear()
    h._mechanic_history_append('user', 'why is coolant high')
    h._mechanic_history_append('assistant', 'engine running warm')
    prompt = h.build_query_context('and what next?')
    assert 'CONVERSATION HISTORY' in prompt
    assert 'why is coolant high' in prompt
    assert 'engine running warm' in prompt


def test_mechanic_history_route_registered():
    routes = h.DashboardHandler._EXACT_GET_ROUTES
    assert '/api/mechanic/history' in routes
    assert '/api/rf/spectrum/summary' in routes


def test_rf_commands_allowlist_includes_tpms_delta_capture():
    assert 'tpms_delta_capture' in h._RF_COMMANDS


# ─── Arsenal read-side routes (BE-2) ──────────────────────────────────
# Each route: 200 + an HONEST empty shape with an empty latest_state
# (never a fabricated 'up'/'idle'/demo-row status), and 403 for a
# non-local peer (mirrors the rfaudio ACL tests above).

class _FakeParsed:
    """Minimal urlparse() stand-in carrying just a query string."""
    def __init__(self, query=''):
        self.query = query


def _call_get(method_name, peer='127.0.0.1', query=''):
    """Invoke an Arsenal GET handler with an empty latest_state and a
    captured _serve_json. Returns (handler, payload_or_None)."""
    state.latest_state.clear()
    handler = _build_post_handler(b'', peer=peer)
    handler._serve_json = MagicMock()
    method = getattr(h.DashboardHandler, method_name)
    method(handler, _FakeParsed(query))
    payload = (handler._serve_json.call_args[0][0]
               if handler._serve_json.called else None)
    return handler, payload


_ARSENAL_ROUTES = {
    '/api/kismet/devices':      '_get_kismet_devices',
    '/api/marauder/status':     '_get_marauder_status',
    '/api/marauder/scan':       '_get_marauder_scan',
    '/api/flycatcher/aircraft': '_get_flycatcher_aircraft',
    '/api/ghost/status':        '_get_ghost_status',
    '/api/alpr/plates':         '_get_alpr_plates',
    '/api/vision/status':       '_get_vision_status',
    '/api/sentry/status':       '_get_sentry_status',
}


def test_arsenal_routes_registered():
    routes = h.DashboardHandler._EXACT_GET_ROUTES
    for path, fn in _ARSENAL_ROUTES.items():
        assert path in routes, path
        assert routes[path] is getattr(h.DashboardHandler, fn)


@pytest.mark.parametrize('method_name', list(_ARSENAL_ROUTES.values()))
def test_arsenal_route_rejects_remote_peer(method_name):
    """Capture/recon data must 403 outside 127.0.0.1 / 10.42.0.0/24."""
    handler, payload = _call_get(method_name, peer='192.168.1.50')
    handler.send_error.assert_called_once()
    assert handler.send_error.call_args[0][0] == 403
    handler._serve_json.assert_not_called()


def test_arsenal_route_allows_hotspot_peer():
    """10.42.0.0/24 hotspot peer is allowed through (no 403)."""
    handler, payload = _call_get('_get_marauder_status', peer='10.42.0.7')
    handler.send_error.assert_not_called()
    handler._serve_json.assert_called_once()


def test_kismet_devices_honest_empty():
    handler, payload = _call_get('_get_kismet_devices')
    assert payload == {'wifi': [], 'ble': [], 'ts': None}


def test_marauder_status_honest_no_hardware():
    handler, payload = _call_get('_get_marauder_status')
    assert payload == {'state': 'no_hardware'}


def test_marauder_scan_honest_empty_default_stream():
    handler, payload = _call_get('_get_marauder_scan')
    assert payload == {'stream': 'ap', 'rows': []}


def test_marauder_scan_honours_stream_querystring():
    handler, payload = _call_get('_get_marauder_scan', query='stream=sta&n=5')
    assert payload == {'stream': 'sta', 'rows': []}


def test_marauder_scan_rejects_unknown_stream_falls_back_to_ap():
    handler, payload = _call_get('_get_marauder_scan', query='stream=bogus')
    assert payload['stream'] == 'ap'
    assert payload['rows'] == []


def test_flycatcher_aircraft_honest_empty():
    handler, payload = _call_get('_get_flycatcher_aircraft')
    assert payload == {'aircraft': [], 'classified': [], 'state': None}


def test_ghost_status_honest_empty():
    handler, payload = _call_get('_get_ghost_status')
    assert payload == {'status': None, 'trackers': [], 'stingray': [],
                       'alpr': [], 'rf': []}


def test_alpr_plates_honest_empty():
    handler, payload = _call_get('_get_alpr_plates')
    assert payload == {'plates': []}


def test_vision_status_honest_empty():
    handler, payload = _call_get('_get_vision_status')
    assert payload == {'status': None, 'objects': []}


def test_sentry_status_honest_empty():
    handler, payload = _call_get('_get_sentry_status')
    assert payload == {'armed': None, 'threshold_g': None,
                       'auto_arm': None, 'ts': None}


def test_marauder_scan_returns_live_rows_when_present():
    """When the scan ring has rows, they pass through (n caps newest)."""
    state.latest_state.clear()
    state.latest_state['marauder_scan_ap'] = {
        'rows': [{'ssid': 'a'}, {'ssid': 'b'}, {'ssid': 'c'}]}
    handler = _build_post_handler(b'', peer='127.0.0.1')
    handler._serve_json = MagicMock()
    h.DashboardHandler._get_marauder_scan(handler, _FakeParsed('stream=ap&n=2'))
    payload = handler._serve_json.call_args[0][0]
    assert payload['stream'] == 'ap'
    assert payload['rows'] == [{'ssid': 'b'}, {'ssid': 'c'}]


def test_sentry_status_passes_live_payload_through():
    """A retained sentry status is surfaced verbatim, not the empty shape."""
    state.latest_state.clear()
    live = {'armed': True, 'threshold_g': 2.5, 'auto_arm': False, 'ts': 99.0}
    state.latest_state['sentry_status'] = live
    handler = _build_post_handler(b'', peer='127.0.0.1')
    handler._serve_json = MagicMock()
    h.DashboardHandler._get_sentry_status(handler, _FakeParsed())
    assert handler._serve_json.call_args[0][0] == live


# ─── /api/arsenal aggregate (BE-3) ────────────────────────────────────

def _call_arsenal(monkeypatch, tmp_path, active_units, peer='127.0.0.1'):
    """Drive _get_arsenal with a mocked /healthz services source.

    `active_units` is the set of units `_systemctl_active` reports active;
    everything else is inactive. Forces 'both' mode so every arsenal unit
    is "expected" and the healthz reading isn't mode-suppressed. Returns
    (handler, payload_or_None)."""
    _reset_healthz_cache()
    monkeypatch.setattr(h, '_systemctl_active',
                        lambda u: u in active_units)
    monkeypatch.setattr(h, '_heartbeat_fresh', lambda *_a, **_kw: True)
    state_path = tmp_path / 'mode.state'
    state_path.write_text('both')
    monkeypatch.setattr(h, 'MODE_STATE_PATH', state_path)
    handler = _build_post_handler(b'', peer=peer)
    handler._serve_json = MagicMock()
    h.DashboardHandler._get_arsenal(handler, _FakeParsed())
    payload = (handler._serve_json.call_args[0][0]
               if handler._serve_json.called else None)
    return handler, payload


def test_arsenal_route_registered():
    routes = h.DashboardHandler._EXACT_GET_ROUTES
    assert '/api/arsenal' in routes
    assert routes['/api/arsenal'] is h.DashboardHandler._get_arsenal


def test_arsenal_rejects_remote_peer(monkeypatch, tmp_path):
    """Aggregate exposes recon posture — must 403 off the hotspot."""
    handler, payload = _call_arsenal(monkeypatch, tmp_path, set(),
                                     peer='192.168.1.50')
    handler.send_error.assert_called_once()
    assert handler.send_error.call_args[0][0] == 403
    handler._serve_json.assert_not_called()


def test_arsenal_shape_and_mode(monkeypatch, tmp_path):
    """200 + contract shape: {ts, mode, tools:[{name,unit,present,state,
    live_meta,actions}]} with one entry per declared foot-mode tool."""
    state.latest_state.clear()
    handler, payload = _call_arsenal(monkeypatch, tmp_path, set())
    handler.send_error.assert_not_called()
    assert set(payload) == {'ts', 'mode', 'tools'}
    assert payload['mode'] == 'both'
    assert isinstance(payload['ts'], (int, float))
    names = [t['name'] for t in payload['tools']]
    assert names == [s['name'] for s in h._ARSENAL_TOOLS]
    for t in payload['tools']:
        assert set(t) >= {'name', 'unit', 'present', 'state',
                          'live_meta', 'actions'}
        assert isinstance(t['present'], bool)
        assert isinstance(t['actions'], list)


def test_arsenal_inactive_unit_is_not_present(monkeypatch, tmp_path):
    """REAL-DATA: a tool whose unit is inactive must report present:false
    and an honest 'absent' state — never a fabricated 'up'."""
    state.latest_state.clear()
    # No units active at all.
    handler, payload = _call_arsenal(monkeypatch, tmp_path, set())
    by_name = {t['name']: t for t in payload['tools']}
    kismet = by_name['kismet']
    assert kismet['unit'] == 'drifter-kismet'
    assert kismet['present'] is False
    assert kismet['state'] == 'absent'
    assert kismet['live_meta']['unit_active'] is False


def test_arsenal_active_unit_without_hw_gate_is_present(monkeypatch, tmp_path):
    """A unit-only tool (no hardware gate) is present once its unit is
    active, and surfaces 'idle' until it publishes a live state."""
    state.latest_state.clear()
    handler, payload = _call_arsenal(monkeypatch, tmp_path,
                                     {'drifter-kismet'})
    kismet = {t['name']: t for t in payload['tools']}['kismet']
    assert kismet['present'] is True
    assert kismet['live_meta']['unit_active'] is True
    assert kismet['state'] == 'idle'


def test_arsenal_marauder_no_hardware_blocks_presence(monkeypatch, tmp_path):
    """Active unit + ESP32 reporting state:'no_hardware' => present:false.
    The hardware signal vetoes presence even though the unit is up."""
    state.latest_state.clear()
    state.latest_state['marauder_status'] = {'state': 'no_hardware'}
    handler, payload = _call_arsenal(monkeypatch, tmp_path,
                                     {'drifter-marauder'})
    marauder = {t['name']: t for t in payload['tools']}['marauder']
    assert marauder['live_meta']['unit_active'] is True
    assert marauder['live_meta']['hardware_ok'] is False
    assert marauder['present'] is False
    assert marauder['state'] == 'no_hardware'


def test_arsenal_ghost_never_present_without_unit(monkeypatch, tmp_path):
    """ghost has no service unit shipped — it stays present:false even
    when 'every' unit is reported active."""
    state.latest_state.clear()
    all_units = {s['unit'] for s in h._ARSENAL_TOOLS if s['unit']}
    handler, payload = _call_arsenal(monkeypatch, tmp_path, all_units)
    ghost = {t['name']: t for t in payload['tools']}['ghost']
    assert ghost['unit'] is None
    assert ghost['present'] is False


def test_arsenal_live_state_surfaced_when_present(monkeypatch, tmp_path):
    """A present tool surfaces its published `state` verbatim."""
    state.latest_state.clear()
    state.latest_state['rfaudio_status'] = {'state': 'playing'}
    handler, payload = _call_arsenal(monkeypatch, tmp_path,
                                     {'drifter-rfaudio'})
    rfaudio = {t['name']: t for t in payload['tools']}['rfaudio']
    assert rfaudio['present'] is True
    assert rfaudio['state'] == 'playing'


# ─── BE-4: /api/service/<unit> start/stop + marauder/sentry relays ─────
# The whole point of this stage is the SAFETY MODEL: local-peer ACL, unit
# allowlist (never DRIVE_ONLY / arbitrary), action allowlist, drive-mode
# foot-gate at the route, audit on every call. Marauder/sentry are thin
# relays (allowlist + 403 + 503 + publish), never reimplementing tiers.

def _service_handler(monkeypatch, tmp_path, body, peer='127.0.0.1',
                     mode='foot', audit=None):
    """Build a handler for _post_service with mode + audit isolated to tmp."""
    state_path = tmp_path / 'mode.state'
    state_path.write_text(mode)
    monkeypatch.setattr(h, 'MODE_STATE_PATH', state_path)
    monkeypatch.setattr(h, '_ARSENAL_AUDIT_LOG',
                        audit if audit is not None else tmp_path / 'arsenal_audit.log')
    handler = _build_post_handler(body, peer=peer)
    handler._serve_json = MagicMock()
    return handler


def _a_foot_unit():
    """A unit guaranteed to be in the arsenal allowlist (foot/shared)."""
    return sorted(h._SERVICE_UNITS)[0]


def test_post_service_rejects_drive_only_unit(monkeypatch, tmp_path):
    """A DRIVE_ONLY unit must be refused 403 — never reaches systemctl."""
    from config import DRIVE_ONLY_SERVICES
    run = MagicMock()
    monkeypatch.setattr(h.subprocess, 'run', run)
    drive_unit = DRIVE_ONLY_SERVICES[0]
    assert drive_unit not in h._SERVICE_UNITS
    handler = _service_handler(monkeypatch, tmp_path, b'{"action":"start"}')
    handler._post_service(drive_unit)
    handler.send_error.assert_called_once()
    assert handler.send_error.call_args[0][0] == 403
    run.assert_not_called()


def test_post_service_rejects_unknown_unit(monkeypatch, tmp_path):
    run = MagicMock()
    monkeypatch.setattr(h.subprocess, 'run', run)
    handler = _service_handler(monkeypatch, tmp_path, b'{"action":"start"}')
    handler._post_service('drifter-nonexistent')
    handler.send_error.assert_called_once()
    assert handler.send_error.call_args[0][0] == 403
    run.assert_not_called()


def test_post_service_rejects_bad_action(monkeypatch, tmp_path):
    run = MagicMock()
    monkeypatch.setattr(h.subprocess, 'run', run)
    handler = _service_handler(monkeypatch, tmp_path, b'{"action":"enable"}')
    handler._post_service(_a_foot_unit())
    handler.send_error.assert_called_once()
    assert handler.send_error.call_args[0][0] == 400
    run.assert_not_called()


def test_post_service_refuses_in_drive_mode(monkeypatch, tmp_path):
    """Foot-gate AT THE ROUTE: 409 when the node is in drive mode."""
    run = MagicMock()
    monkeypatch.setattr(h.subprocess, 'run', run)
    handler = _service_handler(monkeypatch, tmp_path, b'{"action":"start"}',
                               mode='drive')
    handler._post_service(_a_foot_unit())
    handler.send_error.assert_called_once()
    assert handler.send_error.call_args[0][0] == 409
    run.assert_not_called()


def test_post_service_rejects_remote_peer(monkeypatch, tmp_path):
    run = MagicMock()
    monkeypatch.setattr(h.subprocess, 'run', run)
    handler = _service_handler(monkeypatch, tmp_path, b'{"action":"start"}',
                               peer='192.168.1.50')
    handler._post_service(_a_foot_unit())
    handler.send_error.assert_called_once()
    assert handler.send_error.call_args[0][0] == 403
    run.assert_not_called()


def test_post_service_happy_path_invokes_systemctl_and_audits(monkeypatch, tmp_path):
    """A valid start in foot mode runs `sudo -n systemctl start <unit>`,
    returns {ok,unit,action,rc}, and writes an audit record."""
    audit_log = tmp_path / 'arsenal_audit.log'
    completed = MagicMock()
    completed.returncode = 0
    completed.stdout = ''
    completed.stderr = ''
    run = MagicMock(return_value=completed)
    monkeypatch.setattr(h.subprocess, 'run', run)
    unit = _a_foot_unit()
    handler = _service_handler(monkeypatch, tmp_path, b'{"action":"start"}',
                               audit=audit_log)
    handler._post_service(unit)
    # systemctl invoked with sudo -n and the exact unit/action.
    args = run.call_args[0][0]
    assert args == ['sudo', '-n', 'systemctl', 'start', unit]
    # response shape
    handler._serve_json.assert_called_once()
    out = handler._serve_json.call_args[0][0]
    assert out == {'ok': True, 'unit': unit, 'action': 'start', 'rc': 0}
    # audit written with peer + unit + action + rc
    assert audit_log.exists()
    rec = _json.loads(audit_log.read_text().strip().splitlines()[-1])
    assert rec['peer'] == '127.0.0.1'
    assert rec['unit'] == unit
    assert rec['action'] == 'start'
    assert rec['rc'] == 0
    assert rec['event'] == 'RUN'


def test_post_service_audits_blocked_unit(monkeypatch, tmp_path):
    """A refused (non-allowlisted) unit is audited as BLOCKED with peer IP."""
    audit_log = tmp_path / 'arsenal_audit.log'
    monkeypatch.setattr(h.subprocess, 'run', MagicMock())
    handler = _service_handler(monkeypatch, tmp_path, b'{"action":"stop"}',
                               audit=audit_log)
    handler._post_service('drifter-canbridge')
    assert audit_log.exists()
    rec = _json.loads(audit_log.read_text().strip().splitlines()[-1])
    assert rec['event'] == 'BLOCKED'
    assert rec['unit'] == 'drifter-canbridge'
    assert rec['peer'] == '127.0.0.1'


def test_service_allowlist_excludes_all_drive_only_units():
    """Structural invariant — no DRIVE_ONLY unit can ever be controllable."""
    from config import DRIVE_ONLY_SERVICES
    assert not (h._SERVICE_UNITS & set(DRIVE_ONLY_SERVICES))


# ─── Marauder command relay ────────────────────────────────────────────

def test_post_marauder_command_allowlist_relays(monkeypatch):
    """A LOW-tier op publishes to drifter/marauder/cmd as `command`."""
    state.mqtt_client = MagicMock()
    handler = _build_post_handler(b'{"op":"scan_ap"}')
    handler._post_marauder_command()
    state.mqtt_client.publish.assert_called_once()
    topic, payload = state.mqtt_client.publish.call_args[0]
    assert topic == 'drifter/marauder/cmd'
    assert _json.loads(payload)['command'] == 'scan_ap'


def test_post_marauder_command_high_risk_relays_with_confirm_token(monkeypatch):
    """HIGH-risk op is in the allowlist and relays a confirm_token verbatim —
    the bridge owns the confirm gate; the handler never strips it."""
    state.mqtt_client = MagicMock()
    body = b'{"op":"deauth_attack","confirm_token":"tok-123","args":{"bssid":"AA"}}'
    handler = _build_post_handler(body)
    handler._post_marauder_command()
    _topic, payload = state.mqtt_client.publish.call_args[0]
    parsed = _json.loads(payload)
    assert parsed['command'] == 'deauth_attack'
    assert parsed['confirm_token'] == 'tok-123'
    assert parsed['args'] == {'bssid': 'AA'}


def test_post_marauder_command_rejects_unknown_op(monkeypatch):
    state.mqtt_client = MagicMock()
    handler = _build_post_handler(b'{"op":"rm_rf_slash"}')
    handler._post_marauder_command()
    handler.send_error.assert_called_once()
    assert handler.send_error.call_args[0][0] == 400
    state.mqtt_client.publish.assert_not_called()


def test_post_marauder_command_rejects_remote_peer(monkeypatch):
    state.mqtt_client = MagicMock()
    handler = _build_post_handler(b'{"op":"scan_ap"}', peer='8.8.8.8')
    handler._post_marauder_command()
    handler.send_error.assert_called_once()
    assert handler.send_error.call_args[0][0] == 403
    state.mqtt_client.publish.assert_not_called()


def test_post_marauder_command_503_without_mqtt(monkeypatch):
    state.mqtt_client = None
    handler = _build_post_handler(b'{"op":"scan_ap"}')
    handler._post_marauder_command()
    handler.send_error.assert_called_once()
    assert handler.send_error.call_args[0][0] == 503


# ─── Sentry command relay ──────────────────────────────────────────────

def test_post_sentry_command_arm_relays(monkeypatch):
    state.mqtt_client = MagicMock()
    handler = _build_post_handler(b'{"action":"arm"}')
    handler._post_sentry_command()
    state.mqtt_client.publish.assert_called_once()
    topic, payload = state.mqtt_client.publish.call_args[0]
    assert topic == 'drifter/sentry/event'
    assert _json.loads(payload)['action'] == 'arm'


def test_post_sentry_command_rejects_unknown_action(monkeypatch):
    state.mqtt_client = MagicMock()
    handler = _build_post_handler(b'{"action":"detonate"}')
    handler._post_sentry_command()
    handler.send_error.assert_called_once()
    assert handler.send_error.call_args[0][0] == 400
    state.mqtt_client.publish.assert_not_called()


def test_post_sentry_command_rejects_remote_peer(monkeypatch):
    state.mqtt_client = MagicMock()
    handler = _build_post_handler(b'{"action":"disarm"}', peer='172.16.0.1')
    handler._post_sentry_command()
    handler.send_error.assert_called_once()
    assert handler.send_error.call_args[0][0] == 403
    state.mqtt_client.publish.assert_not_called()


def test_post_sentry_command_503_without_mqtt(monkeypatch):
    state.mqtt_client = None
    handler = _build_post_handler(b'{"action":"arm"}')
    handler._post_sentry_command()
    handler.send_error.assert_called_once()
    assert handler.send_error.call_args[0][0] == 503


# ═══════════════════════════════════════════════════════════════════
#  Rubber Ducky / BadUSB HID API (BE-1)
# ═══════════════════════════════════════════════════════════════════

def _build_get_handler(peer: str = '127.0.0.1'):
    """A DashboardHandler wired enough to call a GET/DELETE route method."""
    handler = h.DashboardHandler.__new__(h.DashboardHandler)
    handler.wfile = io.BytesIO()
    handler.client_address = (peer, 0)
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()
    handler.send_error = MagicMock()
    handler._serve_json = MagicMock()
    return handler


def test_hid_routes_registered():
    routes = h.DashboardHandler._EXACT_GET_ROUTES
    assert routes['/api/hid/status'] is h.DashboardHandler._get_hid_status
    assert routes['/api/hid/payloads'] is h.DashboardHandler._get_hid_payloads


def test_hid_command_allowlist_accepts_three_commands():
    state.mqtt_client = MagicMock()
    for body in (
        b'{"command":"hid_arm","payload_id":"ducky-1","backend":"flipper"}',
        b'{"command":"hid_confirm","id":"arm-1"}',
        b'{"command":"hid_cancel","id":"arm-1"}',
    ):
        handler = _build_post_handler(body)
        handler._post_hid_command()
        handler.send_error.assert_not_called()
    # All three relay to drifter/hid/command.
    topics = {c[0][0] for c in state.mqtt_client.publish.call_args_list}
    assert topics == {'drifter/hid/command'}


def test_hid_command_arm_stamps_peer_ip():
    state.mqtt_client = MagicMock()
    handler = _build_post_handler(
        b'{"command":"hid_arm","payload_id":"ducky-1","backend":"flipper"}',
        peer='10.42.0.9')
    handler._post_hid_command()
    _, payload = state.mqtt_client.publish.call_args[0]
    assert _json.loads(payload)['peer'] == '10.42.0.9'


def test_hid_command_rejects_unknown_command():
    state.mqtt_client = MagicMock()
    handler = _build_post_handler(b'{"command":"hid_nuke"}')
    handler._post_hid_command()
    handler.send_error.assert_called_once()
    assert handler.send_error.call_args[0][0] == 400
    state.mqtt_client.publish.assert_not_called()


def test_hid_command_arm_rejects_bad_backend():
    state.mqtt_client = MagicMock()
    handler = _build_post_handler(
        b'{"command":"hid_arm","payload_id":"ducky-1","backend":"usb"}')
    handler._post_hid_command()
    handler.send_error.assert_called_once()
    assert handler.send_error.call_args[0][0] == 400
    state.mqtt_client.publish.assert_not_called()


def test_hid_command_arm_rejects_empty_payload_id():
    state.mqtt_client = MagicMock()
    handler = _build_post_handler(
        b'{"command":"hid_arm","payload_id":"","backend":"flipper"}')
    handler._post_hid_command()
    handler.send_error.assert_called_once()
    assert handler.send_error.call_args[0][0] == 400


def test_hid_command_confirm_rejects_empty_id():
    state.mqtt_client = MagicMock()
    handler = _build_post_handler(b'{"command":"hid_confirm","id":""}')
    handler._post_hid_command()
    handler.send_error.assert_called_once()
    assert handler.send_error.call_args[0][0] == 400


def test_hid_command_rejects_remote_peer():
    state.mqtt_client = MagicMock()
    handler = _build_post_handler(
        b'{"command":"hid_cancel","id":"arm-1"}', peer='192.168.1.50')
    handler._post_hid_command()
    handler.send_error.assert_called_once()
    assert handler.send_error.call_args[0][0] == 403
    state.mqtt_client.publish.assert_not_called()


def test_hid_command_503_when_no_mqtt():
    state.mqtt_client = None
    handler = _build_post_handler(
        b'{"command":"hid_arm","payload_id":"ducky-1","backend":"flipper"}')
    handler._post_hid_command()
    handler.send_error.assert_called_once()
    assert handler.send_error.call_args[0][0] == 503


def test_hid_status_native_not_configured_on_this_host(monkeypatch):
    state.latest_state.clear()
    handler = _build_get_handler()
    handler._get_hid_status(None)
    payload = handler._serve_json.call_args[0][0]
    assert payload['native']['ready'] is False
    assert payload['native']['dr_mode'] != 'peripheral'
    # Flipper honestly not connected when nothing published.
    assert payload['flipper']['connected'] is False


def test_hid_status_rejects_remote_peer():
    handler = _build_get_handler(peer='8.8.8.8')
    handler._get_hid_status(None)
    handler.send_error.assert_called_once()
    assert handler.send_error.call_args[0][0] == 403


def test_hid_payload_upload_compiles_and_persists(monkeypatch, tmp_path):
    monkeypatch.setattr(h, '_HID_PAYLOAD_DIR', tmp_path)
    monkeypatch.setattr(h, '_HID_AUDIT_LOG', tmp_path / 'audit.log')
    state.mqtt_client = MagicMock()
    handler = _build_post_handler(
        b'{"name":"recon","script":"STRING whoami\\nENTER"}')
    handler._post_hid_payload()
    handler.send_error.assert_not_called()
    # The real _serve_json wrote {ok:true,...} to wfile; the payload landed.
    body = _json.loads(handler.wfile.getvalue())
    assert body['ok'] is True
    txts = list(tmp_path.glob('ducky-*.txt'))
    metas = list(tmp_path.glob('ducky-*.meta.json'))
    assert len(txts) == 1 and len(metas) == 1


def test_hid_payload_upload_parse_error_returns_400_with_line(monkeypatch, tmp_path):
    monkeypatch.setattr(h, '_HID_PAYLOAD_DIR', tmp_path)
    monkeypatch.setattr(h, '_HID_AUDIT_LOG', tmp_path / 'audit.log')
    state.mqtt_client = MagicMock()
    handler = _build_post_handler(
        b'{"name":"bad","script":"STRING ok\\nFOOBAR x"}')
    handler._post_hid_payload()
    # Body is written via wfile with a 400 status; nothing persisted.
    handler.send_response.assert_called_with(400)
    body = _json.loads(handler.wfile.getvalue())
    assert body['ok'] is False
    assert body['line'] == 2
    assert list(tmp_path.glob('*.txt')) == []


def test_hid_payload_upload_rejects_remote_peer(monkeypatch, tmp_path):
    monkeypatch.setattr(h, '_HID_PAYLOAD_DIR', tmp_path)
    state.mqtt_client = MagicMock()
    handler = _build_post_handler(
        b'{"name":"x","script":"STRING a"}', peer='1.2.3.4')
    handler._post_hid_payload()
    handler.send_error.assert_called_once()
    assert handler.send_error.call_args[0][0] == 403


def test_hid_payload_delete_removes_files_and_audits(monkeypatch, tmp_path):
    monkeypatch.setattr(h, '_HID_PAYLOAD_DIR', tmp_path)
    monkeypatch.setattr(h, '_HID_AUDIT_LOG', tmp_path / 'audit.log')
    state.mqtt_client = MagicMock()
    (tmp_path / 'ducky-9.txt').write_text('STRING a')
    (tmp_path / 'ducky-9.meta.json').write_text('{"id":"ducky-9"}')
    handler = _build_get_handler()
    handler._delete_hid_payload('ducky-9')
    assert not (tmp_path / 'ducky-9.txt').exists()
    assert not (tmp_path / 'ducky-9.meta.json').exists()
    events = [_json.loads(l)['event']
              for l in (tmp_path / 'audit.log').read_text().splitlines()]
    assert 'DELETE' in events


def test_hid_payload_delete_404_when_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(h, '_HID_PAYLOAD_DIR', tmp_path)
    state.mqtt_client = MagicMock()
    handler = _build_get_handler()
    handler._delete_hid_payload('ducky-absent')
    handler.send_error.assert_called_once()
    assert handler.send_error.call_args[0][0] == 404


def test_hid_payload_delete_path_traversal_400(monkeypatch, tmp_path):
    monkeypatch.setattr(h, '_HID_PAYLOAD_DIR', tmp_path)
    handler = _build_get_handler()
    handler._delete_hid_payload('../config')
    handler.send_error.assert_called_once()
    assert handler.send_error.call_args[0][0] == 400


def test_hid_get_single_payload(monkeypatch, tmp_path):
    monkeypatch.setattr(h, '_HID_PAYLOAD_DIR', tmp_path)
    monkeypatch.setattr(h.hid_inject, 'HID_PAYLOAD_DIR', tmp_path)
    (tmp_path / 'ducky-3.txt').write_text('STRING hi')
    (tmp_path / 'ducky-3.meta.json').write_text('{"id":"ducky-3"}')
    handler = _build_get_handler()
    handler._serve_hid_payload('ducky-3')
    payload = handler._serve_json.call_args[0][0]
    assert payload['script'] == 'STRING hi'
    assert payload['meta']['id'] == 'ducky-3'


# ── Brand / PWA assets ────────────────────────────────────────────────

def test_pwa_static_routes_registered():
    """Favicon, apple-touch-icon and the web manifest are served so the
    phone-tethered cockpit installs to the home screen branded."""
    for route in ('/favicon.svg', '/favicon.ico',
                  '/apple-touch-icon.png', '/manifest.webmanifest'):
        assert route in h._STATIC_FILES, f'{route} not served'


def test_pwa_manifest_is_valid_and_on_brand():
    import json
    from pathlib import Path
    mf = Path(__file__).resolve().parent.parent / 'static' / 'icons' / 'manifest.webmanifest'
    data = json.loads(mf.read_text())
    assert data['name'] == 'MZ1312 DRIFTER'
    assert data['display'] == 'standalone'
    assert data['background_color'] == '#07090d'   # canonical near-black
    assert data['icons'] and any(i['type'] == 'image/svg+xml' for i in data['icons'])

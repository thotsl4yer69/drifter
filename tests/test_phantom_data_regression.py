# tests/test_phantom_data_regression.py
"""Regression suite for the phantom-data incident chain.

Every test here pins a specific failure that historically let the system
present fabricated, stale, or reference data as if it were live truth:

  1. _post_gps_manual once accepted a 25 km IP-geolocation fix as
     authoritative and poisoned the entire feeds pipeline. Tests the
     100m accuracy gate.
  2. feeds.origin() must time out stale fixes after 120s so map markers
     and aircraft snapshots don't outlive their GPS source.
  3. /api/aircraft/recent serves the latest snapshot from MQTT state,
     but the cockpit must reject snapshots older than its freshness
     window. Verifies the snapshot is correctly cached AND that age
     can be derived for the gate.
  4. Vivi's LLM-offline fallback must never quote spec ranges as if
     they were sensor readings, for the high-traffic sensor questions.

These tests must stay green to prove the real-data-only contract holds.
"""
import io
import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, 'src')

import web_dashboard_handlers as h
import web_dashboard_state as state


def _post_handler(body: bytes, peer: str = '127.0.0.1'):
    handler = h.DashboardHandler.__new__(h.DashboardHandler)
    handler.rfile = io.BytesIO(body)
    handler.wfile = io.BytesIO()
    handler.headers = {'Content-Length': str(len(body))}
    handler.client_address = (peer, 0)
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()
    handler.send_error = MagicMock()
    handler._serve_json = MagicMock()
    return handler


# ── (a) Accuracy gate on POST /api/gps/manual ────────────────────────

def test_gps_manual_rejects_5000m_accuracy(monkeypatch, tmp_path):
    """5000m is IP-geolocation territory. Must 400."""
    monkeypatch.setattr(h, '_GPS_STATE_PATH', tmp_path / 'gps.json')
    state.mqtt_client = MagicMock()
    payload = json.dumps({'lat': -37.85, 'lng': 145.12, 'accuracy_m': 5000})
    handler = _post_handler(payload.encode())
    handler._post_gps_manual()
    handler.send_error.assert_called_once()
    assert handler.send_error.call_args[0][0] == 400


# ── (b) feeds.origin() expires after 120s ────────────────────────────

def test_feeds_origin_expires_stale_fix(monkeypatch, tmp_path):
    """A fix 130s old must read as awaiting — even if its 'fix' flag is True."""
    gps_path = tmp_path / 'gps.json'
    # Write a fix dated 130s in the past
    gps_path.write_text(json.dumps({
        'lat': -37.85, 'lng': 145.12, 'lon': 145.12,
        'fix': True, 'mode': 2, 'ts': time.time() - 130,
        'source': 'browser', 'accuracy_m': 30,
    }))
    sys.path.insert(0, 'src')
    import feeds
    monkeypatch.setattr(feeds, 'GPS_PATH', gps_path)
    o = feeds.origin()
    assert o == {'lat': None, 'lon': None, 'source': 'awaiting'}


def test_feeds_origin_accepts_fresh_fix(monkeypatch, tmp_path):
    """Sanity opposite — a fix 10s old reads as gps."""
    gps_path = tmp_path / 'gps.json'
    gps_path.write_text(json.dumps({
        'lat': -37.85, 'lng': 145.12, 'lon': 145.12,
        'fix': True, 'mode': 2, 'ts': time.time() - 10,
        'source': 'browser', 'accuracy_m': 30,
    }))
    import feeds
    monkeypatch.setattr(feeds, 'GPS_PATH', gps_path)
    o = feeds.origin()
    assert o['source'] == 'gps'
    assert o['lat'] == -37.85


# ── (c) Aircraft snapshot freshness ──────────────────────────────────

def test_recent_aircraft_serves_snapshot_with_age_derivable():
    """The endpoint returns whatever's in latest_state. The cockpit's
    AIRCRAFT_FRESH_WINDOW_SEC gate (60s) uses payload.ts to reject
    stale snapshots. This test verifies the ts is preserved verbatim
    so the gate has an honest age to work with."""
    state.latest_state.clear()
    stale_ts = time.time() - 600  # 10 min old
    state.latest_state['feeds_aircraft_snapshot'] = {
        'ts': stale_ts,
        'origin': {'lat': -37.85, 'lon': 145.12, 'source': 'gps'},
        'count': 1,
        'aircraft': [{'hex': 'abc', 'flight': 'STALE1', 'lat': 0, 'lon': 0}],
    }
    handler = _post_handler(b'')
    h.DashboardHandler._get_recent_aircraft(handler, None)
    handler._serve_json.assert_called_once()
    payload = handler._serve_json.call_args[0][0]
    # The endpoint returns the cached snapshot verbatim — the freshness
    # gate is enforced client-side in cockpit-preview.html (see
    # AIRCRAFT_FRESH_WINDOW_SEC). What we verify here is that the ts
    # round-trips so the gate has something to compare against.
    assert payload['ts'] == stale_ts
    assert payload['aircraft'][0]['flight'] == 'STALE1'
    # Sanity: the snapshot IS old.
    assert time.time() - payload['ts'] > 300


def test_cockpit_aircraft_freshness_window_constant():
    """The cockpit gate constant must exist and be a small number of
    seconds, not minutes. If somebody bumps it to "300s for resilience"
    the phantom-aircraft bug returns."""
    cockpit = Path('ui/cockpit-preview.html').read_text()
    assert 'AIRCRAFT_FRESH_WINDOW_SEC' in cockpit
    # Extract the value with a simple regex
    import re
    m = re.search(r'AIRCRAFT_FRESH_WINDOW_SEC\s*=\s*(\d+)', cockpit)
    assert m, 'constant not found in expected form'
    value = int(m.group(1))
    assert value <= 120, f'freshness window too generous: {value}s'


# ── (d) LLM-offline fallback rejects forbidden tokens ────────────────
# Repointed from the retired v1 vivi._rag_fallback to vivi_v2.ask's offline
# path: when every LLM backend is down, the shipped brain must give an
# honest "I'm offline" reply, never substitute spec/manual data that reads
# like a live sensor value.

@pytest.fixture(scope='module')
def vivi_v2_mod():
    import unittest.mock as m
    sys.modules.setdefault('paho', m.MagicMock())
    sys.modules.setdefault('paho.mqtt', m.MagicMock())
    sys.modules.setdefault('paho.mqtt.client', m.MagicMock())
    import vivi_v2
    return vivi_v2


_SENSOR_QUERIES = [
    'what is the coolant temperature',
    'what is the oil pressure',
    'what is the battery voltage',
    'what is the tire pressure',
    'what is the current rpm',
]

# Tokens that should NEVER appear in an LLM-offline response — these
# are the spec-leak shapes that historically read as live readings.
_FORBIDDEN_TOKENS = [
    'normal_range',
    'normal range',
    '°C',
    'psi',
    'PSI',
    'rpm:',
    'RPM:',
    'workshop note',
    'manual:',
    'from the manual',
    '85-100',
    '13.2',
    '14.4',
]


def _offline_reply(vivi_v2_mod, query):
    """Drive vivi_v2.ask with every backend down + collaborators stubbed,
    returning the spoken/published response text."""
    import unittest.mock as m
    patches = [
        m.patch.object(vivi_v2_mod, '_build_prompt', return_value=(query, {})),
        m.patch.object(vivi_v2_mod, '_system_prompt_for', return_value="system"),
        m.patch.object(vivi_v2_mod, '_publish_status'),
        m.patch.object(vivi_v2_mod, '_publish_response'),
        m.patch.object(vivi_v2_mod, 'speak'),
        m.patch.object(vivi_v2_mod.vivi_memory, 'append_turn'),
        m.patch.object(vivi_v2_mod.llm_client_v2, 'stream',
                       side_effect=RuntimeError("All LLM backends failed")),
        m.patch.object(vivi_v2_mod.llm_client_v2, 'query',
                       side_effect=RuntimeError("All LLM backends failed")),
    ]
    for p in patches:
        p.start()
    try:
        return vivi_v2_mod.ask(query)['response']
    finally:
        for p in patches:
            p.stop()


@pytest.mark.parametrize('query', _SENSOR_QUERIES)
def test_llm_offline_fallback_never_quotes_spec_data(vivi_v2_mod, query):
    reply = _offline_reply(vivi_v2_mod, query)
    assert 'offline' in reply.lower(), f'fallback missing identification for {query!r}'
    lowered = reply.lower()
    for tok in _FORBIDDEN_TOKENS:
        assert tok.lower() not in lowered, (
            f'fallback for {query!r} leaked forbidden token {tok!r}: {reply!r}'
        )


def test_llm_offline_fallback_is_deterministic(vivi_v2_mod):
    """Same input → same output. The fallback is not allowed to
    randomly include spec data on some calls and not others."""
    r1 = _offline_reply(vivi_v2_mod, 'coolant temperature')
    r2 = _offline_reply(vivi_v2_mod, 'coolant temperature')
    r3 = _offline_reply(vivi_v2_mod, 'coolant temperature')
    assert r1 == r2 == r3


# ── (e) Cockpit FE real-data guards (Phases A/B) ─────────────────────
# These grep/regex over ui/cockpit-preview.html and GATE the build: they
# pin the two FAKE-DATA violations the polish build fixed (ADS-B
# placeholder-origin canvas + frozen-live hero gauges) plus the hardcoded
# REG/OPERATOR literals and the rfaudio gain:40 default. If any of these
# regress, the cockpit is once again capable of presenting fabricated or
# stale data as live truth.

import re as _re


def _cockpit_text():
    return Path('ui/cockpit-preview.html').read_text()


def _render_adsb_radar_body(src):
    """Slice the renderAdsbRadar() function body so freshness assertions
    are scoped to the canvas renderer, not the whole file."""
    start = src.index('function renderAdsbRadar(')
    # Walk braces from the first '{' after the signature to its match.
    i = src.index('{', start)
    depth = 0
    for j in range(i, len(src)):
        c = src[j]
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
    raise AssertionError('renderAdsbRadar() body not balanced')


def test_cockpit_no_placeholder_origin_constant():
    """FE-A1 / R1: the ADSB_PLACEHOLDER_ORIGIN city-coordinate fallback
    must be gone entirely — blips plot only against a real GPS origin."""
    src = _cockpit_text()
    assert 'ADSB_PLACEHOLDER_ORIGIN' not in src, (
        'ADSB_PLACEHOLDER_ORIGIN reintroduced — phantom origin fallback is back'
    )


def test_cockpit_adsb_radar_gates_on_freshness():
    """FE-A1 / R2: renderAdsbRadar must gate on AIRCRAFT_FRESH_WINDOW_SEC
    and have an AWAITING GPS FIX empty-draw path so the canvas agrees with
    its text counterpart when the snapshot is stale or origin is missing."""
    body = _render_adsb_radar_body(_cockpit_text())
    assert 'AIRCRAFT_FRESH_WINDOW_SEC' in body, (
        'radar renderer no longer references the freshness window'
    )
    assert 'AWAITING GPS FIX' in body, (
        'radar renderer lost its AWAITING GPS FIX empty-draw path'
    )
    # The constant itself must be a small number of seconds, not minutes.
    m = _re.search(r'AIRCRAFT_FRESH_WINDOW_SEC\s*=\s*(\d+)', _cockpit_text())
    assert m and int(m.group(1)) <= 120


def test_cockpit_gauge_staleness_sweep_present():
    """FE-A2 / R3: GAUGE_STALE_SEC must exist and a sweep must remove the
    .live class so a frozen needle can't masquerade as live after a CAN
    dropout."""
    src = _cockpit_text()
    m = _re.search(r'GAUGE_STALE_SEC\s*=\s*(\d+)', src)
    assert m, 'GAUGE_STALE_SEC constant not found'
    assert int(m.group(1)) <= 30, 'gauge stale window too generous'
    assert 'sweepGaugeStaleness' in src, 'staleness sweep function missing'
    # The sweep must actually strip .live (dim != live).
    assert _re.search(r"classList\.remove\(\s*['\"]live['\"]\s*\)", src), (
        'no classList.remove("live") — sweep cannot demote a stale gauge'
    )


def test_cockpit_no_hardcoded_reg_or_operator():
    """FE-A3 / R4: the fake plate N7-DRFT-X25 and the OPERATOR · MAZ
    literal must be gone — empty node shows REG. — and OPERATOR only."""
    src = _cockpit_text()
    assert 'N7-DRFT-X25' not in src, 'hardcoded REG plate reintroduced'
    assert 'OPERATOR · MAZ' not in src, 'hardcoded OPERATOR · MAZ literal reintroduced'


def test_cockpit_rfaudio_gain_not_40():
    """FE-B2 / R5: no rfaudio start POST may default gain to 40 — auto
    gain is 0. A 40 dB literal historically blasted the front-end."""
    src = _cockpit_text()
    assert not _re.search(r'gain\s*:\s*40\b', src), (
        'an rfaudio POST still sends gain:40 — must be gain:0 (auto)'
    )

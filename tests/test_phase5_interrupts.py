# tests/test_phase5_interrupts.py
"""
MZ1312 DRIFTER — Phase 5 cockpit posture: interrupt routing + voice-
controlled map layer toggling.

UNCAGED TECHNOLOGY — EST 1991
"""
from __future__ import annotations

import json
import time
from unittest.mock import MagicMock

import pytest


# ── Vivi police-adjacent interrupt path ────────────────────────────

@pytest.fixture
def vivi_module(monkeypatch):
    """Import vivi and zero out the unprompted-comment cooldown so
    tests don't have to wait 5 minutes between calls. Also stub the
    actual TTS dispatch so the test never tries to render audio."""
    import vivi
    monkeypatch.setattr(vivi, '_last_unprompted_ts', 0.0)
    monkeypatch.setattr(vivi, '_unprompted_count', 0)
    # Capture every comment the interrupt path emits.
    calls = []
    real_maybe = vivi._maybe_unprompted_comment

    def capture(level, message):
        calls.append((level, message))
        # don't actually run the TTS path — just record.
    monkeypatch.setattr(vivi, '_maybe_unprompted_comment', capture)
    vivi._captured_calls = calls
    return vivi


def _ble_msg(target='axon-class', rssi=-65, vivi_alert=False, is_alert=False):
    """Build an MQTT message object that mimics paho's signature for
    the ble_detection topic."""
    payload = {
        'target': target,
        'target_label': target,
        'rssi': rssi,
        'mac': '00:25:DF:11:22:33',
        'ts': time.time(),
        'is_alert': is_alert,
        'vivi_alert': vivi_alert,
    }
    msg = MagicMock()
    msg.topic = 'drifter/ble/detection'
    msg.payload = json.dumps(payload).encode('utf-8')
    return msg


def test_axon_close_range_triggers_police_interrupt(vivi_module):
    """Axon-class device at rssi >= -70 → casual 'Cop nearby.' line.
    Phase 5 contract — this is the sole police-adjacent interrupt
    today, and the phrasing is deliberate (operator asks for details
    if they want them)."""
    vivi_module.on_message(None, None, _ble_msg(target='axon-class', rssi=-65))
    assert vivi_module._captured_calls
    levels = [c[0] for c in vivi_module._captured_calls]
    msgs = [c[1] for c in vivi_module._captured_calls]
    assert msgs[0] == 'Cop nearby.'
    assert levels[0] == 3


def test_axon_alias_target_name_also_triggers(vivi_module):
    """Phase 4.5 ships the target as 'axon'; Phase 5 added the
    'axon-class' alias. Both should hit the police-interrupt branch."""
    vivi_module.on_message(None, None, _ble_msg(target='axon', rssi=-60))
    assert any(m == 'Cop nearby.' for _, m in vivi_module._captured_calls)


def test_axon_far_range_does_not_interrupt(vivi_module):
    """rssi below -70 → no interrupt (device is logged + tracked, but
    isn't close enough to surface). vivi_alert is also False here so
    the longer fallback line shouldn't fire either."""
    vivi_module.on_message(None, None, _ble_msg(target='axon-class', rssi=-90))
    assert vivi_module._captured_calls == []


def test_other_target_does_not_trigger_police_line(vivi_module):
    """Tile or AirTag at close range gets a different code path —
    explicitly NOT the police line. Without vivi_alert+is_alert set,
    the fallback line is also gated off."""
    vivi_module.on_message(None, None, _ble_msg(target='tile', rssi=-55))
    msgs = [m for _, m in vivi_module._captured_calls]
    assert 'Cop nearby.' not in msgs


def test_adsb_police_topic_triggers(vivi_module):
    """drifter/adsb/police is fed by the helicopter watcher (when
    wired). Its presence is enough — payload is informational."""
    msg = MagicMock()
    msg.topic = 'drifter/adsb/police'
    msg.payload = json.dumps({'callsign': 'POL01', 'alt_ft': 800}).encode()
    vivi_module.on_message(None, None, msg)
    assert any(m == 'Helicopter overhead.' for _, m in vivi_module._captured_calls)


def test_drone_topic_triggers_when_published(vivi_module):
    """drifter/drone/detection from the (future) Coral RF pipeline."""
    msg = MagicMock()
    msg.topic = 'drifter/drone/detection'
    msg.payload = json.dumps({'band': '5.8GHz', 'rssi': -60}).encode()
    vivi_module.on_message(None, None, msg)
    assert any(m == 'Drone signal detected.' for _, m in vivi_module._captured_calls)


def test_low_altitude_aircraft_triggers_heads_up(vivi_module):
    """Phase 5.1 — without a police-callsign DB, any aircraft seen
    below 1500ft surfaces as a casual heads-up."""
    msg = MagicMock()
    msg.topic = 'drifter/rf/adsb'
    msg.payload = json.dumps({
        'ts': time.time(),
        'aircraft': [
            {'flight': 'POL01', 'altitude': 800, 'speed': 90},
            {'flight': 'CXA9012', 'altitude': 38000, 'speed': 460},
        ],
    }).encode()
    vivi_module.on_message(None, None, msg)
    assert any(m == 'Low aircraft overhead.' for _, m in vivi_module._captured_calls)


def test_high_altitude_aircraft_does_not_trigger(vivi_module):
    """Cruising commercial traffic is irrelevant — only sub-1500ft
    contacts surface."""
    msg = MagicMock()
    msg.topic = 'drifter/rf/adsb'
    msg.payload = json.dumps({
        'ts': time.time(),
        'aircraft': [{'flight': 'CXA9012', 'altitude': 38000, 'speed': 460}],
    }).encode()
    vivi_module.on_message(None, None, msg)
    msgs = [m for _, m in vivi_module._captured_calls]
    assert 'Low aircraft overhead.' not in msgs


def test_stale_retained_adsb_payload_does_not_trigger(vivi_module):
    """rf_adsb is published RETAINED so vivi sees the latest value on
    every connect — but a low-aircraft heads-up that fired 10 minutes
    ago shouldn't re-fire on reconnect. Skip stale payloads (>120s)."""
    msg = MagicMock()
    msg.topic = 'drifter/rf/adsb'
    msg.payload = json.dumps({
        'ts': time.time() - 600,  # 10 minutes old
        'aircraft': [{'flight': 'POL01', 'altitude': 800}],
    }).encode()
    vivi_module.on_message(None, None, msg)
    assert vivi_module._captured_calls == []


# ── Cooldown / per-drive cap (exercised against the real
# _maybe_unprompted_comment, not the captured stub) ────────────────

def test_interrupt_respects_global_cooldown(monkeypatch):
    """Two close-range axon hits inside the 5-min window should result
    in exactly ONE response from the cooldown gate. We don't stub
    _maybe_unprompted_comment here — we want the real cooldown logic."""
    import vivi
    monkeypatch.setattr(vivi, '_last_unprompted_ts', 0.0)
    monkeypatch.setattr(vivi, '_unprompted_count', 0)
    # Stub the downstream worker so it doesn't try to reach Ollama /
    # piper / MQTT during the test. _maybe_unprompted_comment spawns a
    # daemon thread; without these stubs, the thread blocks on ask_vivi
    # for the full session duration.
    monkeypatch.setattr(vivi, 'ask_vivi', lambda *a, **k: 'stub')
    monkeypatch.setattr(vivi, '_publish_response', lambda *a, **k: None)
    monkeypatch.setattr(vivi, '_publish_status',  lambda *a, **k: None)
    monkeypatch.setattr(vivi, 'speak',            lambda *a, **k: None)

    vivi.on_message(None, None, _ble_msg(target='axon-class', rssi=-65))
    first_ts = vivi._last_unprompted_ts
    vivi.on_message(None, None, _ble_msg(target='axon-class', rssi=-65))
    second_ts = vivi._last_unprompted_ts
    # Only the first call advances the cooldown timestamp; the second
    # is gated out before it changes state.
    assert first_ts > 0
    assert first_ts == second_ts


def test_interrupt_respects_per_drive_cap(monkeypatch):
    """UNPROMPTED_MAX_PER_SESSION caps the count even if the cooldown
    happens to expire (e.g. very long drive). Force the count to the
    cap and verify the next attempt is dropped."""
    import vivi
    monkeypatch.setattr(vivi, '_last_unprompted_ts', 0.0)
    monkeypatch.setattr(vivi, '_unprompted_count', vivi.UNPROMPTED_MAX_PER_SESSION)
    monkeypatch.setattr(vivi, 'ask_vivi', lambda *a, **k: 'stub')
    monkeypatch.setattr(vivi, '_publish_response', lambda *a, **k: None)
    monkeypatch.setattr(vivi, '_publish_status',  lambda *a, **k: None)
    monkeypatch.setattr(vivi, 'speak',            lambda *a, **k: None)

    vivi.on_message(None, None, _ble_msg(target='axon-class', rssi=-65))
    # Count stays at the cap (no further increments allowed).
    assert vivi._unprompted_count == vivi.UNPROMPTED_MAX_PER_SESSION


# ── Voice routing of map-layer commands ────────────────────────────

def test_show_drones_publishes_layer_command(monkeypatch):
    """`show me drones` → drifter/hud/map/layer {layer:drone, action:show}.
    Beats the page-nav classifier (which would have caught 'show me')."""
    import voice_input
    captured = []

    class FakeMQTT:
        def publish(self, topic, payload):
            captured.append((topic, json.loads(payload)))
    monkeypatch.setattr(voice_input, 'mqtt_client', FakeMQTT())
    monkeypatch.setattr(voice_input, '_pub_voice_status', lambda *a, **k: None)

    voice_input.route_transcript('show me drones')
    topics = [c[0] for c in captured]
    assert 'drifter/hud/map/layer' in topics
    payload = next(p for t, p in captured if t == 'drifter/hud/map/layer')
    assert payload['layer'] == 'drone'
    assert payload['action'] == 'show'


def test_hide_police_publishes_layer_command(monkeypatch):
    import voice_input
    captured = []
    monkeypatch.setattr(voice_input, 'mqtt_client',
                         type('M', (), {'publish': lambda self, t, p:
                                         captured.append((t, json.loads(p)))})())
    monkeypatch.setattr(voice_input, '_pub_voice_status', lambda *a, **k: None)
    voice_input.route_transcript('hide police')
    payload = next(p for t, p in captured if t == 'drifter/hud/map/layer')
    assert payload == {'layer': 'police', 'action': 'hide', **{k: payload[k] for k in payload if k == 'ts'}}


def test_show_all_layer_command_works(monkeypatch):
    import voice_input
    captured = []
    monkeypatch.setattr(voice_input, 'mqtt_client',
                         type('M', (), {'publish': lambda self, t, p:
                                         captured.append((t, json.loads(p)))})())
    monkeypatch.setattr(voice_input, '_pub_voice_status', lambda *a, **k: None)
    voice_input.route_transcript('show all')
    p = next(p for t, p in captured if t == 'drifter/hud/map/layer')
    assert p['layer'] == 'all' and p['action'] == 'show'


def test_unrecognised_show_command_falls_through_to_vivi_query(monkeypatch):
    """`show me my fuel` — 'fuel' isn't a map layer noun. The transcript
    must NOT publish to drifter/hud/map/layer; it should fall through
    to the existing classifier (which will route it to vivi as a
    query, since nav requires a strict verb prefix + page noun)."""
    import voice_input
    captured = []
    monkeypatch.setattr(voice_input, 'mqtt_client',
                         type('M', (), {'publish': lambda self, t, p:
                                         captured.append((t, json.loads(p)))})())
    monkeypatch.setattr(voice_input, '_pub_voice_status', lambda *a, **k: None)
    voice_input.route_transcript('show me my fuel')
    topics = [t for t, _ in captured]
    assert 'drifter/hud/map/layer' not in topics


# ── Pure helper ────────────────────────────────────────────────────

def test_classify_map_layer_synonyms():
    """Synonym table maps several nouns to canonical layer names."""
    import voice_input as vi
    cases = [
        ('show drones',     ('drone',  'show')),
        ('hide drones',     ('drone',  'hide')),
        ('show police',     ('police', 'show')),
        ('hide cops',       ('police', 'hide')),
        ('show ble',        ('ble',    'show')),
        ('show bluetooth',  ('ble',    'show')),
        ('show planes',     ('adsb',   'show')),
        ('show aircraft',   ('adsb',   'show')),
        ('show wifi',       ('ap',     'show')),
        ('show all',        ('all',    'show')),
        ('hide everything', ('all',    'hide')),
    ]
    for text, expected in cases:
        got = vi._classify_map_layer(text)
        assert got == expected, f"{text!r}: got {got}, want {expected}"


def test_classify_map_layer_returns_none_for_non_match():
    import voice_input as vi
    assert vi._classify_map_layer('what is engine status') is None
    assert vi._classify_map_layer('show me my fuel') is None
    assert vi._classify_map_layer('') is None

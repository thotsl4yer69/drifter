# tests/test_phase5_interrupts.py
"""
MZ1312 DRIFTER — Phase 5 cockpit posture: voice-controlled map layer
toggling.

NOTE: the Vivi police-adjacent interrupt path (axon/helicopter/drone/low-
aircraft heads-up + per-drive cooldown cap) lived in the retired v1 vivi
module and was NOT carried into vivi_v2 — the shipped brain doesn't
subscribe to those topics. Those tests were removed with the module. The
voice-routing of map-layer commands below is independent (voice_input) and
remains live.

UNCAGED TECHNOLOGY — EST 1991
"""
from __future__ import annotations

import json

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

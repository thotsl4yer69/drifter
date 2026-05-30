# tests/test_gps_publisher.py
"""
MZ1312 DRIFTER — gpsd → MQTT publisher tests
UNCAGED TECHNOLOGY — EST 1991
"""
from __future__ import annotations

import json

import pytest

import gps_publisher as gp


def test_parses_valid_2d_fix():
    line = json.dumps({
        'class': 'TPV', 'mode': 2,
        'lat': -37.8136, 'lon': 144.9631,
        'time': '2026-05-08T13:00:00.000Z',
    })
    fix = gp.parse_tpv(line)
    assert fix is not None
    assert fix['lat'] == pytest.approx(-37.8136)
    assert fix['lng'] == pytest.approx(144.9631)
    assert fix['mode'] == 2
    assert 'ts' in fix


def test_parses_3d_fix_with_alt_speed_track():
    line = json.dumps({
        'class': 'TPV', 'mode': 3,
        'lat': -37.8136, 'lon': 144.9631,
        'alt': 35.4, 'speed': 23.6, 'track': 178.0,
    })
    fix = gp.parse_tpv(line)
    assert fix['alt_m'] == pytest.approx(35.4)
    assert fix['speed_mps'] == pytest.approx(23.6)
    assert fix['track_deg'] == pytest.approx(178.0)


def test_drops_no_fix_messages():
    """mode 0 (no mode info) and mode 1 (no fix) → not published."""
    for mode in (0, 1):
        line = json.dumps({'class': 'TPV', 'mode': mode,
                            'lat': -37.8, 'lon': 144.9})
        assert gp.parse_tpv(line) is None


def test_drops_non_tpv_classes():
    """gpsd emits SKY (satellite info), DEVICE, VERSION, WATCH —
    only TPV carries position."""
    for cls in ('SKY', 'DEVICE', 'VERSION', 'WATCH', 'PPS'):
        line = json.dumps({'class': cls, 'lat': -37.8, 'lon': 144.9})
        assert gp.parse_tpv(line) is None


def test_drops_tpv_without_coordinates():
    """Some TPV reports arrive with mode>=2 but lat/lon still null
    while the receiver is acquiring satellites."""
    line = json.dumps({'class': 'TPV', 'mode': 2})
    assert gp.parse_tpv(line) is None


def test_handles_malformed_json_gracefully():
    """gpsd should never emit malformed JSON, but the parser must
    not crash if it does."""
    assert gp.parse_tpv('{not-json') is None
    assert gp.parse_tpv('') is None


def test_publish_topic_matches_config():
    """The published topic must be the canonical drifter/gps/fix —
    that's what the cockpit subscribes to."""
    assert gp.PUBLISH_TOPIC == 'drifter/gps/fix'


def test_optional_fields_default_to_none():
    """A 2D fix without alt/speed/track must still parse — gpsd
    omits those when not yet known."""
    line = json.dumps({'class': 'TPV', 'mode': 2,
                        'lat': 0.0, 'lon': 0.0})
    fix = gp.parse_tpv(line)
    assert fix['alt_m'] is None
    assert fix['speed_mps'] is None
    assert fix['track_deg'] is None

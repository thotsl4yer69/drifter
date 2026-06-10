"""Proactive counter-surveillance sentinel — restored from retired v1.

Pins the detection→heads-up contract: axon BLE close range, police heli,
drone, low-altitude aircraft (with stale-payload skip), and the negatives.
"""
from __future__ import annotations

import sys
import time

sys.path.insert(0, 'src')

from vivi_sentinel import classify_detection


def test_axon_close_range_is_cop_nearby():
    hit = classify_detection('ble_detection', {'target': 'axon-class', 'rssi': -65})
    assert hit == ('police_ble', 'Cop nearby.')


def test_axon_alias_also_triggers():
    hit = classify_detection('ble_detection', {'target': 'axon', 'rssi': -60})
    assert hit == ('police_ble', 'Cop nearby.')


def test_axon_far_range_does_not_trigger():
    assert classify_detection('ble_detection', {'target': 'axon-class', 'rssi': -90}) is None


def test_other_target_is_not_police_line():
    assert classify_detection('ble_detection', {'target': 'tile', 'rssi': -55}) is None


def test_flagged_ble_alert_gets_longer_line():
    hit = classify_detection('ble_detection', {
        'target': 'tile', 'target_label': 'Tile', 'rssi': -50,
        'vivi_alert': True, 'is_alert': True,
    })
    assert hit and hit[0] == 'ble_alert' and 'Tile nearby' in hit[1]


def test_adsb_police_topic_is_helicopter():
    assert classify_detection('adsb_police', {'callsign': 'POL01'}) == ('police_heli', 'Helicopter overhead.')


def test_drone_topic():
    assert classify_detection('drone_detection', {'band': '5.8GHz'}) == ('drone', 'Drone signal detected.')


def test_low_aircraft_triggers():
    hit = classify_detection('rf_adsb', {
        'ts': time.time(),
        'aircraft': [{'flight': 'POL01', 'altitude': 800},
                     {'flight': 'CXA9012', 'altitude': 38000}],
    })
    assert hit == ('low_aircraft', 'Low aircraft overhead.')


def test_high_aircraft_does_not_trigger():
    assert classify_detection('rf_adsb', {
        'ts': time.time(), 'aircraft': [{'flight': 'CXA9012', 'altitude': 38000}],
    }) is None


def test_stale_retained_adsb_skipped():
    assert classify_detection('rf_adsb', {
        'ts': time.time() - 600, 'aircraft': [{'flight': 'POL01', 'altitude': 800}],
    }) is None


def test_non_dict_payload_is_none():
    assert classify_detection('ble_detection', None) is None
    assert classify_detection('rf_adsb', 'nope') is None

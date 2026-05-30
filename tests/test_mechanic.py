# tests/test_mechanic.py
"""Tests for the data-driven mechanic.py.

The module used to hold ~1600 lines of inline Python constants; it now
loads those constants from src/data/mechanic/*.json. These tests lock in
the public API so any future data reshuffling still works.
"""
import sys
sys.path.insert(0, 'src')

import mechanic


def test_data_loaded_non_empty():
    assert isinstance(mechanic.VEHICLE_SPECS, dict) and mechanic.VEHICLE_SPECS
    assert isinstance(mechanic.COMMON_PROBLEMS, list) and mechanic.COMMON_PROBLEMS
    assert isinstance(mechanic.DTC_REFERENCE, dict) and mechanic.DTC_REFERENCE
    assert isinstance(mechanic.SERVICE_SCHEDULE, list) and mechanic.SERVICE_SCHEDULE
    assert isinstance(mechanic.EMERGENCY_PROCEDURES, list)
    assert isinstance(mechanic.TORQUE_SPECS, dict)
    assert isinstance(mechanic.FUSE_REFERENCE, dict)


def test_search_empty_query_returns_empty():
    assert mechanic.search('') == []
    assert mechanic.search('   ') == []


def test_search_returns_thermostat_hit():
    results = mechanic.search('thermostat')
    assert results, "thermostat is a well-known X-Type failure — should return results"
    assert any(r['type'] == 'problem' for r in results)


def test_search_score_descending():
    results = mechanic.search('coolant thermostat')
    scores = [r['score'] for r in results]
    assert scores == sorted(scores, reverse=True)


def test_search_result_shape():
    results = mechanic.search('rpm')
    for r in results:
        assert set(r.keys()) >= {'type', 'title', 'score', 'data'}


def test_get_dtc_info_known():
    info = mechanic.get_dtc_info('P0125')
    assert info is not None
    assert info['code'] == 'P0125'
    assert 'desc' in info


def test_get_dtc_info_normalises_case():
    assert mechanic.get_dtc_info('p0125') == mechanic.get_dtc_info('P0125')


def test_get_dtc_info_unknown_returns_none():
    assert mechanic.get_dtc_info('P9999') is None
    assert mechanic.get_dtc_info('') is None
    assert mechanic.get_dtc_info(None) is None


def test_get_advice_for_alert_routes():
    # Every pattern in ALERT_ADVICE_PATTERNS should route SOMETHING back.
    for keywords, _ in mechanic.ALERT_ADVICE_PATTERNS:
        alert = f"Test alert {keywords[0]}"
        advice = mechanic.get_advice_for_alert(alert)
        assert advice is not None, f"No advice for keywords={keywords}"


def test_get_advice_for_alert_unknown():
    assert mechanic.get_advice_for_alert('random irrelevant text xyz123') is None
    assert mechanic.get_advice_for_alert('') is None
    assert mechanic.get_advice_for_alert(None) is None


def test_loader_handles_missing_file(tmp_path, monkeypatch):
    """_load() should log + return the default on FileNotFoundError."""
    fake_dir = tmp_path / 'does_not_exist'
    monkeypatch.setattr(mechanic, '_DATA_DIR', fake_dir)
    assert mechanic._load('nope', {}) == {}
    assert mechanic._load('nope', [1, 2]) == [1, 2]

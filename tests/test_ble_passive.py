# tests/test_ble_passive.py
"""
MZ1312 DRIFTER — passive BLE scanner tests
UNCAGED TECHNOLOGY — EST 1991
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

import ble_passive as bp


# ── Match predicates ────────────────────────────────────────────────

def _t(**match):
    return {
        'name': 'test',
        'enabled': True,
        'verified': True,
        'match': match,
        'rssi_alert_threshold': -60,
        'vivi_alert': False,
        'vivi_label': 'test',
    }


def test_oui_prefix_match_case_insensitive():
    target = _t(oui_prefixes=['00:25:DF'])
    assert bp.matches_oui(target, '00:25:DF:11:22:33')
    assert bp.matches_oui(target, '00:25:df:11:22:33')
    assert bp.matches_oui(target, '00:25:Df:aa:bb:cc')


def test_oui_prefix_no_match():
    target = _t(oui_prefixes=['00:25:DF'])
    assert not bp.matches_oui(target, '11:22:33:44:55:66')
    assert not bp.matches_oui(target, '')


def test_manufacturer_id_match():
    target = _t(manufacturer_id=0x004C)
    assert bp.matches_manufacturer_id(target, {0x004C: b'\x12\x19'})
    assert not bp.matches_manufacturer_id(target, {0x0006: b'\x00'})
    assert not bp.matches_manufacturer_id(target, {})


def test_manufacturer_data_prefix_match():
    target = _t(manufacturer_id=0x004C, manufacturer_data_prefix='1219')
    assert bp.matches_manufacturer_data_prefix(target, {0x004C: bytes.fromhex('1219abcd')})
    assert not bp.matches_manufacturer_data_prefix(target, {0x004C: bytes.fromhex('aabbccdd')})
    assert not bp.matches_manufacturer_data_prefix(target, {})


def test_service_uuid_match():
    target = _t(service_uuids=['0000feed-0000-1000-8000-00805f9b34fb'])
    assert bp.matches_service_uuid(target, ['0000feed-0000-1000-8000-00805f9b34fb'])
    assert not bp.matches_service_uuid(target, ['0000abcd-0000-1000-8000-00805f9b34fb'])
    assert not bp.matches_service_uuid(target, [])


def test_service_uuid_match_case_insensitive():
    target = _t(service_uuids=['0000FEED-0000-1000-8000-00805F9B34FB'])
    assert bp.matches_service_uuid(target, ['0000feed-0000-1000-8000-00805f9b34fb'])


def test_combined_match_or_semantics():
    """A target with two criteria fires on EITHER, not both required."""
    target = _t(
        oui_prefixes=['00:25:DF'],
        service_uuids=['0000feed-0000-1000-8000-00805f9b34fb'],
    )
    # Only OUI matches → still a hit
    assert bp.target_matches(target, '00:25:DF:11:22:33', {}, [])
    # Only service UUID matches → still a hit
    assert bp.target_matches(target, '11:22:33:44:55:66', {},
                             ['0000feed-0000-1000-8000-00805f9b34fb'])
    # Neither matches → miss
    assert not bp.target_matches(target, '11:22:33:44:55:66', {}, [])


def test_match_mode_all_requires_every_set_criterion():
    """match_mode=all: every populated criterion must match. The AirTag
    use case: manufacturer_id 0x004C is every Apple device, so the
    Find My prefix must tighten — not loosen — the rule."""
    target = _t(manufacturer_id=0x004C, manufacturer_data_prefix='1219')
    target['match_mode'] = 'all'
    # Both match → hit
    assert bp.target_matches(target, '11:22:33:44:55:66',
                             {0x004C: bytes.fromhex('1219abcd')}, [])
    # Apple ID without the Find My prefix → miss (would be a hit under 'any')
    assert not bp.target_matches(target, '11:22:33:44:55:66',
                                 {0x004C: bytes.fromhex('aabbccdd')}, [])
    # Wrong manufacturer ID → miss
    assert not bp.target_matches(target, '11:22:33:44:55:66',
                                 {0x0006: bytes.fromhex('1219abcd')}, [])
    # No manufacturer data at all → miss
    assert not bp.target_matches(target, '11:22:33:44:55:66', {}, [])


def test_match_mode_any_is_default(tmp_path):
    """A target with no match_mode field must keep OR semantics."""
    yaml_path = tmp_path / 'targets.yaml'
    yaml_path.write_text(
        "targets:\n"
        "  - name: legacy\n"
        "    enabled: true\n"
        "    verified: true\n"
        "    match: {oui_prefixes: ['00:25:DF']}\n"
    )
    [t] = bp.load_targets(yaml_path)
    assert t['match_mode'] == 'any'


def test_match_mode_invalid_falls_back_to_any(tmp_path):
    yaml_path = tmp_path / 'targets.yaml'
    yaml_path.write_text(
        "targets:\n"
        "  - name: weird\n"
        "    enabled: true\n"
        "    verified: true\n"
        "    match_mode: xor\n"
        "    match: {oui_prefixes: ['00:25:DF']}\n"
    )
    [t] = bp.load_targets(yaml_path)
    assert t['match_mode'] == 'any'


def test_unverified_target_disabled_at_runtime(tmp_path, monkeypatch):
    """A target with verified=false AND enabled=true must be force-disabled
    by load_targets (with a WARNING log)."""
    yaml_path = tmp_path / 'targets.yaml'
    yaml_path.write_text(
        "targets:\n"
        "  - name: shady\n"
        "    enabled: true\n"
        "    verified: false\n"
        "    match: {oui_prefixes: ['AA:BB:CC']}\n"
        "    vivi_alert: false\n"
        "    vivi_label: shady\n"
    )
    targets = bp.load_targets(yaml_path)
    assert len(targets) == 1
    assert targets[0]['name'] == 'shady'
    assert targets[0]['enabled'] is False  # forced off at runtime


def test_load_targets_skips_no_match_criteria(tmp_path):
    yaml_path = tmp_path / 'targets.yaml'
    yaml_path.write_text(
        "targets:\n"
        "  - name: empty\n"
        "    enabled: true\n"
        "    verified: true\n"
        "    match: {}\n"
    )
    assert bp.load_targets(yaml_path) == []


# ── Rate limiter ────────────────────────────────────────────────────

def test_same_mac_rate_limited_within_30s():
    rl = bp.RateLimiter(cooldown=30.0)
    assert rl.allow('axon', '00:25:DF:11', now=1000)
    assert not rl.allow('axon', '00:25:DF:11', now=1015)
    assert rl.allow('axon', '00:25:DF:11', now=1031)


def test_different_macs_not_rate_limited():
    rl = bp.RateLimiter(cooldown=30.0)
    assert rl.allow('axon', '00:25:DF:11', now=1000)
    assert rl.allow('axon', '00:25:DF:22', now=1001)
    assert rl.allow('axon', '00:25:DF:33', now=1002)


def test_rate_limit_cache_pruning_after_5min():
    rl = bp.RateLimiter(cooldown=30.0)
    rl.allow('axon', '00:25:DF:11', now=1000)
    assert len(rl) == 1
    # Trigger a check far enough in the future that prune fires.
    rl.allow('axon', '00:25:DF:22', now=2000)
    assert len(rl) == 1  # the old 00:11 entry pruned, only the 2000 one survives


# ── GPS injection ───────────────────────────────────────────────────

def test_fresh_gps_attached_to_detection(monkeypatch):
    cache = bp.GpsCache(fresh_sec=10.0)
    cache.update({'lat': -36.7, 'lng': 144.2})
    fix = cache.get()
    assert fix == {'lat': -36.7, 'lng': 144.2}


def test_stale_gps_not_attached(monkeypatch):
    cache = bp.GpsCache(fresh_sec=10.0)
    cache.update({'lat': -36.7, 'lng': 144.2})
    cache._ts -= 60
    assert cache.get() is None


def test_no_gps_yields_null():
    assert bp.GpsCache(fresh_sec=10.0).get() is None


# ── SQLite event log ────────────────────────────────────────────────

def test_detection_logged_to_sqlite(tmp_path):
    db = bp.EventLog(tmp_path / 'b.db')
    db.insert({
        'ts': time.time(), 'target': 'axon', 'mac': '00:25:DF:11:22:33',
        'rssi': -55, 'gps': None, 'manufacturer_id': '0x0006',
        'advertised_name': None, 'raw_advertisement': '',
        'is_alert': True,
    })
    assert db.count() == 1


def test_prune_old_detections(tmp_path):
    db = bp.EventLog(tmp_path / 'b.db')
    old = time.time() - (40 * 86400)
    new = time.time()
    for ts in (old, new):
        db.insert({
            'ts': ts, 'target': 'axon', 'mac': '00:25:DF:11:22:33',
            'rssi': -55, 'gps': None, 'manufacturer_id': None,
            'advertised_name': None, 'raw_advertisement': '',
            'is_alert': False,
        })
    assert db.count() == 2
    pruned = db.prune_older_than(30)
    assert pruned == 1
    assert db.count() == 1


# ── home_sync exclusion ─────────────────────────────────────────────

def test_homesync_excludes_ble_topics():
    import home_sync
    assert home_sync._topic_excluded('drifter/ble/detection', ['drifter/ble/+'])
    assert home_sync._topic_excluded('drifter/audio/wav', ['drifter/audio/+'])
    assert not home_sync._topic_excluded('drifter/engine/rpm',
                                         ['drifter/ble/+', 'drifter/audio/+'])


def test_homesync_excludes_hash_wildcard():
    import home_sync
    assert home_sync._topic_excluded('drifter/ble/detection/v2',
                                     ['drifter/ble/#'])
    assert home_sync._topic_excluded('drifter/ble/detection',
                                     ['drifter/ble/#'])

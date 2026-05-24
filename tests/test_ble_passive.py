# tests/test_ble_passive.py
"""
MZ1312 DRIFTER — passive BLE scanner v2 tests
UNCAGED TECHNOLOGY — EST 1991
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest

# bleak is a runtime-only dependency; stub it out before importing ble_passive
# so the test suite runs on the bench without a Bluetooth adapter.
_bleak_stub = ModuleType('bleak')
_bleak_stub.BleakScanner = MagicMock()
sys.modules.setdefault('bleak', _bleak_stub)

import ble_passive as bp  # noqa: E402 — must come after bleak stub


# ── classify_oui ──────────────────────────────────────────────────────

def test_classify_oui_axon():
    result = bp.classify_oui('00:25:DF:11:22:33')
    assert result is not None
    target, severity, desc = result
    assert target == 'axon-class'
    assert severity == 'high'
    assert 'Axon' in desc


def test_classify_oui_cradlepoint_003044():
    result = bp.classify_oui('00:30:44:AA:BB:CC')
    assert result is not None
    target, severity, _ = result
    assert target == 'cradlepoint-class'
    assert severity == 'high'


def test_classify_oui_cradlepoint_00170d():
    result = bp.classify_oui('00:17:0D:01:02:03')
    assert result is not None
    target, severity, _ = result
    assert target == 'cradlepoint-class'
    assert severity == 'high'


def test_classify_oui_ruckus():
    result = bp.classify_oui('F8:E7:1E:AA:BB:CC')
    assert result is not None
    target, severity, _ = result
    assert target == 'ruckus-class'
    assert severity == 'medium'


def test_classify_oui_case_insensitive_lower():
    assert bp.classify_oui('00:25:df:11:22:33') is not None


def test_classify_oui_dash_separator():
    assert bp.classify_oui('00-25-DF-11-22-33') is not None


def test_classify_oui_unknown_returns_none():
    assert bp.classify_oui('FF:FF:FF:11:22:33') is None


# ── is_apple_findmy ──────────────────────────────────────────────────

def test_is_apple_findmy_true():
    assert bp.is_apple_findmy({0x004C: b'\x12\x19\xaa\xbb'}) is True


def test_is_apple_findmy_wrong_type_byte():
    assert bp.is_apple_findmy({0x004C: b'\x07\x19'}) is False


def test_is_apple_findmy_missing_key():
    assert bp.is_apple_findmy({0x0006: b'\x12\x19'}) is False


def test_is_apple_findmy_empty_dict():
    assert bp.is_apple_findmy({}) is False


def test_is_apple_findmy_empty_payload():
    assert bp.is_apple_findmy({0x004C: b''}) is False


# ── oui_of ────────────────────────────────────────────────────────────

def test_oui_of_strips_colons_and_lowercases():
    assert bp.oui_of('00:25:DF:11:22:33') == '0025df'


def test_oui_of_strips_dashes():
    assert bp.oui_of('00-25-DF-11-22-33') == '0025df'


# ── serialise_mfr + first_legacy_mfr_hex ─────────────────────────────

def test_serialise_mfr_format():
    result = bp.serialise_mfr({0x004C: bytes.fromhex('1219')})
    parsed = json.loads(result)
    assert parsed == {'0x004C': '1219'}


def test_first_legacy_mfr_hex_nonempty():
    assert bp.first_legacy_mfr_hex({0x004C: b'\x12'}) == '0x004C'


def test_first_legacy_mfr_hex_empty():
    assert bp.first_legacy_mfr_hex({}) is None


# ── read_drive_id ─────────────────────────────────────────────────────

def test_read_drive_id_prefers_new_path(tmp_path, monkeypatch):
    new_file = tmp_path / 'current_drive'
    legacy_file = tmp_path / 'current_drive_id'
    new_file.write_text('drive-new-abc123')
    legacy_file.write_text('drive-legacy-xyz')
    monkeypatch.setattr(bp, 'DRIVE_PATH_NEW', new_file)
    monkeypatch.setattr(bp, 'DRIVE_PATH_LEGACY', legacy_file)
    assert bp.read_drive_id() == 'drive-new-abc123'


def test_read_drive_id_falls_back_to_legacy(tmp_path, monkeypatch):
    legacy_file = tmp_path / 'current_drive_id'
    legacy_file.write_text('drive-legacy-xyz')
    monkeypatch.setattr(bp, 'DRIVE_PATH_NEW', tmp_path / 'missing')
    monkeypatch.setattr(bp, 'DRIVE_PATH_LEGACY', legacy_file)
    assert bp.read_drive_id() == 'drive-legacy-xyz'


def test_read_drive_id_no_drive_when_neither_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(bp, 'DRIVE_PATH_NEW', tmp_path / 'missing_new')
    monkeypatch.setattr(bp, 'DRIVE_PATH_LEGACY', tmp_path / 'missing_legacy')
    assert bp.read_drive_id() == 'no-drive'


# ── read_gps ──────────────────────────────────────────────────────────

def test_read_gps_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(bp, 'GPS_PATH', tmp_path / 'no_gps.json')
    assert bp.read_gps() == (None, None)


def test_read_gps_fix_false(tmp_path, monkeypatch):
    gps = tmp_path / 'gps.json'
    gps.write_text(json.dumps({'lat': 37.7, 'lon': -122.4, 'fix': False, 'ts': time.time()}))
    monkeypatch.setattr(bp, 'GPS_PATH', gps)
    assert bp.read_gps() == (None, None)


def test_read_gps_stale_ts(tmp_path, monkeypatch):
    gps = tmp_path / 'gps.json'
    gps.write_text(json.dumps({'lat': 37.7, 'lon': -122.4, 'fix': True, 'ts': time.time() - 60}))
    monkeypatch.setattr(bp, 'GPS_PATH', gps)
    assert bp.read_gps() == (None, None)


def test_read_gps_fresh_fix(tmp_path, monkeypatch):
    gps = tmp_path / 'gps.json'
    gps.write_text(json.dumps({'lat': 37.7749, 'lon': -122.4194, 'fix': True, 'ts': time.time()}))
    monkeypatch.setattr(bp, 'GPS_PATH', gps)
    lat, lon = bp.read_gps()
    assert lat == pytest.approx(37.7749)
    assert lon == pytest.approx(-122.4194)


# ── open_db ───────────────────────────────────────────────────────────

def test_open_db_creates_fresh_schema(tmp_path):
    conn = bp.open_db(tmp_path / 'fresh.db')
    cols = {r[1] for r in conn.execute('PRAGMA table_info(detections)')}
    for expected in ('ts', 'target', 'mac', 'rssi', 'name', 'severity',
                     'description', 'manufacturer_data', 'service_uuids',
                     'lat', 'lon', 'drive_id'):
        assert expected in cols


def test_open_db_migrates_v1_adds_v2_columns(tmp_path):
    db_path = tmp_path / 'v1.db'
    # Seed a v1 schema (legacy columns only, no v2 columns)
    conn0 = sqlite3.connect(str(db_path))
    conn0.execute('''
        CREATE TABLE detections (
          id              INTEGER PRIMARY KEY AUTOINCREMENT,
          ts              REAL NOT NULL,
          target          TEXT NOT NULL,
          mac             TEXT NOT NULL,
          rssi            INTEGER,
          manufacturer_id TEXT,
          adv_name        TEXT,
          lat             REAL,
          lng             REAL,
          is_alert        INTEGER DEFAULT 0,
          drive_id        TEXT NOT NULL DEFAULT 'unknown'
        )
    ''')
    conn0.commit()
    conn0.close()

    conn = bp.open_db(db_path)
    cols = {r[1] for r in conn.execute('PRAGMA table_info(detections)')}
    # v2 columns should now be present
    for v2col in ('name', 'severity', 'lon'):
        assert v2col in cols
    # legacy columns must still be present
    for v1col in ('manufacturer_id', 'adv_name', 'lng', 'is_alert'):
        assert v1col in cols


# ── insert_detection dual-write ───────────────────────────────────────

def test_insert_detection_dual_writes_legacy_columns(tmp_path):
    """insert_detection must write both v2 columns and legacy aliases."""
    db_path = tmp_path / 'dual.db'
    # Build a v1+v2 DB (open_db handles migration if we seed v1 first)
    conn0 = sqlite3.connect(str(db_path))
    conn0.execute('''
        CREATE TABLE detections (
          id              INTEGER PRIMARY KEY AUTOINCREMENT,
          ts              REAL NOT NULL,
          target          TEXT NOT NULL,
          mac             TEXT NOT NULL,
          rssi            INTEGER,
          manufacturer_id TEXT,
          adv_name        TEXT,
          lat             REAL,
          lng             REAL,
          is_alert        INTEGER DEFAULT 0,
          drive_id        TEXT NOT NULL DEFAULT 'unknown'
        )
    ''')
    conn0.commit()
    conn0.close()
    conn = bp.open_db(db_path)  # ALTER ADD v2 columns

    det = {
        'ts': time.time(),
        'target': 'axon-class',
        'mac': '00:25:DF:11:22:33',
        'rssi': -55,
        'name': 'test-device',
        'severity': 'high',
        'description': 'Axon body cam',
        'manufacturer_data': '{}',
        'service_uuids': '[]',
        'lat': 37.77,
        'lon': -122.41,
        'drive_id': 'drive-test-001',
        '_legacy_mfr_hex': '0x0006',
    }
    bp.insert_detection(conn, det)

    row = conn.execute(
        'SELECT name, adv_name, severity, is_alert, lon, lng, manufacturer_id '
        'FROM detections LIMIT 1'
    ).fetchone()
    name_v2, adv_name_v1, severity_v2, is_alert_v1, lon_v2, lng_v1, mfr_id_v1 = row

    assert name_v2 == 'test-device'
    assert adv_name_v1 == 'test-device'         # dual-write
    assert severity_v2 == 'high'
    assert is_alert_v1 == 1                      # severity high → is_alert=1
    assert lon_v2 == pytest.approx(-122.41)
    assert lng_v1 == pytest.approx(-122.41)      # dual-write
    assert mfr_id_v1 == '0x0006'                 # from _legacy_mfr_hex


# ── Bleconv._check_persistent ─────────────────────────────────────────

class _FakeMqtt:
    def __init__(self):
        self.publishes: list = []

    def publish(self, topic, payload, qos=0, retain=False):
        self.publishes.append((topic, payload))


def _make_bleconv(tmp_path):
    conn = bp.open_db(tmp_path / 'bc.db')
    mqttc = _FakeMqtt()
    bc = bp.Bleconv(conn, mqttc)
    return bc, conn


def _insert_raw(conn, mac, drive_id, ts=None):
    conn.execute(
        "INSERT INTO detections (ts, target, mac, rssi, drive_id) VALUES (?,?,?,?,?)",
        (ts or time.time(), 'axon-class', mac, -55, drive_id),
    )
    conn.commit()


def test_check_persistent_returns_dict_for_two_drives(tmp_path):
    bc, conn = _make_bleconv(tmp_path)
    now = time.time()
    _insert_raw(conn, 'AA:BB:CC:DD:EE:01', 'drive-alpha', now - 100)
    _insert_raw(conn, 'AA:BB:CC:DD:EE:01', 'drive-alpha', now - 90)
    _insert_raw(conn, 'AA:BB:CC:DD:EE:01', 'drive-beta',  now - 50)
    result = bc._check_persistent('AA:BB:CC:DD:EE:01')
    assert result is not None
    assert result['unique_drives'] == 2
    assert result['mac'] == 'AA:BB:CC:DD:EE:01'


def test_check_persistent_returns_none_for_single_drive(tmp_path):
    bc, conn = _make_bleconv(tmp_path)
    now = time.time()
    _insert_raw(conn, 'AA:BB:CC:DD:EE:02', 'drive-only', now - 100)
    _insert_raw(conn, 'AA:BB:CC:DD:EE:02', 'drive-only', now - 50)
    assert bc._check_persistent('AA:BB:CC:DD:EE:02') is None


def test_check_persistent_outside_30day_window_returns_none(tmp_path):
    bc, conn = _make_bleconv(tmp_path)
    old = time.time() - (31 * 86400)
    _insert_raw(conn, 'AA:BB:CC:DD:EE:03', 'drive-old-1', old)
    _insert_raw(conn, 'AA:BB:CC:DD:EE:03', 'drive-old-2', old + 3600)
    assert bc._check_persistent('AA:BB:CC:DD:EE:03') is None


# ── Bleconv._cooldown_ok ──────────────────────────────────────────────

def test_cooldown_ok_first_call_true(tmp_path):
    bc, _ = _make_bleconv(tmp_path)
    assert bc._cooldown_ok('DE:AD:BE:EF:00:01') is True


def test_cooldown_ok_second_call_within_60s_false(tmp_path, monkeypatch):
    bc, _ = _make_bleconv(tmp_path)
    mac = 'DE:AD:BE:EF:00:02'
    t0 = 1_000_000.0
    monkeypatch.setattr(bp.time, 'time', lambda: t0)
    bc._cooldown_ok(mac)
    monkeypatch.setattr(bp.time, 'time', lambda: t0 + 30.0)
    assert bc._cooldown_ok(mac) is False


def test_cooldown_ok_true_after_cooldown_elapsed(tmp_path, monkeypatch):
    bc, _ = _make_bleconv(tmp_path)
    mac = 'DE:AD:BE:EF:00:03'
    t0 = 1_000_000.0
    monkeypatch.setattr(bp.time, 'time', lambda: t0)
    bc._cooldown_ok(mac)
    monkeypatch.setattr(bp.time, 'time', lambda: t0 + 61.0)
    assert bc._cooldown_ok(mac) is True

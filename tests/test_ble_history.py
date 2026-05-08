# tests/test_ble_history.py
"""
MZ1312 DRIFTER — Phase 4.7 forensic persistence tests
UNCAGED TECHNOLOGY — EST 1991
"""
from __future__ import annotations

import csv
import io
import json
import os
import time
from pathlib import Path

import pytest

import ble_history as bh


# ── helpers ────────────────────────────────────────────────────────

def _det(**overrides) -> dict:
    base = {
        'ts': time.time(),
        'target': 'axon',
        'mac': '00:25:DF:11:22:33',
        'rssi': -55,
        'manufacturer_id': '0x0006',
        'adv_name': None,
        'lat': None,
        'lng': None,
        'is_alert': False,
        'drive_id': 'drive-test-aaaaaa',
    }
    base.update(overrides)
    return base


# ── 1. schema ──────────────────────────────────────────────────────

def test_schema_creates_clean(tmp_path):
    db = tmp_path / 'h.db'
    conn = bh.open_db(db)
    # Table exists
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='detections'"
    ).fetchall()
    assert rows == [('detections',)]
    # All required columns present
    cols = [r[1] for r in conn.execute("PRAGMA table_info(detections)").fetchall()]
    expected = {'id', 'ts', 'target', 'mac', 'rssi', 'manufacturer_id',
                'adv_name', 'lat', 'lng', 'is_alert', 'drive_id'}
    assert expected.issubset(set(cols))
    # All four indexes present
    idx = [r[1] for r in conn.execute(
        "SELECT * FROM sqlite_master WHERE type='index' AND tbl_name='detections'"
    ).fetchall()]
    for name in ('idx_ts', 'idx_target_ts', 'idx_drive_id', 'idx_mac_ts'):
        assert name in idx
    # Schema version stamped
    assert conn.execute("PRAGMA user_version").fetchone()[0] == bh.SCHEMA_VERSION


# ── 2. round-trip ──────────────────────────────────────────────────

def test_insert_then_query_roundtrip(tmp_path):
    conn = bh.open_db(tmp_path / 'h.db')
    bh.insert_detection(conn, _det(rssi=-42, lat=37.7749, lng=-122.4194,
                                    is_alert=True))
    [row] = bh.query_history(conn, limit=10)
    assert row['target'] == 'axon'
    assert row['mac'] == '00:25:DF:11:22:33'
    assert row['rssi'] == -42
    assert row['lat'] == pytest.approx(37.7749)
    assert row['lng'] == pytest.approx(-122.4194)
    assert row['is_alert'] is True
    assert row['drive_id'] == 'drive-test-aaaaaa'


# ── 3-4. drive_id derivation ───────────────────────────────────────

def test_drive_id_reuse_within_30min(tmp_path):
    state = tmp_path / 'drive_id'
    drive_id = bh.current_drive_id(state, idle_seconds=1800.0)
    assert drive_id.startswith('drive-')
    # Mtime is fresh — should reuse.
    again = bh.current_drive_id(state, idle_seconds=1800.0)
    assert again == drive_id


def test_drive_id_new_after_idle(tmp_path):
    state = tmp_path / 'drive_id'
    first = bh.current_drive_id(state, idle_seconds=1800.0)
    # Backdate the file by 2h.
    past = time.time() - (2 * 3600)
    os.utime(state, (past, past))
    second = bh.current_drive_id(state, idle_seconds=1800.0)
    assert second != first
    assert second.startswith('drive-')


# ── 5. filtering ───────────────────────────────────────────────────

def test_query_filters_by_target_and_window(tmp_path):
    conn = bh.open_db(tmp_path / 'h.db')
    now = time.time()
    bh.insert_detection(conn, _det(ts=now - 3600, target='axon', drive_id='d1'))
    bh.insert_detection(conn, _det(ts=now - 1800, target='axon', drive_id='d1'))
    bh.insert_detection(conn, _det(ts=now - 1800, target='tile', drive_id='d2'))
    bh.insert_detection(conn, _det(ts=now,        target='axon', drive_id='d2'))

    # Target filter
    axon = bh.query_history(conn, target='axon')
    assert len(axon) == 3

    # Time window
    last_hour = bh.query_history(conn, since=now - 3601, until=now - 1799)
    assert len(last_hour) == 3

    # Drive filter
    d2 = bh.query_history(conn, drive_id='d2')
    assert {r['target'] for r in d2} == {'axon', 'tile'}


# ── 6-7. export formats ────────────────────────────────────────────

def test_geojson_export_shape(tmp_path):
    conn = bh.open_db(tmp_path / 'h.db')
    bh.insert_detection(conn, _det(lat=37.0, lng=-122.0, is_alert=True))
    bh.insert_detection(conn, _det(lat=None, lng=None))   # dropped — no GPS
    bh.insert_detection(conn, _det(lat=37.5, lng=-122.5))

    rows = bh.query_history(conn, limit=100)
    fc = bh.to_geojson(rows)
    assert fc['type'] == 'FeatureCollection'
    assert len(fc['features']) == 2  # null-GPS row is silently dropped
    f = fc['features'][0]
    assert f['type'] == 'Feature'
    assert f['geometry']['type'] == 'Point'
    # GeoJSON ordering: longitude first, latitude second.
    assert len(f['geometry']['coordinates']) == 2
    assert f['properties']['target'] == 'axon'
    # Round-trips through json
    json.loads(json.dumps(fc))


def test_csv_export_columns(tmp_path):
    conn = bh.open_db(tmp_path / 'h.db')
    bh.insert_detection(conn, _det(rssi=-55, lat=1.0, lng=2.0))
    out = bh.to_csv(bh.query_history(conn))
    rows = list(csv.reader(io.StringIO(out)))
    assert rows[0] == [
        'iso_ts', 'unix_ts', 'target', 'mac', 'rssi',
        'manufacturer_id', 'adv_name', 'lat', 'lng',
        'is_alert', 'drive_id',
    ]
    assert len(rows) == 2
    # ISO timestamp parses
    assert rows[1][0].endswith('Z')
    # Coordinates round-trip
    assert float(rows[1][7]) == 1.0


# ── 8. persistence failure must not break MQTT publish ─────────────

def test_persistence_failure_does_not_break_mqtt_publish(monkeypatch):
    """The Phase 4.7 promise: a sqlite outage means we lose history,
    NOT the live tile and NOT vivi proactive comments. _record_detection
    publishes first, persists second — and the persist path is wrapped
    in try/except."""
    import ble_passive as bp

    # Build a scanner without going through __init__ (which loads
    # targets, opens bleak, etc).
    scanner = bp.BLEScanner.__new__(bp.BLEScanner)

    published: list = []
    class FakeMqtt:
        def publish(self, topic, payload):
            published.append((topic, payload))
    scanner._mqtt = FakeMqtt()

    # History "connection" that always raises.
    class BrokenConn:
        def execute(self, *a, **k):
            raise RuntimeError("disk full")
    scanner._history = BrokenConn()

    # Force insert_detection through the broken conn → must raise.
    monkeypatch.setattr(bp.ble_history, 'insert_detection',
                        lambda c, d: c.execute("INSERT", ()))
    monkeypatch.setattr(bp.ble_history, 'touch_drive_id', lambda *a, **k: None)

    detection = _det(target='axon', mac='00:25:DF:00:00:01')
    scanner._record_detection(detection)

    # Publish ran first, succeeded; persist raised, was swallowed.
    assert len(published) == 1
    topic, payload = published[0]
    assert topic == 'drifter/ble/detection'
    parsed = json.loads(payload)
    assert parsed['target'] == 'axon'


# ── 9. history endpoint contract (via the underlying query) ────────

def test_history_endpoint_filters(tmp_path):
    """The /api/ble/history endpoint is a thin wrapper around
    query_history with the same params. Test the filter contract."""
    conn = bh.open_db(tmp_path / 'h.db')
    now = time.time()
    bh.insert_detection(conn, _det(ts=now - 7200, target='axon', drive_id='dA'))
    bh.insert_detection(conn, _det(ts=now - 60,   target='tile', drive_id='dB'))
    bh.insert_detection(conn, _det(ts=now,        target='axon', drive_id='dB'))

    # since= filter
    fresh = bh.query_history(conn, since=now - 600)
    assert len(fresh) == 2

    # target+drive filter
    out = bh.query_history(conn, target='axon', drive_id='dB')
    assert len(out) == 1
    assert out[0]['drive_id'] == 'dB'

    # limit cap
    capped = bh.query_history(conn, limit=999999)
    assert len(capped) == 3   # only 3 total, the cap applies but data is small


# ── 10. drives endpoint summary ────────────────────────────────────

def test_drives_endpoint_summarises_correctly(tmp_path):
    conn = bh.open_db(tmp_path / 'h.db')
    base = time.time()
    # Drive A: 3 detections, 2 unique targets, span 5 min
    bh.insert_detection(conn, _det(ts=base - 600, target='axon', drive_id='dA'))
    bh.insert_detection(conn, _det(ts=base - 300, target='axon', drive_id='dA'))
    bh.insert_detection(conn, _det(ts=base - 60,  target='tile', drive_id='dA'))
    # Drive B: 1 detection
    bh.insert_detection(conn, _det(ts=base, target='axon', drive_id='dB'))

    drives = bh.query_drives(conn)
    by_id = {d['drive_id']: d for d in drives}

    assert by_id['dA']['detection_count'] == 3
    assert by_id['dA']['unique_targets'] == 2
    assert by_id['dA']['ended_ts'] - by_id['dA']['started_ts'] == pytest.approx(540, abs=1)
    assert by_id['dB']['detection_count'] == 1
    assert by_id['dB']['unique_targets'] == 1

    # Most-recent-drive first
    assert drives[0]['drive_id'] == 'dB'


# ── prune coverage (fills the gap left by removed test_prune_old_detections) ──

def test_prune_older_than_drops_old_rows(tmp_path):
    conn = bh.open_db(tmp_path / 'h.db')
    old = time.time() - (40 * 86400)
    new = time.time()
    bh.insert_detection(conn, _det(ts=old))
    bh.insert_detection(conn, _det(ts=new))
    assert bh.count(conn) == 2
    assert bh.prune_older_than(conn, 30) == 1
    assert bh.count(conn) == 1


# ── parse_relative ─────────────────────────────────────────────────

def test_parse_relative_units():
    now = 1_700_000_000
    assert bh.parse_relative('1h', now=now) == now - 3600
    assert bh.parse_relative('24h', now=now) == now - 86400
    assert bh.parse_relative('7d', now=now) == now - 7 * 86400
    assert bh.parse_relative('30m', now=now) == now - 1800

    iso = bh.parse_relative('2026-05-01')
    assert isinstance(iso, float)

    with pytest.raises(ValueError):
        bh.parse_relative('whatever')

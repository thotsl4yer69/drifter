# tests/test_ble_persistence.py
"""
MZ1312 DRIFTER — Phase 4.8.3 persistence-scoring tests
UNCAGED TECHNOLOGY — EST 1991
"""
from __future__ import annotations

import time

import pytest

import ble_history as bh
import ble_persistence as bp


# ── fixtures ────────────────────────────────────────────────────────

@pytest.fixture
def conn(tmp_path):
    return bh.open_db(tmp_path / 'h.db')


def _ins(conn, **overrides):
    base = {
        'ts':              time.time(),
        'target':          'axon-class',
        'mac':             '00:25:DF:11:22:33',
        'rssi':            -55,
        'manufacturer_id': '0x0006',
        'adv_name':        None,
        'lat':             37.7749,
        'lng':             -122.4194,
        'is_alert':        False,
        'drive_id':        'drive-A',
    }
    base.update(overrides)
    bh.insert_detection(conn, base)


# ── filter rules ────────────────────────────────────────────────────

def test_single_drive_identity_filtered_out(conn):
    """unique_drive_ids < 2 → drop. Same drive = locality, not following."""
    for _ in range(5):
        _ins(conn, drive_id='drive-A')
    contacts, noise = bp.score_persistent_contacts(conn)
    assert contacts == []
    assert noise >= 1


def test_low_count_identity_filtered_out(conn):
    """detection_count < 3 → drop. Two hits aren't a pattern."""
    _ins(conn, drive_id='drive-A')
    _ins(conn, drive_id='drive-B')
    contacts, noise = bp.score_persistent_contacts(conn)
    assert contacts == []
    assert noise >= 1


def test_no_gps_identity_excluded(conn):
    """All hits in cluster_id=-1 → drop. Can't tell if device travelled."""
    for d in ('drive-A', 'drive-B', 'drive-C'):
        _ins(conn, drive_id=d, lat=None, lng=None,
             ts=time.time() - {'drive-A': 0, 'drive-B': 60, 'drive-C': 120}[d])
    contacts, noise = bp.score_persistent_contacts(conn)
    assert contacts == []
    assert noise >= 1


# ── positive cases ──────────────────────────────────────────────────

def test_cross_drive_cross_location_scores_high(conn):
    """Same axon MAC seen across 2 drives at 2 separated locations →
    appears in the contacts list with the right counters."""
    base_ts = time.time() - 86400
    # Drive A at SF City Hall area
    for i in range(3):
        _ins(conn, drive_id='drive-A',
             ts=base_ts + i * 60,
             lat=37.7793 + i * 1e-5, lng=-122.4192)
    # Drive B at SF Ferry Building (~2 km away)
    for i in range(3):
        _ins(conn, drive_id='drive-B',
             ts=base_ts + 7200 + i * 60,
             lat=37.7955 + i * 1e-5, lng=-122.3937)

    contacts, _ = bp.score_persistent_contacts(conn)
    assert len(contacts) == 1
    c = contacts[0]
    assert c['detection_count'] == 6
    assert c['unique_drive_ids'] == 2
    assert c['unique_geo_clusters'] == 2
    # axon → mac identity, confidence 0.85, score = 2*2*0.85 = 3.4
    assert c['follower_score'] == pytest.approx(2 * 2 * 0.85, abs=1e-6)
    # 3.4 ≥ 3 and confidence 0.85 ≥ 0.7 → wait, score must be ≥ 6 for high.
    # 3.4 hits 'medium' bucket.
    assert c['tier'] == 'medium'


def test_high_tier_when_score_clears_threshold(conn):
    """3 drives × 3 locations × 0.85 confidence = 7.65 → high."""
    base_ts = time.time() - 86400
    locations = [
        (37.7793, -122.4192),
        (37.7955, -122.3937),
        (37.8044, -122.4194),
    ]
    drives = ['drive-X', 'drive-Y', 'drive-Z']
    t = base_ts
    for d in drives:
        for lat, lng in locations:
            for k in range(2):  # 2 hits at each loc to clear cluster min_samples
                t += 30
                _ins(conn, drive_id=d, ts=t,
                     lat=lat + k * 1e-5, lng=lng)
    contacts, _ = bp.score_persistent_contacts(conn)
    assert len(contacts) == 1
    c = contacts[0]
    assert c['unique_drive_ids'] == 3
    assert c['unique_geo_clusters'] == 3
    assert c['tier'] == 'high'
    assert c['follower_score'] >= 6


def test_tier_assignment_matches_thresholds(conn):
    """Boundary check: scores < 3 → weak, 3–6 → medium, ≥6 + conf≥0.7 → high."""
    # Weak: 2 drives × 1 cluster × 0.4 conf (airtag) = 0.8 → weak
    base_ts = time.time() - 86400
    for d, t_off in (('drive-A', 0), ('drive-B', 3600)):
        for i in range(3):
            _ins(conn, drive_id=d,
                 target='airtag', manufacturer_id='0x004C',
                 mac=f'AA:BB:CC:DD:EE:{i:02X}',
                 adv_name='', lat=37.7749, lng=-122.4194,
                 ts=base_ts + t_off + i)
    contacts, _ = bp.score_persistent_contacts(conn)
    # Airtag identities collapse to mfr|name:anon, so all 6 share one identity
    assert len(contacts) == 1
    # 1 cluster × 2 drives × 0.4 = 0.8 → weak
    assert contacts[0]['tier'] == 'weak'
    assert contacts[0]['follower_score'] == pytest.approx(0.8, abs=1e-6)


def test_noise_excluded_counter_increments(conn):
    """noise_excluded should reflect how many distinct identities were
    filtered out — useful for the dashboard's 'X candidates rejected'
    line."""
    # 2 separate identities, both filtered: one single-drive, one low-count
    for _ in range(5):
        _ins(conn, drive_id='drive-X', mac='00:25:DF:11:22:33')
    _ins(conn, drive_id='drive-A', mac='00:25:DF:99:99:99')
    _ins(conn, drive_id='drive-B', mac='00:25:DF:99:99:99')
    contacts, noise = bp.score_persistent_contacts(conn)
    assert contacts == []
    assert noise == 2


# ── endpoint shape ──────────────────────────────────────────────────

def test_endpoint_returns_valid_shape(conn):
    """The endpoint serialises score_persistent_contacts. Verify the
    contract fields are all present and JSON-serialisable."""
    import json
    base_ts = time.time() - 86400
    for d, lat in (('drive-A', 37.7793), ('drive-B', 37.7955)):
        for i in range(3):
            _ins(conn, drive_id=d, ts=base_ts + i,
                 lat=lat + i * 1e-5, lng=-122.4)
    contacts, noise = bp.score_persistent_contacts(conn)
    assert len(contacts) >= 1
    payload = {
        'window':         '7d',
        'computed_at':    time.time(),
        'contacts':       contacts,
        'count':          len(contacts),
        'noise_excluded': noise,
    }
    # Must round-trip through json without TypeErrors.
    s = json.dumps(payload)
    back = json.loads(s)
    c = back['contacts'][0]
    for k in ('identity', 'target', 'detection_count', 'unique_drive_ids',
              'unique_geo_clusters', 'first_seen', 'last_seen',
              'span_hours', 'confidence', 'follower_score', 'tier'):
        assert k in c, f"missing field {k}"


def test_endpoint_window_param_works(conn):
    """since_ts/until_ts should narrow the rows considered."""
    now = time.time()
    # Old hits (8 days ago) — should be invisible to a 7d window
    for d in ('drive-OLD-A', 'drive-OLD-B'):
        for i in range(3):
            _ins(conn, drive_id=d, ts=now - 8 * 86400 - i,
                 lat=37.78 + i * 1e-5, lng=-122.4)
    # Fresh hits — should appear
    for d in ('drive-NEW-A', 'drive-NEW-B'):
        for i in range(3):
            _ins(conn, drive_id=d, ts=now - 3600 - i,
                 mac='AA:BB:CC:DD:EE:FF',
                 lat=37.79 + i * 1e-5, lng=-122.41)

    fresh, _ = bp.score_persistent_contacts(conn,
                                            since_ts=now - 7 * 86400,
                                            until_ts=now)
    macs = {c['identity'] for c in fresh}
    assert any('AA:BB:CC:DD:EE:FF' in m for m in macs)
    assert all('drive-OLD' not in str(c) for c in fresh)


# ── Vivi summary ────────────────────────────────────────────────────

def test_vivi_summary_empty_when_no_contacts(conn):
    assert bp.get_persistent_contact_summary(conn) == ''


def test_vivi_summary_describes_high_tier_contacts(conn):
    """When a high-tier contact exists, the summary names target,
    drive count, and location count."""
    base_ts = time.time() - 86400
    locations = [(37.7793, -122.4192), (37.7955, -122.3937),
                 (37.8044, -122.4194)]
    for d in ('drive-X', 'drive-Y', 'drive-Z'):
        for lat, lng in locations:
            for k in range(2):
                _ins(conn, drive_id=d,
                     ts=base_ts + len(d) + k + lat,
                     lat=lat + k * 1e-5, lng=lng)
    summary = bp.get_persistent_contact_summary(conn)
    assert 'persistent contact' in summary
    assert 'high' in summary
    assert 'axon' in summary
    assert '3 drives' in summary
    assert 'locations' in summary

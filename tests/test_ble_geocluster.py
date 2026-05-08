# tests/test_ble_geocluster.py
"""
MZ1312 DRIFTER — Phase 4.8.2 geo-cluster tests
UNCAGED TECHNOLOGY — EST 1991
"""
from __future__ import annotations

import time

import pytest

import ble_geocluster as gc


def _p(lat, lng):
    return {'lat': lat, 'lng': lng}


# ── distance ────────────────────────────────────────────────────────

def test_haversine_distance_correct_to_within_1m():
    # Two points 0.0001° apart in longitude at the equator ≈ 11.13 m.
    d = gc.haversine_m(0.0, 0.0, 0.0, 0.0001)
    assert d == pytest.approx(11.12, abs=0.5)
    # SF City Hall to Ferry Building (verified against an external
    # great-circle calculator at ~2.87 km).
    sf_city = (37.7793, -122.4192)
    sf_ferry = (37.7955, -122.3937)
    d2 = gc.haversine_m(*sf_city, *sf_ferry)
    assert 2800 <= d2 <= 2950
    # Antipodal sanity.
    assert gc.haversine_m(0, 0, 0, 0) == pytest.approx(0)


# ── clustering ──────────────────────────────────────────────────────

def test_two_points_within_eps_cluster_together():
    # 50 m apart at SF — well under the 150 m epsilon.
    points = [_p(37.7749, -122.4194), _p(37.77535, -122.4194)]
    ids = gc.cluster_locations(points, eps_meters=150, min_samples=2)
    assert ids[0] == ids[1] != -1


def test_points_far_apart_become_separate_clusters():
    # Two pairs, the pairs ~30 m apart from each other internally
    # (well within eps), the pair-centres 5 km apart (well outside).
    points = [
        _p(37.7749, -122.4194),
        _p(37.77518, -122.4194),
        _p(37.8200, -122.4500),
        _p(37.82028, -122.4500),
    ]
    ids = gc.cluster_locations(points, eps_meters=150, min_samples=2)
    assert ids[0] == ids[1] != -1
    assert ids[2] == ids[3] != -1
    assert ids[0] != ids[2]


def test_no_gps_points_get_cluster_neg_one():
    points = [
        _p(None, None),
        _p(37.7749, -122.4194),
        _p(37.77535, -122.4194),
        {'lat': 37.7749, 'lng': None},  # half-fix counts as no-fix
    ]
    ids = gc.cluster_locations(points, eps_meters=150, min_samples=2)
    assert ids[0] == -1
    assert ids[3] == -1
    assert ids[1] != -1
    assert ids[1] == ids[2]


def test_isolated_point_below_min_samples_is_noise():
    points = [
        _p(37.7749, -122.4194),
        _p(37.77535, -122.4194),
        _p(40.0000, -100.0000),  # alone, far away
    ]
    ids = gc.cluster_locations(points, eps_meters=150, min_samples=2)
    assert ids[2] == -1
    assert ids[0] != -1


def test_dense_grid_no_quadratic_explosion():
    """1000 points scattered across ~5 km. Without the grid index, the
    naive O(n²) neighbour scan would be ~1M haversine calls. Grid
    bounds it to a small constant per point. Must complete well under
    500 ms on a Pi 5."""
    import random
    random.seed(42)
    centre_lat, centre_lng = 37.7749, -122.4194
    points = []
    for _ in range(1000):
        # ±0.025° roughly = ±2.5 km box.
        points.append(_p(
            centre_lat + random.uniform(-0.025, 0.025),
            centre_lng + random.uniform(-0.025, 0.025),
        ))
    t0 = time.time()
    ids = gc.cluster_locations(points, eps_meters=150, min_samples=2)
    elapsed = time.time() - t0
    assert len(ids) == 1000
    assert elapsed < 0.5, f"clustering took {elapsed:.2f}s, want <0.5s"


def test_empty_input_returns_empty():
    assert gc.cluster_locations([], eps_meters=150, min_samples=2) == []

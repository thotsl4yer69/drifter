#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Geographic clustering for BLE detections (Phase 4.8.2)

Pure-python DBSCAN-style clustering on (lat, lng) detections. No
scikit-learn dependency — the runtime is on a Pi 5, and pulling in
sklearn for a 200-line algorithm is overkill.

Distance is haversine (great-circle, accurate to ~0.5% globally; far
better than the 150 m epsilon we cluster at). Neighbour search is
backed by a ~eps-sized grid index so the worst-case isn't O(n²) on
busy windows.

UNCAGED TECHNOLOGY — EST 1991
"""
from __future__ import annotations

import math

EARTH_RADIUS_M = 6_371_000


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in metres."""
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def cluster_locations(points: list[dict],
                      eps_meters: float = 150.0,
                      min_samples: int = 2) -> list[int]:
    """DBSCAN clustering. `points` is a list of dicts with 'lat'/'lng'
    (None allowed for ungeolocated detections). Returns a list of the
    same length where each entry is the cluster_id of the corresponding
    point, or -1 for noise (insufficient density) or no-GPS rows.

    Implementation:
      1. Stash every (lat, lng)-bearing point into a grid keyed by
         (round(lat/eps_deg), round(lng/eps_deg)). Cell size ~= eps,
         so neighbour search only walks the 3x3 window around a cell.
      2. Standard DBSCAN expansion. Border points join the first
         cluster that pulls them in.
    """
    n = len(points)
    cluster_ids: list[int] = [-1] * n

    # Index of points that actually have GPS — others stay at -1.
    valid = [
        i for i, p in enumerate(points)
        if p.get('lat') is not None and p.get('lng') is not None
    ]
    if not valid:
        return cluster_ids

    # Degree-equivalent of eps. 1° latitude ≈ 111 000 m everywhere.
    # Longitude shrinks toward the poles; using the latitude figure
    # over-buckets near the poles (smaller cells) which is fine — we
    # still check exact distance inside the cell window.
    eps_deg = eps_meters / 111_000.0

    grid: dict[tuple[int, int], list[int]] = {}
    for i in valid:
        p = points[i]
        key = (int(p['lat'] / eps_deg), int(p['lng'] / eps_deg))
        grid.setdefault(key, []).append(i)

    def neighbours(i: int) -> list[int]:
        p = points[i]
        kx = int(p['lat'] / eps_deg)
        ky = int(p['lng'] / eps_deg)
        out: list[int] = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                bucket = grid.get((kx + dx, ky + dy))
                if not bucket:
                    continue
                for j in bucket:
                    if j == i:
                        continue
                    q = points[j]
                    if haversine_m(p['lat'], p['lng'],
                                   q['lat'], q['lng']) <= eps_meters:
                        out.append(j)
        return out

    visited: set[int] = set()
    next_cid = 0

    for i in valid:
        if i in visited:
            continue
        visited.add(i)
        nbrs = neighbours(i)
        if len(nbrs) + 1 < min_samples:
            # Noise — but a future expansion may pull this in as a
            # border point.
            continue
        cid = next_cid
        next_cid += 1
        cluster_ids[i] = cid
        seeds = list(nbrs)
        while seeds:
            j = seeds.pop()
            if j not in visited:
                visited.add(j)
                nb2 = neighbours(j)
                if len(nb2) + 1 >= min_samples:
                    seeds.extend(nb2)
            if cluster_ids[j] == -1:
                cluster_ids[j] = cid

    return cluster_ids

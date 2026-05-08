#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Persistent contact scoring (Phase 4.8.3)

Turns ble_history.db into intelligence: which identities keep showing
up across unrelated drives and locations? A MAC seen once is noise.
The same identity at three stops over four days is a follower.

This is heuristic counter-surveillance, not statistical inference.
False positives are expected:
  - carpool partners
  - frequent shared locations (gym, partner's place, the same coffee
    shop you go to every Tuesday)
  - your own gear riding along
The persistence score gives you a ranked list to inspect, not a
verdict.

Filtering rules (applied before scoring):
  - detection_count < 3 → drop. Single-digit hits aren't a pattern.
  - unique_drive_ids < 2 → drop. Same drive = same locality, not
    following.
  - all detections in cluster_id == -1 → drop. No GPS means we can't
    tell whether the device travelled with us or just sat in the
    same WiFi soup.

Score = unique_geo_clusters * unique_drive_ids * confidence

Tier:
  high   — score ≥ 6 AND confidence ≥ 0.7
  medium — score ≥ 3
  weak   — anything else passing filters

UNCAGED TECHNOLOGY — EST 1991
"""
from __future__ import annotations

from typing import Optional

import ble_geocluster
import ble_history
import ble_identity


def score_persistent_contacts(conn,
                              since_ts: Optional[float] = None,
                              until_ts: Optional[float] = None
                              ) -> tuple[list[dict], int]:
    """Return (contacts, noise_excluded). `contacts` is sorted by
    follower_score desc; noise_excluded is how many distinct
    identities were filtered out by the rules above (useful for the
    dashboard panel's empty state)."""
    rows = ble_history.query_history(
        conn, since=since_ts, until=until_ts, limit=100_000,
    )
    if not rows:
        return [], 0

    annotated: list[dict] = []
    for r in rows:
        identity, confidence = ble_identity.compute_identity(r)
        annotated.append({**r, 'identity': identity, 'confidence': confidence})

    # Cluster everything together so cluster_ids are comparable across
    # identities — "two devices in the same parking lot" should land
    # in the same cluster regardless of which target they matched.
    cluster_ids = ble_geocluster.cluster_locations(annotated)
    for i, row in enumerate(annotated):
        row['cluster_id'] = cluster_ids[i]

    by_identity: dict[str, list[dict]] = {}
    for row in annotated:
        by_identity.setdefault(row['identity'], []).append(row)

    contacts: list[dict] = []
    noise_excluded = 0

    for identity, hits in by_identity.items():
        detection_count = len(hits)
        unique_drives = len({h['drive_id'] for h in hits})
        non_noise = [h for h in hits if h['cluster_id'] >= 0]
        unique_clusters = len({h['cluster_id'] for h in non_noise})

        # Filter rules.
        if detection_count < 3:
            noise_excluded += 1
            continue
        if unique_drives < 2:
            noise_excluded += 1
            continue
        if not non_noise:
            noise_excluded += 1
            continue
        # Phase 4.8.1 — require >=2 distinct geo clusters. A stable
        # device sitting at one location and pinged across multiple
        # drives (neighbour's phone at home, kit at the workshop) is
        # locality, not following. The score multiplies clusters in
        # so 1-cluster cases would be tier=weak anyway, but keeping
        # them out of the contacts list cleans the dashboard panel.
        if unique_clusters < 2:
            noise_excluded += 1
            continue

        # Worst-case confidence — if any contributing detection used
        # the weak fallback, the whole identity is treated as weak.
        confidence = min(h['confidence'] for h in hits)
        first_seen = min(h['ts'] for h in hits)
        last_seen = max(h['ts'] for h in hits)
        span_hours = max(0.0, (last_seen - first_seen) / 3600.0)
        score = unique_clusters * unique_drives * confidence

        if score >= 6 and confidence >= 0.7:
            tier = 'high'
        elif score >= 3:
            tier = 'medium'
        else:
            tier = 'weak'

        # Pick the most-frequent target as the representative label.
        target_counts: dict[str, int] = {}
        for h in hits:
            target_counts[h['target']] = target_counts.get(h['target'], 0) + 1
        target = max(target_counts.items(), key=lambda kv: kv[1])[0]

        contacts.append({
            'identity':            identity,
            'target':              target,
            'detection_count':     detection_count,
            'unique_drive_ids':    unique_drives,
            'unique_geo_clusters': unique_clusters,
            'first_seen':          first_seen,
            'last_seen':           last_seen,
            'span_hours':          span_hours,
            'confidence':          confidence,
            'follower_score':      score,
            'tier':                tier,
        })

    contacts.sort(key=lambda c: c['follower_score'], reverse=True)
    return contacts, noise_excluded


def get_persistent_contact_summary(conn,
                                   window_seconds: float = 7 * 86400,
                                   now_ts: Optional[float] = None) -> str:
    """Short string for Vivi's prompt context. Empty when nothing
    above the weak tier has been seen in the window — Vivi is
    on-demand only in this phase, no proactive comments."""
    import time as _t
    now_ts = now_ts if now_ts is not None else _t.time()
    contacts, _ = score_persistent_contacts(
        conn, since_ts=now_ts - window_seconds, until_ts=now_ts,
    )
    if not contacts:
        return ''

    high = [c for c in contacts if c['tier'] == 'high']
    medium = [c for c in contacts if c['tier'] == 'medium']
    weak = [c for c in contacts if c['tier'] == 'weak']

    bits = [f"{len(contacts)} persistent contact{'s' if len(contacts) != 1 else ''} this week"]
    if high:
        top = high[0]
        bits.append(
            f"{len(high)} high "
            f"({top['target']}, {top['unique_drive_ids']} drives, "
            f"{top['unique_geo_clusters']} locations)"
        )
    if medium:
        bits.append(f"{len(medium)} medium")
    if weak:
        bits.append(f"{len(weak)} weak")
    return ' — '.join(bits) + '.'

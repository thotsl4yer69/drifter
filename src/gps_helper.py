#!/usr/bin/env python3
"""
MZ1312 DRIFTER — GPS Fix Helper

Shared accessor for the latest GPS fix written by drifter-gps (gpsd) or
POST /api/gps/manual. Persisted at /opt/drifter/state/gps.json. Used by
every persistence path that wants to geo-tag a record (flipper captures,
CAN discovery CSVs, anomaly alerts, session-recorder JSONL lines).

UNCAGED TECHNOLOGY — EST 1991
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)

# Same file feeds.origin() reads. Single source of truth for vehicle position.
GPS_STATE_PATH = Path('/opt/drifter/state/gps.json')

# In-memory cache so callers hammering this at 50Hz don't keep re-reading
# the JSON file. The cache is intentionally short — a real fix updates
# every ~1s and we want to track motion, not pin a stale read.
_CACHE_TTL_SEC = 5.0
_MAX_FIX_AGE_SEC = 120.0

_cache: dict = {'ts': 0.0, 'fix': None}


def current_fix(max_age_sec: float = _MAX_FIX_AGE_SEC) -> dict | None:
    """Return the latest GPS fix, or None when no fresh fix is available.

    Shape (when present):
      {'lat': float, 'lon': float, 'speed': float | None,
       'heading': float | None, 'gps_ts': float}

    Callers MUST treat None as "no fix" and persist the record without
    geo fields rather than fabricating a position.
    """
    now = time.time()
    if _cache['fix'] is not None and (now - _cache['ts']) < _CACHE_TTL_SEC:
        return _cache['fix']
    fix = _read_fix(max_age_sec, now)
    _cache['ts'] = now
    _cache['fix'] = fix
    return fix


def _read_fix(max_age_sec: float, now: float) -> dict | None:
    try:
        j = json.loads(GPS_STATE_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    try:
        if not j.get('fix'):
            return None
        ts = float(j.get('ts', 0))
        if (now - ts) > max_age_sec:
            return None
        lat = float(j['lat'])
        # Accept either 'lon' or 'lng' (browser POST uses 'lng', gpsd uses 'lon').
        if 'lon' in j:
            lon = float(j['lon'])
        elif 'lng' in j:
            lon = float(j['lng'])
        else:
            return None
    except (KeyError, TypeError, ValueError):
        return None
    speed = j.get('speed')
    heading = j.get('heading') or j.get('track')
    try:
        speed_f = float(speed) if speed is not None else None
    except (TypeError, ValueError):
        speed_f = None
    try:
        heading_f = float(heading) if heading is not None else None
    except (TypeError, ValueError):
        heading_f = None
    return {
        'lat': lat,
        'lon': lon,
        'speed': speed_f,
        'heading': heading_f,
        'gps_ts': ts,
    }


def annotate(record: dict) -> dict:
    """Merge the current GPS fix into a record dict.

    Returns the same dict (mutated) for caller convenience. Fields that
    are already present in the record are left alone — explicit caller
    values win over the cached fix. Missing fix → no-op.
    """
    if not isinstance(record, dict):
        return record
    fix = current_fix()
    if fix is None:
        return record
    for k, v in fix.items():
        record.setdefault(k, v)
    return record


def reset_cache() -> None:
    """Drop the in-memory cache. Test helper."""
    _cache['ts'] = 0.0
    _cache['fix'] = None

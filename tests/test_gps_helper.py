"""Tests for gps_helper.current_fix caching — including the no-fix path."""
from __future__ import annotations

import sys

sys.path.insert(0, 'src')

import gps_helper


def _reset_cache():
    gps_helper._cache = {'ts': 0.0, 'fix': None}


def test_no_fix_is_cached_within_ttl(monkeypatch):
    """A 'no fix' (None) result must be cached for the TTL window — otherwise
    the no-fix path re-reads gps.json on every call (50Hz disk thrash)."""
    _reset_cache()
    calls = {'n': 0}

    def fake_read(max_age_sec, now):
        calls['n'] += 1
        return None

    monkeypatch.setattr(gps_helper, '_read_fix', fake_read)
    assert gps_helper.current_fix() is None
    assert gps_helper.current_fix() is None
    assert calls['n'] == 1, "second call within TTL must hit the cache, not disk"


def test_fix_is_cached_within_ttl(monkeypatch):
    _reset_cache()
    calls = {'n': 0}
    fix = {'lat': -36.7, 'lon': 144.3, 'gps_ts': 1.0}

    def fake_read(max_age_sec, now):
        calls['n'] += 1
        return fix

    monkeypatch.setattr(gps_helper, '_read_fix', fake_read)
    assert gps_helper.current_fix() == fix
    assert gps_helper.current_fix() == fix
    assert calls['n'] == 1


def test_cache_expires_after_ttl(monkeypatch):
    """Once the TTL lapses, the next call must re-read."""
    _reset_cache()
    calls = {'n': 0}

    def fake_read(max_age_sec, now):
        calls['n'] += 1
        return None

    monkeypatch.setattr(gps_helper, '_read_fix', fake_read)
    gps_helper.current_fix()
    # Age the cache past the TTL.
    gps_helper._cache['ts'] -= (gps_helper._CACHE_TTL_SEC + 1.0)
    gps_helper.current_fix()
    assert calls['n'] == 2

"""Regression tests for logger drive-session distance + engine-off detection.

- Distance must cap the per-update time step so a stalled/bursting speed feed
  can't inject phantom kilometres.
- Engine-off must be detected via a consecutive low-RPM streak, so short drives
  finalize correctly (the old fixed-size ring buffer required >=600 samples to
  ever fill, so brief drives never ended).
"""
from __future__ import annotations

import sys

sys.path.insert(0, 'src')

import logger
from logger import ENGINE_OFF_SAMPLES, ENGINE_ON_RPM, DriveSession


def test_distance_caps_long_gap():
    s = DriveSession()
    s.start()
    # First speed sample at t=1000 establishes the baseline (no integration).
    s.update('drifter/engine/speed', 80, ts=1000.0)
    assert s.distance_km == 0.0
    # Next sample arrives 10 minutes later (stall/burst). Without the 5s cap
    # this would add 80 km/h * 600s ≈ 13.3 km; capped it's 80 * 5/3600 ≈ 0.111.
    s.update('drifter/engine/speed', 80, ts=1600.0)
    assert s.distance_km < 0.2


def test_distance_normal_step():
    s = DriveSession()
    s.start()
    s.update('drifter/engine/speed', 60, ts=2000.0)
    s.update('drifter/engine/speed', 60, ts=2001.0)  # 1 s @ 60 km/h
    # 60 km/h * 1/3600 h ≈ 0.0167 km
    assert abs(s.distance_km - (60 / 3600.0)) < 1e-6


def test_engine_off_detected_by_streak(monkeypatch):
    # Use the module-global session that detect_session_change mutates.
    monkeypatch.setattr(logger, 'session', DriveSession())
    client = type('C', (), {'publish': lambda *a, **k: None})()

    # Engine on -> session starts.
    logger.detect_session_change(ENGINE_ON_RPM + 200, client)
    assert logger.session.active

    # ENGINE_OFF_SAMPLES-1 low samples: still active.
    for _ in range(ENGINE_OFF_SAMPLES - 1):
        logger.detect_session_change(0, client)
    assert logger.session.active

    # One more low sample crosses the threshold -> session ends.
    logger.detect_session_change(0, client)
    assert not logger.session.active


def test_low_streak_resets_on_rev(monkeypatch):
    monkeypatch.setattr(logger, 'session', DriveSession())
    client = type('C', (), {'publish': lambda *a, **k: None})()
    logger.detect_session_change(ENGINE_ON_RPM + 200, client)

    for _ in range(ENGINE_OFF_SAMPLES - 1):
        logger.detect_session_change(0, client)
    # A rev resets the streak, so the session must survive a subsequent dip.
    logger.detect_session_change(ENGINE_ON_RPM + 500, client)
    assert logger.session.low_rpm_streak == 0
    logger.detect_session_change(0, client)
    assert logger.session.active

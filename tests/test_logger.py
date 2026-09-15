"""Regression tests for logger distance + poll-rate-independent session timing."""
from __future__ import annotations

import sys

sys.path.insert(0, 'src')

import logger
from logger import ENGINE_OFF_SECONDS, ENGINE_ON_RPM, DriveSession


def test_distance_caps_long_gap():
    s = DriveSession()
    s.start(999.0)
    s.update('drifter/engine/speed', 80, ts=1000.0)
    # First update integrates only the capped one-second interval from start.
    first = s.distance_km
    s.update('drifter/engine/speed', 80, ts=1600.0)
    assert s.distance_km - first < 0.2


def test_distance_normal_step():
    s = DriveSession()
    s.start(2000.0)
    s.update('drifter/engine/speed', 60, ts=2000.0)
    before = s.distance_km
    s.update('drifter/engine/speed', 60, ts=2001.0)
    assert abs((s.distance_km - before) - (60 / 3600.0)) < 1e-6


def test_engine_off_detected_by_elapsed_time(monkeypatch, tmp_path):
    monkeypatch.setattr(logger, 'session', DriveSession())
    monkeypatch.setattr(logger, 'SESSION_DIR', tmp_path)
    client = type('C', (), {'publish': lambda *a, **k: None})()

    logger.detect_session_change(ENGINE_ON_RPM + 200, client, now=1000.0)
    assert logger.session.active

    logger.detect_session_change(0, client, now=1000.0 + ENGINE_OFF_SECONDS - 0.1)
    assert logger.session.active

    logger.detect_session_change(0, client, now=1000.0 + ENGINE_OFF_SECONDS + 0.1)
    assert not logger.session.active


def test_rev_resets_engine_off_clock(monkeypatch, tmp_path):
    monkeypatch.setattr(logger, 'session', DriveSession())
    monkeypatch.setattr(logger, 'SESSION_DIR', tmp_path)
    client = type('C', (), {'publish': lambda *a, **k: None})()

    logger.detect_session_change(ENGINE_ON_RPM + 200, client, now=1000.0)
    logger.detect_session_change(0, client, now=1000.0 + ENGINE_OFF_SECONDS - 1)
    assert logger.session.active

    logger.detect_session_change(ENGINE_ON_RPM + 500, client, now=1020.0)
    assert logger.session.last_running_ts == 1020.0

    logger.detect_session_change(0, client, now=1020.0 + ENGINE_OFF_SECONDS - 0.1)
    assert logger.session.active
    logger.detect_session_change(0, client, now=1020.0 + ENGINE_OFF_SECONDS + 0.1)
    assert not logger.session.active


def test_non_rpm_engine_pid_refreshes_kline_liveness():
    s = DriveSession()
    s.start(1000.0)
    assert s.last_telemetry_ts == 1000.0
    s.update('drifter/engine/coolant', 90, ts=1025.0)
    assert s.last_telemetry_ts == 1025.0
    # Power-only adapter voltage is intentionally not treated as ECU liveness;
    # ATRV can continue after ignition/key-off on some installations.
    s.update('drifter/power/voltage', 12.4, ts=1030.0)
    assert s.last_telemetry_ts == 1025.0

"""Regression tests for logger distance + session timing + evidence retention."""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, 'src')

import logger
from logger import ENGINE_OFF_SECONDS, ENGINE_ON_RPM, DriveSession


def test_distance_caps_long_gap():
    s = DriveSession()
    s.start(999.0)
    s.update('drifter/engine/speed', 80, ts=1000.0)
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
    s.update('drifter/power/voltage', 12.4, ts=1030.0)
    assert s.last_telemetry_ts == 1025.0


def _write_sized(path, size, mtime):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'x' * size)
    os.utime(path, (mtime, mtime))


def test_cleanup_prunes_old_incident_bundle_but_preserves_newest(monkeypatch, tmp_path):
    log_dir = tmp_path / 'logs'
    incident_dir = log_dir / 'incidents'
    session_dir = log_dir / 'sessions'
    monkeypatch.setattr(logger, 'LOG_DIR', log_dir)
    monkeypatch.setattr(logger, 'INCIDENT_DIR', incident_dir)
    monkeypatch.setattr(logger, 'SESSION_DIR', session_dir)
    monkeypatch.setattr(logger, 'MAX_LOG_SIZE_MB', 0.003)
    monkeypatch.setattr(logger, 'INCIDENT_MIN_KEEP', 1)

    old_data = incident_dir / 'incident_20260101-old.jsonl.gz'
    old_summary = incident_dir / 'incident_20260101-old.json'
    new_data = incident_dir / 'incident_20260102-new.jsonl.gz'
    new_summary = incident_dir / 'incident_20260102-new.json'
    _write_sized(old_data, 1200, 1000)
    _write_sized(old_summary, 200, 1000)
    _write_sized(new_data, 1200, 2000)
    _write_sized(new_summary, 200, 2000)

    logger.cleanup_old_logs()

    assert not old_data.exists()
    assert not old_summary.exists()
    assert new_data.exists()
    assert new_summary.exists()


def test_cleanup_removes_stale_incident_temp_files(monkeypatch, tmp_path):
    log_dir = tmp_path / 'logs'
    incident_dir = log_dir / 'incidents'
    session_dir = log_dir / 'sessions'
    monkeypatch.setattr(logger, 'LOG_DIR', log_dir)
    monkeypatch.setattr(logger, 'INCIDENT_DIR', incident_dir)
    monkeypatch.setattr(logger, 'SESSION_DIR', session_dir)
    monkeypatch.setattr(logger, 'STALE_TEMP_SECONDS', 10.0)

    temp = incident_dir / 'incident_x.json.tmp.123'
    _write_sized(temp, 20, time.time() - 100)

    logger.cleanup_old_logs()

    assert not temp.exists()

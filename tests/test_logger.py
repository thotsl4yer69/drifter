"""Regression tests for logger distance + poll-rate-independent session timing."""
from __future__ import annotations

import json
import sys

sys.path.insert(0, 'src')

import logger
from config import TOPICS
from logger import ENGINE_OFF_SECONDS, ENGINE_ON_RPM, DriveSession


class Msg:
    def __init__(self, topic, payload):
        self.topic = topic
        self.payload = json.dumps(payload).encode()


class Client:
    def __init__(self):
        self.published = []

    def publish(self, *args, **kwargs):
        self.published.append((args, kwargs))


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


def test_missing_voltage_is_none_not_fake_99_volts():
    s = DriveSession()
    s.start(1000.0)
    assert s.summary()['min_voltage'] is None
    s.update('drifter/power/voltage', 13.9, ts=1001.0)
    s.update('drifter/power/voltage', 13.2, ts=1002.0)
    assert s.summary()['min_voltage'] == 13.2


def test_first_running_rpm_is_not_lost(monkeypatch):
    monkeypatch.setattr(logger, 'session', DriveSession())
    monkeypatch.setattr(logger, '_process_blackbox', lambda *args, **kwargs: None)
    logger.latest_dtcs.clear()
    client = Client()

    logger.on_message(client, None, Msg(TOPICS['rpm'], {'value': 825, 'unit': 'rpm'}))

    assert logger.session.active
    assert logger.session.max_rpm == 825


def test_retained_and_live_dtcs_are_carried_into_session(monkeypatch):
    monkeypatch.setattr(logger, 'session', DriveSession())
    monkeypatch.setattr(logger, '_process_blackbox', lambda *args, **kwargs: None)
    logger.latest_dtcs.clear()
    client = Client()

    # Retained DTC state may arrive before the engine/session starts.
    logger.on_message(client, None, Msg(TOPICS['dtc'], {
        'stored': ['P0171'], 'pending': [{'code': 'P0174'}],
    }))
    logger.on_message(client, None, Msg(TOPICS['rpm'], {'value': 700}))
    # A later poll during the drive adds newly observed codes.
    logger.on_message(client, None, Msg(TOPICS['dtc'], {
        'stored': ['P0171', 'P0300'], 'pending': [],
    }))

    assert json.loads(logger.session.summary()['dtcs_seen']) == ['P0171', 'P0174', 'P0300']


def test_alert_level_payload_updates_session_counters(monkeypatch):
    monkeypatch.setattr(logger, 'session', DriveSession())
    monkeypatch.setattr(logger, '_process_blackbox', lambda *args, **kwargs: None)
    logger.session.start(1000.0)
    client = Client()

    logger.on_message(client, None, Msg(TOPICS['alert_level'], {'level': 3, 'name': 'RED'}))

    assert logger.session.alert_count == 1
    assert logger.session.highest_alert == 3


def test_session_end_payload_carries_active_incident_deadline(monkeypatch, tmp_path):
    monkeypatch.setattr(logger, 'session', DriveSession())
    monkeypatch.setattr(logger, 'SESSION_DIR', tmp_path)
    monkeypatch.setattr(logger.blackbox, 'status', lambda: {
        'active': True, 'id': 'incident-1', 'end_at': 1060.0,
    })
    logger.session.start(1000.0)
    client = Client()

    logger._finish_session(client, now=1030.0)

    payloads = [json.loads(args[1]) for args, _kwargs in client.published
                if args and args[0] == TOPICS['drive_session']]
    assert payloads
    assert payloads[-1]['incident_active'] is True
    assert payloads[-1]['incident_end_at'] == 1060.0

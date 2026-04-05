#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Database Layer Tests
Tests thread-local connection pooling and CRUD operations.
Run: pytest tests/test_db.py -v
UNCAGED TECHNOLOGY — EST 1991
"""

import time
import json
import threading
import pytest
from unittest.mock import patch
from pathlib import Path

import db


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    """Use a temporary database for each test."""
    test_db = tmp_path / 'test_drifter.db'
    monkeypatch.setattr('config.DB_PATH', test_db)
    monkeypatch.setattr('config.REPORTS_DIR', tmp_path / 'reports')

    # Clear any cached thread-local connection
    if hasattr(db._local, 'conn'):
        del db._local.conn

    db.init_db()
    yield test_db

    # Cleanup thread-local connection
    if hasattr(db._local, 'conn'):
        try:
            db._local.conn.close()
        except Exception:
            pass
        del db._local.conn


def _make_session(session_id='test-001', **overrides):
    """Create a session dict with defaults."""
    data = {
        'session_id': session_id,
        'start_ts': time.time() - 3600,
        'end_ts': time.time(),
        'distance_km': 15.5,
        'duration_seconds': 1800,
        'max_rpm': 4500.0,
        'max_speed': 80.0,
        'max_coolant': 92.0,
        'min_voltage': 13.8,
        'warmup_seconds': 120.0,
        'avg_stft_b1': 1.5,
        'avg_stft_b2': -0.5,
        'avg_ltft_b1': 2.0,
        'avg_ltft_b2': -1.0,
        'idle_rpm_stddev': 15.0,
        'dtcs_seen': '[]',
        'alert_count': 0,
    }
    data.update(overrides)
    return data


class TestInitDB:
    def test_creates_tables(self, temp_db):
        """init_db should create sessions, anomaly_events, and reports tables."""
        conn = db._conn()
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        names = {r['name'] for r in tables}
        assert 'sessions' in names
        assert 'anomaly_events' in names
        assert 'reports' in names


class TestSessionCRUD:
    def test_insert_and_retrieve_session(self):
        """Insert a session and retrieve it."""
        session = _make_session()
        db.insert_session(session)
        rows = db.get_recent_sessions(5)
        assert len(rows) == 1
        assert rows[0]['session_id'] == 'test-001'
        assert rows[0]['distance_km'] == 15.5

    def test_upsert_session(self):
        """INSERT OR REPLACE should update existing sessions."""
        db.insert_session(_make_session(distance_km=10.0))
        db.insert_session(_make_session(distance_km=20.0))
        rows = db.get_recent_sessions(5)
        assert len(rows) == 1
        assert rows[0]['distance_km'] == 20.0


class TestAnomalyEvents:
    def test_insert_and_get_anomalies(self):
        """Insert anomaly events and retrieve by session."""
        db.insert_session(_make_session())
        db.insert_anomaly_event({
            'session_id': 'test-001',
            'ts': time.time(),
            'sensor': 'coolant',
            'value': 110.0,
            'z_score': 3.5,
            'severity': 'high',
            'context_json': '{}',
        })
        anomalies = db.get_session_anomalies('test-001')
        assert len(anomalies) == 1
        assert anomalies[0]['sensor'] == 'coolant'


class TestBaseline:
    def test_baseline_excludes_current_session(self):
        """Baseline should average other sessions, not the current one."""
        for i in range(5):
            db.insert_session(_make_session(
                session_id=f'session-{i}',
                max_coolant=90.0 + i,
            ))
        baseline = db.get_baseline('session-2', n=10)
        assert baseline is not None
        assert baseline['session_count'] == 4  # excluded session-2

    def test_baseline_returns_none_when_empty(self):
        """Baseline should return None if no other sessions exist."""
        db.insert_session(_make_session())
        baseline = db.get_baseline('test-001')
        assert baseline is None


class TestConnectionPooling:
    def test_same_thread_reuses_connection(self):
        """Same thread should get the same connection object."""
        conn1 = db._conn()
        conn2 = db._conn()
        assert conn1 is conn2

    def test_different_threads_get_different_connections(self):
        """Different threads should get independent connections."""
        results = {}

        def worker(name):
            results[name] = id(db._conn())

        t1 = threading.Thread(target=worker, args=('t1',))
        t2 = threading.Thread(target=worker, args=('t2',))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert results['t1'] != results['t2']

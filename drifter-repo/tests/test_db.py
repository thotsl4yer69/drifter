# tests/test_db.py
import sqlite3
import tempfile
import os
import pytest

import sys
sys.path.insert(0, 'src')

@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Temporary SQLite DB for tests."""
    db_file = tmp_path / "test.db"
    import config
    monkeypatch.setattr(config, 'DB_PATH', db_file)
    monkeypatch.setattr(config, 'REPORTS_DIR', tmp_path / "reports")
    import db
    import importlib
    importlib.reload(db)
    db.init_db()
    return db

def test_init_creates_tables(tmp_db):
    conn = sqlite3.connect(tmp_db.DB_PATH)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    conn.close()
    assert {'sessions', 'anomaly_events', 'reports'}.issubset(tables)

def test_insert_and_get_session(tmp_db):
    session = {
        'session_id': '20260315_140000',
        'start_ts': 1000.0, 'end_ts': 2000.0,
        'distance_km': 12.4, 'duration_seconds': 1000.0,
        'max_rpm': 3200.0, 'max_speed': 80.0,
        'max_coolant': 98.0, 'min_voltage': 13.1,
        'warmup_seconds': 480.0,
        'avg_stft_b1': 6.2, 'avg_stft_b2': 5.8,
        'avg_ltft_b1': 3.1, 'avg_ltft_b2': 2.9,
        'idle_rpm_stddev': 45.0,
        'dtcs_seen': '["P0171","P0174"]',
        'alert_count': 2,
    }
    tmp_db.insert_session(session)
    result = tmp_db.get_recent_sessions(5)
    assert len(result) == 1
    assert result[0]['session_id'] == '20260315_140000'

def test_insert_anomaly_event(tmp_db):
    import json
    tmp_db.insert_session({
        'session_id': 'S1', 'start_ts': 0, 'end_ts': 1,
        'distance_km': 0, 'duration_seconds': 0,
        'max_rpm': 0, 'max_speed': 0, 'max_coolant': 0,
        'min_voltage': 0, 'warmup_seconds': 0,
        'avg_stft_b1': 0, 'avg_stft_b2': 0,
        'avg_ltft_b1': 0, 'avg_ltft_b2': 0,
        'idle_rpm_stddev': 0, 'dtcs_seen': '[]', 'alert_count': 0,
    })
    tmp_db.insert_anomaly_event({
        'session_id': 'S1', 'ts': 500.0, 'sensor': 'stft_b1',
        'value': 14.2, 'z_score': 3.8, 'severity': 'high',
        'context_json': json.dumps({'rpm': 1200}),
    })
    events = tmp_db.get_session_anomalies('S1')
    assert len(events) == 1
    assert events[0]['sensor'] == 'stft_b1'

def test_insert_report(tmp_db):
    import json
    tmp_db.insert_session({
        'session_id': 'S2', 'start_ts': 0, 'end_ts': 1,
        'distance_km': 0, 'duration_seconds': 0,
        'max_rpm': 0, 'max_speed': 0, 'max_coolant': 0,
        'min_voltage': 0, 'warmup_seconds': 0,
        'avg_stft_b1': 0, 'avg_stft_b2': 0,
        'avg_ltft_b1': 0, 'avg_ltft_b2': 0,
        'idle_rpm_stddev': 0, 'dtcs_seen': '[]', 'alert_count': 0,
    })
    tmp_db.insert_report({
        'session_id': 'S2', 'generated_at': 1000.0,
        'model_used': 'groq/test', 'report_json': '{}', 'tokens_used': 100,
    })
    reports = tmp_db.get_recent_reports(5)
    assert len(reports) == 1
    assert reports[0]['model_used'] == 'groq/test'

def test_get_baseline_returns_averages(tmp_db):
    for i in range(3):
        tmp_db.insert_session({
            'session_id': f'S{i}', 'start_ts': i, 'end_ts': i+1,
            'distance_km': 10.0, 'duration_seconds': 600.0,
            'max_rpm': 3000.0, 'max_speed': 70.0,
            'max_coolant': 95.0, 'min_voltage': 13.5,
            'warmup_seconds': float(300 + i * 60),
            'avg_stft_b1': float(2 + i), 'avg_stft_b2': float(2 + i),
            'avg_ltft_b1': 1.0, 'avg_ltft_b2': 1.0,
            'idle_rpm_stddev': 30.0, 'dtcs_seen': '[]', 'alert_count': 0,
        })
    baseline = tmp_db.get_baseline(exclude_session_id='S2', n=10)
    assert baseline is not None
    # avg_stft_b1 for S0=2.0, S1=3.0 (S2 excluded) -> avg=2.5
    assert abs(baseline['avg_stft_b1'] - 2.5) < 0.01

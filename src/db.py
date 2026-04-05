#!/usr/bin/env python3
"""
MZ1312 DRIFTER — SQLite Database Layer
Single source of truth for all persistent analyst data.
UNCAGED TECHNOLOGY — EST 1991
"""

import sqlite3
import logging
import threading
from pathlib import Path
from typing import Optional

import config
from config import DB_PATH, REPORTS_DIR

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    start_ts REAL, end_ts REAL,
    distance_km REAL, duration_seconds REAL,
    max_rpm REAL, max_speed REAL,
    max_coolant REAL, min_voltage REAL,
    warmup_seconds REAL,
    avg_stft_b1 REAL, avg_stft_b2 REAL,
    avg_ltft_b1 REAL, avg_ltft_b2 REAL,
    idle_rpm_stddev REAL,
    dtcs_seen TEXT,
    alert_count INTEGER
);

CREATE TABLE IF NOT EXISTS anomaly_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    ts REAL, sensor TEXT,
    value REAL, z_score REAL,
    severity TEXT, context_json TEXT,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    generated_at REAL,
    model_used TEXT,
    report_json TEXT,
    tokens_used INTEGER,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);
"""

# Thread-local storage: each thread gets its own reusable connection
# instead of opening+closing the DB file on every single query.
_local = threading.local()


def _conn():
    """Return a per-thread reusable connection with row_factory."""
    conn = getattr(_local, 'conn', None)
    if conn is None:
        conn = sqlite3.connect(str(config.DB_PATH), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        _local.conn = conn
    return conn


def init_db():
    """Create tables if they don't exist."""
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    conn = _conn()
    conn.executescript(SCHEMA)
    conn.commit()
    log.info(f"DB initialised at {config.DB_PATH}")


def insert_session(session: dict):
    sql = """INSERT OR REPLACE INTO sessions VALUES (
        :session_id, :start_ts, :end_ts, :distance_km, :duration_seconds,
        :max_rpm, :max_speed, :max_coolant, :min_voltage, :warmup_seconds,
        :avg_stft_b1, :avg_stft_b2, :avg_ltft_b1, :avg_ltft_b2,
        :idle_rpm_stddev, :dtcs_seen, :alert_count
    )"""
    conn = _conn()
    conn.execute(sql, session)
    conn.commit()


def insert_anomaly_event(event: dict):
    sql = """INSERT INTO anomaly_events
        (session_id, ts, sensor, value, z_score, severity, context_json)
        VALUES (:session_id, :ts, :sensor, :value, :z_score, :severity, :context_json)"""
    conn = _conn()
    conn.execute(sql, event)
    conn.commit()


def insert_report(report: dict):
    sql = """INSERT INTO reports
        (session_id, generated_at, model_used, report_json, tokens_used)
        VALUES (:session_id, :generated_at, :model_used, :report_json, :tokens_used)"""
    conn = _conn()
    conn.execute(sql, report)
    conn.commit()


def get_session_anomalies(session_id: str) -> list:
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM anomaly_events WHERE session_id=? ORDER BY ts",
        (session_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_recent_sessions(n: int) -> list:
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM sessions ORDER BY start_ts DESC LIMIT ?", (n,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_recent_reports(n: int) -> list:
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM reports ORDER BY generated_at DESC LIMIT ?", (n,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_baseline(exclude_session_id: str, n: int = 10) -> Optional[dict]:
    """Return column averages for the last N sessions excluding the current one."""
    conn = _conn()
    rows = conn.execute(
        """SELECT * FROM sessions WHERE session_id != ?
           ORDER BY start_ts DESC LIMIT ?""",
        (exclude_session_id, n)
    ).fetchall()
    if not rows:
        return None
    cols = ['distance_km', 'duration_seconds', 'max_rpm', 'max_speed',
            'max_coolant', 'min_voltage', 'warmup_seconds',
            'avg_stft_b1', 'avg_stft_b2', 'avg_ltft_b1', 'avg_ltft_b2',
            'idle_rpm_stddev', 'alert_count']
    result = {}
    for col in cols:
        vals = [r[col] for r in rows if r[col] is not None]
        result[col] = sum(vals) / len(vals) if vals else None
    result['session_count'] = len(rows)
    return result

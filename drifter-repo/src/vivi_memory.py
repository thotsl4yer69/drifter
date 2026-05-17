#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Vivi Persistent Memory
SQLite-backed conversation memory and long-term notes for Vivi v2.
Stores per-session turns plus a small set of pinned facts the user wants
the assistant to remember across boots (favourite stations, mechanic
contacts, parking spots, etc.).
UNCAGED TECHNOLOGY — EST 1991
"""

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import List, Optional

from config import DRIFTER_DIR, VIVI2_HISTORY_TURNS, VIVI2_MEMORY_MAX_ENTRIES

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [VIVIMEM] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

DB_PATH = DRIFTER_DIR / "memory" / "vivi.db"
_lock = threading.Lock()


SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    ts REAL NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id);
CREATE INDEX IF NOT EXISTS idx_turns_ts ON turns(ts);

CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    tag TEXT,
    content TEXT NOT NULL,
    meta_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_facts_tag ON facts(tag);
"""


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _lock, _connect() as conn:
        conn.executescript(SCHEMA)


def append_turn(session_id: str, role: str, content: str) -> None:
    if role not in ("user", "assistant", "system"):
        log.warning(f"Refusing turn with role={role}")
        return
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO turns (session_id, ts, role, content) VALUES (?,?,?,?)",
            (session_id, time.time(), role, content[:8000]),
        )


def history(session_id: Optional[str] = None, n: int = VIVI2_HISTORY_TURNS) -> List[dict]:
    with _lock, _connect() as conn:
        if session_id:
            rows = conn.execute(
                "SELECT role, content, ts FROM turns WHERE session_id=? "
                "ORDER BY id DESC LIMIT ?", (session_id, n)).fetchall()
        else:
            rows = conn.execute(
                "SELECT role, content, ts FROM turns ORDER BY id DESC LIMIT ?",
                (n,)).fetchall()
    return [dict(r) for r in reversed(rows)]


def remember(content: str, tag: str = "", meta: Optional[dict] = None) -> int:
    with _lock, _connect() as conn:
        cursor = conn.execute(
            "INSERT INTO facts (ts, tag, content, meta_json) VALUES (?,?,?,?)",
            (time.time(), tag, content[:2000], json.dumps(meta or {})),
        )
        new_id = cursor.lastrowid
        # Prune to VIVI2_MEMORY_MAX_ENTRIES (oldest untagged go first)
        count = conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
        if count > VIVI2_MEMORY_MAX_ENTRIES:
            to_drop = count - VIVI2_MEMORY_MAX_ENTRIES
            conn.execute(
                "DELETE FROM facts WHERE id IN ("
                "  SELECT id FROM facts WHERE tag='' ORDER BY ts ASC LIMIT ?"
                ")", (to_drop,))
    return new_id


def recall(query: Optional[str] = None, tag: Optional[str] = None, n: int = 8) -> List[dict]:
    with _lock, _connect() as conn:
        sql = "SELECT id, tag, content, meta_json, ts FROM facts"
        params: list = []
        clauses = []
        if tag:
            clauses.append("tag = ?")
            params.append(tag)
        if query:
            clauses.append("content LIKE ?")
            params.append(f"%{query}%")
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(n)
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def forget(fact_id: int) -> bool:
    with _lock, _connect() as conn:
        result = conn.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
        return result.rowcount > 0


def export_session(session_id: str) -> dict:
    return {
        'session_id': session_id,
        'turns': history(session_id, n=10_000),
        'exported_at': time.time(),
    }

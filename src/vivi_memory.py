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
from collections.abc import Iterator
from contextlib import contextmanager

from config import DRIFTER_DIR, VIVI2_HISTORY_TURNS, VIVI2_MEMORY_MAX_ENTRIES

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [VIVIMEM] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

DB_PATH = DRIFTER_DIR / "memory" / "vivi.db"
_lock = threading.Lock()

# Keep at most this many turns in the turns table — prevents unbounded growth
# across long-running sessions. Tuned to ~50× the per-prompt window so we can
# still rebuild a session's history; older turns get pruned on append.
TURNS_MAX_ROWS = max(2000, VIVI2_HISTORY_TURNS * 50)

# Prune every N inserts to keep the DELETE cost amortised low.
_TURNS_PRUNE_INTERVAL = 200
_turns_since_prune = 0


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


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    """Connection context manager that actually closes on exit.

    sqlite3.Connection's own ``with`` block only commits/rollbacks — it does
    NOT close. We wrap it so every call site gets a closed handle and the
    process never leaks file descriptors.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=5.0)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass


def init_db() -> None:
    with _lock:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=5.0)
        try:
            # WAL lets readers and writers progress in parallel and survives
            # crashes better than the default rollback journal.
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
            except sqlite3.Error as e:
                log.debug(f"pragma setup: {e}")
            conn.executescript(SCHEMA)
            conn.commit()
        finally:
            conn.close()


def _maybe_prune_turns(conn: sqlite3.Connection) -> None:
    global _turns_since_prune
    _turns_since_prune += 1
    if _turns_since_prune < _TURNS_PRUNE_INTERVAL:
        return
    _turns_since_prune = 0
    try:
        row = conn.execute("SELECT COUNT(*) FROM turns").fetchone()
        total = row[0] if row else 0
        if total > TURNS_MAX_ROWS:
            drop = total - TURNS_MAX_ROWS
            conn.execute(
                "DELETE FROM turns WHERE id IN ("
                "  SELECT id FROM turns ORDER BY id ASC LIMIT ?"
                ")", (drop,))
            log.info(f"pruned {drop} old turns")
    except sqlite3.Error as e:
        log.warning(f"prune turns failed: {e}")


def append_turn(session_id: str, role: str, content: str) -> None:
    if role not in ("user", "assistant", "system"):
        log.warning(f"refusing turn with role={role}")
        return
    if not session_id or not content:
        return
    try:
        with _lock, _conn() as conn:
            conn.execute(
                "INSERT INTO turns (session_id, ts, role, content) VALUES (?,?,?,?)",
                (session_id, time.time(), role, content[:8000]),
            )
            _maybe_prune_turns(conn)
    except sqlite3.Error as e:
        log.warning(f"append_turn failed: {e}")


def history(session_id: str | None = None, n: int = VIVI2_HISTORY_TURNS) -> list[dict]:
    n = max(1, int(n))
    try:
        with _lock, _conn() as conn:
            if session_id:
                rows = conn.execute(
                    "SELECT role, content, ts FROM turns WHERE session_id=? "
                    "ORDER BY id DESC LIMIT ?", (session_id, n)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT role, content, ts FROM turns ORDER BY id DESC LIMIT ?",
                    (n,)).fetchall()
        return [dict(r) for r in reversed(rows)]
    except sqlite3.Error as e:
        log.warning(f"history failed: {e}")
        return []


def remember(content: str, tag: str = "", meta: dict | None = None) -> int | None:
    content = (content or "").strip()
    if not content:
        return None
    try:
        meta_json = json.dumps(meta or {})
    except (TypeError, ValueError):
        meta_json = "{}"
    try:
        with _lock, _conn() as conn:
            cursor = conn.execute(
                "INSERT INTO facts (ts, tag, content, meta_json) VALUES (?,?,?,?)",
                (time.time(), tag or "", content[:2000], meta_json),
            )
            new_id = cursor.lastrowid
            row = conn.execute("SELECT COUNT(*) FROM facts").fetchone()
            count = row[0] if row else 0
            if count > VIVI2_MEMORY_MAX_ENTRIES:
                to_drop = count - VIVI2_MEMORY_MAX_ENTRIES
                # Prefer dropping untagged facts first (pinned/tagged facts stay).
                conn.execute(
                    "DELETE FROM facts WHERE id IN ("
                    "  SELECT id FROM facts WHERE tag='' ORDER BY ts ASC LIMIT ?"
                    ")", (to_drop,))
                # If we still overflow (everything tagged), drop oldest anyway.
                row = conn.execute("SELECT COUNT(*) FROM facts").fetchone()
                still = row[0] if row else 0
                if still > VIVI2_MEMORY_MAX_ENTRIES:
                    remainder = still - VIVI2_MEMORY_MAX_ENTRIES
                    conn.execute(
                        "DELETE FROM facts WHERE id IN ("
                        "  SELECT id FROM facts ORDER BY ts ASC LIMIT ?"
                        ")", (remainder,))
        return new_id
    except sqlite3.Error as e:
        log.warning(f"remember failed: {e}")
        return None


def recall(query: str | None = None, tag: str | None = None, n: int = 8) -> list[dict]:
    n = max(1, int(n))
    try:
        with _lock, _conn() as conn:
            sql = "SELECT id, tag, content, meta_json, ts FROM facts"
            params: list = []
            clauses = []
            if tag:
                clauses.append("tag = ?")
                params.append(tag)
            if query:
                # Escape LIKE wildcards in the user-supplied query so a stray %
                # or _ doesn't blow the match out.
                safe = (query.replace('\\', '\\\\')
                              .replace('%', '\\%')
                              .replace('_', '\\_'))
                clauses.append("content LIKE ? ESCAPE '\\'")
                params.append(f"%{safe}%")
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY ts DESC LIMIT ?"
            params.append(n)
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error as e:
        log.warning(f"recall failed: {e}")
        return []


def forget(fact_id: int) -> bool:
    try:
        fid = int(fact_id)
    except (TypeError, ValueError):
        return False
    try:
        with _lock, _conn() as conn:
            result = conn.execute("DELETE FROM facts WHERE id = ?", (fid,))
            return result.rowcount > 0
    except sqlite3.Error as e:
        log.warning(f"forget failed: {e}")
        return False


def export_session(session_id: str) -> dict:
    return {
        'session_id': session_id,
        'turns': history(session_id, n=10_000),
        'exported_at': time.time(),
    }

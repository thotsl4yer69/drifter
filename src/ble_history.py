#!/usr/bin/env python3
"""
MZ1312 DRIFTER — BLE forensic persistence layer (Phase 4.7)

Single source of truth for BLE detection history at
/opt/drifter/state/ble_history.db. Adds drive_id derivation so a
post-drive review can answer "which detections happened during today's
errand run?" instead of just "what's been seen ever."

UNCAGED TECHNOLOGY — EST 1991
"""
from __future__ import annotations

import os
import random
import sqlite3
import string
import threading
import time
from pathlib import Path
from typing import Optional

SCHEMA_VERSION = 1
DEFAULT_DB = Path("/opt/drifter/state/ble_history.db")
DEFAULT_DRIVE_FILE = Path("/opt/drifter/state/current_drive_id")
DRIVE_IDLE_SECONDS = 1800.0      # 30 min — gap that ends a drive
DRIVE_TOUCH_DEBOUNCE = 60.0      # only refresh mtime once a minute

_lock = threading.Lock()
_last_touch = 0.0


# ── Connection + migrations ───────────────────────────────────────

def open_db(path: Path = DEFAULT_DB) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False because the scanner's asyncio loop and
    # the MQTT paho thread both hand detections to the same connection.
    # autocommit (isolation_level=None) keeps writes durable without an
    # explicit commit per insert.
    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    cur = conn.execute("PRAGMA user_version").fetchone()
    version = int(cur[0]) if cur else 0
    if version < 1:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS detections (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                ts              REAL NOT NULL,
                target          TEXT NOT NULL,
                mac             TEXT NOT NULL,
                rssi            INTEGER,
                manufacturer_id TEXT,
                adv_name        TEXT,
                lat             REAL,
                lng             REAL,
                is_alert        INTEGER NOT NULL DEFAULT 0,
                drive_id        TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ts ON detections(ts);
            CREATE INDEX IF NOT EXISTS idx_target_ts ON detections(target, ts);
            CREATE INDEX IF NOT EXISTS idx_drive_id ON detections(drive_id);
            CREATE INDEX IF NOT EXISTS idx_mac_ts ON detections(mac, ts);
        """)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


# ── drive_id derivation ───────────────────────────────────────────

def _gen_drive_id(now: Optional[float] = None) -> str:
    suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
    t = time.localtime(now) if now is not None else time.localtime()
    return time.strftime("drive-%Y%m%d-%H%M%S-", t) + suffix


def current_drive_id(state_path: Path = DEFAULT_DRIVE_FILE,
                     idle_seconds: float = DRIVE_IDLE_SECONDS,
                     now: Optional[float] = None) -> str:
    """Return the active drive_id, minting a new one if the file is
    missing or stale. Stale = file mtime older than idle_seconds.
    Locked so two threads can't race on the mint path."""
    now = now if now is not None else time.time()
    state_path = Path(state_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        if state_path.exists():
            try:
                mtime = state_path.stat().st_mtime
            except OSError:
                mtime = 0.0
            if mtime and (now - mtime) <= idle_seconds:
                try:
                    drive_id = state_path.read_text(encoding='utf-8').strip()
                except OSError:
                    drive_id = ''
                if drive_id:
                    return drive_id
        drive_id = _gen_drive_id(now)
        state_path.write_text(drive_id, encoding='utf-8')
        os.utime(state_path, (now, now))
        return drive_id


def touch_drive_id(state_path: Path = DEFAULT_DRIVE_FILE,
                   now: Optional[float] = None,
                   debounce: float = DRIVE_TOUCH_DEBOUNCE) -> None:
    """Refresh the drive-id file mtime so the next current_drive_id call
    sees it as still-active. Debounced — at most once per `debounce`
    seconds — so a flood of detections doesn't pound the filesystem."""
    global _last_touch
    now = now if now is not None else time.time()
    if (now - _last_touch) < debounce:
        return
    _last_touch = now
    try:
        os.utime(state_path, (now, now))
    except OSError:
        pass


# ── Read/write ────────────────────────────────────────────────────

def insert_detection(conn: sqlite3.Connection, det: dict) -> None:
    """Insert one detection. Reads gps from either the Phase 4.5 nested
    shape (`det['gps']` = {'lat':..,'lng':..} or None) or the flat
    shape (`det['lat']`, `det['lng']`). adv_name accepts the Phase 4.5
    'advertised_name' alias."""
    gps = det.get('gps') or {}
    lat = gps.get('lat') if gps else det.get('lat')
    lng = gps.get('lng') if gps else det.get('lng')
    rssi = det.get('rssi')
    conn.execute(
        """INSERT INTO detections
           (ts, target, mac, rssi, manufacturer_id, adv_name,
            lat, lng, is_alert, drive_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            float(det['ts']),
            str(det['target']),
            str(det['mac']),
            int(rssi) if rssi is not None else None,
            det.get('manufacturer_id'),
            det.get('adv_name') or det.get('advertised_name'),
            float(lat) if lat is not None else None,
            float(lng) if lng is not None else None,
            1 if det.get('is_alert') else 0,
            str(det['drive_id']),
        ),
    )


def query_history(conn: sqlite3.Connection,
                  since: Optional[float] = None,
                  until: Optional[float] = None,
                  target: Optional[str] = None,
                  drive_id: Optional[str] = None,
                  limit: int = 200) -> list[dict]:
    """Filterable history read. Returns most-recent-first.

    NOTE: `limit` is applied AFTER `ORDER BY ts DESC`, so when a window
    contains more than `limit` rows the OLDEST rows in the window are
    silently truncated. The map page uses limit=2000 over the last 24h;
    busy environments (multiple Axon devices in range with the 30s
    rate-limit) can exceed that. Bump the cap or paginate if a drive's
    history page comes back short of expected."""
    sql = (
        "SELECT ts, target, mac, rssi, manufacturer_id, adv_name, "
        "lat, lng, is_alert, drive_id "
        "FROM detections WHERE 1=1"
    )
    params: list = []
    if since is not None:
        sql += " AND ts >= ?"; params.append(float(since))
    if until is not None:
        sql += " AND ts <= ?"; params.append(float(until))
    if target:
        sql += " AND target = ?"; params.append(target)
    if drive_id:
        sql += " AND drive_id = ?"; params.append(drive_id)
    sql += " ORDER BY ts DESC LIMIT ?"
    params.append(max(1, min(int(limit), 2000)))
    rows = conn.execute(sql, params).fetchall()
    return [
        {
            'ts':              r[0],
            'target':          r[1],
            'mac':             r[2],
            'rssi':            r[3],
            'manufacturer_id': r[4],
            'adv_name':        r[5],
            'lat':             r[6],
            'lng':             r[7],
            'is_alert':        bool(r[8]),
            'drive_id':        r[9],
        }
        for r in rows
    ]


def query_drives(conn: sqlite3.Connection) -> list[dict]:
    """Per-drive summary: started_ts, ended_ts, detection_count,
    unique_targets. Most-recent-drive first."""
    rows = conn.execute(
        """SELECT drive_id, MIN(ts), MAX(ts),
                  COUNT(*), COUNT(DISTINCT target)
           FROM detections
           GROUP BY drive_id
           ORDER BY MIN(ts) DESC"""
    ).fetchall()
    return [
        {
            'drive_id':        r[0],
            'started_ts':      r[1],
            'ended_ts':        r[2],
            'detection_count': r[3],
            'unique_targets':  r[4],
        }
        for r in rows
    ]


def prune_older_than(conn: sqlite3.Connection, days: int) -> int:
    cutoff = time.time() - (max(0, days) * 86400)
    cur = conn.execute("DELETE FROM detections WHERE ts < ?", (cutoff,))
    return cur.rowcount


def count(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM detections").fetchone()[0])


# ── Export formats (CLI + tests) ──────────────────────────────────

def parse_relative(spec: str, now: Optional[float] = None) -> float:
    """Parse '24h', '7d', '30m', '90s' or an ISO date 'YYYY-MM-DD' into
    a unix timestamp. Used by `drifter-ble-export --since` / --until."""
    now = now if now is not None else time.time()
    spec = (spec or '').strip()
    if not spec:
        raise ValueError("empty time spec")
    units = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400, 'w': 604800}
    if spec[-1] in units and spec[:-1].replace('.', '', 1).isdigit():
        return now - float(spec[:-1]) * units[spec[-1]]
    # Date or full ISO timestamp.
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return time.mktime(time.strptime(spec, fmt))
        except ValueError:
            continue
    raise ValueError(f"unparseable time: {spec!r}")


def to_csv(rows: list[dict]) -> str:
    """CSV with ISO8601 timestamps. Header is the canonical column order
    every consumer should rely on."""
    import csv
    import io
    cols = [
        'iso_ts', 'unix_ts', 'target', 'mac', 'rssi',
        'manufacturer_id', 'adv_name', 'lat', 'lng',
        'is_alert', 'drive_id',
    ]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for r in rows:
        ts = float(r.get('ts', 0) or 0)
        iso = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(ts)) if ts else ''
        w.writerow([
            iso, ts, r.get('target', ''), r.get('mac', ''),
            r.get('rssi', '') if r.get('rssi') is not None else '',
            r.get('manufacturer_id', '') or '',
            r.get('adv_name', '') or '',
            r.get('lat', '') if r.get('lat') is not None else '',
            r.get('lng', '') if r.get('lng') is not None else '',
            1 if r.get('is_alert') else 0,
            r.get('drive_id', ''),
        ])
    return buf.getvalue()


def to_geojson(rows: list[dict]) -> dict:
    """FeatureCollection of Points. Detections without a GPS fix are
    silently dropped — GeoJSON requires a geometry, and a null-island
    fallback would lie about location."""
    features = []
    for r in rows:
        lat, lng = r.get('lat'), r.get('lng')
        if lat is None or lng is None:
            continue
        features.append({
            'type': 'Feature',
            'geometry': {
                'type': 'Point',
                'coordinates': [float(lng), float(lat)],  # GeoJSON: lng,lat
            },
            'properties': {
                'ts': r.get('ts'),
                'target': r.get('target'),
                'mac': r.get('mac'),
                'rssi': r.get('rssi'),
                'is_alert': bool(r.get('is_alert')),
                'drive_id': r.get('drive_id'),
                'adv_name': r.get('adv_name'),
                'manufacturer_id': r.get('manufacturer_id'),
            },
        })
    return {'type': 'FeatureCollection', 'features': features}

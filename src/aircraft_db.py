#!/usr/bin/env python3
"""MZ1312 DRIFTER — Aircraft type/operator database lookup.

Wraps OpenSky Network's basestation.sqb (a SQLite database of every known
ICAO 24-bit address) into a tiny query helper. The cockpit's ADS-B radar
hover tooltip uses this to enrich an aircraft hex with manufacturer/type/
operator/registration/country — fields that tar1090's aircraft.json doesn't
always carry.

The .sqb file is a one-time download from
https://opensky-network.org/datasets/metadata/ and lives at
/opt/drifter/state/aircraft_db.sqlite. Absent file = lookup() returns
None for every hex (the cockpit then renders without enrichment).

UNCAGED TECHNOLOGY — EST 1991
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# Default DB path — the install instructions point operators here so the
# basestation.sqb download lands in the expected spot.
DEFAULT_DB_PATH = Path('/opt/drifter/state/aircraft_db.sqlite')

# Connection per-thread; sqlite connections aren't safe to share across
# threads by default and the cockpit handler may call lookup from the
# HTTP worker pool.
_local = threading.local()


def _conn(db_path: Path) -> Optional[sqlite3.Connection]:
    """Return a thread-local SQLite connection to the OpenSky DB."""
    if not db_path.exists():
        return None
    existing = getattr(_local, 'conn', None)
    existing_path = getattr(_local, 'path', None)
    if existing is not None and existing_path == str(db_path):
        return existing
    try:
        conn = sqlite3.connect(
            f'file:{db_path}?mode=ro',
            uri=True,
            check_same_thread=False,
        )
    except sqlite3.Error as e:
        log.debug("aircraft DB open failed: %s", e)
        return None
    _local.conn = conn
    _local.path = str(db_path)
    return conn


def _detect_aircraft_table(conn: sqlite3.Connection) -> Optional[str]:
    """OpenSky's basestation.sqb uses 'Aircraft'; some derivatives use
    'aircraftDatabase' or 'aircraft'. Probe sqlite_master to find it."""
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    except sqlite3.Error:
        return None
    names = {row[0] for row in rows}
    for candidate in ('Aircraft', 'aircraft', 'aircraftDatabase'):
        if candidate in names:
            return candidate
    return None


def lookup(icao_hex: str, db_path: Path = DEFAULT_DB_PATH) -> Optional[dict]:
    """Return {manufacturer, type, operator, registration, country} or None.

    ICAO hex is case-insensitive; OpenSky stores upper-case. Returns None
    if the DB file is missing, the hex isn't present, or the query fails.
    """
    if not icao_hex or not isinstance(icao_hex, str):
        return None
    icao = icao_hex.strip().upper()
    if not icao:
        return None
    conn = _conn(Path(db_path))
    if conn is None:
        return None
    table = _detect_aircraft_table(conn)
    if not table:
        return None
    # OpenSky basestation.sqb columns: ModeS, ICAOTypeCode, Manufacturer,
    # Type, OperatorFlagCode, RegisteredOwners, Registration, Country, ...
    # Some forks rename — we coalesce known aliases.
    try:
        row = conn.execute(
            f"SELECT * FROM {table} WHERE UPPER(ModeS)=? LIMIT 1", (icao,)
        ).fetchone()
    except sqlite3.Error as e:
        log.debug("aircraft lookup failed: %s", e)
        return None
    if row is None:
        return None
    cols = [d[0] for d in conn.execute(f"SELECT * FROM {table} LIMIT 0").description]
    record = dict(zip(cols, row))

    def _pick(*keys):
        for k in keys:
            v = record.get(k)
            if v:
                return v
        return None

    return {
        'manufacturer': _pick('Manufacturer', 'manufacturer'),
        'type': _pick('Type', 'ICAOTypeCode', 'icao_type_code'),
        'operator': _pick('RegisteredOwners', 'Operator', 'operator',
                          'OperatorFlagCode'),
        'registration': _pick('Registration', 'registration'),
        'country': _pick('Country', 'country', 'CountryName'),
    }


def is_available(db_path: Path = DEFAULT_DB_PATH) -> bool:
    """Return True if the OpenSky DB file exists at the configured path."""
    return Path(db_path).exists()

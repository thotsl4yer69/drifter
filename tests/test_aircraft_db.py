"""Tests for aircraft_db — OpenSky basestation.sqb lookup helper.

We synthesise a tiny SQLite DB matching basestation.sqb's schema so the
suite exercises the real lookup path without requiring the operator to
download the 100 MB upstream file. The on-disk file at
/opt/drifter/state/aircraft_db.sqlite is exercised by the integration
verify step in the brief.
"""
from __future__ import annotations

import sqlite3

import pytest

import aircraft_db


@pytest.fixture
def opensky_db(tmp_path):
    """Build a minimal Aircraft table mirroring basestation.sqb's shape."""
    db_path = tmp_path / 'aircraft_db.sqlite'
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE Aircraft (
            ModeS TEXT PRIMARY KEY,
            ICAOTypeCode TEXT,
            Manufacturer TEXT,
            Type TEXT,
            RegisteredOwners TEXT,
            Registration TEXT,
            Country TEXT
        )"""
    )
    conn.execute(
        "INSERT INTO Aircraft VALUES (?, ?, ?, ?, ?, ?, ?)",
        ('A12345', 'B738', 'Boeing', '737-800',
         'Qantas Airways', 'VH-ABC', 'Australia'),
    )
    conn.execute(
        "INSERT INTO Aircraft VALUES (?, ?, ?, ?, ?, ?, ?)",
        ('7C7C7C', 'C172', 'Cessna', '172 Skyhawk',
         'Private Owner', 'VH-XYZ', 'Australia'),
    )
    conn.commit()
    conn.close()
    return db_path


def test_is_available_false_when_missing(tmp_path):
    assert aircraft_db.is_available(tmp_path / 'absent.sqlite') is False


def test_is_available_true_when_present(opensky_db):
    assert aircraft_db.is_available(opensky_db) is True


def test_lookup_returns_record_for_known_hex(opensky_db):
    result = aircraft_db.lookup('A12345', db_path=opensky_db)
    assert result is not None
    assert result['manufacturer'] == 'Boeing'
    assert result['type'] == '737-800'
    assert result['registration'] == 'VH-ABC'
    assert result['country'] == 'Australia'
    assert result['operator'] == 'Qantas Airways'


def test_lookup_is_case_insensitive(opensky_db):
    result = aircraft_db.lookup('a12345', db_path=opensky_db)
    assert result is not None
    assert result['manufacturer'] == 'Boeing'


def test_lookup_returns_none_for_unknown(opensky_db):
    assert aircraft_db.lookup('DEADBEEF', db_path=opensky_db) is None


def test_lookup_returns_none_when_db_missing(tmp_path):
    assert aircraft_db.lookup('A12345',
                              db_path=tmp_path / 'absent.sqlite') is None


def test_lookup_handles_empty_string():
    assert aircraft_db.lookup('') is None


def test_lookup_handles_non_string():
    assert aircraft_db.lookup(None) is None


def test_lookup_falls_back_through_type_aliases(tmp_path):
    """Some opensky derivatives store the type under icao_type_code instead
    of Type — _pick must fall through aliases."""
    db_path = tmp_path / 'alt.sqlite'
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE Aircraft (
            ModeS TEXT PRIMARY KEY,
            ICAOTypeCode TEXT,
            Manufacturer TEXT,
            RegisteredOwners TEXT,
            Registration TEXT,
            Country TEXT
        )"""
    )
    conn.execute(
        "INSERT INTO Aircraft VALUES (?, ?, ?, ?, ?, ?)",
        ('B71B81', 'A320', 'Airbus', 'British Airways', 'G-EUUA', 'UK'),
    )
    conn.commit()
    conn.close()
    result = aircraft_db.lookup('B71B81', db_path=db_path)
    assert result is not None
    # Type column missing — should fall back to ICAOTypeCode.
    assert result['type'] == 'A320'
    assert result['manufacturer'] == 'Airbus'

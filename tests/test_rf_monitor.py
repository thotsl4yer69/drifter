"""Tests for rf_monitor — the periodic-scan-vs-rfaudio cooperation contract.

The critical invariant: when a peer service (rfaudio) publishes
`pause_rtl_433` over MQTT, rf_monitor must (a) stop rtl_433 AND (b) set the
`held_external` flag so the main loop's own periodic SDR scans don't grab
the device away from the peer mid-stream. The original bug had only (a),
which let emergency_scan / spectrum / ADS-B race rtl_fm.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

import rf_monitor


@pytest.fixture(autouse=True)
def _reset_rtl_control():
    """rf_monitor._rtl_control is module-level; reset around each test."""
    saved = dict(rf_monitor._rtl_control)
    rf_monitor._rtl_control['pause'] = None
    rf_monitor._rtl_control['resume'] = None
    rf_monitor._rtl_control['held_external'] = False
    yield
    rf_monitor._rtl_control.clear()
    rf_monitor._rtl_control.update(saved)


def _msg(command: str):
    m = MagicMock()
    m.payload = json.dumps({'command': command, 'ts': 0}).encode()
    return m


def test_pause_rtl_433_sets_held_external_flag():
    """rfaudio publishes pause_rtl_433 → held_external must be True so the
    main loop skips spectrum / emergency / ADS-B scans."""
    pause_called = []
    rf_monitor._rtl_control['pause'] = lambda: pause_called.append(True) or True
    rf_monitor._rtl_control['resume'] = lambda: None

    rf_monitor.on_message(MagicMock(), None, _msg('pause_rtl_433'))

    assert rf_monitor._rtl_control['held_external'] is True
    assert pause_called == [True]


def test_resume_rtl_433_clears_held_external_flag():
    """Resume must restore periodic scanning."""
    resume_called = []
    rf_monitor._rtl_control['pause'] = lambda: True
    rf_monitor._rtl_control['resume'] = lambda: resume_called.append(True)
    rf_monitor._rtl_control['held_external'] = True

    rf_monitor.on_message(MagicMock(), None, _msg('resume_rtl_433'))

    assert rf_monitor._rtl_control['held_external'] is False
    assert resume_called == [True]


def test_pause_sets_flag_even_when_pause_closure_not_yet_installed():
    """Flag must be set BEFORE the pause closure runs, so a fast-arriving
    MQTT command during startup still blocks the next scan tick even if
    the closure registration races."""
    rf_monitor._rtl_control['pause'] = None  # not yet installed
    rf_monitor._rtl_control['resume'] = None

    rf_monitor.on_message(MagicMock(), None, _msg('pause_rtl_433'))

    assert rf_monitor._rtl_control['held_external'] is True

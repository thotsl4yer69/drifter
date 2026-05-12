"""Tests for rf_monitor — held_external cooperation flag with peer services."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

import rf_monitor


@pytest.fixture(autouse=True)
def _reset_rtl_control():
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
    """pause must set held_external so the main loop skips periodic scans."""
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
    """Flag set BEFORE closure runs — covers startup race with on_message."""
    rf_monitor._rtl_control['pause'] = None  # not yet installed
    rf_monitor._rtl_control['resume'] = None

    rf_monitor.on_message(MagicMock(), None, _msg('pause_rtl_433'))

    assert rf_monitor._rtl_control['held_external'] is True

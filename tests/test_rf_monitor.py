"""Tests for rf_monitor — held_external cooperation flag with peer services."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

import rf_monitor


@pytest.fixture(autouse=True)
def _reset_rtl_control():
    saved = dict(rf_monitor._rtl_control)
    rf_monitor._rtl_control['pause'] = None
    rf_monitor._rtl_control['resume'] = None
    rf_monitor._rtl_control['held_external'] = False
    rf_monitor._interrupt.clear()
    yield
    rf_monitor._rtl_control.clear()
    rf_monitor._rtl_control.update(saved)
    rf_monitor._interrupt.clear()


def _msg(command: str, **extra):
    m = MagicMock()
    payload = {'command': command, 'ts': 0}
    payload.update(extra)
    m.payload = json.dumps(payload).encode()
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


# ── force_spectrum command ────────────────────────────────────────────

def test_force_spectrum_spawns_worker_thread():
    """force_spectrum spawns a background thread so the MQTT callback returns."""
    with patch.object(rf_monitor.threading, 'Thread') as MockThread:
        MockThread.return_value = MagicMock()
        rf_monitor.on_message(MagicMock(), None, _msg('force_spectrum'))
        MockThread.assert_called_once()
        # daemon=True so the worker doesn't block service shutdown.
        kwargs = MockThread.call_args.kwargs
        assert kwargs.get('daemon') is True
        assert kwargs.get('target') is rf_monitor._force_spectrum


def test_force_spectrum_publishes_error_when_lock_unavailable(monkeypatch):
    """If the dongle lock can't be acquired, an error message is published."""
    client = MagicMock()
    # Take the lock from another "thread" so the force_spectrum worker
    # can't acquire it.
    rf_monitor._dongle_lock.acquire()
    try:
        rf_monitor._force_spectrum(client, {})
    finally:
        rf_monitor._dongle_lock.release()
    # Some publish call with a 'dongle locked' style error
    assert client.publish.called
    args = client.publish.call_args_list
    found = False
    for a in args:
        topic, payload = a.args[:2]
        if 'rf/error' in topic or 'locked' in str(payload):
            found = True
    assert found, f"expected an error publish, got {args}"


# ── TPMS harvest + per-corner assignment ──────────────────────────────

def test_tpms_harvest_start_activates_collector():
    rf_monitor.tpms_harvest.active = False
    rf_monitor.tpms_harvest.sensors = {}
    client = MagicMock()
    rf_monitor.on_message(client, None, _msg('tpms_harvest_start'))
    assert rf_monitor.tpms_harvest.active is True


def test_tpms_harvest_record_dedupes_by_sensor_id():
    rf_monitor.tpms_harvest.start()
    try:
        rf_monitor.tpms_harvest.record('abc', 32.0, 25.0, -55)
        rf_monitor.tpms_harvest.record('abc', 31.9, 25.0, -56)
        rf_monitor.tpms_harvest.record('def', 30.0, 22.0, -60)
        snap = rf_monitor.tpms_harvest.snapshot()
        assert set(snap['ids_seen']) == {'abc', 'def'}
        assert snap['samples_per_id']['abc'] == 2
        assert snap['samples_per_id']['def'] == 1
    finally:
        rf_monitor.tpms_harvest.stop()
        rf_monitor.tpms_harvest.sensors = {}


def test_tpms_assign_corner_persists_to_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(rf_monitor, 'TPMS_ASSIGNMENTS_PATH',
                         tmp_path / 'tpms_assignments.json')
    client = MagicMock()
    # Save sensors should not require disk writes for the persistent map
    monkeypatch.setattr(rf_monitor.tpms, 'save_sensors', lambda: None)
    rf_monitor.tpms.sensor_map = {}
    rf_monitor.on_message(client, None, _msg(
        'tpms_assign_corner', sensor_id='deadbeef', corner='FL'))
    data = json.loads((tmp_path / 'tpms_assignments.json').read_text())
    assert data == {'FL': 'deadbeef'}
    assert rf_monitor.tpms.sensor_map['deadbeef'] == 'fl'


def test_tpms_assign_corner_rejects_invalid_corner(tmp_path, monkeypatch):
    monkeypatch.setattr(rf_monitor, 'TPMS_ASSIGNMENTS_PATH',
                         tmp_path / 'tpms_assignments.json')
    monkeypatch.setattr(rf_monitor.tpms, 'save_sensors', lambda: None)
    client = MagicMock()
    rf_monitor.on_message(client, None, _msg(
        'tpms_assign_corner', sensor_id='abc', corner='XY'))
    assert not (tmp_path / 'tpms_assignments.json').exists()


def test_tpms_clear_assignments_wipes_file(tmp_path, monkeypatch):
    monkeypatch.setattr(rf_monitor, 'TPMS_ASSIGNMENTS_PATH',
                         tmp_path / 'tpms_assignments.json')
    monkeypatch.setattr(rf_monitor.tpms, 'save_sensors', lambda: None)
    (tmp_path / 'tpms_assignments.json').write_text('{"FL": "deadbeef"}')
    rf_monitor.tpms.sensor_map = {'deadbeef': 'fl'}
    client = MagicMock()
    rf_monitor.on_message(client, None, _msg('tpms_clear_assignments'))
    assert json.loads((tmp_path / 'tpms_assignments.json').read_text()) == {}
    assert rf_monitor.tpms.sensor_map == {}


def test_load_tpms_assignments_handles_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(rf_monitor, 'TPMS_ASSIGNMENTS_PATH',
                         tmp_path / 'missing.json')
    assert rf_monitor.load_tpms_assignments() == {}

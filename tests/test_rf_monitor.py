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


# ── Spectrum downsample (Task B1) ─────────────────────────────────────

def test_downsample_spectrum_1024_to_256_groups_of_4():
    """1024 input bins → 256 output groups of 4 with correct min/max/mean."""
    bins = [{'freq_hz': float(i), 'db': float(i)} for i in range(1024)]
    out = rf_monitor.downsample_spectrum(bins, max_bins=256)
    assert len(out) == 256
    # First group: indices 0..3 → min=0, max=3, mean=1.5
    assert out[0]['level_db_min'] == 0.0
    assert out[0]['level_db_max'] == 3.0
    assert out[0]['level_db_mean'] == 1.5
    assert out[0]['freq_hz'] == 0.0
    # Last group: indices 1020..1023 → min=1020, max=1023, mean=1021.5
    assert out[-1]['level_db_min'] == 1020.0
    assert out[-1]['level_db_max'] == 1023.0
    assert out[-1]['level_db_mean'] == 1021.5
    assert out[-1]['freq_hz'] == 1020.0


def test_downsample_spectrum_caps_at_max_bins():
    """1742 input (real-world full sweep) → never exceeds 256."""
    bins = [{'freq_hz': float(i), 'db': -50.0} for i in range(1742)]
    out = rf_monitor.downsample_spectrum(bins, max_bins=256)
    assert 1 <= len(out) <= 256


def test_downsample_spectrum_small_input_passthrough():
    """Input ≤ max_bins → one output bin per input bin."""
    bins = [{'freq_hz': float(i), 'db': float(i)} for i in range(10)]
    out = rf_monitor.downsample_spectrum(bins, max_bins=256)
    assert len(out) == 10
    for i, o in enumerate(out):
        assert o['level_db_min'] == o['level_db_max'] == float(i)


def test_downsample_spectrum_empty_input():
    assert rf_monitor.downsample_spectrum([]) == []


# ── TPMS delta capture (Task B2) ──────────────────────────────────────

def test_tpms_delta_capture_flags_sensor_with_negative_delta():
    """A sensor reading 5+ kPa below baseline must surface as a candidate."""
    rf_monitor.tpms_delta.start('FL', baseline_kpa=220.0)
    try:
        # 220 kPa baseline. 210 kPa → -10 kPa delta → flagged.
        # 210 kPa = 210 * 0.145038 ≈ 30.46 PSI
        rf_monitor.tpms_delta.record('sensor_FL', 30.46, -55)
        # 219 kPa → -1 kPa delta → below threshold, still tracked as candidate
        rf_monitor.tpms_delta.record('sensor_other', 31.76, -60)
        best = rf_monitor.tpms_delta.best_match()
        assert best is not None
        assert best['sensor_id'] == 'sensor_FL'
        assert best['delta_kpa'] <= -rf_monitor.TPMS_DELTA_THRESHOLD_KPA
    finally:
        rf_monitor.tpms_delta.stop()
        rf_monitor.tpms_delta.candidates = {}


def test_tpms_delta_capture_no_match_when_no_sensor_below_threshold():
    """Every candidate stayed within ±5 kPa → best_match is None."""
    rf_monitor.tpms_delta.start('FR', baseline_kpa=220.0)
    try:
        # 218 kPa → -2 kPa delta (within tolerance)
        rf_monitor.tpms_delta.record('sensor_a', 218 * 0.145038, -50)
        assert rf_monitor.tpms_delta.best_match() is None
    finally:
        rf_monitor.tpms_delta.stop()
        rf_monitor.tpms_delta.candidates = {}


def test_tpms_delta_capture_snapshot_shape():
    rf_monitor.tpms_delta.start('RL', baseline_kpa=200.0)
    try:
        rf_monitor.tpms_delta.record('abc', 25.0, -65)
        snap = rf_monitor.tpms_delta.snapshot()
        assert snap['active'] is True
        assert snap['corner'] == 'RL'
        assert snap['baseline_kpa'] == 200.0
        assert isinstance(snap['candidates'], list)
        assert snap['candidates'][0]['sensor_id'] == 'abc'
        # Most-negative delta first (descending by absolute negative).
        assert snap['candidates'] == sorted(
            snap['candidates'], key=lambda c: c['delta_kpa'])
    finally:
        rf_monitor.tpms_delta.stop()
        rf_monitor.tpms_delta.candidates = {}


def test_tpms_delta_capture_command_starts_window(monkeypatch):
    """MQTT command with valid corner + baseline opens the window."""
    client = MagicMock()
    rf_monitor.tpms_delta.active = False
    rf_monitor.tpms_delta.candidates = {}
    rf_monitor.on_message(client, None, _msg(
        'tpms_delta_capture', corner='FL', baseline_kpa=220.0))
    try:
        assert rf_monitor.tpms_delta.active is True
        assert rf_monitor.tpms_delta.corner == 'FL'
        assert rf_monitor.tpms_delta.baseline_kpa == 220.0
        # An immediate progress publish lets the cockpit confirm.
        topics = [c.args[0] for c in client.publish.call_args_list]
        assert 'drifter/rf/tpms/delta' in topics
    finally:
        rf_monitor.tpms_delta.stop()
        rf_monitor.tpms_delta.candidates = {}


def test_tpms_delta_capture_command_rejects_bad_corner():
    client = MagicMock()
    rf_monitor.tpms_delta.active = False
    rf_monitor.on_message(client, None, _msg(
        'tpms_delta_capture', corner='XX', baseline_kpa=220.0))
    assert rf_monitor.tpms_delta.active is False


def test_tpms_delta_capture_command_rejects_missing_baseline():
    client = MagicMock()
    rf_monitor.tpms_delta.active = False
    rf_monitor.on_message(client, None, _msg(
        'tpms_delta_capture', corner='FL'))
    assert rf_monitor.tpms_delta.active is False


def test_tpms_delta_capture_ignores_record_when_inactive():
    rf_monitor.tpms_delta.active = False
    rf_monitor.tpms_delta.candidates = {}
    rf_monitor.tpms_delta.record('abc', 25.0, -60)
    assert rf_monitor.tpms_delta.candidates == {}


def test_tpms_delta_capture_handles_none_pressure():
    """A TPMS hit with no pressure field must not crash the recorder."""
    rf_monitor.tpms_delta.start('FL', baseline_kpa=220.0)
    try:
        rf_monitor.tpms_delta.record('abc', None, -60)
        assert rf_monitor.tpms_delta.candidates == {}
    finally:
        rf_monitor.tpms_delta.stop()
        rf_monitor.tpms_delta.candidates = {}

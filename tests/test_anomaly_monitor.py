# tests/test_anomaly_monitor.py
import pytest
import json
import sys
sys.path.insert(0, 'src')

import config
import db
from anomaly_monitor import SensorWindow, AnomalyMonitor, MONITORED_SENSORS


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    """Redirect DB to a temp directory so AnomalyMonitor() can call db.init_db()."""
    monkeypatch.setattr(config, 'DB_PATH', tmp_path / 'test.db')
    monkeypatch.setattr(config, 'REPORTS_DIR', tmp_path / 'reports')
    # Clear any cached thread-local connection
    if hasattr(db._local, 'conn'):
        try:
            db._local.conn.close()
        except Exception:
            pass
        del db._local.conn
    yield
    if hasattr(db._local, 'conn'):
        try:
            db._local.conn.close()
        except Exception:
            pass
        del db._local.conn

def test_sensor_window_no_anomaly_on_stable_data():
    w = SensorWindow(window_size=10)
    for _ in range(10):
        w.add(5.0)
    result = w.check(5.1)
    assert result is None  # within normal range

def test_sensor_window_detects_high_anomaly():
    w = SensorWindow(window_size=10)
    for _ in range(10):
        w.add(5.0)
    # std is ~0; use value far outside
    result = w.check(100.0)
    assert result is not None
    assert result['z_score'] > 3.5
    assert result['severity'] in ('high', 'critical')

def test_sensor_window_requires_minimum_readings():
    w = SensorWindow(window_size=10)
    for _ in range(4):  # less than 5 minimum
        w.add(5.0)
    result = w.check(100.0)
    assert result is None  # not enough data yet

def test_cold_start_filter_suppresses_anomalies():
    monitor = AnomalyMonitor()
    monitor.current_coolant = 50.0  # below WARMUP_COOLANT_THRESHOLD (60°C)
    monitor.current_session_id = "TEST"
    for _ in range(10):
        monitor.windows['stft_b1'].add(2.0)
    # spike that would normally be anomalous
    events = monitor._check_sensor('stft_b1', 20.0)
    assert events == []  # suppressed by cold-start filter

def test_warm_engine_logs_anomaly():
    monitor = AnomalyMonitor()
    monitor.current_coolant = 85.0  # above threshold
    monitor.current_session_id = "TEST"
    for _ in range(10):
        monitor.windows['stft_b1'].add(2.0)
    events = monitor._check_sensor('stft_b1', 20.0)
    assert len(events) == 1
    assert events[0]['sensor'] == 'stft_b1'
    assert events[0]['session_id'] == 'TEST'

def test_rpm_instability_at_idle():
    monitor = AnomalyMonitor()
    monitor.current_coolant = 90.0
    monitor.current_session_id = "TEST"
    monitor.current_speed = 0  # at idle
    # Load with varying RPM (high stddev)
    rpms = [720, 820, 680, 900, 700, 850, 700, 780, 820, 700]
    for r in rpms:
        monitor.rpm_idle_window.append(r)
    events = monitor._check_rpm_instability()
    assert len(events) == 1
    assert 'instability' in events[0]['sensor']

def test_monitored_sensors_list_not_empty():
    assert len(MONITORED_SENSORS) >= 8
    assert 'stft_b1' in MONITORED_SENSORS


def test_check_sensor_rejects_non_finite_value():
    """SensorWindow.check must reject NaN/Inf instead of producing bogus anomalies."""
    monitor = AnomalyMonitor()
    monitor.current_coolant = 90.0
    monitor.current_session_id = "TEST"
    for _ in range(10):
        monitor.windows['stft_b1'].add(2.0)
    # Non-finite values must be rejected — they'd poison z-score math.
    assert monitor.windows['stft_b1'].check(float('inf')) is None
    assert monitor.windows['stft_b1'].check(float('-inf')) is None
    assert monitor.windows['stft_b1'].check(float('nan')) is None
    # A sane outlier still flags.
    assert monitor.windows['stft_b1'].check(50.0) is not None


def test_float_coercion_in_on_message(monkeypatch):
    """Non-numeric MQTT values must be silently dropped (no exception)."""
    import json
    monitor = AnomalyMonitor()
    monitor.current_session_id = "TEST"

    class FakeMsg:
        topic = 'drifter/engine/stft1'
        payload = json.dumps({'value': 'not_a_number'}).encode()

    # Should not raise
    monitor._on_message(None, None, FakeMsg())
    assert 'coolant' in MONITORED_SENSORS
    assert 'voltage' in MONITORED_SENSORS


def test_cold_start_does_not_contaminate_baseline():
    """Cold-start readings must NOT enter the rolling baseline.

    Regression: the previous cold-start filter added suppressed values to the
    sensor window, which skewed the mean once warmup completed and produced
    spurious anomalies on the first few warm samples.
    """
    monitor = AnomalyMonitor()
    monitor.current_session_id = "TEST"
    # Cold phase: spike values that should be *ignored* by the baseline.
    monitor.current_coolant = 30.0  # cold
    for v in [50.0, 60.0, 40.0, 55.0, 45.0, 50.0, 52.0, 48.0, 51.0, 49.0]:
        monitor._check_sensor('stft_b1', v)
    # Window should still be empty — cold-start readings must not pollute it.
    assert len(monitor.windows['stft_b1'].window) == 0


def test_non_dict_payload_is_ignored():
    """_on_message must not crash on bare JSON primitives (int/str/array)."""
    monitor = AnomalyMonitor()
    monitor.current_session_id = "TEST"

    class FakeMsg:
        topic = 'drifter/engine/stft1'
        payload = b'"just_a_string"'

    # Should not raise
    monitor._on_message(None, None, FakeMsg())


def test_sensor_window_floor_prevents_zero_std_zscore_storm():
    """A constant-value sensor at 0 must not produce huge z-scores on noise.

    Regression: old formula produced std = max(0.01, |mean|*0.01). For a
    sensor pinned at 0, std=0.01 → a reading of 0.5 gave z=50 (critical).
    """
    w = SensorWindow(window_size=10)
    for _ in range(10):
        w.add(0.0)
    # 0.5 is within normal sensor noise — should NOT flag as critical.
    result = w.check(0.5)
    assert result is None or result.get('severity') != 'critical'

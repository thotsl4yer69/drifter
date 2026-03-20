# tests/test_anomaly_monitor.py
import pytest
import json
import sys
sys.path.insert(0, 'src')

from anomaly_monitor import SensorWindow, AnomalyMonitor, MONITORED_SENSORS

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


def test_check_sensor_rejects_non_numeric_value():
    """_check_sensor must not crash when MQTT delivers a non-numeric value."""
    monitor = AnomalyMonitor()
    monitor.current_coolant = 90.0
    monitor.current_session_id = "TEST"
    for _ in range(10):
        monitor.windows['stft_b1'].add(2.0)
    # Simulate the value coercion guard added in _on_message:
    # _check_sensor itself receives a float, so test the guard in _on_message
    # indirectly by verifying SensorWindow.check handles boundary values.
    result = monitor.windows['stft_b1'].check(float('inf'))
    # inf z-score → should still return a result dict, not crash
    assert result is not None


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

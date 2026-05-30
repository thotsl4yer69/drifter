# tests/test_telemetry_batcher.py
"""Smoke tests for telemetry_batcher: rolling window stats."""
import sys
import time

sys.path.insert(0, 'src')

import pytest


def _fresh_batcher():
    """Import batcher with empty module-level buffers."""
    import importlib

    import telemetry_batcher
    importlib.reload(telemetry_batcher)
    return telemetry_batcher


def test_window_stats_basic():
    tb = _fresh_batcher()
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    samples = [(time.time(), v) for v in values]
    stats = tb._window_stats(samples)
    assert stats is not None
    assert stats['mean'] == pytest.approx(3.0, abs=0.001)
    assert stats['min'] == 1.0
    assert stats['max'] == 5.0
    assert stats['count'] == 5
    assert stats['last'] == 5.0
    assert stats['stddev'] > 0


def test_window_stats_empty_returns_none():
    tb = _fresh_batcher()
    assert tb._window_stats([]) is None


def test_record_and_build_window():
    import json
    tb = _fresh_batcher()
    now = time.time()
    # Push 5 RPM samples into buffer
    rpm_topic = tb._TOPIC_TO_KEY and {v: k for k, v in tb._TOPIC_TO_KEY.items()}.get('rpm')
    if rpm_topic is None:
        pytest.skip("rpm topic not mapped")
    for i in range(5):
        payload = json.dumps({'value': 800.0 + i * 10, 'ts': now - (4 - i)}).encode()
        tb._record(rpm_topic, payload)

    window = tb.build_window(now, window_seconds=30)
    assert 'metrics' in window
    assert 'rpm' in window['metrics']
    stats = window['metrics']['rpm']
    assert stats['count'] == 5
    assert stats['mean'] == pytest.approx(820.0, abs=0.1)


def test_record_ignores_unknown_topic():
    tb = _fresh_batcher()
    # Should not raise; unknown topics are silently ignored
    tb._record('drifter/unknown/topic', b'{"value": 42}')


def test_record_ignores_bad_json():
    tb = _fresh_batcher()
    from config import TOPICS
    rpm_topic = TOPICS.get('rpm', 'drifter/engine/rpm')
    tb._record(rpm_topic, b'not json')
    window = tb.build_window(time.time())
    # rpm key should not be in metrics (or stats count 0) since nothing recorded
    metrics = window.get('metrics', {})
    assert 'rpm' not in metrics or metrics['rpm']['count'] == 0


def test_window_excludes_old_samples():
    import json
    tb = _fresh_batcher()
    from config import TOPICS
    rpm_topic = TOPICS.get('rpm', 'drifter/engine/rpm')
    now = time.time()
    # One old sample (60s ago) and one fresh
    tb._record(rpm_topic, json.dumps({'value': 999.0, 'ts': now - 60}).encode())
    tb._record(rpm_topic, json.dumps({'value': 800.0, 'ts': now - 1}).encode())
    # With 30s window, only the fresh one should appear
    window = tb.build_window(now, window_seconds=30)
    if 'rpm' in window.get('metrics', {}):
        assert window['metrics']['rpm']['count'] == 1
        assert window['metrics']['rpm']['mean'] == pytest.approx(800.0, abs=0.1)

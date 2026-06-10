# tests/test_watchdog.py
"""Regression tests for src/watchdog.py."""
import sys
import time

import pytest

sys.path.insert(0, 'src')

import watchdog


@pytest.fixture
def reset_watchdog_state():
    """Give each test a clean last_mqtt_data dict."""
    watchdog.last_mqtt_data.clear()
    watchdog.service_restarts.clear()
    yield
    watchdog.last_mqtt_data.clear()
    watchdog.service_restarts.clear()


class FakeMQTT:
    def __init__(self):
        self.published = []

    def publish(self, topic, payload, **kwargs):
        self.published.append((topic, payload))


def _patch_services(monkeypatch, status_map):
    monkeypatch.setattr(watchdog, 'get_service_status',
                        lambda name: status_map.get(name, 'active'))
    monkeypatch.setattr(watchdog, 'get_system_metrics', lambda: {})


def test_missing_critical_topic_flags_after_grace(monkeypatch,
                                                   reset_watchdog_state):
    """Regression: topics that never receive data were silently OK forever.

    After the grace period (WATCHDOG_MQTT_TIMEOUT) with no messages, the
    watchdog should flag them instead of reporting healthy.
    """
    _patch_services(monkeypatch, {})
    monkeypatch.setattr(watchdog, 'WATCHDOG_START_TIME',
                        time.time() - watchdog.WATCHDOG_MQTT_TIMEOUT - 10)
    monkeypatch.setattr(watchdog, 'SERVICES', [])
    mq = FakeMQTT()
    health = watchdog.check_health(mq)
    assert 'drifter/engine/rpm' in health['mqtt_stale']
    assert health['overall'] == 'degraded'


def test_grace_period_suppresses_missing_topic_warning(monkeypatch,
                                                       reset_watchdog_state):
    """Within the grace period, a missing topic is not yet an issue."""
    _patch_services(monkeypatch, {})
    monkeypatch.setattr(watchdog, 'WATCHDOG_START_TIME', time.time())
    monkeypatch.setattr(watchdog, 'SERVICES', [])
    mq = FakeMQTT()
    health = watchdog.check_health(mq)
    assert health['mqtt_stale'] == []


def test_service_in_unexpected_state_is_flagged(monkeypatch,
                                                 reset_watchdog_state):
    """Regression: the old `status != 'inactive'` check silently allowed
    'unknown', empty output, and other non-healthy states."""
    monkeypatch.setattr(watchdog, 'SERVICES', ['drifter-test'])
    _patch_services(monkeypatch, {'drifter-test': 'unknown'})
    monkeypatch.setattr(watchdog, 'WATCHDOG_START_TIME', time.time())
    mq = FakeMQTT()
    health = watchdog.check_health(mq)
    assert any('drifter-test' in issue for issue in health.get('issues', []))


def test_active_service_does_not_trigger_issue(monkeypatch,
                                                reset_watchdog_state):
    monkeypatch.setattr(watchdog, 'SERVICES', ['drifter-test'])
    _patch_services(monkeypatch, {'drifter-test': 'active'})
    monkeypatch.setattr(watchdog, 'WATCHDOG_START_TIME', time.time())
    # Also provide fresh data on critical topics so we isolate service check.
    watchdog.last_mqtt_data['drifter/engine/rpm'] = time.time()
    watchdog.last_mqtt_data['drifter/snapshot'] = time.time()
    mq = FakeMQTT()
    health = watchdog.check_health(mq)
    assert health['overall'] == 'healthy'


# ── Auto-demote to diag under sustained pressure ────────────────────
class _RecordingClient:
    def __init__(self):
        self.published = []

    def publish(self, topic, payload, retain=False):
        self.published.append((topic, payload, retain))


def _reset_pressure(monkeypatch):
    monkeypatch.setattr(watchdog, '_pressure_count', 0, raising=False)
    monkeypatch.setattr(watchdog, 'WATCHDOG_AUTO_DIAG', True)
    monkeypatch.setattr(watchdog, 'WATCHDOG_MEM_CRITICAL_PCT', 92.0)
    monkeypatch.setattr(watchdog, 'WATCHDOG_TEMP_CRITICAL_C', 82.0)
    monkeypatch.setattr(watchdog, 'WATCHDOG_PRESSURE_CHECKS', 3)


def test_no_demote_below_threshold(monkeypatch):
    _reset_pressure(monkeypatch)
    import mode
    switched = []
    monkeypatch.setattr(mode, 'read_mode', lambda: 'drive')
    monkeypatch.setattr(mode, 'switch', lambda m: switched.append(m))
    c = _RecordingClient()
    for _ in range(5):
        assert watchdog._maybe_demote_to_diag(c, {'memory_percent': 70, 'cpu_temp': 60}) is None
    assert switched == []


def test_demote_only_after_sustained_pressure(monkeypatch):
    _reset_pressure(monkeypatch)
    import mode
    switched = []
    monkeypatch.setattr(mode, 'read_mode', lambda: 'drive')
    monkeypatch.setattr(mode, 'switch', lambda m: switched.append(m))
    c = _RecordingClient()
    hot = {'memory_percent': 95, 'cpu_temp': 60}
    # First two critical checks: still building up, no demote.
    assert watchdog._maybe_demote_to_diag(c, hot) is None
    assert watchdog._maybe_demote_to_diag(c, hot) is None
    assert switched == []
    # Third sustained check: fire.
    ev = watchdog._maybe_demote_to_diag(c, hot)
    assert ev and ev['action'] == 'auto_demote_to_diag' and ev['reason'] == 'memory'
    assert switched == ['diag']
    assert c.published  # operator notified


def test_thermal_pressure_also_demotes(monkeypatch):
    _reset_pressure(monkeypatch)
    import mode
    switched = []
    monkeypatch.setattr(mode, 'read_mode', lambda: 'drive')
    monkeypatch.setattr(mode, 'switch', lambda m: switched.append(m))
    c = _RecordingClient()
    hot = {'memory_percent': 50, 'cpu_temp': 85}
    for _ in range(3):
        ev = watchdog._maybe_demote_to_diag(c, hot)
    assert ev['reason'] == 'thermal' and switched == ['diag']


def test_no_demote_when_already_diag(monkeypatch):
    _reset_pressure(monkeypatch)
    import mode
    switched = []
    monkeypatch.setattr(mode, 'read_mode', lambda: 'diag')
    monkeypatch.setattr(mode, 'switch', lambda m: switched.append(m))
    c = _RecordingClient()
    hot = {'memory_percent': 99, 'cpu_temp': 90}
    for _ in range(4):
        watchdog._maybe_demote_to_diag(c, hot)
    assert switched == []  # already lean, nothing to shed


def test_disabled_never_demotes(monkeypatch):
    _reset_pressure(monkeypatch)
    monkeypatch.setattr(watchdog, 'WATCHDOG_AUTO_DIAG', False)
    import mode
    switched = []
    monkeypatch.setattr(mode, 'switch', lambda m: switched.append(m))
    c = _RecordingClient()
    for _ in range(5):
        assert watchdog._maybe_demote_to_diag(c, {'memory_percent': 99, 'cpu_temp': 95}) is None
    assert switched == []

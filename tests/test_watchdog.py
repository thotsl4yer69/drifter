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

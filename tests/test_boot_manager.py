"""Regression coverage for the field boot sequencer."""
from pathlib import Path

import boot_manager as boot


def test_boot_status_is_retained_qos1():
    calls = []

    class Client:
        def publish(self, topic, payload, **kwargs):
            calls.append((topic, payload, kwargs))

    boot._publish(Client(), 'ready', 'all core services up', True)

    assert calls
    topic, _payload, kwargs = calls[-1]
    assert topic == boot.TOPICS['boot_status']
    assert kwargs == {'qos': 1, 'retain': True}


def test_core_services_share_one_readiness_window(monkeypatch):
    checks = []

    def active(service):
        checks.append(service)
        return service == 'a'

    monkeypatch.setattr(boot, '_systemctl_active', active)
    states = boot._wait_for_core_services(['a', 'b', 'c'], timeout=0, poll=0)

    assert states == {'a': True, 'b': False, 'c': False}
    # A zero-length shared deadline performs bounded observations only; it does
    # not sleep 20 seconds separately for every unavailable service.
    assert set(checks) == {'a', 'b', 'c'}


def test_default_boot_budget_fits_systemd_timeout():
    service_text = Path('services/drifter-boot-manager.service').read_text(encoding='utf-8')
    timeout_line = next(
        line for line in service_text.splitlines() if line.startswith('TimeoutStartSec=')
    )
    systemd_timeout = float(timeout_line.split('=', 1)[1])
    expected_worst_case = (
        boot.BOOT_NETWORK_WAIT_SEC
        + boot.BOOT_MQTT_WAIT_SEC
        + boot.BOOT_CORE_WAIT_SEC
        + boot.BOOT_HANDOFF_DELAY_SEC
    )
    assert expected_worst_case < systemd_timeout
    assert systemd_timeout - expected_worst_case >= 30


def test_degraded_core_state_never_blocks_boot_handoff(monkeypatch):
    class Screen:
        def __init__(self):
            self.lines = []

        def add(self, text, level='fg'):
            self.lines.append((text, level))

    monkeypatch.setattr(boot, 'BootScreen', Screen)
    monkeypatch.setattr(boot, '_mqtt_reachable', lambda: False)
    monkeypatch.setattr(boot, '_have_ip', lambda: False)
    monkeypatch.setattr(boot, '_wait_for', lambda predicate, timeout, poll=1.0: False)
    monkeypatch.setattr(
        boot,
        '_wait_for_core_services',
        lambda services: {service: False for service in services},
    )
    monkeypatch.setattr(boot.time, 'sleep', lambda _seconds: None)

    assert boot.main() == 0

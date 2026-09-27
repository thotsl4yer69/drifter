"""Elapsed-budget regressions; no Pi, broker or vehicle evidence is implied."""
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

import boot_manager as boot


class Clock:
    def __init__(self):
        self.now = 100.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        assert seconds >= 0
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    fake = Clock()
    monkeypatch.setattr(boot.time, 'monotonic', fake.monotonic)
    monkeypatch.setattr(boot.time, 'sleep', fake.sleep)
    monkeypatch.setattr(boot.shutil, 'which', lambda name: '/usr/bin/systemctl')
    return fake


@pytest.mark.parametrize('count,budget', [(1, 0.25), (4, 1.0), (12, 4.0)])
def test_stalled_probes_cannot_multiply_stage_deadline(monkeypatch, clock, count, budget):
    calls = []

    def stalled(args, **kwargs):
        calls.append((args[-1], kwargs['timeout']))
        clock.now += kwargs['timeout']
        raise subprocess.TimeoutExpired(args, kwargs['timeout'])

    monkeypatch.setattr(boot.subprocess, 'run', stalled)
    services = [f'drifter-test-{index}' for index in range(count)]
    start = clock.now
    states = boot._wait_for_core_services(services, timeout=budget, poll=1)

    elapsed = clock.now - start
    assert elapsed <= budget + 1e-9, f'{budget}s budget took {elapsed}s: {calls}'
    assert states == dict.fromkeys(services, False)
    assert all(0 < seconds <= 3 for _, seconds in calls)


@pytest.mark.parametrize('budget', [0, -1])
def test_expired_budget_never_starts_a_probe(monkeypatch, clock, budget):
    def unexpected(*args, **kwargs):
        pytest.fail('Probe started without any readiness budget')

    monkeypatch.setattr(boot.subprocess, 'run', unexpected)
    assert boot._wait_for_core_services(['a', 'b'], timeout=budget) == {'a': False, 'b': False}
    assert not clock.sleeps


def test_poll_sleep_is_capped_at_deadline(monkeypatch, clock):
    calls = []

    def inactive(args, **kwargs):
        calls.append(args[-1])
        return SimpleNamespace(stdout='inactive\n', returncode=3)

    monkeypatch.setattr(boot.subprocess, 'run', inactive)
    states = boot._wait_for_core_services(['a', 'b'], timeout=0.25, poll=30)

    assert states == {'a': False, 'b': False}
    assert clock.now == 100.25
    assert clock.sleeps == [0.25]
    assert calls == ['a', 'b']  # No extra probe sweep after the deadline.


def test_positive_results_survive_a_later_probe_timeout(monkeypatch, clock):
    calls = []

    def probe(args, **kwargs):
        service = args[-1]
        calls.append(service)
        if service == 'ready':
            clock.now += 0.1
            return SimpleNamespace(stdout='active\n', returncode=0)
        clock.now += kwargs['timeout']
        raise subprocess.TimeoutExpired(args, kwargs['timeout'])

    monkeypatch.setattr(boot.subprocess, 'run', probe)
    states = boot._wait_for_core_services(['ready', 'stuck', 'unobserved'], timeout=1)

    assert states == {'ready': True, 'stuck': False, 'unobserved': False}
    assert calls == ['ready', 'stuck']
    assert clock.now == pytest.approx(101)


def test_late_service_can_become_ready_inside_budget(monkeypatch, clock):
    calls = []

    def probe(args, **kwargs):
        service = args[-1]
        calls.append(service)
        active = service == 'a' or clock.now >= 100.5
        return SimpleNamespace(stdout='active\n' if active else 'activating\n', returncode=0 if active else 3)

    monkeypatch.setattr(boot.subprocess, 'run', probe)
    assert boot._wait_for_core_services(['a', 'b'], timeout=1, poll=0.25) == {'a': True, 'b': True}
    assert clock.now == 100.5
    assert calls == ['a', 'b', 'b', 'b']


def test_empty_service_list_returns_without_sleep(monkeypatch, clock):
    def unexpected(*args, **kwargs):
        pytest.fail('Empty service list must not launch systemctl')

    monkeypatch.setattr(boot.subprocess, 'run', unexpected)
    assert boot._wait_for_core_services([], timeout=45) == {}
    assert not clock.sleeps


def test_duplicates_are_probed_once_in_declared_order(monkeypatch, clock):
    calls = []

    def active(args, **kwargs):
        calls.append(args[-1])
        return SimpleNamespace(stdout='active\n', returncode=0)

    monkeypatch.setattr(boot.subprocess, 'run', active)
    assert boot._wait_for_core_services(['z', 'a', 'z'], timeout=1) == {'z': True, 'a': True}
    assert calls == ['z', 'a']


@pytest.mark.parametrize('state,expected', [('active', True), ('inactive', False), ('activating', False), ('failed', False)])
def test_service_probe_reports_only_active(monkeypatch, state, expected):
    monkeypatch.setattr(boot.shutil, 'which', lambda _: '/usr/bin/systemctl')
    monkeypatch.setattr(boot.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(stdout=state + '\n'))
    assert boot._systemctl_active('a', timeout=0.1) is expected


def test_missing_systemctl_is_degraded(monkeypatch):
    monkeypatch.setattr(boot.shutil, 'which', lambda _: None)
    assert boot._systemctl_active('a', timeout=0.1) is False


def test_probe_exception_is_degraded(monkeypatch):
    monkeypatch.setattr(boot.shutil, 'which', lambda _: '/usr/bin/systemctl')

    def denied(*args, **kwargs):
        raise PermissionError('simulated command failure')

    monkeypatch.setattr(boot.subprocess, 'run', denied)
    assert boot._systemctl_active('a', timeout=0.1) is False


def test_real_child_timeout_is_reaped_and_returns_degraded(monkeypatch):
    """Real Python child-process timeout, NOT a real systemd/service test."""
    run = subprocess.run
    monkeypatch.setattr(boot.shutil, 'which', lambda _: '/usr/bin/systemctl')
    results = []

    def sleeping_child(args, **kwargs):
        results.append(kwargs['timeout'])
        return run([sys.executable, '-c', 'import time; time.sleep(10)'], **kwargs)

    monkeypatch.setattr(boot.subprocess, 'run', sleeping_child)
    start = time.perf_counter()
    states = boot._wait_for_core_services(['a', 'b', 'c'], timeout=0.15, poll=1)
    elapsed = time.perf_counter() - start
    assert states == {'a': False, 'b': False, 'c': False}
    assert len(results) == 1
    assert 0 < results[0] <= 0.15
    # Allow process creation/reaping and shared-runner scheduling overhead.
    assert elapsed < 2, f'Unexpected child timeout overrun: {elapsed:.3f}s'


@pytest.mark.parametrize('budget', [0, -1])
def test_generic_wait_expired_budget_never_calls_predicate(clock, budget):
    calls = []

    def probe():
        calls.append(clock.now)
        return True

    assert boot._wait_for(probe, timeout=budget) is False
    assert calls == []
    assert clock.sleeps == []


def test_generic_wait_caps_sleep_and_never_probes_after_deadline(clock):
    calls = []

    def probe():
        calls.append(clock.now)
        return False

    assert boot._wait_for(probe, timeout=0.25, poll=30) is False
    assert calls == [100.0]
    assert clock.now == 100.25
    assert clock.sleeps == [0.25]


def test_generic_wait_can_succeed_before_deadline(clock):
    calls = []

    def probe():
        calls.append(clock.now)
        return len(calls) == 2

    assert boot._wait_for(probe, timeout=1, poll=0.25) is True
    assert calls == [100.0, 100.25]
    assert clock.sleeps == [0.25]

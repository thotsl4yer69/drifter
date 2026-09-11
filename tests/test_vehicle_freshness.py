"""Regression tests for physical observation times and exclusive ownership."""
import time
from collections import deque
from unittest.mock import MagicMock

import pytest

import config
import obd_transport
import safety_engine as safety
import web_dashboard_handlers as handlers
import web_dashboard_state as dashboard


def test_hard_braking_uses_elapsed_seconds():
    state = safety.SafetyState(speed_hist=deque([100, 50]), speed_ts=deque([100, 105]))
    assert safety._rate(state.speed_hist, state.speed_ts) == -10
    assert safety.rule_hard_brake(state) is None
    state.speed_ts = deque([100, 100.5])
    assert safety.rule_hard_brake(state) is not None


def test_snapshot_duplicates_and_stale_samples_do_not_extend_safety_history(monkeypatch):
    state = safety.SafetyState()
    monkeypatch.setattr(safety, '_state', state)
    monkeypatch.setattr(safety.time, 'time', lambda: 100)
    payload = {'rpm': 800, 'ts': 100, 'sample_ts': {'rpm': 100}}
    safety._on_snapshot(payload)
    safety._on_snapshot(payload)
    safety._on_snapshot({'rpm': 7000, 'ts': 100, 'sample_ts': {'rpm': 80}})
    safety._on_snapshot({'rpm': 900, 'ts': 100, 'sample_ts': None})
    assert list(state.rpm_hist) == [800]
    monkeypatch.setattr(safety.time, 'time', lambda: 116)
    safety.evaluate(MagicMock())
    assert not state.rpm_hist


def test_telemetry_lease_prevents_overlap_and_is_released_on_close(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'DRIFTER_DIR', tmp_path)
    first = obd_transport.acquire_telemetry_lease()
    try:
        assert obd_transport.acquire_telemetry_lease() is None
    finally:
        first.close()
    second = obd_transport.acquire_telemetry_lease()
    assert second is not None
    second.close()


def test_can_boot_setup_leaves_serial_hardware_alone_when_elm_selected(monkeypatch):
    monkeypatch.setattr(obd_transport, 'select_transport', lambda: obd_transport.ELM327)
    runner = MagicMock()
    monkeypatch.setattr(obd_transport.subprocess, 'run', runner)
    assert obd_transport.prepare_can() == 0
    runner.assert_not_called()


@pytest.mark.parametrize('snapshot,expected', [
    ({'rpm': 800, 'sample_ts': {'rpm': 'fresh'}}, False),
    ({'rpm': 800, 'sample_ts': None}, False),
    ({'rpm': None}, False),
    ({'rpm': float('nan')}, False),
    ({'rpm': 800, 'sample_ts': {'rpm': 1}}, False),
    ({'rpm': 800}, True),
])
def test_healthz_needs_fresh_measured_values(monkeypatch, snapshot, expected):
    import web_dashboard_health as health
    now = time.time()
    monkeypatch.setattr(handlers, '_systemctl_active', lambda _: True)
    monkeypatch.setattr(health, '_healthz_cache', {'ts': 0.0, 'payload': None, 'http_status': 200})
    monkeypatch.setattr(dashboard, 'mqtt_client', MagicMock(is_connected=lambda: True))
    monkeypatch.setattr(dashboard, 'latest_state', {'snapshot': {
        **snapshot, 'source': 'obd_bridge', 'ts': now}, '_last_update': now})
    payload, _ = handlers._healthz_payload()
    assert payload['vehicle_ready'] is expected

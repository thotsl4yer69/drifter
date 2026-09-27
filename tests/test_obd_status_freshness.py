"""Running-bridge proof must not turn malformed/old evidence into success."""
import json
from types import SimpleNamespace

import pytest

import obd_setup_strict as strict


def install_status(monkeypatch, **overrides):
    status = {
        'state': 'online', 'adapter_ok': True, 'ecu_ok': True,
        'ts': 1995.0, 'protocol': 'ISO 9141-2',
    }
    status.update(overrides)
    monkeypatch.setattr(strict.base, '_service_state', lambda: {'active': True})
    monkeypatch.setattr(strict.base, '_configured_cfg', lambda: SimpleNamespace(mode='serial'))
    monkeypatch.setattr(strict.base, '_mqtt_status', lambda _timeout: json.dumps(status))
    return status


@pytest.mark.parametrize('timestamp', [
    float('nan'), float('inf'), float('-inf'), True, False, None,
    0, -1, 'invalid', [], {}, 10 ** 400,
])
def test_invalid_timestamp_cannot_prove_vehicle(monkeypatch, timestamp):
    install_status(monkeypatch, ts=timestamp)
    result = strict.bridge_status_probe(now=2000.0)
    assert result['ok'] is False
    assert result['ecu_ok'] is False
    assert strict.result_code(result) != 0
    assert result['error']
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize('offset', [0.001, 1.0, 3600.0])
def test_future_status_cannot_prove_vehicle_after_clock_rollback(monkeypatch, offset):
    install_status(monkeypatch, ts=2000.0 + offset)
    result = strict.bridge_status_probe(now=2000.0)
    assert result['ok'] is False
    assert result['ecu_ok'] is False
    assert strict.result_code(result) == strict.EXIT_ADAPTER_ONLY
    assert 'future' in result['error']


@pytest.mark.parametrize('clock', [
    float('nan'), float('inf'), float('-inf'), True, False, 0, -1,
    'invalid', [], 10 ** 400,
])
def test_invalid_clock_degrades_instead_of_success_or_exception(monkeypatch, clock):
    install_status(monkeypatch)
    result = strict.bridge_status_probe(now=clock)
    assert result['ok'] is False
    assert result['ecu_ok'] is False
    assert result['status_age_s'] is None
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize('field', ['adapter_ok', 'ecu_ok'])
@pytest.mark.parametrize('value', ['false', 'true', 1, ['true'], {'ok': True}])
def test_only_boolean_true_is_link_proof(monkeypatch, field, value):
    install_status(monkeypatch, **{field: value})
    result = strict.bridge_status_probe(now=2000.0)
    assert result[field] is False
    assert result['ok'] is False
    assert strict.result_code(result) != 0


@pytest.mark.parametrize('age, expected', [(0.0, True), (5.0, True), (20.0, True), (20.001, False)])
def test_freshness_boundary_and_valid_vehicle_proof(monkeypatch, age, expected):
    install_status(monkeypatch, ts=2000.0 - age)
    result = strict.bridge_status_probe(now=2000.0)
    assert result['ok'] is expected
    assert result['ecu_ok'] is expected
    assert result['status_age_s'] == pytest.approx(round(age, 3))
    assert result['adapter_ok'] is True  # stale adapter evidence is historical, not ECU proof


def test_numeric_timestamp_string_remains_compatible(monkeypatch):
    install_status(monkeypatch, ts='1995.0')
    assert strict.bridge_status_probe(now=2000.0)['ok'] is True


def test_rejected_running_status_does_not_open_second_elm_link(monkeypatch, capsys):
    install_status(monkeypatch, ts=float('nan'))
    monkeypatch.setattr(strict.time, 'time', lambda: 2000.0)

    def forbidden(_cfg):
        raise AssertionError('active bridge still owns the ELM link')

    monkeypatch.setattr(strict, 'runtime_probe', forbidden)
    rc = strict.cmd_test(SimpleNamespace(json=True))
    result = json.loads(capsys.readouterr().out)
    assert rc == strict.EXIT_ADAPTER_ONLY
    assert result['rc'] == rc
    assert result['ok'] is False
    assert result['source'] == 'running_bridge_status'


def test_inactive_bridge_still_permits_direct_probe(monkeypatch):
    monkeypatch.setattr(strict.base, '_service_state', lambda: {'active': False})
    assert strict.bridge_status_probe(now=2000.0) is None


@pytest.mark.parametrize('payload', ['', '{invalid', '[]', 'null'])
def test_missing_or_malformed_status_never_proves_link(monkeypatch, payload):
    install_status(monkeypatch)
    monkeypatch.setattr(strict.base, '_mqtt_status', lambda _timeout: payload)
    result = strict.bridge_status_probe(now=2000.0)
    assert result['ok'] is False
    assert result['ecu_ok'] is False
    assert strict.result_code(result) == strict.EXIT_ADAPTER_UNREACHABLE

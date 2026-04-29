"""Tests for src/diagnose.py — fleet-contract operator CLI.

These tests cover the JSON output shape, exit-code semantics, and the
graceful-degradation behaviour of individual checks (missing tools,
missing devices, etc.) — i.e. the contract surface the fleet
`mesh status drifter` probe relies on.
"""
from __future__ import annotations

import io
import json
import socket
import subprocess
import sys
import threading
from contextlib import contextmanager

import pytest

sys.path.insert(0, 'src')

import diagnose  # noqa: E402


# ── CheckResult ────────────────────────────────────────────────────

def test_check_result_to_dict_round_trip():
    r = diagnose.CheckResult('foo', True, 'all good', fatal=True)
    d = r.to_dict()
    assert d == {'name': 'foo', 'ok': True, 'message': 'all good', 'fatal': True}


# ── Argparse / main() ─────────────────────────────────────────────

def test_main_requires_subcommand(capsys):
    with pytest.raises(SystemExit):
        diagnose.main([])
    err = capsys.readouterr().err
    assert 'required' in err or 'arguments' in err


def test_main_diagnose_json_runs_and_emits_valid_json(capsys, monkeypatch):
    """All checks stubbed to PASS → rc=0, JSON parses, ok=True."""
    monkeypatch.setattr(diagnose, 'check_systemd_units',
                        lambda: [diagnose.CheckResult('service:foo', True, '')])
    monkeypatch.setattr(diagnose, 'check_can_bridge',
                        lambda: diagnose.CheckResult('can0', True, 'UP'))
    monkeypatch.setattr(diagnose, 'check_realdash_socket',
                        lambda: diagnose.CheckResult('realdash', True, 'TCP open'))
    monkeypatch.setattr(diagnose, 'check_audio_devices',
                        lambda: diagnose.CheckResult('audio', True, '1 device'))
    monkeypatch.setattr(diagnose, 'check_rf_sdr',
                        lambda: diagnose.CheckResult('rf_sdr', True, 'present'))
    monkeypatch.setattr(diagnose, 'check_mqtt_broker',
                        lambda: diagnose.CheckResult('mqtt', True, 'reachable'))
    monkeypatch.setattr(diagnose, 'check_dashboard_healthz',
                        lambda: diagnose.CheckResult('healthz', True, '200'))

    rc = diagnose.main(['diagnose', '--json'])
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload['ok'] is True
    assert {c['name'] for c in payload['checks']} >= {
        'service:foo', 'can0', 'realdash', 'audio', 'rf_sdr', 'mqtt', 'healthz',
    }


def test_main_diagnose_fatal_failure_returns_nonzero(capsys, monkeypatch):
    """A fatal failed check → rc=1."""
    monkeypatch.setattr(diagnose, 'check_systemd_units',
                        lambda: [diagnose.CheckResult('service:x', False, 'inactive', fatal=True)])
    for name in ('check_can_bridge', 'check_realdash_socket',
                 'check_audio_devices', 'check_rf_sdr',
                 'check_mqtt_broker', 'check_dashboard_healthz'):
        monkeypatch.setattr(diagnose, name,
                            lambda _n=name: diagnose.CheckResult(_n, True, ''))
    rc = diagnose.main(['diagnose', '--json'])
    assert rc == 1


def test_main_non_fatal_warning_does_not_fail(capsys, monkeypatch):
    """rf_sdr / audio are warn-only — failing them shouldn't fail the run."""
    monkeypatch.setattr(diagnose, 'check_systemd_units',
                        lambda: [diagnose.CheckResult('service:x', True, '')])
    monkeypatch.setattr(diagnose, 'check_can_bridge',
                        lambda: diagnose.CheckResult('can0', True, 'UP'))
    monkeypatch.setattr(diagnose, 'check_realdash_socket',
                        lambda: diagnose.CheckResult('realdash', True, 'open'))
    monkeypatch.setattr(diagnose, 'check_audio_devices',
                        lambda: diagnose.CheckResult('audio', False, 'no card', fatal=False))
    monkeypatch.setattr(diagnose, 'check_rf_sdr',
                        lambda: diagnose.CheckResult('rf_sdr', False, 'no dongle', fatal=False))
    monkeypatch.setattr(diagnose, 'check_mqtt_broker',
                        lambda: diagnose.CheckResult('mqtt', True, ''))
    monkeypatch.setattr(diagnose, 'check_dashboard_healthz',
                        lambda: diagnose.CheckResult('healthz', True, '200'))

    rc = diagnose.main(['diagnose', '--json'])
    assert rc == 0


def test_main_check_raising_is_caught(capsys, monkeypatch):
    """A raising check converts to a fatal CheckResult, not a crash."""
    def boom():
        raise RuntimeError('disk on fire')

    monkeypatch.setattr(diagnose, 'check_systemd_units', boom)
    for name in ('check_can_bridge', 'check_realdash_socket',
                 'check_audio_devices', 'check_rf_sdr',
                 'check_mqtt_broker', 'check_dashboard_healthz'):
        monkeypatch.setattr(diagnose, name,
                            lambda _n=name: diagnose.CheckResult(_n, True, ''))

    rc = diagnose.main(['diagnose', '--json'])
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    raised = next(c for c in payload['checks'] if 'raised' in c['message'])
    assert raised['ok'] is False


# ── Individual probes ─────────────────────────────────────────────

def test_check_systemd_units_returns_one_result_per_service(monkeypatch):
    """Stub `systemctl is-active` so we don't poke the host."""
    monkeypatch.setattr(diagnose.shutil, 'which', lambda _: '/usr/bin/systemctl')

    def fake_run(cmd, **_):
        # cmd is ['systemctl', 'is-active', '<unit>']
        unit = cmd[-1]
        out = 'active' if unit == 'drifter-canbridge' else 'inactive'
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr='')

    monkeypatch.setattr(diagnose.subprocess, 'run', fake_run)

    from config import SERVICES
    results = diagnose.check_systemd_units()
    assert len(results) == len(SERVICES)
    by_name = {r.name: r for r in results}
    assert by_name['service:drifter-canbridge'].ok is True
    assert by_name['service:drifter-alerts'].ok is False


def test_check_systemd_units_when_systemctl_missing(monkeypatch):
    monkeypatch.setattr(diagnose.shutil, 'which', lambda _: None)
    results = diagnose.check_systemd_units()
    assert len(results) == 1
    assert results[0].ok is False
    assert 'systemctl' in results[0].message


def test_check_can_bridge_no_iproute(monkeypatch):
    monkeypatch.setattr(diagnose.shutil, 'which', lambda x: None)
    r = diagnose.check_can_bridge()
    assert r.ok is False
    assert 'iproute2' in r.message


def test_check_realdash_socket_unreachable(monkeypatch):
    """If the bridge isn't running, we get a fatal failure with the port."""
    monkeypatch.setattr(diagnose, 'REALDASH_TCP_PORT', 1)  # privileged, never listening
    r = diagnose.check_realdash_socket()
    assert r.ok is False
    assert '127.0.0.1:1' in r.message


def test_check_realdash_socket_reachable():
    """Bind a throwaway TCP server on a free port, verify the probe sees it."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('127.0.0.1', 0))
    sock.listen(1)
    port = sock.getsockname()[1]

    accepted = threading.Event()

    def _accept():
        try:
            conn, _ = sock.accept()
            conn.close()
        except OSError:
            pass
        finally:
            accepted.set()

    t = threading.Thread(target=_accept, daemon=True)
    t.start()
    try:
        original = diagnose.REALDASH_TCP_PORT
        diagnose.REALDASH_TCP_PORT = port
        r = diagnose.check_realdash_socket()
    finally:
        diagnose.REALDASH_TCP_PORT = original
        sock.close()
        accepted.wait(timeout=1)

    assert r.ok is True


def test_check_rf_sdr_no_lsusb(monkeypatch):
    monkeypatch.setattr(diagnose.shutil, 'which', lambda _: None)
    r = diagnose.check_rf_sdr()
    assert r.ok is False
    assert r.fatal is False  # rf_sdr is warn-only


def test_check_rf_sdr_detects_realtek(monkeypatch):
    monkeypatch.setattr(diagnose.shutil, 'which', lambda _: '/usr/bin/lsusb')
    monkeypatch.setattr(diagnose.subprocess, 'run',
                        lambda *a, **k: subprocess.CompletedProcess(
                            a[0], 0,
                            stdout='Bus 001 Device 005: ID 0bda:2838 Realtek RTL2838\n',
                            stderr=''))
    r = diagnose.check_rf_sdr()
    assert r.ok is True


def test_check_mqtt_broker_unreachable(monkeypatch):
    monkeypatch.setattr(diagnose, 'MQTT_HOST', '127.0.0.1')
    monkeypatch.setattr(diagnose, 'MQTT_PORT', 1)  # privileged, never listening
    r = diagnose.check_mqtt_broker()
    assert r.ok is False


def test_check_dashboard_healthz_unreachable(monkeypatch):
    monkeypatch.setattr(diagnose, 'DASHBOARD_PORT', 1)  # privileged, never listening
    r = diagnose.check_dashboard_healthz()
    assert r.ok is False

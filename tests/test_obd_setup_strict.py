"""Release-gate tests for the field-facing OBD CLI contract."""
from pathlib import Path
from types import SimpleNamespace

import obd_setup_strict as strict


def _result(*, adapter=False, ecu=False):
    return {
        "ok": bool(adapter and ecu),
        "adapter_ok": bool(adapter),
        "ecu_ok": bool(ecu),
        "mode": "serial",
        "error": "ECU not proved" if adapter and not ecu else "",
    }


def test_result_code_distinguishes_vehicle_adapter_and_transport_states():
    assert strict.result_code(_result(adapter=True, ecu=True)) == 0
    assert strict.result_code(_result(adapter=True, ecu=False)) == 3
    assert strict.result_code(_result(adapter=False, ecu=False)) == 2


def test_cmd_test_does_not_report_adapter_only_as_success(monkeypatch, capsys):
    monkeypatch.setattr(strict.base, "_configured_cfg", lambda: object())
    monkeypatch.setattr(strict.base, "probe", lambda _cfg: _result(adapter=True, ecu=False))
    monkeypatch.setattr(strict.base, "_print_probe", lambda result: print("probe", result["ecu_ok"]))

    rc = strict.cmd_test(SimpleNamespace(json=False))

    assert rc == 3
    assert "probe False" in capsys.readouterr().out


def test_json_test_includes_degraded_exit_code(monkeypatch, capsys):
    monkeypatch.setattr(strict.base, "_configured_cfg", lambda: object())
    monkeypatch.setattr(strict.base, "probe", lambda _cfg: _result(adapter=True, ecu=False))

    rc = strict.cmd_test(SimpleNamespace(json=True))
    output = capsys.readouterr().out

    assert rc == 3
    assert '"adapter_ok": true' in output
    assert '"ecu_ok": false' in output
    assert '"rc": 3' in output


def test_strict_activate_persists_adapter_but_returns_degraded_without_ecu(monkeypatch):
    calls = []
    persisted = []
    cfg = SimpleNamespace(mode="serial")

    monkeypatch.setattr(strict.base, "_require_root", lambda: None)
    monkeypatch.setattr(strict.base, "_service", lambda action: calls.append(action))
    monkeypatch.setattr(strict.base, "probe", lambda _cfg: _result(adapter=True, ecu=False))
    monkeypatch.setattr(strict.base, "_print_probe", lambda _result: None)
    monkeypatch.setattr(strict.base, "_persist_cfg", lambda value: persisted.append(value))
    monkeypatch.setattr(strict.base, "_mqtt_status", lambda _timeout: "")
    monkeypatch.setattr(strict.time, "sleep", lambda _seconds: None)

    rc = strict._strict_activate(cfg)

    assert rc == 3
    assert persisted == [cfg]
    assert calls == ["stop", "restart"]


def test_strict_activate_never_persists_unreachable_adapter(monkeypatch):
    calls = []
    persisted = []
    cfg = SimpleNamespace(mode="serial")

    monkeypatch.setattr(strict.base, "_require_root", lambda: None)
    monkeypatch.setattr(strict.base, "_service", lambda action: calls.append(action))
    monkeypatch.setattr(strict.base, "probe", lambda _cfg: _result(adapter=False, ecu=False))
    monkeypatch.setattr(strict.base, "_print_probe", lambda _result: None)
    monkeypatch.setattr(strict.base, "_persist_cfg", lambda value: persisted.append(value))

    rc = strict._strict_activate(cfg)

    assert rc == 2
    assert persisted == []
    assert calls == ["stop", "restart"]


def test_setup_reprobes_persisted_link_and_requires_ecu(monkeypatch):
    calls = []
    monkeypatch.setattr(strict.base, "cmd_setup", lambda _args: 0)
    monkeypatch.setattr(strict.base, "_configured_cfg", lambda: object())
    monkeypatch.setattr(strict.base, "_service", lambda action: calls.append(action))
    monkeypatch.setattr(strict.base, "probe", lambda _cfg: _result(adapter=True, ecu=False))
    monkeypatch.setattr(strict.base, "_print_probe", lambda _result: None)

    rc = strict.cmd_setup(SimpleNamespace())

    assert rc == 3
    assert calls == ["stop", "restart"]


def test_operator_cli_routes_obd_through_strict_wrapper():
    text = Path("bin/drifter").read_text(encoding="utf-8")
    assert 'exec "$PY" /opt/drifter/obd_setup_strict.py "$@"' in text

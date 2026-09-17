"""Release-gate tests for the field-facing OBD CLI contract."""
import json
from pathlib import Path
from types import SimpleNamespace

import obd_setup_strict as strict


def _result(*, adapter=False, ecu=False, attempt=""):
    return {
        "ok": bool(adapter and ecu),
        "adapter_ok": bool(adapter),
        "ecu_ok": bool(ecu),
        "mode": "serial",
        "protocol_attempt": attempt,
        "error": "ECU not proved" if adapter and not ecu else "",
    }


class FakeStream:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_result_code_distinguishes_vehicle_adapter_and_transport_states():
    assert strict.result_code(_result(adapter=True, ecu=True)) == 0
    assert strict.result_code(_result(adapter=True, ecu=False)) == 3
    assert strict.result_code(_result(adapter=False, ecu=False)) == 2


def test_runtime_probe_uses_production_initializer_and_reports_fallback(monkeypatch):
    stream = FakeStream()
    cfg = SimpleNamespace(mode="serial")
    calls = []
    monkeypatch.setattr(strict.base.elm_link, "open_elm_link", lambda _cfg: (stream, "fake ELM"))

    def initialise(value):
        calls.append(value)
        return {
            "adapter_ok": True,
            "ecu_ok": True,
            "protocol": "ISO 9141-2",
            "identity": "ELM327 v1.5",
            "protocol_attempt": "3",
            "reason": "online",
        }

    monkeypatch.setattr(strict.obd_bridge, "initialise_elm", initialise)
    monkeypatch.setattr(strict.obd_bridge, "_query_pid", lambda _stream, _pid: [0x0C, 0x80])

    result = strict.runtime_probe(cfg)

    assert calls == [stream]
    assert result["ok"] is True
    assert result["adapter_ok"] is True
    assert result["ecu_ok"] is True
    assert result["protocol"] == "ISO 9141-2"
    assert result["protocol_attempt"] == "3"
    assert result["rpm"] == 800.0
    assert result["source"] == "direct_runtime_probe"
    assert stream.closed is True


def test_runtime_probe_adapter_only_is_explicitly_degraded(monkeypatch):
    stream = FakeStream()
    cfg = SimpleNamespace(mode="serial")
    monkeypatch.setattr(strict.base.elm_link, "open_elm_link", lambda _cfg: (stream, "fake ELM"))
    monkeypatch.setattr(
        strict.obd_bridge,
        "initialise_elm",
        lambda _stream: {
            "adapter_ok": True,
            "ecu_ok": False,
            "protocol": "auto (ECU waiting)",
            "identity": "ELM327 v1.5",
            "protocol_attempt": "auto+3/4/5/6",
            "reason": "ecu_waiting",
        },
    )

    result = strict.runtime_probe(cfg)

    assert result["ok"] is False
    assert result["adapter_ok"] is True
    assert result["ecu_ok"] is False
    assert result["protocol_attempt"] == "auto+3/4/5/6"
    assert "ECU communication was not proved" in result["error"]
    assert stream.closed is True


def test_bridge_status_probe_uses_fresh_running_bridge_without_opening_elm(monkeypatch):
    now = 2000.0
    monkeypatch.setattr(strict.base, "_service_state", lambda: {"active": True})
    monkeypatch.setattr(strict.base, "_configured_cfg", lambda: SimpleNamespace(mode="bluetooth"))
    monkeypatch.setattr(
        strict.base,
        "_mqtt_status",
        lambda _timeout: json.dumps({
            "state": "online",
            "adapter_ok": True,
            "ecu_ok": True,
            "device": "bluetooth:AA:BB",
            "identity": "ELM327 v1.5",
            "protocol": "ISO 9141-2",
            "ts": now - 5,
        }),
    )
    monkeypatch.setattr(
        strict.base.elm_link,
        "open_elm_link",
        lambda _cfg: (_ for _ in ()).throw(AssertionError("must not open a second ELM link")),
    )

    result = strict.bridge_status_probe(now=now)

    assert result is not None
    assert result["ok"] is True
    assert result["source"] == "running_bridge_status"
    assert result["status_age_s"] == 5.0


def test_bridge_status_probe_rejects_stale_retained_success(monkeypatch):
    now = 2000.0
    monkeypatch.setattr(strict.base, "_service_state", lambda: {"active": True})
    monkeypatch.setattr(strict.base, "_configured_cfg", lambda: SimpleNamespace(mode="serial"))
    monkeypatch.setattr(
        strict.base,
        "_mqtt_status",
        lambda _timeout: json.dumps({
            "state": "online",
            "adapter_ok": True,
            "ecu_ok": True,
            "protocol": "ISO 9141-2",
            "ts": now - strict.BRIDGE_STATUS_MAX_AGE_SEC - 1,
        }),
    )

    result = strict.bridge_status_probe(now=now)

    assert result is not None
    assert result["ok"] is False
    assert result["adapter_ok"] is True
    assert result["ecu_ok"] is False
    assert "stale" in result["error"]


def test_cmd_test_does_not_report_adapter_only_as_success(monkeypatch, capsys):
    monkeypatch.setattr(strict, "bridge_status_probe", lambda: None)
    monkeypatch.setattr(strict.base, "_configured_cfg", lambda: object())
    monkeypatch.setattr(strict, "runtime_probe", lambda _cfg: _result(adapter=True, ecu=False))
    monkeypatch.setattr(strict.base, "_print_probe", lambda result: print("probe", result["ecu_ok"]))

    rc = strict.cmd_test(SimpleNamespace(json=False))

    assert rc == 3
    assert "probe False" in capsys.readouterr().out


def test_cmd_test_prefers_running_bridge_status(monkeypatch):
    live = {**_result(adapter=True, ecu=True), "source": "running_bridge_status"}
    monkeypatch.setattr(strict, "bridge_status_probe", lambda: live)
    monkeypatch.setattr(
        strict,
        "runtime_probe",
        lambda _cfg: (_ for _ in ()).throw(AssertionError("direct probe must not run")),
    )
    assert strict.cmd_test(SimpleNamespace(json=True)) == 0


def test_json_test_includes_degraded_exit_code(monkeypatch, capsys):
    monkeypatch.setattr(strict, "bridge_status_probe", lambda: None)
    monkeypatch.setattr(strict.base, "_configured_cfg", lambda: object())
    monkeypatch.setattr(strict, "runtime_probe", lambda _cfg: _result(adapter=True, ecu=False))

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
    monkeypatch.setattr(strict, "runtime_probe", lambda _cfg: _result(adapter=True, ecu=False))
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
    monkeypatch.setattr(strict, "runtime_probe", lambda _cfg: _result(adapter=False, ecu=False))
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
    monkeypatch.setattr(strict, "runtime_probe", lambda _cfg: _result(adapter=True, ecu=False))
    monkeypatch.setattr(strict.base, "_print_probe", lambda _result: None)

    rc = strict.cmd_setup(SimpleNamespace())

    assert rc == 3
    assert calls == ["stop", "restart"]


def test_operator_cli_routes_obd_through_strict_wrapper():
    text = Path("bin/drifter").read_text(encoding="utf-8")
    assert 'exec "$PY" /opt/drifter/obd_setup_strict.py "$@"' in text

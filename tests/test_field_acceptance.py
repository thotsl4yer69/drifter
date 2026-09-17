"""Regression coverage for the physical field-acceptance gate."""
from pathlib import Path
from types import SimpleNamespace

import field_acceptance as acceptance


def _live_ok():
    return {
        "obd": {"ok": True},
        "display": {"ok": True},
        "services_ok": True,
        "power": {"ok": True},
    }


def test_telemetry_summary_requires_all_four_streams_continuous():
    start = 100.0
    end = 130.0
    samples = {
        name: [(100.0, 1.0), (110.0, 2.0), (130.0, 3.0)]
        for name in acceptance.TELEMETRY_TOPICS
    }
    report = acceptance._summarize_telemetry(samples, start, end, 30.0, [])
    assert report["ok"] is True
    assert all(sensor["ok"] for sensor in report["sensors"].values())


def test_telemetry_summary_fails_on_obd_fault_even_with_sensor_data():
    samples = {
        name: [(0.0, 1.0), (5.0, 2.0), (10.0, 3.0)]
        for name in acceptance.TELEMETRY_TOPICS
    }
    report = acceptance._summarize_telemetry(
        samples,
        0.0,
        10.0,
        30.0,
        [{"state": "bus_unreachable"}],
    )
    assert report["ok"] is False


def test_status_requires_ten_unique_boots_full_soak_and_physical_gates(monkeypatch, tmp_path, capsys):
    state_file = tmp_path / "acceptance.json"
    state = {
        "cold_boots": [
            {"boot_id": f"boot-{idx}", "ok": True}
            for idx in range(10)
        ],
        "telemetry_soak": {
            "ok": True,
            "duration_s": acceptance.MIN_SOAK_SECONDS,
            "evidence": "/tmp/soak.json",
        },
        "physical": {
            gate: {"ok": True, "recorded": "now"}
            for gate in acceptance.PHYSICAL_GATES
        },
    }
    acceptance._save_state(state, state_file)
    monkeypatch.setattr(acceptance, "STATE_PATH", state_file)
    monkeypatch.setattr(acceptance, "_live_checks", _live_ok)

    rc = acceptance._status(SimpleNamespace(no_live=False))
    output = capsys.readouterr().out

    assert rc == 0
    assert '"signoff_ready": true' in output


def test_duplicate_boot_id_does_not_inflate_gate(monkeypatch, tmp_path, capsys):
    state_file = tmp_path / "acceptance.json"
    acceptance._save_state(
        {
            "cold_boots": [{"boot_id": "same", "ok": True}],
            "telemetry_soak": {},
            "physical": {},
        },
        state_file,
    )
    monkeypatch.setattr(acceptance, "STATE_PATH", state_file)
    monkeypatch.setattr(acceptance, "_live_checks", _live_ok)
    monkeypatch.setattr(acceptance, "_boot_id", lambda: "same")
    monkeypatch.setattr(acceptance, "_field_dump", lambda: "/tmp/dump.txt")

    rc = acceptance._record_cold_boot(SimpleNamespace(no_replug=True, note="repeat"))
    output = capsys.readouterr().out

    assert rc == 0
    assert '"passed_unique_boots": 1' in output
    saved = acceptance._load_state(state_file)
    assert len(saved["cold_boots"]) == 1


def test_cold_boot_refuses_pass_when_power_flags_are_not_clean(monkeypatch, tmp_path):
    state_file = tmp_path / "acceptance.json"
    monkeypatch.setattr(acceptance, "STATE_PATH", state_file)
    monkeypatch.setattr(acceptance, "_boot_id", lambda: "boot-x")
    monkeypatch.setattr(acceptance, "_field_dump", lambda: "/tmp/dump.txt")
    monkeypatch.setattr(
        acceptance,
        "_live_checks",
        lambda: {
            "obd": {"ok": True},
            "display": {"ok": True},
            "services_ok": True,
            "power": {"ok": False, "flags": 0x50000},
        },
    )

    assert acceptance._record_cold_boot(SimpleNamespace(no_replug=True, note="")) == 2
    assert acceptance._load_state(state_file)["cold_boots"][0]["ok"] is False


def test_cli_routes_acceptance_to_harness():
    text = Path("bin/drifter").read_text(encoding="utf-8")
    assert 'acceptance|accept)' in text
    assert 'exec "$PY" /opt/drifter/field_acceptance.py "$@"' in text

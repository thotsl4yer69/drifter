"""Regression tests for the DRIFTER main-branch self updater."""
from __future__ import annotations

import subprocess

import auto_update as updater


def test_telemetry_parser_accepts_value_envelopes_and_plain_numbers():
    text = "\n".join(
        [
            'drifter/engine/rpm {"value": 735}',
            'drifter/vehicle/speed 0',
            'drifter/engine/coolant {"value": 90}',
        ]
    )
    values = updater._telemetry_values(text)
    assert values[updater.RPM_TOPIC] == 735.0
    assert values[updater.SPEED_TOPIC] == 0.0
    assert len(values) == 2


def test_vehicle_active_defers_update_when_engine_running(monkeypatch):
    monkeypatch.setattr(updater.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        updater,
        "_run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0],
            0,
            'drifter/engine/rpm {"value": 710}\n'
            'drifter/vehicle/speed {"value": 0}\n',
            "",
        ),
    )
    active, detail = updater._vehicle_active()
    assert active is True
    assert detail["rpm"] == 710.0


def test_vehicle_inactive_when_observed_rpm_and_speed_are_safe(monkeypatch):
    monkeypatch.setattr(updater.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        updater,
        "_run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0],
            0,
            'drifter/engine/rpm {"value": 0}\n'
            'drifter/vehicle/speed {"value": 0}\n',
            "",
        ),
    )
    active, detail = updater._vehicle_active()
    assert active is False
    assert detail["speed"] == 0.0


def test_preflight_rejects_diff_check_failure(monkeypatch):
    monkeypatch.setattr(
        updater,
        "_git",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            ["git"], 2, "", "whitespace error"
        ),
    )
    ok, detail = updater._preflight("deadbeef")
    assert ok is False
    assert "whitespace error" in detail


def test_rollback_restores_exact_previous_tree_before_redeploy(monkeypatch):
    calls = []

    def fake_git(*args, **kwargs):
        calls.append(("git", args))
        return subprocess.CompletedProcess(["git"], 0, "", "")

    def fake_deploy():
        calls.append(("deploy", ()))
        return subprocess.CompletedProcess(["deploy"], 0, "DEPLOY: ok", "")

    monkeypatch.setattr(updater, "_git", fake_git)
    monkeypatch.setattr(updater, "_deploy", fake_deploy)

    ok, _detail = updater._rollback("abc123")
    assert ok is True
    assert calls[0] == ("git", ("reset", "--hard", "abc123"))
    assert calls[1] == ("git", ("clean", "-fd"))
    assert calls[2][0] == "deploy"


def test_systemd_timer_is_installed_by_existing_installer_contract():
    install_text = open("install.sh", encoding="utf-8").read()
    assert "services/drifter-*.timer" in install_text
    assert 'systemctl enable "$tmr_name"' in install_text


def test_updater_source_is_deployed_by_existing_installer_contract():
    install_text = open("install.sh", encoding="utf-8").read()
    assert 'src/*.py' in install_text

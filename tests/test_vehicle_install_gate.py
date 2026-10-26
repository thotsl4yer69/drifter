"""Regression gates for DRIFTER's pre-vehicle deployment and updater."""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import auto_update as updater

ROOT = Path(__file__).resolve().parents[1]


def test_telemetry_cannot_authorize_with_invalid_or_simulated_zeroes():
    payload = "\n".join([
        'drifter/engine/rpm {"value": false}',
        'drifter/vehicle/speed {"value": -1}',
        'drifter/engine/rpm {"value": 0, "simulated": true}',
        'drifter/vehicle/speed {"value": 0, "source": "replay"}',
        'drifter/engine/rpm {"value": 0}',
        'drifter/vehicle/speed {"value": 0}',
    ])
    values = updater._telemetry_values(payload)
    assert values == {updater.RPM_TOPIC: 0.0, updater.SPEED_TOPIC: 0.0}
    assert updater._telemetry_values('drifter/vehicle/speed {"value": false}') == {}
    assert updater._telemetry_values('drifter/engine/rpm -1') == {}



def test_timestamped_probe_rejects_old_and_future_frames():
    now = time.time()
    cases = [
        (now - 90, False), (now + 90, False),
        (float("nan"), False), (now - 1, True),
    ]
    for ts, expected in cases:
        packet = updater.RPM_TOPIC + " " + json.dumps({"value": 0, "ts": ts})
        assert (updater.RPM_TOPIC in updater._telemetry_values(packet)) is expected


def test_probe_ignores_broker_retained_values(monkeypatch):
    seen = []

    def fake_run(argv, **kwargs):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 27, "", "Timed out")

    monkeypatch.setattr(updater.shutil, "which", lambda name: "/usr/bin/mosquitto_sub")
    monkeypatch.setattr(updater, "_run", fake_run)
    active, detail = updater._vehicle_active()
    assert active is None
    assert detail["safe_to_update"] is False
    assert "-R" in seen[0], "retained MQTT snapshots must not authorize updates"


def test_timer_default_disables_unattended_updates(monkeypatch):
    states = []
    monkeypatch.delenv("DRIFTER_AUTO_UPDATE", raising=False)
    monkeypatch.setattr(updater, "_save_state", states.append)
    assert updater.run_update() == 0
    assert states[-1]["status"] == "disabled"


def test_operator_update_still_checks_vehicle_state(monkeypatch, tmp_path):
    repo = tmp_path / "drifter"
    (repo / ".git").mkdir(parents=True)
    monkeypatch.delenv("DRIFTER_AUTO_UPDATE", raising=False)
    monkeypatch.setattr(updater, "REPO", repo)
    monkeypatch.setattr(updater, "LOCK_PATH", tmp_path / "update.lock")
    monkeypatch.setattr(updater, "_current_branch", lambda: updater.BRANCH)
    monkeypatch.setattr(updater, "_checkout_clean", lambda: True)
    monkeypatch.setattr(updater, "_vehicle_active", lambda: (None, {"safe_to_update": False}))
    states = []
    monkeypatch.setattr(updater, "_save_state", states.append)
    assert updater.run_update(manual=True) == 0
    assert states[-1]["status"] == "deferred"
    assert states[-1]["reason"] == "vehicle state unknown"


def test_cli_selects_manual_operator_path():
    script = (ROOT / "bin" / "drifter").read_text(encoding="utf-8")
    assert 'auto_update.py --manual "$@"' in script
    assert "ExecStart=/opt/drifter/venv/bin/python3 /opt/drifter/auto_update.py" in (
        ROOT / "services" / "drifter-update.service"
    ).read_text(encoding="utf-8")


def test_installer_starts_only_saved_persona():
    script = (ROOT / "scripts" / "oneshot.sh").read_text(encoding="utf-8")
    stage = script.split('stage_start 40 "activate selected vehicle persona"', 1)[1].split(
        "stage_ok 45", 1
    )[0]
    assert 'TARGET_MODE="${TARGET_MODE:-diag}"' in stage
    assert 'drifter mode "$TARGET_MODE"' in stage
    assert 'systemctl restart "${ACTIVE_SERVICES[@]}"' in stage
    assert 'systemctl restart "$svc"' not in stage
    assert 'systemctl enable "$svc"' not in stage
    subprocess.run(["bash", "-n", str(ROOT / "scripts" / "oneshot.sh")], check=True)


def test_direct_installer_does_not_enable_every_monitored_service():
    script = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert 'INSTALL_MODE="${INSTALL_MODE:-diag}"' in script
    assert 'INSTALL_MODE_SERVICES=' in script
    assert 'systemctl disable --now "$svc"' in script
    subprocess.run(["bash", "-n", str(ROOT / "install.sh")], check=True)


def test_updater_cli_manual_and_timer_are_distinct(monkeypatch):
    observed = []
    monkeypatch.setattr(updater, "run_update", lambda *, manual=False: (observed.append(manual), 0)[1])
    assert updater.main(["--manual"]) == 0
    assert updater.main([]) == 0
    assert observed == [True, False]

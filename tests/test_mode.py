"""Tests for src/mode.py — operator-mode CLI."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import mode  # noqa: E402
import config  # noqa: E402


# ── plan() ─────────────────────────────────────────────────────────────

def test_plan_drive_excludes_foot_only():
    p = mode.plan('drive')
    assert 'drifter-flipper' in p['disable']
    assert 'drifter-wardrive' in p['disable']
    assert 'drifter-canbridge' in p['enable']


def test_plan_foot_excludes_drive_only():
    p = mode.plan('foot')
    assert 'drifter-canbridge' in p['disable']
    assert 'drifter-realdash' in p['disable']
    assert 'drifter-rf' in p['disable']  # split decision: rf is drive-only
    assert 'drifter-flipper' in p['enable']
    assert 'drifter-wardrive' in p['enable']


def test_plan_both_includes_all():
    p = mode.plan('both')
    assert set(p['enable']) == set(config.SERVICES)
    assert p['disable'] == []


def test_plan_shared_services_in_every_mode():
    """Shared services must appear in DRIVE, FOOT, and BOTH."""
    for svc in config.SHARED_SERVICES:
        for m in ('drive', 'foot', 'both'):
            assert svc in mode.plan(m)['enable'], f"{svc} missing from {m}"


def test_plan_unknown_mode_raises():
    with pytest.raises(ValueError, match='unknown mode'):
        mode.plan('beast-mode')


# ── read_mode / write_mode ─────────────────────────────────────────────

def test_read_mode_default_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(mode, 'MODE_STATE_PATH', tmp_path / 'nope.state')
    assert mode.read_mode() == config.DEFAULT_MODE


def test_read_mode_returns_persisted_value(monkeypatch, tmp_path):
    p = tmp_path / 'mode.state'
    p.write_text('foot\n')
    monkeypatch.setattr(mode, 'MODE_STATE_PATH', p)
    assert mode.read_mode() == 'foot'


def test_write_mode_round_trips(monkeypatch, tmp_path):
    p = tmp_path / 'mode.state'
    monkeypatch.setattr(mode, 'MODE_STATE_PATH', p)
    mode.write_mode('foot')
    assert mode.read_mode() == 'foot'


def test_read_mode_strips_whitespace(monkeypatch, tmp_path):
    p = tmp_path / 'mode.state'
    p.write_text('  drive  \n\n')
    monkeypatch.setattr(mode, 'MODE_STATE_PATH', p)
    assert mode.read_mode() == 'drive'


# ── switch() ───────────────────────────────────────────────────────────

def test_switch_dry_run_does_not_invoke_systemctl(monkeypatch, tmp_path):
    monkeypatch.setattr(mode, 'MODE_STATE_PATH', tmp_path / 'mode.state')
    calls: list = []
    monkeypatch.setattr(mode, '_systemctl', lambda a, u: (calls.append((a, u)), (0, ''))[1])
    res = mode.switch('foot', dry_run=True)
    assert calls == []
    assert res['dry_run'] is True
    assert not (tmp_path / 'mode.state').exists()  # state untouched on dry-run


def test_switch_writes_state_only_on_success(monkeypatch, tmp_path):
    state_path = tmp_path / 'mode.state'
    monkeypatch.setattr(mode, 'MODE_STATE_PATH', state_path)
    monkeypatch.setattr(mode, '_systemctl', lambda a, u: (0, ''))
    res = mode.switch('foot')
    assert res['errors'] == []
    assert state_path.read_text().strip() == 'foot'


def test_switch_skips_state_write_on_systemctl_failure(monkeypatch, tmp_path):
    state_path = tmp_path / 'mode.state'
    monkeypatch.setattr(mode, 'MODE_STATE_PATH', state_path)

    def fake_systemctl(action, units):
        return (1, 'permission denied') if action == 'enable' else (0, '')
    monkeypatch.setattr(mode, '_systemctl', fake_systemctl)

    res = mode.switch('drive')
    assert res['errors']
    assert not state_path.exists(), 'mode state must not be written when systemctl failed'


# ── status() ───────────────────────────────────────────────────────────

def test_status_reports_drift_for_unexpected_active(monkeypatch, tmp_path):
    monkeypatch.setattr(mode, 'MODE_STATE_PATH', tmp_path / 'mode.state')
    (tmp_path / 'mode.state').write_text('drive\n')

    # All services report active — but flipper and wardrive shouldn't be in DRIVE.
    class FakeRun:
        def __init__(self, stdout): self.stdout = stdout
    monkeypatch.setattr(
        mode.subprocess, 'run',
        lambda *a, **kw: FakeRun('active\n'),
    )
    s = mode.status()
    assert s['mode'] == 'drive'
    assert 'drifter-flipper' in s['drift']
    assert 'drifter-wardrive' in s['drift']
    assert 'drifter-canbridge' not in s['drift']  # belongs in DRIVE


def test_status_no_drift_when_set_matches(monkeypatch, tmp_path):
    monkeypatch.setattr(mode, 'MODE_STATE_PATH', tmp_path / 'mode.state')
    (tmp_path / 'mode.state').write_text('drive\n')
    drive_set = config.MODES['drive']

    class FakeRun:
        def __init__(self, stdout): self.stdout = stdout

    def run(cmd, *_a, **_kw):
        unit = cmd[-1]
        return FakeRun('active\n' if unit in drive_set else 'inactive\n')

    monkeypatch.setattr(mode.subprocess, 'run', run)
    s = mode.status()
    assert s['drift'] == []


# ── main() CLI ─────────────────────────────────────────────────────────

def test_main_status_subcommand_emits_json(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(mode, 'MODE_STATE_PATH', tmp_path / 'mode.state')

    class FakeRun:
        def __init__(self): self.stdout = 'inactive\n'
    monkeypatch.setattr(mode.subprocess, 'run', lambda *a, **kw: FakeRun())

    rc = mode.main(['status', '--json'])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert 'mode' in out and 'drift' in out


def test_main_dry_run_returns_zero(monkeypatch, tmp_path):
    monkeypatch.setattr(mode, 'MODE_STATE_PATH', tmp_path / 'mode.state')
    monkeypatch.setattr(mode, '_systemctl', lambda a, u: (0, ''))
    assert mode.main(['foot', '--dry-run', '--json']) == 0


def test_main_unknown_mode_subcommand_exits_nonzero():
    with pytest.raises(SystemExit) as ei:
        mode.main(['beast-mode'])
    assert ei.value.code != 0

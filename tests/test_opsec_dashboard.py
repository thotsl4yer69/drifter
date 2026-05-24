"""Tests for src/opsec_dashboard.py — OPSEC dashboard.

Focuses on logic that doesn't need the HTTP server up:
  • MqttCache thread safety + ring behaviour
  • Killswitch primitives (mock subprocess)
  • Allowlist constraints (PROBES / TOOLS shape)
"""

from __future__ import annotations

import re
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import opsec_dashboard as od  # noqa: E402


# ── MqttCache ──────────────────────────────────────────────────────────

def test_cache_store_latest_overwrites_per_topic():
    c = od.MqttCache()
    c.store_latest('t/x', {'a': 1})
    c.store_latest('t/x', {'a': 2})
    snap = c.snapshot()
    assert snap['latest']['t/x']['payload'] == {'a': 2}


def test_cache_captures_ring_caps_at_history_size():
    c = od.MqttCache(capture_history=3)
    for i in range(10):
        c.push_capture({'i': i})
    snap = c.snapshot()
    assert len(snap['captures']) == 3
    # Newest first (appendleft).
    assert snap['captures'][0]['payload']['i'] == 9


def test_cache_concurrent_writes_dont_lose_messages():
    c = od.MqttCache(capture_history=500)
    def writer(start):
        for i in range(start, start + 100):
            c.store_latest(f't/{i}', {'i': i})
    threads = [threading.Thread(target=writer, args=(i*100,)) for i in range(5)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert len(c.snapshot()['latest']) == 500


# ── Allowlist shape ────────────────────────────────────────────────────

def test_probes_are_argv_lists_not_shell_strings():
    """No probe may be a shell string — argv only, prevents injection."""
    for name, argv in od.PROBES.items():
        assert isinstance(argv, list), f'{name} not a list'
        assert all(isinstance(a, str) for a in argv), f'{name} non-str arg'
        assert argv, f'{name} empty argv'


def test_tools_have_required_keys_and_argv_list():
    for name, spec in od.TOOLS.items():
        assert {'argv', 'default', 'hint'}.issubset(spec), f'{name} missing keys'
        assert isinstance(spec['argv'], list)


def test_no_probe_or_tool_uses_shell_metacharacters_in_template():
    """Argv templates must not contain shell-meta — they're argv, not shell."""
    metas = re.compile(r'[;&|<>$`\\]')
    for argv in list(od.PROBES.values()) + [s['argv'] for s in od.TOOLS.values()]:
        for token in argv:
            assert not metas.search(token), f'shell-meta in template: {argv}'


# ── Killswitch ─────────────────────────────────────────────────────────

def test_mac_randomize_generates_valid_locally_administered_address(monkeypatch):
    captured = []
    class R:
        def __init__(self): self.returncode = 0; self.stderr = ''
    def fake_run(cmd, *a, **kw):
        captured.append(cmd)
        return R()
    monkeypatch.setattr(od.subprocess, 'run', fake_run)
    res = od.kill_mac_randomize('wlan0')
    assert res['ok']
    assert res['iface'] == 'wlan0'
    # Locally-administered prefix (02:) — valid MAC format.
    assert re.fullmatch(r'02:[0-9a-f]{2}(:[0-9a-f]{2}){4}', res['mac'])
    # Three steps: down, set address, up — in that order.
    assert len(captured) == 3
    assert captured[0][-2:] == ['wlan0', 'down']
    assert 'address' in captured[1]
    assert captured[1][-1] == res['mac']
    assert captured[2][-2:] == ['wlan0', 'up']


def test_mac_randomize_aborts_on_first_failed_step(monkeypatch):
    class R:
        def __init__(self, rc): self.returncode = rc; self.stderr = 'denied'
    calls = {'n': 0}
    def fake_run(cmd, *a, **kw):
        calls['n'] += 1
        return R(1 if calls['n'] == 1 else 0)
    monkeypatch.setattr(od.subprocess, 'run', fake_run)
    res = od.kill_mac_randomize()
    assert not res['ok']
    assert calls['n'] == 1, 'should not run later steps after first failure'


def test_wipe_logs_handles_missing_dirs_gracefully(monkeypatch, tmp_path):
    monkeypatch.setattr(od, 'LOG_DIRS_TO_WIPE', [tmp_path / 'nope'])
    res = od.kill_wipe_logs()
    assert res['ok'] is True
    assert res['deleted'] == []
    assert res['total_bytes'] == 0


def test_wipe_logs_deletes_files_and_reports_bytes(monkeypatch, tmp_path):
    d = tmp_path / 'logs'
    d.mkdir()
    (d / 'a.log').write_bytes(b'hello')
    (d / 'b.log').write_bytes(b'world!!')
    monkeypatch.setattr(od, 'LOG_DIRS_TO_WIPE', [d])
    res = od.kill_wipe_logs()
    assert res['ok']
    assert res['total_bytes'] == 5 + 7
    assert sorted(p['path'].split('/')[-1] for p in res['deleted']) == ['a.log', 'b.log']
    # Files actually gone.
    assert not (d / 'a.log').exists()
    assert not (d / 'b.log').exists()


def test_halt_recon_targets_only_recon_services(monkeypatch):
    issued = []
    class R:
        def __init__(self): self.returncode = 0; self.stderr = ''
    def fake_run(cmd, *a, **kw):
        issued.append(cmd)
        return R()
    monkeypatch.setattr(od.subprocess, 'run', fake_run)
    res = od.kill_halt_recon()
    assert res['ok']
    units_stopped = [c[-1] for c in issued]
    assert units_stopped == ['drifter-flipper', 'drifter-wardrive']
    # Critical: must NOT stop the dashboard or opsec itself — operator
    # would lose UI access mid-action.
    assert 'drifter-opsec' not in units_stopped
    assert 'drifter-dashboard' not in units_stopped

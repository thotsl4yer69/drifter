"""Operator-mode CLI — flip the Pi between DRIVE and FOOT personas.

DRIVE  — vehicle telemetry stack (CAN, RealDash, fbmirror, alerts, …)
FOOT   — battery-pack mobile recon (Flipper, wardrive, …)
BOTH   — every service active (lab / bench)

Mode is persisted in /opt/drifter/mode.state so that after a reboot the same
persona comes back up. Switching enables+starts services in the target mode
and disables+stops services that no longer belong, so a reboot can't re-enable
silenced services behind your back.

Requires root (systemctl enable/disable). Run via `sudo drifter mode …`.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from config import MODES, MODE_STATE_PATH, DEFAULT_MODE, SERVICES


def read_mode() -> str:
    try:
        return Path(MODE_STATE_PATH).read_text(encoding='utf-8').strip() or DEFAULT_MODE
    except OSError:
        return DEFAULT_MODE


def write_mode(mode: str) -> None:
    Path(MODE_STATE_PATH).write_text(mode + '\n', encoding='utf-8')


def _systemctl(action: str, units: list[str]) -> tuple[int, str]:
    """Run a single systemctl action across many units. Returns (rc, stderr)."""
    if not units:
        return 0, ''
    cmd = ['systemctl', action, *units]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode, (r.stderr or '').strip()


def plan(target: str) -> dict:
    """Compute which services to enable+start vs disable+stop for a mode."""
    if target not in MODES:
        raise ValueError(f"unknown mode {target!r}; pick from {sorted(MODES)}")
    on = sorted(MODES[target])
    off = sorted(set(SERVICES) - MODES[target])
    return {'mode': target, 'enable': on, 'disable': off}


def switch(target: str, dry_run: bool = False) -> dict:
    p = plan(target)
    result: dict = {**p, 'dry_run': dry_run, 'errors': []}
    if dry_run:
        return result
    rc1, err1 = _systemctl('disable', ['--now', *p['disable']])
    rc2, err2 = _systemctl('enable',  ['--now', *p['enable']])
    if rc1: result['errors'].append({'phase': 'disable', 'rc': rc1, 'err': err1})
    if rc2: result['errors'].append({'phase': 'enable',  'rc': rc2, 'err': err2})
    if not result['errors']:
        write_mode(target)
    return result


def status() -> dict:
    current = read_mode()
    active: dict[str, bool] = {}
    for svc in SERVICES:
        r = subprocess.run(
            ['systemctl', 'is-active', svc],
            capture_output=True, text=True, timeout=2,
        )
        active[svc] = r.stdout.strip() == 'active'
    return {
        'mode': current,
        'expected': sorted(MODES[current]) if current in MODES else [],
        'active': active,
        'drift': sorted(
            svc for svc, on in active.items()
            if (svc in MODES.get(current, set())) != on
        ),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog='drifter mode', description=__doc__)
    sub = p.add_subparsers(dest='cmd', required=True)
    for m in MODES:
        sp = sub.add_parser(m, help=f'switch to {m.upper()} mode')
        sp.add_argument('--dry-run', action='store_true')
        sp.add_argument('--json', action='store_true')
    sp_status = sub.add_parser('status', help='show current mode + service drift')
    sp_status.add_argument('--json', action='store_true')

    args = p.parse_args(argv)

    if args.cmd == 'status':
        s = status()
        if args.json:
            print(json.dumps(s, indent=2))
        else:
            print(f"mode: {s['mode']}")
            if s['drift']:
                print(f"drift: {', '.join(s['drift'])}")
            else:
                print("drift: none — service set matches mode")
        return 0

    res = switch(args.cmd, dry_run=args.dry_run)
    if args.json:
        print(json.dumps(res, indent=2))
        return 1 if res['errors'] else 0
    print(f"mode → {res['mode']}{' (dry-run)' if res['dry_run'] else ''}")
    print(f"  enable: {len(res['enable'])} services")
    print(f"  disable: {len(res['disable'])} services")
    if res['errors']:
        for e in res['errors']:
            print(f"  ERROR ({e['phase']} rc={e['rc']}): {e['err']}", file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())

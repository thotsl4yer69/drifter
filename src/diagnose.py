#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Diagnose CLI

Fleet-contract diagnostic. Exits 0 if every required check passes,
non-zero otherwise. Used by:

  - `drifter diagnose` from the operator shell
  - scripts/oneshot.sh stage 20 (post-install verification)
  - the fleet `mesh status drifter` probe

UNCAGED TECHNOLOGY — EST 1991
"""
from __future__ import annotations

import argparse
import json
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

try:
    from config import (
        SERVICES, SERVICE_PREFIX, REALDASH_TCP_PORT, MQTT_HOST, MQTT_PORT,
    )
except ImportError:
    # Allow running from repo root for dev — point at src/ on sys.path.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from config import (  # type: ignore
        SERVICES, SERVICE_PREFIX, REALDASH_TCP_PORT, MQTT_HOST, MQTT_PORT,
    )

DASHBOARD_PORT = 8080
WS_TELEMETRY_PORT = 8081

if sys.stdout.isatty():
    GREEN, RED, AMBER, CYAN, NC = (
        '\033[0;32m', '\033[0;31m', '\033[0;33m', '\033[0;36m', '\033[0m')
else:
    GREEN = RED = AMBER = CYAN = NC = ''


class CheckResult:
    __slots__ = ('name', 'ok', 'message', 'fatal')

    def __init__(self, name: str, ok: bool, message: str = '', fatal: bool = True):
        self.name = name
        self.ok = ok
        self.message = message
        self.fatal = fatal

    def to_dict(self) -> dict:
        return {
            'name': self.name, 'ok': self.ok,
            'message': self.message, 'fatal': self.fatal,
        }


def _run(cmd: list[str], timeout: float = 5.0) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _systemctl_is_active(units: list[str], timeout: float = 5.0) -> dict[str, str]:
    """Batched `systemctl is-active u1 u2 …` — one fork instead of N.

    Returns {unit: state-string}. Missing systemctl → all units mapped to
    'unknown'. systemctl prints one state per line in the same order, and
    exits non-zero if any unit isn't active — we still parse stdout fully.
    """
    if not units:
        return {}
    if not shutil.which('systemctl'):
        return {u: 'unknown' for u in units}
    try:
        r = _run(['systemctl', 'is-active', *units], timeout=timeout)
    except subprocess.SubprocessError:
        return {u: 'error' for u in units}
    lines = r.stdout.strip().splitlines() if r.stdout else []
    # Pad with 'unknown' if systemctl returned fewer lines than units (shouldn't
    # happen, but defensive — never raise IndexError on a degraded host).
    states = lines + ['unknown'] * (len(units) - len(lines))
    return dict(zip(units, states))


def _http_get(host: str, port: int, path: str, timeout: float = 3.0) -> tuple[int, bytes]:
    """Tiny urllib wrapper. Returns (status_code, body). 0 on connect error."""
    import urllib.request
    import urllib.error
    url = f'http://{host}:{port}{path}'
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        # 5xx still gives us a status + body (e.g. /healthz 503 with JSON).
        return e.code, e.read() if hasattr(e, 'read') else b''
    except (urllib.error.URLError, OSError):
        return 0, b''


# ── Checks ───────────────────────────────────────────────────────────

def check_systemd_units() -> list[CheckResult]:
    """Every service in config.SERVICES must report `active`."""
    if not shutil.which('systemctl'):
        return [CheckResult('systemd', False, 'systemctl not available')]
    states = _systemctl_is_active(list(SERVICES), timeout=5)
    return [
        CheckResult(
            f'service:{svc}',
            state == 'active',
            '' if state == 'active' else f'state={state}',
        )
        for svc, state in states.items()
    ]


def _link_state(iface: str) -> str | None:
    """Return the link state for `iface`, or None if it doesn't exist."""
    try:
        r = _run(['ip', '-brief', 'link', 'show', iface], timeout=2)
    except subprocess.SubprocessError:
        return None
    if r.returncode != 0:
        return None
    parts = r.stdout.split()
    return parts[1] if len(parts) > 1 else 'UNKNOWN'


def check_can_bridge() -> CheckResult:
    """can0 (or slcan0 fallback) must exist + be UP. If candump is available,
    sniff briefly for any frame as a soft round-trip indicator — the ECU on
    the OBD-II bus answers 0x7DF requests; we just confirm the wire is alive.
    """
    if not shutil.which('ip'):
        return CheckResult('can0', False, 'iproute2 not installed')
    iface = 'can0'
    state = _link_state(iface)
    if state is None:
        iface = 'slcan0'
        state = _link_state(iface)
    if state is None:
        return CheckResult('can0', False, 'no can0 / slcan0 interface')
    if state not in ('UP', 'UNKNOWN'):
        return CheckResult('can0', False, f'link state={state}')

    if not shutil.which('candump'):
        return CheckResult('can0', True, f'link {state} (no candump for round-trip)')
    try:
        r = _run(['candump', '-T', '750', '-n', '1', iface], timeout=3)
    except subprocess.TimeoutExpired:
        return CheckResult('can0', True, f'link {state} (no frames in 3s)')
    except subprocess.SubprocessError as e:
        return CheckResult('can0', True, f'link {state} ({e})')
    # candump exits 0 when -n 1 fires; non-zero on timeout — both are fine.
    # Absence of frames is warn-not-fail since the ECU may be off.
    saw_frame = bool(r.stdout.strip())
    return CheckResult(
        'can0', True,
        f'link {state}, frames {"seen" if saw_frame else "none in 750ms (ECU may be off)"}',
    )


def check_realdash_socket() -> CheckResult:
    """RealDash bridge listens TCP on REALDASH_TCP_PORT (default 35000).
    Connect-and-close is the cheapest reachability probe."""
    try:
        with socket.create_connection(('127.0.0.1', REALDASH_TCP_PORT), timeout=2):
            return CheckResult(
                'realdash', True,
                f'TCP 127.0.0.1:{REALDASH_TCP_PORT} accepting connections',
            )
    except OSError as e:
        return CheckResult(
            'realdash', False,
            f'TCP 127.0.0.1:{REALDASH_TCP_PORT}: {e}',
        )


def check_audio_devices() -> CheckResult:
    """PortAudio + ALSA capture device for the voicein service. We don't
    require sound output here — drifter-voice writes WAV via TTS and
    streams it over WebSocket, not direct ALSA."""
    try:
        import pyaudio  # type: ignore
    except ImportError:
        # PortAudio Python binding not installed yet — fall back to ALSA.
        if shutil.which('arecord'):
            try:
                r = _run(['arecord', '-l'], timeout=3)
                if 'card' in r.stdout:
                    return CheckResult(
                        'audio', True,
                        'arecord lists capture cards (pyaudio missing — voicein may need pip install)',
                        fatal=False,
                    )
            except subprocess.SubprocessError:
                pass
        return CheckResult('audio', False, 'pyaudio not importable, no arecord output', fatal=False)

    try:
        pa = pyaudio.PyAudio()
        try:
            count = pa.get_device_count()
            inputs = [pa.get_device_info_by_index(i) for i in range(count)]
            input_capable = [d for d in inputs if d.get('maxInputChannels', 0) > 0]
        finally:
            pa.terminate()
    except Exception as e:
        return CheckResult('audio', False, f'PortAudio enumeration failed: {e}')

    if not input_capable:
        return CheckResult('audio', False, 'PortAudio sees no input-capable devices', fatal=False)
    return CheckResult('audio', True, f'PortAudio: {len(input_capable)} input device(s)')


def check_rf_sdr() -> CheckResult:
    """RTL-SDR dongle on USB. lsusb is enough; rtl_test would actually
    grab the radio and starve drifter-rf if it's already running."""
    if not shutil.which('lsusb'):
        return CheckResult('rf_sdr', False, 'lsusb not installed', fatal=False)
    try:
        r = _run(['lsusb'], timeout=3)
    except subprocess.SubprocessError as e:
        return CheckResult('rf_sdr', False, str(e), fatal=False)
    # RTL2832U-based dongles are by far the most common; Realtek vendor 0bda.
    haystack = r.stdout.lower()
    rtl_markers = ('rtl2832', 'rtl2838', 'realtek', 'r820t', '0bda:2838', '0bda:2832')
    if any(m in haystack for m in rtl_markers):
        return CheckResult('rf_sdr', True, 'RTL-SDR USB device present')
    return CheckResult('rf_sdr', False, 'no RTL-SDR vendor/device IDs in lsusb', fatal=False)


def check_mqtt_broker() -> CheckResult:
    """MQTT broker is the central nervous system — every service publishes
    or subscribes through it. Plain TCP connect is sufficient."""
    try:
        with socket.create_connection((MQTT_HOST, MQTT_PORT), timeout=2):
            return CheckResult('mqtt', True, f'broker {MQTT_HOST}:{MQTT_PORT} reachable')
    except OSError as e:
        return CheckResult('mqtt', False, f'{MQTT_HOST}:{MQTT_PORT}: {e}')


def check_dashboard_healthz() -> CheckResult:
    """Sanity-poke /healthz on the dashboard. This is the contract probe
    the fleet `mesh status` uses, so it has to round-trip locally too."""
    code, _ = _http_get('127.0.0.1', DASHBOARD_PORT, '/healthz', timeout=2)
    if code == 200:
        return CheckResult('healthz', True, 'HTTP 200')
    if code == 503:
        return CheckResult('healthz', False, 'dashboard returned 503 (degraded)')
    if code == 0:
        return CheckResult('healthz', False, f'127.0.0.1:{DASHBOARD_PORT} unreachable')
    return CheckResult('healthz', False, f'unexpected HTTP {code}')


# ── CLI ──────────────────────────────────────────────────────────────

def cmd_diagnose(args: argparse.Namespace) -> int:
    checks: list[Callable[[], object]] = [
        check_systemd_units,
        check_can_bridge,
        check_realdash_socket,
        check_audio_devices,
        check_rf_sdr,
        check_mqtt_broker,
        check_dashboard_healthz,
    ]

    results: list[CheckResult] = []
    for fn in checks:
        try:
            r = fn()
        except Exception as e:
            r = CheckResult(fn.__name__, False, f'check raised: {e}')
        if isinstance(r, list):
            results.extend(r)
        else:
            results.append(r)

    if args.json:
        out = {
            'ok': all(r.ok or not r.fatal for r in results),
            'ts': time.time(),
            'checks': [r.to_dict() for r in results],
        }
        print(json.dumps(out, indent=2))
    else:
        print(f"{CYAN}DRIFTER DIAGNOSE — MZ1312 UNCAGED TECHNOLOGY{NC}\n")
        for r in results:
            if r.ok:
                tag = f"{GREEN}PASS{NC}"
            elif not r.fatal:
                tag = f"{AMBER}WARN{NC}"
            else:
                tag = f"{RED}FAIL{NC}"
            extra = f" — {r.message}" if r.message else ''
            print(f"  [{tag}] {r.name}{extra}")
        passed = sum(1 for r in results if r.ok)
        warned = sum(1 for r in results if not r.ok and not r.fatal)
        failed = sum(1 for r in results if not r.ok and r.fatal)
        print(
            f"\n  {GREEN}{passed} passed{NC}  "
            f"{AMBER}{warned} warn{NC}  "
            f"{RED}{failed} failed{NC}"
        )

    return 0 if all(r.ok or not r.fatal for r in results) else 1


# ── Field-operator subcommands (status / logs / restart / healthz / version) ──

def _resolve_unit(name: str) -> str:
    """Accept both 'canbridge' and 'drifter-canbridge'; return the systemd unit."""
    return name if name.startswith(SERVICE_PREFIX) else f'{SERVICE_PREFIX}{name}'


def cmd_status(args: argparse.Namespace) -> int:
    """One-line-per-service status. Designed for at-the-car triage."""
    if not shutil.which('systemctl'):
        print(f"{RED}systemctl not available — not on a systemd host{NC}", file=sys.stderr)
        return 2
    states = _systemctl_is_active(list(SERVICES), timeout=5)
    if args.json:
        print(json.dumps(states, indent=2))
        return 0 if all(st == 'active' for st in states.values()) else 1
    width = max(len(s) for s in states)
    for svc, state in states.items():
        if state == 'active':
            tag = f"{GREEN}●{NC} active"
        elif state in ('activating', 'reloading'):
            tag = f"{AMBER}●{NC} {state}"
        else:
            tag = f"{RED}●{NC} {state}"
        print(f"  {svc:<{width}}   {tag}")
    bad = [s for s, st in states.items() if st != 'active']
    if bad:
        print(f"\n  {RED}{len(bad)} service(s) not active:{NC} {', '.join(bad)}")
        print(f"  Try: drifter logs <name>    or    drifter restart <name>")
        return 1
    print(f"\n  {GREEN}all {len(states)} services active{NC}")
    return 0


def cmd_logs(args: argparse.Namespace) -> int:
    """Tail journalctl for one drifter unit. Defaults to last 50 lines."""
    if not shutil.which('journalctl'):
        print("journalctl not available", file=sys.stderr)
        return 2
    unit = _resolve_unit(args.service)
    cmd = ['journalctl', '-u', unit, '-n', str(args.lines), '--no-pager']
    if args.follow:
        cmd.append('-f')
    # Inherit stdout/stderr so journalctl colour + paging works as the user
    # expects. We don't capture — this is a TTY tool.
    try:
        return subprocess.call(cmd)
    except KeyboardInterrupt:
        return 130


def cmd_restart(args: argparse.Namespace) -> int:
    """Restart one service or every drifter-* service."""
    if not shutil.which('systemctl'):
        print("systemctl not available", file=sys.stderr)
        return 2
    targets = SERVICES if args.service in (None, 'all') else [_resolve_unit(args.service)]
    rc = 0
    for svc in targets:
        print(f"  restarting {svc}...", end=' ', flush=True)
        try:
            r = _run(['systemctl', 'restart', svc], timeout=15)
        except subprocess.TimeoutExpired:
            print(f"{RED}TIMEOUT{NC}")
            rc = 1
            continue
        if r.returncode == 0:
            print(f"{GREEN}OK{NC}")
        else:
            print(f"{RED}FAIL{NC} ({r.stderr.strip() or r.stdout.strip()})")
            rc = 1
    return rc


def cmd_healthz(args: argparse.Namespace) -> int:
    """Curl-equivalent for /healthz — works without the curl binary."""
    code, body = _http_get(args.host, args.port, '/healthz', timeout=3)
    if code == 0:
        print(f"{RED}cannot reach {args.host}:{args.port}{NC}", file=sys.stderr)
        return 2

    try:
        parsed = json.loads(body.decode())
    except (json.JSONDecodeError, UnicodeDecodeError):
        parsed = None

    if args.json:
        if parsed is not None:
            print(json.dumps(parsed, indent=2))
        else:
            sys.stdout.buffer.write(body)
        return 0 if code == 200 else 1

    colour = GREEN if code == 200 else RED
    print(f"  {colour}HTTP {code}{NC}")
    if parsed is not None:
        for k, v in parsed.items():
            if isinstance(v, dict):
                print(f"  {k}:")
                for sk, sv in v.items():
                    mark = f"{GREEN}✓{NC}" if sv else f"{RED}✗{NC}"
                    print(f"    {mark} {sk}")
            else:
                print(f"  {k}: {v}")
    else:
        sys.stdout.buffer.write(body)
    return 0 if code == 200 else 1


def cmd_version(args: argparse.Namespace) -> int:
    """Print the deployed git rev so the operator knows what's on the Pi."""
    rev = 'unknown'
    branch = 'unknown'
    # Try the repo first (dev), fall back to /opt/drifter/VERSION (deployed).
    for repo_dir in (Path(__file__).resolve().parent.parent, Path('/opt/drifter')):
        try:
            r = _run(['git', '-C', str(repo_dir), 'rev-parse', '--short', 'HEAD'], timeout=2)
            if r.returncode == 0 and r.stdout.strip():
                rev = r.stdout.strip()
                br = _run(['git', '-C', str(repo_dir), 'rev-parse',
                           '--abbrev-ref', 'HEAD'], timeout=2)
                branch = br.stdout.strip() or 'unknown'
                break
        except (subprocess.SubprocessError, FileNotFoundError):
            continue
    version_file = Path('/opt/drifter/VERSION')
    if version_file.is_file() and rev == 'unknown':
        rev = version_file.read_text().strip()
    print(f"drifter {rev} ({branch})")
    print("MZ1312 UNCAGED TECHNOLOGY — EST 1991")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog='drifter',
        description='DRIFTER fleet-contract operator CLI.',
    )
    json_parent = argparse.ArgumentParser(add_help=False)
    json_parent.add_argument('--json', action='store_true',
                             help='Emit JSON instead of text')
    sub = parser.add_subparsers(dest='cmd', required=True)

    sub.add_parser('diagnose', help='Run fleet-contract diagnostics',
                   parents=[json_parent]).set_defaults(func=cmd_diagnose)
    sub.add_parser('status', help='One-line-per-service status overview',
                   parents=[json_parent]).set_defaults(func=cmd_status)

    p_logs = sub.add_parser('logs', help='Tail journalctl for one drifter unit')
    p_logs.add_argument('service', help="Service name (e.g. 'canbridge' or 'drifter-canbridge')")
    p_logs.add_argument('-n', '--lines', type=int, default=50, help='Lines to show (default 50)')
    p_logs.add_argument('-f', '--follow', action='store_true', help='Follow new log lines')
    p_logs.set_defaults(func=cmd_logs)

    p_rs = sub.add_parser('restart', help='Restart one service or all drifter-* services')
    p_rs.add_argument('service', nargs='?', default='all',
                      help="Service name or 'all' (default: all)")
    p_rs.set_defaults(func=cmd_restart)

    p_h = sub.add_parser('healthz', help='Probe /healthz on the local dashboard',
                         parents=[json_parent])
    p_h.add_argument('--host', default='127.0.0.1')
    p_h.add_argument('--port', type=int, default=DASHBOARD_PORT)
    p_h.set_defaults(func=cmd_healthz)

    sub.add_parser('version', help='Print deployed git rev / branch'
                   ).set_defaults(func=cmd_version)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())

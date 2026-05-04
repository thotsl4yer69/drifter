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
        SERVICES, REALDASH_TCP_PORT, MQTT_HOST, MQTT_PORT,
    )
except ImportError:
    # Allow running from repo root for dev — point at src/ on sys.path.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from config import (  # type: ignore
        SERVICES, REALDASH_TCP_PORT, MQTT_HOST, MQTT_PORT,
    )

DASHBOARD_PORT = 8080
WS_TELEMETRY_PORT = 8081

# ANSI colour codes — drop them when stdout isn't a tty.
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


# ── Checks ───────────────────────────────────────────────────────────

def check_systemd_units() -> list[CheckResult]:
    """Every service in config.SERVICES must report `active`."""
    results = []
    if not shutil.which('systemctl'):
        return [CheckResult('systemd', False, 'systemctl not available')]
    for svc in SERVICES:
        try:
            r = _run(['systemctl', 'is-active', svc], timeout=2)
            active = r.stdout.strip() == 'active'
            results.append(CheckResult(
                f'service:{svc}', active,
                '' if active else f'state={r.stdout.strip() or "unknown"}',
            ))
        except subprocess.SubprocessError as e:
            results.append(CheckResult(f'service:{svc}', False, str(e)))
    return results


def check_can_bridge() -> CheckResult:
    """can0 must exist + be UP. If candump is available, sniff briefly for
    any frame as a soft round-trip indicator (the ECU on the OBD-II bus
    answers 0x7DF requests; we just confirm the wire is alive)."""
    if not shutil.which('ip'):
        return CheckResult('can0', False, 'iproute2 not installed')
    try:
        r = _run(['ip', '-brief', 'link', 'show', 'can0'], timeout=2)
    except subprocess.SubprocessError as e:
        return CheckResult('can0', False, str(e))
    if r.returncode != 0:
        # Fallback: slcan adapter exposes itself as slcan0 instead of can0.
        try:
            r = _run(['ip', '-brief', 'link', 'show', 'slcan0'], timeout=2)
        except subprocess.SubprocessError as e:
            return CheckResult('can0', False, str(e))
        if r.returncode != 0:
            return CheckResult('can0', False, 'no can0 / slcan0 interface')
    parts = r.stdout.split()
    state = parts[1] if len(parts) > 1 else 'UNKNOWN'
    if state not in ('UP', 'UNKNOWN'):
        return CheckResult('can0', False, f'link state={state}')

    # Soft round-trip: candump for 750ms. Any frame = bus alive.
    if not shutil.which('candump'):
        return CheckResult('can0', True, f'link {state} (no candump for round-trip)')
    iface = parts[0].rstrip(':') if parts else 'can0'
    try:
        r = _run(['candump', '-T', '750', '-n', '1', iface], timeout=3)
        # candump exits 0 when -n 1 fires; non-zero when timeout — that's fine,
        # we treat absence of frames as "warn" not "fail" since the ECU may
        # be off (key out of ignition).
        saw_frame = bool(r.stdout.strip())
        return CheckResult(
            'can0', True,
            f'link {state}, frames {"seen" if saw_frame else "none in 750ms (ECU may be off)"}',
        )
    except subprocess.TimeoutExpired:
        return CheckResult('can0', True, f'link {state} (no frames in 3s)')
    except subprocess.SubprocessError as e:
        return CheckResult('can0', True, f'link {state} ({e})')


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
    try:
        with socket.create_connection(('127.0.0.1', DASHBOARD_PORT), timeout=2) as s:
            s.sendall(b'GET /healthz HTTP/1.0\r\nHost: localhost\r\n\r\n')
            data = b''
            s.settimeout(2)
            while True:
                try:
                    chunk = s.recv(4096)
                except socket.timeout:
                    break
                if not chunk:
                    break
                data += chunk
                if len(data) > 16384:
                    break
        head, _, _ = data.partition(b'\r\n')
        if b'200' in head:
            return CheckResult('healthz', True, head.decode(errors='replace').strip())
        if b'503' in head:
            return CheckResult('healthz', False, 'dashboard returned 503 (degraded)')
        return CheckResult('healthz', False, f'unexpected response: {head[:80]!r}')
    except OSError as e:
        return CheckResult('healthz', False, f'127.0.0.1:{DASHBOARD_PORT}: {e}')


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
    return name if name.startswith('drifter-') else f'drifter-{name}'


def cmd_status(args: argparse.Namespace) -> int:
    """One-line-per-service status. Designed for at-the-car triage."""
    if not shutil.which('systemctl'):
        print(f"{RED}systemctl not available — not on a systemd host{NC}", file=sys.stderr)
        return 2
    rows = []
    for svc in SERVICES:
        try:
            r = _run(['systemctl', 'is-active', svc], timeout=2)
            state = r.stdout.strip() or 'unknown'
        except subprocess.SubprocessError:
            state = 'error'
        rows.append((svc, state))
    if args.json:
        print(json.dumps({s: st for s, st in rows}, indent=2))
        return 0 if all(st == 'active' for _, st in rows) else 1
    width = max(len(s) for s, _ in rows)
    for svc, state in rows:
        if state == 'active':
            tag = f"{GREEN}●{NC} active"
        elif state in ('activating', 'reloading'):
            tag = f"{AMBER}●{NC} {state}"
        else:
            tag = f"{RED}●{NC} {state}"
        print(f"  {svc:<{width}}   {tag}")
    bad = [s for s, st in rows if st != 'active']
    if bad:
        print(f"\n  {RED}{len(bad)} service(s) not active:{NC} {', '.join(bad)}")
        print(f"  Try: drifter logs <name>    or    drifter restart <name>")
        return 1
    print(f"\n  {GREEN}all {len(rows)} services active{NC}")
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
    host, port = args.host, args.port
    try:
        with socket.create_connection((host, port), timeout=3) as s:
            s.sendall(f'GET /healthz HTTP/1.0\r\nHost: {host}\r\n\r\n'.encode())
            data = b''
            s.settimeout(3)
            while True:
                try:
                    chunk = s.recv(8192)
                except socket.timeout:
                    break
                if not chunk:
                    break
                data += chunk
                if len(data) > 64 * 1024:
                    break
    except OSError as e:
        print(f"{RED}cannot reach {host}:{port}: {e}{NC}", file=sys.stderr)
        return 2

    head, _, body = data.partition(b'\r\n\r\n')
    status_line = head.split(b'\r\n', 1)[0].decode(errors='replace')
    code = 0
    try:
        code = int(status_line.split()[1])
    except (IndexError, ValueError):
        pass

    if args.json:
        # Best-effort JSON pass-through. If body isn't JSON, dump raw.
        try:
            print(json.dumps(json.loads(body.decode()), indent=2))
        except (json.JSONDecodeError, UnicodeDecodeError):
            sys.stdout.buffer.write(body)
        return 0 if code == 200 else 1

    colour = GREEN if code == 200 else RED
    print(f"  {colour}{status_line}{NC}")
    try:
        parsed = json.loads(body.decode())
        for k, v in parsed.items():
            if isinstance(v, dict):
                print(f"  {k}:")
                for sk, sv in v.items():
                    mark = f"{GREEN}✓{NC}" if sv else f"{RED}✗{NC}"
                    print(f"    {mark} {sk}")
            else:
                print(f"  {k}: {v}")
    except (json.JSONDecodeError, UnicodeDecodeError):
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
    sub = parser.add_subparsers(dest='cmd', required=True)

    p_diag = sub.add_parser('diagnose', help='Run fleet-contract diagnostics')
    p_diag.add_argument('--json', action='store_true', help='Emit JSON instead of text')
    p_diag.set_defaults(func=cmd_diagnose)

    p_status = sub.add_parser('status', help='One-line-per-service status overview')
    p_status.add_argument('--json', action='store_true', help='Emit JSON instead of text')
    p_status.set_defaults(func=cmd_status)

    p_logs = sub.add_parser('logs', help='Tail journalctl for one drifter unit')
    p_logs.add_argument('service', help="Service name (e.g. 'canbridge' or 'drifter-canbridge')")
    p_logs.add_argument('-n', '--lines', type=int, default=50, help='Lines to show (default 50)')
    p_logs.add_argument('-f', '--follow', action='store_true', help='Follow new log lines')
    p_logs.set_defaults(func=cmd_logs)

    p_rs = sub.add_parser('restart', help='Restart one service or all drifter-* services')
    p_rs.add_argument('service', nargs='?', default='all',
                      help="Service name or 'all' (default: all)")
    p_rs.set_defaults(func=cmd_restart)

    p_h = sub.add_parser('healthz', help='Probe /healthz on the local dashboard')
    p_h.add_argument('--host', default='127.0.0.1')
    p_h.add_argument('--port', type=int, default=DASHBOARD_PORT)
    p_h.add_argument('--json', action='store_true', help='Emit raw JSON body')
    p_h.set_defaults(func=cmd_healthz)

    p_v = sub.add_parser('version', help='Print deployed git rev / branch')
    p_v.set_defaults(func=cmd_version)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())

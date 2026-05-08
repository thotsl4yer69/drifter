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
        MODES, MODE_STATE_PATH, DEFAULT_MODE,
    )
except ImportError:
    # Allow running from repo root for dev — point at src/ on sys.path.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from config import (  # type: ignore
        SERVICES, REALDASH_TCP_PORT, MQTT_HOST, MQTT_PORT,
        MODES, MODE_STATE_PATH, DEFAULT_MODE,
    )


def _active_mode() -> str:
    try:
        return Path(MODE_STATE_PATH).read_text(encoding='utf-8').strip() or DEFAULT_MODE
    except OSError:
        return DEFAULT_MODE


def _expected_services() -> set[str]:
    """Services that must be active in the currently-armed persona.
    Out-of-mode services are checked but reported non-fatal."""
    return MODES.get(_active_mode(), set(SERVICES))

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

# Services that require optional hardware (Flipper Zero, voice models, etc.)
# and should not fail the deploy contract when that hardware is absent.
_HARDWARE_OPTIONAL_SERVICES = frozenset({
    'drifter-vivi',      # requires Ollama + faster-whisper + piper + audio input
    'drifter-flipper',   # requires Flipper Zero connected via UART/USB
    'drifter-voicein',   # requires wake-word model or GPIO PTT button
    'drifter-canbridge', # needs USB2CANFD plugged into OBD-II
    'drifter-rf',        # needs RTL-SDR dongle — TPMS sniffing only viable with hardware
    'drifter-bleconv',   # needs hci0 active (Pi 5 onboard BLE) + bleak in venv
})


def check_systemd_units() -> list[CheckResult]:
    """Every in-mode service must report `active`. Out-of-mode services
    (e.g. drifter-canbridge while persona=foot) are checked non-fatal."""
    results = []
    if not shutil.which('systemctl'):
        return [CheckResult('systemd', False, 'systemctl not available')]
    expected = _expected_services()
    for svc in SERVICES:
        in_mode = svc in expected
        try:
            r = _run(['systemctl', 'is-active', svc], timeout=2)
            active = r.stdout.strip() == 'active'
            fatal = in_mode and svc not in _HARDWARE_OPTIONAL_SERVICES
            msg = '' if active else f'state={r.stdout.strip() or "unknown"}'
            if not in_mode:
                msg = f'out-of-mode ({_active_mode()}) — {msg}' if msg else f'out-of-mode ({_active_mode()})'
            results.append(CheckResult(f'service:{svc}', active, msg, fatal=fatal))
        except subprocess.SubprocessError as e:
            results.append(CheckResult(f'service:{svc}', False, str(e), fatal=in_mode))
    return results


def check_can_bridge() -> CheckResult:
    """can0 must exist + be UP. If candump is available, sniff briefly for
    any frame as a soft round-trip indicator (the ECU on the OBD-II bus
    answers 0x7DF requests; we just confirm the wire is alive). Skipped
    when drifter-canbridge is not in the active persona's service set."""
    if 'drifter-canbridge' not in _expected_services():
        return CheckResult(
            'can0', True,
            f'skipped (out-of-mode: {_active_mode()})',
            fatal=False,
        )
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
            return CheckResult('can0', False, 'no can0 / slcan0 interface', fatal=False)
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
    Connect-and-close is the cheapest reachability probe. Skipped when
    drifter-realdash is not in the active persona's service set."""
    if 'drifter-realdash' not in _expected_services():
        return CheckResult(
            'realdash', True,
            f'skipped (out-of-mode: {_active_mode()})',
            fatal=False,
        )
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog='drifter',
        description='DRIFTER fleet-contract operator CLI.',
    )
    sub = parser.add_subparsers(dest='cmd', required=True)

    p_diag = sub.add_parser('diagnose', help='Run fleet-contract diagnostics')
    p_diag.add_argument('--json', action='store_true', help='Emit JSON instead of text')
    p_diag.set_defaults(func=cmd_diagnose)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())

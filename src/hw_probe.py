#!/usr/bin/env python3
"""MZ1312 DRIFTER — Hardware Probes
Single source of truth for what's physically connected.

Every probe returns a uniform dict:
    {'device': str, 'connected': bool, 'detail': str, 'action': str, 'ts': float}

`connected` reflects *physical presence*. Service-level health
(MQTT errors, decode failures) is layered on top by the owning service,
which publishes drifter/hw/<device> with the richer state.

UNCAGED TECHNOLOGY — EST 1991
"""
from __future__ import annotations

import glob as globmod
import json
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

from config import TOPICS

# Topic prefix used by every owning service.
HW_TOPIC_PREFIX = 'drifter/hw'

# Devices the system surfaces. Order is the display order on the dashboard.
DEVICES = (
    'can',
    'gps',
    'rtl_sdr',
    'bluetooth',
    'microphone',
    'flipper',
    'framebuffer',
)


def _run(args: list[str], timeout: float = 2.0) -> str:
    """subprocess wrapper — never raises, returns '' on failure."""
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return out.stdout
    except Exception:
        return ''


def _has_binary(name: str) -> bool:
    """True if binary is on PATH."""
    return subprocess.run(['which', name], capture_output=True).returncode == 0


def _result(device: str, connected: bool, detail: str, action: str = '') -> dict[str, Any]:
    return {
        'device': device,
        'connected': connected,
        'detail': detail,
        'action': action if not connected else '',
        'ts': time.time(),
    }


def probe_can() -> dict[str, Any]:
    """SocketCAN interface state."""
    for line in _run(['ip', '-brief', 'link', 'show', 'type', 'can']).strip().splitlines():
        parts = line.split()
        if not parts:
            continue
        name, state = parts[0], (parts[1] if len(parts) > 1 else 'UNKNOWN')
        if state == 'UP':
            return _result('can', True, f'{name} UP')
        return _result('can', False, f'{name} {state}',
                       'Interface down — check USB2CAN cable and ignition')
    usb_serial = globmod.glob('/dev/ttyUSB*') + globmod.glob('/dev/ttyACM*')
    if usb_serial:
        return _result('can', False, f'USB serial seen ({usb_serial[0]}) but no can0',
                       'Adapter detected but CAN not configured — check candump dmesg')
    return _result('can', False, 'No CAN adapter detected',
                   'Plug in USB2CAN dongle')


def probe_gps() -> dict[str, Any]:
    """gpsd liveness via TPV poll on localhost:2947."""
    try:
        with socket.create_connection(('127.0.0.1', 2947), timeout=1.0) as s:
            s.sendall(b'?WATCH={"enable":true,"json":true};\n')
            s.settimeout(1.5)
            buf = b''
            deadline = time.time() + 1.5
            while time.time() < deadline:
                try:
                    chunk = s.recv(4096)
                except socket.timeout:
                    break
                if not chunk:
                    break
                buf += chunk
                if b'"class":"DEVICES"' in buf or b'"class":"TPV"' in buf:
                    break
            text = buf.decode('utf-8', errors='replace')
            devices_seen = []
            for line in text.splitlines():
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get('class') == 'DEVICES':
                    devices_seen = [d.get('path', '?') for d in obj.get('devices', [])]
            if devices_seen:
                return _result('gps', True, f'gpsd: {", ".join(devices_seen)}')
            return _result('gps', False, 'gpsd up but no GPS device attached',
                           'Plug in USB GPS dongle (gpsd will auto-detect)')
    except (ConnectionRefusedError, OSError):
        return _result('gps', False, 'gpsd not reachable on :2947',
                       'sudo systemctl start gpsd (or check gpsd config)')


def probe_rtl_sdr() -> dict[str, Any]:
    """RTL-SDR via lsusb. Avoids rtl_test (slow, locks device)."""
    lsusb = _run(['lsusb']).lower()
    markers = ('rtl2838', 'rtl2832', '0bda:2832', '0bda:2838', 'realtek.*dvb')
    for m in markers:
        if m in lsusb:
            return _result('rtl_sdr', True, 'RTL-SDR dongle on USB bus')
    return _result('rtl_sdr', False, 'No RTL-SDR detected',
                   'Plug in RTL-SDR dongle — TPMS/ADS-B/RF disabled until then')


def probe_bluetooth() -> dict[str, Any]:
    """hciconfig for any 'UP RUNNING' adapter."""
    if not _has_binary('hciconfig'):
        return _result('bluetooth', False, 'hciconfig not installed',
                       'sudo apt install bluez')
    out = _run(['hciconfig'])
    if not out:
        return _result('bluetooth', False, 'No Bluetooth adapter',
                       'Enable on-board BT or plug in a USB BT dongle')
    current = None
    for line in out.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not line.startswith('\t') and not line.startswith(' '):
            current = stripped.split(':', 1)[0]
        if 'UP RUNNING' in stripped and current:
            return _result('bluetooth', True, f'{current} UP RUNNING')
    if current:
        return _result('bluetooth', False, f'{current} down',
                       f'sudo hciconfig {current} up')
    return _result('bluetooth', False, 'No Bluetooth adapter',
                   'Enable on-board BT or plug in a USB BT dongle')


def probe_microphone() -> dict[str, Any]:
    """arecord -l for capture cards. arecord exits non-zero when no cards exist."""
    if not _has_binary('arecord'):
        return _result('microphone', False, 'arecord not installed',
                       'sudo apt install alsa-utils')
    out = _run(['arecord', '-l'])
    cards = [line for line in out.splitlines() if line.startswith('card ')]
    if cards:
        return _result('microphone', True, f'{len(cards)} capture device(s)')
    return _result('microphone', False, 'No ALSA capture device',
                   'Plug in USB mic — voice input + Vivi PTT disabled until then')


def probe_flipper() -> dict[str, Any]:
    """Flipper Zero presents as /dev/ttyACM* with USB ID 0483:5740."""
    lsusb = _run(['lsusb']).lower()
    if '0483:5740' in lsusb:
        candidates = sorted(globmod.glob('/dev/ttyACM*'))
        detail = f'Flipper on {candidates[0]}' if candidates else 'Flipper on USB (no ttyACM yet)'
        return _result('flipper', True, detail)
    return _result('flipper', False, 'No Flipper Zero on USB',
                   'Plug in Flipper Zero via USB-C')


def probe_framebuffer() -> dict[str, Any]:
    """SPI LCD presents /dev/fb1 (fb0 is HDMI/onboard)."""
    if Path('/dev/fb1').exists():
        return _result('framebuffer', True, '/dev/fb1 present')
    return _result('framebuffer', False, '/dev/fb1 missing',
                   'SPI LCD not wired — fb1 overlay disabled')


_PROBES = {
    'can': probe_can,
    'gps': probe_gps,
    'rtl_sdr': probe_rtl_sdr,
    'bluetooth': probe_bluetooth,
    'microphone': probe_microphone,
    'flipper': probe_flipper,
    'framebuffer': probe_framebuffer,
}


def probe_all() -> dict[str, dict[str, Any]]:
    """Run every probe. Returns {device: result}."""
    return {name: fn() for name, fn in _PROBES.items()}


def probe(device: str) -> dict[str, Any]:
    if device not in _PROBES:
        raise KeyError(f'unknown device: {device}')
    return _PROBES[device]()


def hw_topic(device: str) -> str:
    return f'{HW_TOPIC_PREFIX}/{device}'


def publish_hw_state(mqtt_client, device: str, result: dict[str, Any]) -> None:
    """Publish a probe result to drifter/hw/<device>. Retained.

    Owning services should call this on connect/disconnect transitions and
    on first publish after start. Dashboard subscribes to the retained
    snapshot so it always has a current view.
    """
    payload = json.dumps(result)
    mqtt_client.publish(hw_topic(device), payload, retain=True, qos=1)

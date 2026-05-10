"""Hardware & service probes for the web dashboard.

Single-source probe results come from `hw_probe`. This module layers in
systemd service state, network info, and MQTT freshness so the dashboard
can show one unified hardware view.

`check_hardware()` returns:
- probes:   {device: {connected, detail, action, ts}}  — from hw_probe
- services: {drifter-*: 'active'|'inactive'|...}
- network:  {iface: {state, addrs}}
- mqtt:     {broker, has_data, last_data_age, topics_seen, engine_topics}
- summary:  [{item, status, detail, action}]
- ready:    bool — CAN up AND fresh engine data
"""
from __future__ import annotations

import subprocess
import time

from hw_probe import probe_all, DEVICES
from web_dashboard_state import latest_state

_WATCHED_SERVICES = (
    'drifter-canbridge', 'drifter-alerts',   'drifter-dashboard',
    'drifter-watchdog',  'drifter-hotspot',  'drifter-rf',
    'drifter-wardrive',  'drifter-voice',    'drifter-realdash',
    'drifter-logger',    'drifter-homesync', 'drifter-anomaly',
    'drifter-analyst',   'drifter-voicein',  'drifter-fbmirror',
    'drifter-bleconv',   'drifter-flipper',  'drifter-gps',
    'drifter-feeds',     'drifter-vivi',     'drifter-opsec',
    'nanomq',            'mosquitto',
)

_DEVICE_LABELS = {
    'can':         'CAN Bus',
    'gps':         'GPS',
    'rtl_sdr':     'RTL-SDR',
    'bluetooth':   'Bluetooth',
    'microphone':  'Microphone',
    'flipper':     'Flipper Zero',
    'framebuffer': 'SPI LCD (fb1)',
}


def _run(args, timeout=3) -> str:
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return out.stdout
    except Exception:
        return ''


def _probe_networks() -> dict[str, dict]:
    networks: dict[str, dict] = {}
    for line in _run(['ip', '-brief', 'addr', 'show']).strip().splitlines():
        parts = line.split()
        if len(parts) < 3 or parts[0] == 'lo':
            continue
        addrs = [p.split('/')[0] for p in parts[2:] if '.' in p]
        if addrs:
            networks[parts[0]] = {'state': parts[1], 'addrs': addrs}
    return networks


def _probe_services() -> dict[str, str]:
    out: dict[str, str] = {}
    for svc in _WATCHED_SERVICES:
        status = _run(['systemctl', 'is-active', svc]).strip()
        out[svc] = status or 'unknown'
    return out


def _build_summary(probes: dict, has_engine_data: bool, engine_keys: list,
                   data_age, services_status: dict) -> list[dict]:
    """One row per hardware device + derived rows for OBD data and failed services."""
    summary: list[dict] = []
    for device in DEVICES:
        r = probes[device]
        summary.append({
            'item':   _DEVICE_LABELS.get(device, device),
            'status': 'ok' if r['connected'] else 'missing',
            'detail': r['detail'],
            'action': r.get('action', ''),
        })

    can_ok = probes['can']['connected']
    if can_ok and has_engine_data:
        summary.append({
            'item': 'OBD Data', 'status': 'ok',
            'detail': f'{len(engine_keys)} engine params, {data_age:.0f}s ago',
            'action': '',
        })
    elif can_ok:
        summary.append({
            'item': 'OBD Data', 'status': 'waiting',
            'detail': 'CAN up — waiting for ECU frames',
            'action': 'Turn ignition on',
        })

    failed = [s for s, v in services_status.items() if v == 'failed']
    if failed:
        summary.append({
            'item': 'Services', 'status': 'error',
            'detail': f'Failed: {", ".join(failed)}',
            'action': 'journalctl -u <service> for details',
        })

    return summary


def check_hardware() -> dict:
    probes = probe_all()
    services_status = _probe_services()

    last_update = latest_state.get('_last_update', 0)
    data_age = time.time() - last_update if last_update else None
    engine_keys = [k for k in latest_state
                   if k.startswith('engine_') and not k.startswith('_')]
    all_keys = [k for k in latest_state if not k.startswith('_')]
    has_engine_data = bool(engine_keys) and data_age is not None and data_age < 60

    can_ok = probes['can']['connected']
    return {
        'probes': probes,
        'network': _probe_networks(),
        'services': services_status,
        'mqtt': {
            'broker': services_status.get('mosquitto', 'unknown'),
            'last_data_age': round(data_age, 1) if data_age else None,
            'has_data': has_engine_data,
            'topics_seen': len(all_keys),
            'engine_topics': len(engine_keys),
        },
        'ready': can_ok and has_engine_data,
        'summary': _build_summary(probes, has_engine_data, engine_keys,
                                  data_age, services_status),
    }

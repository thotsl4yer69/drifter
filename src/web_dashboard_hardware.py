"""Hardware & service probes for the web dashboard.

The first vehicle test exposed an important assumption in the original hardware
page: it treated raw CAN as synonymous with "vehicle connected". DRIFTER now
surfaces the actual OBD transport (raw CAN or ELM327 over serial/Bluetooth/Wi-Fi),
its retained bridge state, and a concrete operator action when telemetry is not
flowing.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from hw_probe import DEVICES, probe_all
from web_dashboard_state import latest_state

_WATCHED_SERVICES = (
    'drifter-canbridge', 'drifter-obdbridge', 'drifter-alerts', 'drifter-dashboard',
    'drifter-watchdog', 'drifter-hotspot', 'drifter-rf', 'drifter-wardrive',
    'drifter-voice', 'drifter-realdash', 'drifter-logger', 'drifter-homesync',
    'drifter-anomaly', 'drifter-analyst', 'drifter-voicein', 'drifter-lcd',
    'drifter-bleconv', 'drifter-flipper', 'drifter-gps', 'drifter-feeds',
    'drifter-vivi', 'drifter-opsec', 'nanomq', 'mosquitto',
)

_DEVICE_LABELS = {
    'can': 'Raw CAN Adapter',
    'gps': 'GPS',
    'rtl_sdr': 'RTL-SDR',
    'bluetooth': 'Bluetooth',
    'microphone': 'Microphone',
    'speaker': 'USB Audio',
    'flipper': 'Flipper Zero',
    'framebuffer': 'SPI LCD',
}

_ENV_PATH = Path('/opt/drifter/.env')
_OBD_TOPIC = 'drifter/obd/status'


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


def _field_env() -> dict[str, str]:
    """Read only non-secret vehicle-link settings from the live env file."""
    wanted = {
        'DRIFTER_TRANSPORT', 'DRIFTER_ELM_LINK', 'OBD_SERIAL_DEV',
        'ELM_BT_MAC', 'ELM_BT_CHANNEL', 'ELM_WIFI_HOST', 'ELM_WIFI_PORT',
    }
    values: dict[str, str] = {}
    try:
        lines = _ENV_PATH.read_text(encoding='utf-8').splitlines()
    except OSError:
        lines = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        if key in wanted:
            values[key] = value.strip().strip('"\'')
    # systemd receives process env too; allow it to override file values.
    for key in wanted:
        if key in os.environ:
            values[key] = os.environ[key]
    return values


def _retained_obd_status() -> dict:
    """Read the bridge's retained status without depending on dashboard cache."""
    if not shutil.which('mosquitto_sub'):
        return {}
    raw = _run(
        ['mosquitto_sub', '-h', '127.0.0.1', '-t', _OBD_TOPIC,
         '-C', '1', '-W', '1'],
        timeout=2,
    ).strip()
    try:
        data = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _vehicle_link(services_status: dict[str, str], can_connected: bool,
                  has_engine_data: bool, data_age: float | None) -> dict:
    """Return one operator-facing truth source for CAN/ELM/ECU state."""
    env = _field_env()
    retained = _retained_obd_status()
    forced = (env.get('DRIFTER_TRANSPORT') or '').strip().lower()
    elm_mode = (env.get('DRIFTER_ELM_LINK') or '').strip().lower()
    bt_mac = (env.get('ELM_BT_MAC') or '').strip()
    wifi_host = (env.get('ELM_WIFI_HOST') or '').strip()
    serial_dev = (env.get('OBD_SERIAL_DEV') or '/dev/drifter-obd').strip()

    if forced in {'elm327', 'elm', 'obd', 'obdbridge', 'serial', 'kline', 'k-line'}:
        transport = 'elm327'
    elif forced in {'can', 'socketcan', 'raw', 'canbridge'}:
        transport = 'can'
    elif elm_mode in {'bluetooth', 'bt', 'wifi', 'tcp', 'network'} or bt_mac or wifi_host:
        transport = 'elm327'
    elif can_connected:
        transport = 'can'
    else:
        transport = 'unconfigured'

    bridge_state = str(retained.get('state') or '').strip().lower()
    device = str(retained.get('device') or '').strip()
    if transport == 'elm327' and not device:
        if elm_mode in {'bluetooth', 'bt'} and bt_mac:
            device = f'bluetooth:{bt_mac}'
        elif elm_mode in {'wifi', 'tcp', 'network'} and wifi_host:
            device = f'wifi:{wifi_host}:{env.get("ELM_WIFI_PORT", "35000")}'
        else:
            device = serial_dev
    elif transport == 'can' and not device:
        device = 'SocketCAN/CANable'

    if has_engine_data:
        age = f'{data_age:.0f}s' if data_age is not None else 'now'
        return {
            'transport': transport,
            'state': 'online',
            'device': device,
            'detail': f'ECU telemetry live ({age} ago)',
            'action': '',
            'ready': True,
            'retained': retained,
        }

    if transport == 'elm327':
        service = services_status.get('drifter-obdbridge', 'unknown')
        if service != 'active':
            detail = f'ELM327 configured ({device or elm_mode or "auto"}); bridge is {service}'
            action = 'Restart bridge: sudo systemctl restart drifter-obdbridge'
            state = 'error' if service == 'failed' else 'waiting'
        elif bridge_state == 'online':
            detail = f'ELM327 link online ({device or "adapter"}); no ECU data yet'
            action = 'Ignition ON, then run: drifter obd test'
            state = 'waiting'
        elif bridge_state == 'hw_pending':
            detail = f'ELM327 configured ({device or elm_mode or "auto"}); adapter not ready'
            action = 'Run: drifter obd scan  →  sudo drifter obd setup'
            state = 'waiting'
        elif elm_mode in {'bluetooth', 'bt'} and not bt_mac:
            detail = 'Bluetooth ELM327 selected but no adapter is paired/configured'
            action = 'Run: drifter obd scan  →  sudo drifter obd pair <MAC>'
            state = 'missing'
        elif elm_mode in {'wifi', 'tcp', 'network'} and not wifi_host:
            detail = 'Wi-Fi ELM327 selected but no adapter host is configured'
            action = 'Join the ELM Wi-Fi, then: sudo drifter obd setup'
            state = 'missing'
        else:
            detail = f'ELM327 selected ({device or "auto"}); connection not proved'
            action = 'Run: sudo drifter obd setup'
            state = 'waiting'
        return {
            'transport': transport,
            'state': state,
            'device': device,
            'detail': detail,
            'action': action,
            'ready': False,
            'retained': retained,
        }

    if transport == 'can':
        if can_connected:
            return {
                'transport': 'can', 'state': 'waiting', 'device': device,
                'detail': 'Raw CAN adapter present; waiting for ECU replies',
                'action': 'Ignition ON. If no ECU replies on this Jaguar, use ELM327 setup.',
                'ready': False, 'retained': retained,
            }
        return {
            'transport': 'can', 'state': 'missing', 'device': device,
            'detail': 'Raw CAN selected but no CAN adapter is present',
            'action': 'For the Jaguar, connect an ELM327 and run: sudo drifter obd setup',
            'ready': False, 'retained': retained,
        }

    return {
        'transport': 'unconfigured', 'state': 'missing', 'device': '',
        'detail': 'No working vehicle diagnostic link has been selected',
        'action': 'Connect an ELM327, then run: drifter obd scan  →  sudo drifter obd setup',
        'ready': False, 'retained': retained,
    }


def _build_summary(probes: dict, vehicle_link: dict, services_status: dict) -> list[dict]:
    summary: list[dict] = []

    # Vehicle Link is first because that is the primary field job of DRIFTER.
    summary.append({
        'item': 'Vehicle Link',
        'status': 'ok' if vehicle_link['ready'] else vehicle_link['state'],
        'detail': vehicle_link['detail'],
        'action': vehicle_link['action'],
    })

    for device in DEVICES:
        result = probes[device]
        # Raw CAN being absent is informational when ELM327 owns the vehicle link.
        status = 'ok' if result['connected'] else 'missing'
        action = result.get('action', '')
        if device == 'can' and vehicle_link['transport'] == 'elm327':
            status = 'unused'
            action = 'ELM327 is the active OBD transport'
        summary.append({
            'item': _DEVICE_LABELS.get(device, device),
            'status': status,
            'detail': result['detail'],
            'action': action,
        })

    failed = [svc for svc, value in services_status.items() if value == 'failed']
    if failed:
        summary.append({
            'item': 'Services',
            'status': 'error',
            'detail': f'Failed: {", ".join(failed)}',
            'action': 'Run: drifter field-dump',
        })
    return summary


def check_hardware() -> dict:
    probes = probe_all()
    services_status = _probe_services()

    last_update = latest_state.get('_last_update', 0)
    data_age = time.time() - last_update if last_update else None
    engine_keys = [
        key for key in latest_state
        if key.startswith('engine_') and not key.startswith('_')
    ]
    all_keys = [key for key in latest_state if not key.startswith('_')]
    has_engine_data = bool(engine_keys) and data_age is not None and data_age < 60

    vehicle_link = _vehicle_link(
        services_status,
        probes['can']['connected'],
        has_engine_data,
        data_age,
    )
    return {
        'probes': probes,
        'network': _probe_networks(),
        'services': services_status,
        'vehicle_link': vehicle_link,
        'mqtt': {
            'broker': services_status.get('mosquitto', 'unknown'),
            'last_data_age': round(data_age, 1) if data_age is not None else None,
            'has_data': has_engine_data,
            'topics_seen': len(all_keys),
            'engine_topics': len(engine_keys),
        },
        'ready': bool(vehicle_link['ready']),
        'summary': _build_summary(probes, vehicle_link, services_status),
    }

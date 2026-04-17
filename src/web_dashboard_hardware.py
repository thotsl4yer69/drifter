"""Hardware & service probes for the web dashboard.

Returns a dict describing what's plugged in (CAN adapter, RTL-SDR),
what systemd thinks of each drifter-* unit, and whether MQTT is
flowing. The dashboard renders this as the "Setup/Status" summary.

Depends on state.latest_state to know whether engine data is fresh, so
the state module must be loaded first.
"""
from __future__ import annotations

import glob as globmod
import subprocess
import time

from web_dashboard_state import latest_state

# Services we surface on the dashboard. Order is preserved in the output.
_WATCHED_SERVICES = (
    'drifter-canbridge', 'drifter-alerts', 'drifter-dashboard',
    'drifter-watchdog',  'drifter-hotspot', 'drifter-rf',
    'drifter-wardrive',  'drifter-voice',   'drifter-realdash',
    'drifter-logger',    'drifter-homesync', 'drifter-anomaly',
    'drifter-analyst',   'drifter-voicein',  'drifter-fbmirror',
    'nanomq',            'mosquitto',
)


def _run(args, timeout=3) -> str:
    """subprocess.run wrapper that never raises — returns '' on failure."""
    try:
        out = subprocess.run(args, capture_output=True, text=True,
                             timeout=timeout)
        return out.stdout
    except Exception:
        return ''


def _probe_can() -> tuple[list[dict], list[str]]:
    """Return (can_interfaces, usb_serial_devices)."""
    can_ifaces: list[dict] = []
    for line in _run(['ip', '-brief', 'link', 'show', 'type', 'can']
                     ).strip().splitlines():
        parts = line.split()
        if parts:
            can_ifaces.append({
                'name': parts[0],
                'state': parts[1] if len(parts) > 1 else 'UNKNOWN',
            })
    usb_serial = globmod.glob('/dev/ttyUSB*') + globmod.glob('/dev/ttyACM*')
    return can_ifaces, usb_serial


def _probe_rtl_sdr() -> bool:
    """True if an RTL-SDR dongle shows up on the USB bus."""
    lsusb = _run(['lsusb']).lower()
    return 'rtl2838' in lsusb or 'rtl2832' in lsusb or 'realtek' in lsusb


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


def _build_summary(can_ifaces, usb_serial, rtl_ok, has_engine_data,
                   engine_keys, data_age, services_status) -> list[dict]:
    """Human-readable summary rows shown on the dashboard setup panel."""
    summary: list[dict] = []
    can_ok = any(i['state'] == 'UP' for i in can_ifaces)

    if not can_ok:
        if not usb_serial and not can_ifaces:
            summary.append({'item': 'USB2CAN', 'status': 'missing',
                            'detail': 'No adapter detected. Plug in USB2CAN.'})
        elif usb_serial and not can_ifaces:
            summary.append({'item': 'USB2CAN', 'status': 'setup',
                            'detail': (f'Serial device {usb_serial[0]} found '
                                       'but CAN not configured.')})
        else:
            name, state = can_ifaces[0]['name'], can_ifaces[0]['state']
            summary.append({'item': 'CAN Bus', 'status': 'down',
                            'detail': (f'{name} is {state}. '
                                       'Start car / check wiring.')})
    else:
        summary.append({'item': 'CAN Bus', 'status': 'ok',
                        'detail': f'{can_ifaces[0]["name"]} UP'})

    if not has_engine_data:
        detail = ('CAN is up but no engine data yet. Turn ignition on.'
                  if can_ok else 'Waiting for CAN connection.')
        summary.append({'item': 'OBD Data', 'status': 'waiting',
                        'detail': detail})
    else:
        summary.append({'item': 'OBD Data', 'status': 'ok',
                        'detail': (f'{len(engine_keys)} engine params, '
                                   f'{data_age:.0f}s ago')})

    summary.append({'item': 'RTL-SDR',
                    'status': 'ok' if rtl_ok else 'missing',
                    'detail': ('SDR dongle detected' if rtl_ok
                               else 'No SDR dongle. TPMS/RF unavailable.')})

    svc_failed = [s for s, v in services_status.items() if v == 'failed']
    if svc_failed:
        summary.append({'item': 'Services', 'status': 'error',
                        'detail': f'Failed: {", ".join(svc_failed)}'})

    return summary


def check_hardware() -> dict:
    """Probe CAN, RTL-SDR, network, services, and MQTT liveness."""
    can_ifaces, usb_serial = _probe_can()
    can_ok = any(i['state'] == 'UP' for i in can_ifaces)

    can_hint = ''
    if not can_ifaces and not usb_serial:
        can_hint = 'Plug in USB2CAN adapter and start the car'
    elif can_ifaces and not can_ok:
        can_hint = 'CAN interface down — check adapter'
    elif usb_serial and not can_ifaces:
        can_hint = 'USB serial detected but no CAN interface — adapter may need setup'

    rtl_ok = _probe_rtl_sdr()
    services_status = _probe_services()

    # MQTT data flow — distinguish engine data from system/watchdog data.
    last_update = latest_state.get('_last_update', 0)
    data_age = time.time() - last_update if last_update else None
    engine_keys = [k for k in latest_state
                   if k.startswith('engine_') and not k.startswith('_')]
    all_keys = [k for k in latest_state if not k.startswith('_')]
    has_engine_data = (bool(engine_keys) and data_age is not None
                       and data_age < 60)

    hw = {
        'can': {
            'interfaces': can_ifaces,
            'usb_serial': usb_serial,
            'ok': can_ok,
            'hint': can_hint,
        },
        'rtl_sdr': {
            'ok': rtl_ok,
            'hint': '' if rtl_ok else 'No RTL-SDR dongle detected',
        },
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
    }
    hw['summary'] = _build_summary(
        can_ifaces, usb_serial, rtl_ok, has_engine_data,
        engine_keys, data_age, services_status,
    )
    return hw

"""Field-facing vehicle-link state tests for the cockpit Hardware page."""
from __future__ import annotations

import sys

sys.path.insert(0, 'src')

import web_dashboard_hardware as hw


def _services(obd='active'):
    return {'drifter-obdbridge': obd, 'mosquitto': 'active'}


def test_unconfigured_link_points_operator_to_elm_setup(monkeypatch):
    monkeypatch.setattr(hw, '_field_env', lambda: {})
    monkeypatch.setattr(hw, '_retained_obd_status', lambda: {})

    link = hw._vehicle_link(_services(), False, False, None)

    assert link['transport'] == 'unconfigured'
    assert link['state'] == 'missing'
    assert 'drifter obd scan' in link['action']
    assert 'drifter obd setup' in link['action']


def test_configured_bluetooth_elm_hw_pending_is_actionable(monkeypatch):
    monkeypatch.setattr(
        hw,
        '_field_env',
        lambda: {
            'DRIFTER_TRANSPORT': 'elm327',
            'DRIFTER_ELM_LINK': 'bluetooth',
            'ELM_BT_MAC': 'AA:BB:CC:DD:EE:FF',
        },
    )
    monkeypatch.setattr(
        hw,
        '_retained_obd_status',
        lambda: {'state': 'hw_pending', 'device': 'bluetooth://AA:BB:CC:DD:EE:FF:1'},
    )

    link = hw._vehicle_link(_services(), False, False, None)

    assert link['transport'] == 'elm327'
    assert link['state'] == 'waiting'
    assert 'adapter not ready' in link['detail']
    assert 'drifter obd scan' in link['action']


def test_live_engine_data_makes_elm_vehicle_ready(monkeypatch):
    monkeypatch.setattr(
        hw,
        '_field_env',
        lambda: {
            'DRIFTER_TRANSPORT': 'elm327',
            'DRIFTER_ELM_LINK': 'wifi',
            'ELM_WIFI_HOST': '192.168.0.10',
            'ELM_WIFI_PORT': '35000',
        },
    )
    monkeypatch.setattr(
        hw,
        '_retained_obd_status',
        lambda: {'state': 'online', 'device': 'wifi://192.168.0.10:35000'},
    )

    link = hw._vehicle_link(_services(), False, True, 0.4)

    assert link['transport'] == 'elm327'
    assert link['state'] == 'online'
    assert link['ready'] is True
    assert link['action'] == ''


def test_failed_obdbridge_is_reported_before_blame_is_put_on_vehicle(monkeypatch):
    monkeypatch.setattr(
        hw,
        '_field_env',
        lambda: {
            'DRIFTER_TRANSPORT': 'elm327',
            'DRIFTER_ELM_LINK': 'bluetooth',
            'ELM_BT_MAC': 'AA:BB:CC:DD:EE:FF',
        },
    )
    monkeypatch.setattr(hw, '_retained_obd_status', lambda: {})

    link = hw._vehicle_link(_services(obd='failed'), False, False, None)

    assert link['state'] == 'error'
    assert 'bridge is failed' in link['detail']
    assert 'systemctl restart drifter-obdbridge' in link['action']


def test_raw_can_absence_is_not_failure_when_elm_owns_vehicle_link(monkeypatch):
    probes = {
        device: {'connected': False, 'detail': f'{device} absent', 'action': 'plug it in'}
        for device in hw.DEVICES
    }
    link = {
        'transport': 'elm327',
        'state': 'waiting',
        'ready': False,
        'detail': 'ELM327 configured',
        'action': 'drifter obd test',
    }

    rows = hw._build_summary(probes, link, _services())
    can = next(row for row in rows if row['item'] == 'Raw CAN Adapter')

    assert rows[0]['item'] == 'Vehicle Link'
    assert can['status'] == 'unused'
    assert can['action'] == 'ELM327 is the active OBD transport'

"""Tests for hw_probe — the single source of truth for hardware presence."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

import hw_probe


_RESULT_KEYS = {'device', 'connected', 'detail', 'action', 'ts'}


def test_devices_list_matches_probe_registry():
    """DEVICES tuple is the public contract — must match the dispatch table."""
    assert set(hw_probe.DEVICES) == set(hw_probe._PROBES.keys())


def test_probe_all_returns_one_result_per_device():
    results = hw_probe.probe_all()
    assert set(results.keys()) == set(hw_probe.DEVICES)
    for device, r in results.items():
        assert set(r.keys()) == _RESULT_KEYS, f'{device} missing keys'
        assert r['device'] == device
        assert isinstance(r['connected'], bool)
        # Action must be empty when connected; populated when not.
        if r['connected']:
            assert r['action'] == ''
        else:
            assert r['action'], f'{device}: missing action hint'


def test_probe_rejects_unknown_device():
    with pytest.raises(KeyError):
        hw_probe.probe('not-a-device')


def test_hw_topic_format():
    assert hw_probe.hw_topic('can') == 'drifter/hw/can'
    assert hw_probe.hw_topic('rtl_sdr') == 'drifter/hw/rtl_sdr'


def test_publish_hw_state_retains_payload_on_topic():
    client = MagicMock()
    result = {'device': 'can', 'connected': True, 'detail': 'can0 UP',
              'action': '', 'ts': 1.0}
    hw_probe.publish_hw_state(client, 'can', result)
    client.publish.assert_called_once()
    args, kwargs = client.publish.call_args
    assert args[0] == 'drifter/hw/can'
    assert json.loads(args[1]) == result
    assert kwargs.get('retain') is True
    assert kwargs.get('qos') == 1


def test_probe_can_handles_missing_adapter():
    """No `ip` output → reports missing with actionable hint."""
    with patch.object(hw_probe, '_run', return_value=''):
        r = hw_probe.probe_can()
    assert r['connected'] is False
    assert r['device'] == 'can'
    assert 'USB2CAN' in r['action']


def test_probe_can_recognises_up_interface():
    fake = 'can0             UP             <NOARP,UP,LOWER_UP,ECHO>'
    with patch.object(hw_probe, '_run', return_value=fake):
        r = hw_probe.probe_can()
    assert r['connected'] is True
    assert 'can0' in r['detail']
    assert r['action'] == ''


def test_probe_can_distinguishes_serial_only():
    """USB serial present but no can0 → reports the configuration gap."""
    def fake_run(args, **kw):
        if 'link' in args:
            return ''
        return ''
    with patch.object(hw_probe, '_run', side_effect=fake_run), \
         patch('hw_probe.globmod.glob', side_effect=lambda p: ['/dev/ttyUSB0'] if 'USB' in p else []):
        r = hw_probe.probe_can()
    assert r['connected'] is False
    assert '/dev/ttyUSB0' in r['detail']


def test_probe_rtl_sdr_detects_realtek_marker():
    with patch.object(hw_probe, '_run', return_value='Bus 001 Device 005: ID 0bda:2838 Realtek RTL2838'):
        r = hw_probe.probe_rtl_sdr()
    assert r['connected'] is True


def test_probe_bluetooth_parses_up_running():
    fake_hci = (
        'hci0:\tType: Primary  Bus: UART\n'
        '\tBD Address: AA:BB:CC:DD:EE:FF\n'
        '\tUP RUNNING\n'
    )
    with patch.object(hw_probe, '_has_binary', return_value=True), \
         patch.object(hw_probe, '_run', return_value=fake_hci):
        r = hw_probe.probe_bluetooth()
    assert r['connected'] is True
    assert 'hci0' in r['detail']


def test_probe_bluetooth_reports_down_adapter():
    fake_hci = (
        'hci0:\tType: Primary  Bus: UART\n'
        '\tBD Address: AA:BB:CC:DD:EE:FF\n'
        '\tDOWN\n'
    )
    with patch.object(hw_probe, '_has_binary', return_value=True), \
         patch.object(hw_probe, '_run', return_value=fake_hci):
        r = hw_probe.probe_bluetooth()
    assert r['connected'] is False
    assert 'hciconfig' in r['action']


def test_probe_microphone_no_cards():
    with patch.object(hw_probe, '_has_binary', return_value=True), \
         patch.object(hw_probe, '_run', return_value=''):
        r = hw_probe.probe_microphone()
    assert r['connected'] is False
    assert 'USB mic' in r['action']


def test_probe_microphone_with_cards():
    fake = 'card 0: U18dB [USB Audio Device], device 0: USB Audio'
    with patch.object(hw_probe, '_has_binary', return_value=True), \
         patch.object(hw_probe, '_run', return_value=fake):
        r = hw_probe.probe_microphone()
    assert r['connected'] is True


def test_probe_flipper_via_usb_id():
    with patch.object(hw_probe, '_run', return_value='Bus 003 Device 007: ID 0483:5740 STMicroelectronics'), \
         patch('hw_probe.globmod.glob', return_value=['/dev/ttyACM0']):
        r = hw_probe.probe_flipper()
    assert r['connected'] is True
    assert '/dev/ttyACM0' in r['detail']


def test_probe_framebuffer_present(tmp_path, monkeypatch):
    # We can't manufacture /dev/fb1, but the probe is a one-liner Path check.
    real_path = hw_probe.Path
    fake_fb = tmp_path / 'fb1'
    fake_fb.touch()

    class FakePath:
        def __init__(self, p): self._p = real_path(p) if p != '/dev/fb1' else fake_fb
        def exists(self): return self._p.exists()

    with patch.object(hw_probe, 'Path', FakePath):
        r = hw_probe.probe_framebuffer()
    assert r['connected'] is True

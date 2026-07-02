# tests/test_obd_transport.py
"""
Transport auto-selection between the raw-CAN bridge and the ELM327/K-line
bridge. These are mutually-exclusive telemetry sources; select_transport picks
which one publishes so they never double-publish.
"""
import sys

import pytest

sys.path.insert(0, 'src')

import obd_transport


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv('DRIFTER_TRANSPORT', raising=False)


def _stub(monkeypatch, *, socketcan=False, can_serial=False, elm327=False):
    monkeypatch.setattr(obd_transport, '_socketcan_iface_present', lambda: socketcan)
    monkeypatch.setattr(obd_transport, '_can_serial_adapter_present', lambda: can_serial)
    monkeypatch.setattr(obd_transport, '_elm327_present', lambda: elm327)


class TestPrecedence:
    def test_env_override_can(self, monkeypatch):
        monkeypatch.setenv('DRIFTER_TRANSPORT', 'can')
        # Even with an ELM327 plugged, the operator override wins.
        _stub(monkeypatch, elm327=True)
        assert obd_transport.select_transport() == obd_transport.CAN

    def test_env_override_elm327(self, monkeypatch):
        monkeypatch.setenv('DRIFTER_TRANSPORT', 'elm327')
        _stub(monkeypatch, socketcan=True)
        assert obd_transport.select_transport() == obd_transport.ELM327

    def test_env_override_aliases(self, monkeypatch):
        _stub(monkeypatch)
        for val in ('kline', 'obd', 'serial'):
            monkeypatch.setenv('DRIFTER_TRANSPORT', val)
            assert obd_transport.select_transport() == obd_transport.ELM327

    def test_socketcan_iface_wins(self, monkeypatch):
        _stub(monkeypatch, socketcan=True, elm327=True)
        assert obd_transport.select_transport() == obd_transport.CAN

    def test_can_serial_adapter_wins_over_elm327(self, monkeypatch):
        # A CANable plugged (serial adapter) before slcand brings its iface up
        # must still select CAN, not be mistaken for an ELM327.
        _stub(monkeypatch, can_serial=True, elm327=True)
        assert obd_transport.select_transport() == obd_transport.CAN

    def test_elm327_selected_when_only_serial_present(self, monkeypatch):
        _stub(monkeypatch, elm327=True)
        assert obd_transport.select_transport() == obd_transport.ELM327

    def test_default_is_can(self, monkeypatch):
        # Nothing plugged -> canbridge is the active-waiting transport.
        _stub(monkeypatch)
        assert obd_transport.select_transport() == obd_transport.CAN


class TestHelpers:
    def test_socketcan_iface_present(self, monkeypatch):
        monkeypatch.setattr(obd_transport.os.path, 'exists',
                            lambda p: p == '/sys/class/net/slcan0')
        assert obd_transport._socketcan_iface_present() is True
        monkeypatch.setattr(obd_transport.os.path, 'exists', lambda p: False)
        assert obd_transport._socketcan_iface_present() is False

    def test_can_serial_adapter_detected_by_vid_pid(self, monkeypatch):
        monkeypatch.setattr(obd_transport.glob, 'glob',
                            lambda pat: ['/dev/ttyACM0'] if 'ACM' in pat else [])
        # udev reports a CANable (0483:5740) -> a CAN adapter.
        monkeypatch.setattr(obd_transport, '_usb_vid_pid',
                            lambda dev: ('0483', '5740'))
        assert obd_transport._can_serial_adapter_present() is True

    def test_generic_serial_is_not_a_can_adapter(self, monkeypatch):
        monkeypatch.setattr(obd_transport.glob, 'glob',
                            lambda pat: ['/dev/ttyUSB0'] if 'USB' in pat else [])
        # A CH340 ELM327 (1a86:7523) is NOT on the CAN allowlist.
        monkeypatch.setattr(obd_transport, '_usb_vid_pid',
                            lambda dev: ('1a86', '7523'))
        assert obd_transport._can_serial_adapter_present() is False

    def test_elm327_present_checks_device_path(self, monkeypatch):
        monkeypatch.setattr(obd_transport.os.path, 'exists', lambda p: True)
        assert obd_transport._elm327_present() is True
        monkeypatch.setattr(obd_transport.os.path, 'exists', lambda p: False)
        assert obd_transport._elm327_present() is False

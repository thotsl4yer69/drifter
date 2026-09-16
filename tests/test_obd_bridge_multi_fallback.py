import sys

sys.path.insert(0, 'src')

import elm_link
import obd_bridge

# Importing the runtime multi-link entrypoint intentionally replaces
# obd_bridge._open_elm. Restore the original immediately so test collection does
# not alter unrelated obd_bridge tests; call _open_multi_elm directly below.
_original_open_elm = obd_bridge._open_elm
import obd_bridge_multi  # noqa: E402
obd_bridge._open_elm = _original_open_elm


class FakeStream:
    def __init__(self, name):
        self.name = name
        self.closed = False

    def close(self):
        self.closed = True


def test_auto_mode_skips_open_but_invalid_serial_for_valid_bluetooth(monkeypatch):
    cfg = elm_link.LinkConfig(
        mode='auto', serial_dev='/dev/ttyUSB0', serial_baud=38400,
        bt_mac='AA:BB:CC:DD:EE:FF', bt_channel=1, timeout=1.0,
    )
    serial = FakeStream('serial')
    bluetooth = FakeStream('bluetooth')
    opened = []

    monkeypatch.setattr(elm_link.LinkConfig, 'from_env', classmethod(
        lambda cls, **kwargs: cfg
    ))
    monkeypatch.setattr(elm_link, 'candidate_modes', lambda _cfg: ['serial', 'bluetooth'])

    def fake_open(candidate):
        opened.append(candidate.mode)
        if candidate.mode == 'serial':
            return serial, 'serial:///dev/ttyUSB0@38400'
        return bluetooth, 'bluetooth://AA:BB:CC:DD:EE:FF:1'

    monkeypatch.setattr(elm_link, 'open_elm_link', fake_open)

    def fake_init(stream):
        if stream is serial:
            return {
                'adapter_ok': False, 'ecu_ok': False,
                'protocol': 'unknown', 'reason': 'adapter_no_identity',
            }
        return {
            'adapter_ok': True, 'ecu_ok': True,
            'protocol': 'ISO 9141-2 (K-line)', 'reason': 'online',
            'identity': 'ELM327 v1.5',
        }

    monkeypatch.setattr(obd_bridge, 'initialise_elm', fake_init)

    stream = obd_bridge_multi._open_multi_elm()

    assert stream is bluetooth
    assert opened == ['serial', 'bluetooth']
    assert serial.closed is True
    assert bluetooth.closed is False
    assert obd_bridge._last_elm_meta['adapter_ok'] is True
    assert obd_bridge._last_elm_meta['device'].startswith('bluetooth://')


def test_exhausted_candidates_report_combined_failure(monkeypatch):
    cfg = elm_link.LinkConfig(mode='auto', serial_dev='/dev/ttyUSB0', serial_baud=38400)
    serial = FakeStream('serial')

    monkeypatch.setattr(elm_link.LinkConfig, 'from_env', classmethod(
        lambda cls, **kwargs: cfg
    ))
    monkeypatch.setattr(elm_link, 'candidate_modes', lambda _cfg: ['serial', 'wifi'])

    def fake_open(candidate):
        if candidate.mode == 'serial':
            return serial, 'serial:///dev/ttyUSB0@38400'
        raise RuntimeError('connection refused')

    monkeypatch.setattr(elm_link, 'open_elm_link', fake_open)
    monkeypatch.setattr(obd_bridge, 'initialise_elm', lambda _stream: {
        'adapter_ok': False, 'ecu_ok': False,
        'protocol': 'unknown', 'reason': 'adapter_no_identity',
    })

    assert obd_bridge_multi._open_multi_elm() is None
    assert serial.closed is True
    assert obd_bridge._last_elm_meta['adapter_ok'] is False
    assert 'serial: ELM proof failed' in obd_bridge._last_elm_meta['reason']
    assert 'wifi: open failed' in obd_bridge._last_elm_meta['reason']

import sys

sys.path.insert(0, 'src')

import obd_bridge


class FakeELM:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.commands = []
        self._pending = b''

    def reset_input_buffer(self):
        self._pending = b''

    def write(self, data):
        cmd = data.decode('ascii').strip()
        self.commands.append(cmd)
        value = self.responses.get(cmd, b'OK>')
        self._pending = value if isinstance(value, bytes) else value.encode('ascii')
        return len(data)

    def read(self, _size):
        out, self._pending = self._pending, b''
        return out

    def close(self):
        pass


def test_query_pid_accepts_spaced_response():
    elm = FakeELM({'010C': b'41 0C 1A F8>'})
    assert obd_bridge._query_pid(elm, '010C') == [0x1A, 0xF8]


def test_query_pid_accepts_compact_clone_response():
    elm = FakeELM({'010C': b'410C1AF8>'})
    assert obd_bridge._query_pid(elm, '010C') == [0x1A, 0xF8]


def test_parser_tolerates_searching_and_kline_bus_init_text():
    elm = FakeELM({'0100': b'SEARCHING...\rBUS INIT: OK\r41 00 BE 3E B8 13>'})
    assert obd_bridge._query_pid(elm, '0100') == [0xBE, 0x3E, 0xB8, 0x13]


def test_adapter_setup_keeps_spaces_enabled():
    elm = FakeELM()
    assert obd_bridge._configure_adapter(elm, '0') is True
    assert 'ATS1' in elm.commands
    assert 'ATS0' not in elm.commands


def test_initialise_elm_proves_adapter_and_ecu_separately():
    elm = FakeELM({
        'ATI': b'ELM327 v1.5>',
        '0100': b'41 00 BE 3E B8 13>',
        'ATDPN': b'A3>',
    })
    meta = obd_bridge.initialise_elm(elm)
    assert meta['adapter_ok'] is True
    assert meta['ecu_ok'] is True
    assert 'ISO 9141-2' in meta['protocol']


def test_voltage_fallback_uses_adapter_supply_voltage():
    elm = FakeELM({'ATRV': b'12.6V>'})
    assert obd_bridge._query_voltage(elm) == 12.6


def test_mode03_dtc_decoder():
    # 01 33 -> P0133, 03 00 -> P0300
    assert obd_bridge._decode_dtcs([0x01, 0x33, 0x03, 0x00, 0x00, 0x00]) == ['P0133', 'P0300']


def test_mode03_query_handles_no_codes():
    elm = FakeELM({'03': b'NO DATA>'})
    assert obd_bridge._query_dtcs(elm) == []

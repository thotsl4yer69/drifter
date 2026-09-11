"""Wire-format regressions from the vehicle readiness audit; no vehicle needed."""
import socket
import threading
from collections import deque
from types import SimpleNamespace

import pytest

import elm_link
import elm_protocol as ep
import obd_bridge
import obd_transport
from obd_pids import PID_TABLE


class Chunks:
    def __init__(self, chunks):
        self.chunks = deque(chunks)
        self.writes = []
    def read(self, _n):
        return self.chunks.popleft() if self.chunks else b''
    def write(self, value):
        self.writes.append(value)


@pytest.mark.parametrize('raw', ['41 0C 1A F8\r>', '410c1af8\r>', '010C\rSEARCHING...\r410C1AF8\r>'])
def test_spaced_compact_echo_and_search(raw):
    assert ep.pid_data(raw, 0x0C, 2) == [0x1A, 0xF8]


def test_pid_echo_and_length_are_checked():
    assert ep.pid_data('410D50\r>', 0x0C, 2) is None
    assert ep.pid_data('410C1A\r>', 0x0C, 2) is None
    assert ep.pid_data('NO DATA\r>', 0x0C, 2) is None


def test_multiple_ecu_lines_are_not_concatenated():
    assert ep.pid_data('410C1AF8\r410C2222\r>', 0x0C, 2) == [0x1A, 0xF8]


def test_fragmented_reply_waits_for_prompt():
    stream = Chunks([b'41', b'0C', b'1A', b'F8\r', b'>'])
    assert ep.command(stream, '010C') == '410C1AF8\r'
    assert stream.writes == [b'010C\r']


def test_partial_reply_is_a_connection_failure():
    with pytest.raises(ep.ELMError, match='prompt timeout'):
        ep.command(Chunks([b'410C1AF8']), '010C', timeout=0.02)


def test_init_rejects_a_non_elm_serial_device():
    with pytest.raises(ep.ELMError, match='identify'):
        ep.initialize(Chunks([b'GPS receiver\r>']))


def test_socket_eof_is_not_a_timeout():
    a, b = socket.socketpair()
    b.close()
    stream = elm_link.SocketStream(a, 'test')
    try:
        with pytest.raises(ConnectionError):
            stream.read()
    finally:
        stream.close()


def test_socket_fragmented_command_roundtrip():
    a, b = socket.socketpair()
    a.settimeout(0.02)
    received = []
    def responder():
        received.append(b.recv(128))
        for part in [b'41', b'0C1AF8', b'\r>']:
            b.sendall(part)
        b.close()
    t = threading.Thread(target=responder)
    t.start()
    stream = elm_link.SocketStream(a, 'test')
    try:
        assert ep.pid_data(ep.command(stream, '010C'), 0x0C, 2) == [0x1A, 0xF8]
    finally:
        stream.close()
        t.join(timeout=1)
    assert received == [b'010C\r']


def test_fallback_closes_a_connected_but_invalid_candidate(monkeypatch):
    closed = []
    first = SimpleNamespace(label='bluetooth://test', close=lambda: closed.append('bt'))
    second = SimpleNamespace(label='wifi://test', close=lambda: None)
    monkeypatch.setattr(elm_link, '_open_bluetooth', lambda _: first)
    monkeypatch.setattr(elm_link, '_open_wifi', lambda _: second)
    monkeypatch.setattr(elm_link.os.path, 'exists', lambda _: False)
    def init(stream):
        if stream is first:
            raise ep.ELMError('bad adapter')
    cfg = elm_link.LinkConfig(bt_mac='AA:BB:CC:DD:EE:FF', wifi_host='localhost')
    assert elm_link.open_elm_link(cfg, init)[0] is second
    assert closed == ['bt']


@pytest.mark.parametrize('kwargs', [{'wifi_port': 0}, {'bt_channel': 31}, {'timeout': float('nan')}, {'bt_mac': 'garbage'}, {'mode': 'unknown'}])
def test_invalid_configuration_fails_before_io(kwargs):
    with pytest.raises(ValueError):
        elm_link.LinkConfig(**kwargs)


@pytest.mark.parametrize('protocol,raw', [(False, '43 01 71 03 01 00 00\r>'), (True, '43 02 01 71 03 01\r>')])
def test_kline_and_can_dtcs(protocol, raw):
    assert ep.dtc_codes(raw, 3, can_protocol=protocol) == ['P0171', 'P0301']


def test_dtc_unknown_and_empty_are_distinct():
    assert ep.dtc_codes('NO DATA\r>', 3, can_protocol=False) is None
    assert ep.dtc_codes('43000000000000\r>', 3, can_protocol=False) == []
    assert ep.dtc_codes('4300\r>', 3, can_protocol=True) == []
    assert ep.dtc_codes('43030171\r>', 3, can_protocol=True) is None


def test_elm_numbered_vin_and_incomplete_response():
    raw = '014\r0:49020153414A\r1:45413531443434\r2:58443339323833\r>'
    assert ep.vin_from_reply(raw) == 'SAJEA51D44XD39283'
    assert ep.vin_from_reply('014\r0:49020153414A\r>') is None


def test_kline_chunked_vin():
    vin = b'SAJEA51D44XD39283'
    padded = b'\0\0\0' + vin
    raw = '\r'.join((b'\x49\x02' + bytes([i + 1]) + padded[4*i:4*i+4]).hex() for i in range(5))
    assert ep.vin_from_reply(raw) == vin.decode()


def test_zero_support_bitmap_does_not_poll_everything():
    stream = Chunks([b'410000000000\r>'])
    assert obd_bridge.active_pid_defs(stream) == {}
    assert stream.writes == [b'0100\r']


def test_bank_two_upstream_sensor_pid():
    assert PID_TABLE[0x18].name == 'o2_b2s1'
    assert 0x15 not in PID_TABLE


def test_snapshot_keeps_only_fresh_original_timestamps():
    snap = obd_bridge.fresh_snapshot({'rpm': 2000, 'coolant': 95}, {'rpm': 10, 'coolant': 29}, 30)
    assert 'rpm' not in snap
    assert snap['coolant'] == 95 and snap['sample_ts'] == {'coolant': 29}


def test_both_processes_select_configured_wireless_over_can(monkeypatch):
    monkeypatch.delenv('DRIFTER_TRANSPORT', raising=False)
    monkeypatch.setenv('DRIFTER_ELM_LINK', 'wifi')
    monkeypatch.setattr(obd_transport, '_socketcan_iface_present', lambda: True)
    assert obd_transport.select_transport() == obd_transport.ELM327
    monkeypatch.setenv('DRIFTER_TRANSPORT', 'can')
    assert obd_transport.select_transport() == obd_transport.CAN

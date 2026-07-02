# tests/test_iso_tp.py
"""
ISO-TP (ISO 15765-2) reassembly on the raw CAN path. The `can` module is mocked
so these run without hardware; can.Message is a tiny real class so we can assert
the exact frames sent (notably that Flow Control goes to the right ECU).
"""
import sys

import pytest


class _FakeCanError(Exception):
    pass


class _Msg:
    def __init__(self, arbitration_id=0, data=None, is_extended_id=False):
        self.arbitration_id = arbitration_id
        self.data = list(data) if data is not None else []
        self.is_extended_id = is_extended_id


class _FakeCanMod:
    Message = _Msg
    CanError = _FakeCanError


sys.modules.setdefault('can', _FakeCanMod())
sys.path.insert(0, 'src')

import iso_tp  # noqa: E402


@pytest.fixture(autouse=True)
def _patch_can(monkeypatch):
    # Other test modules also stub sys.modules['can'] (as a MagicMock), and
    # iso_tp binds `can` at import — so pin iso_tp.can to our real-message fake
    # here, independent of collection order. monkeypatch auto-reverts.
    monkeypatch.setattr(iso_tp, 'can', _FakeCanMod)


class _FakeBus:
    """Returns queued frames from recv() in order, records sent messages."""

    def __init__(self, frames):
        # frames: list of (arb_id, [data bytes])
        self._frames = list(frames)
        self.sent = []

    def send(self, msg):
        self.sent.append(msg)

    def recv(self, timeout=0):
        if self._frames:
            arb, data = self._frames.pop(0)
            return _Msg(arbitration_id=arb, data=data)
        return None


class TestSingleFrame:
    def test_single_frame_payload(self):
        # SF: [len, 0x43, count, b1, b2] -> payload [0x43, count, b1, b2]
        bus = _FakeBus([(0x7E8, [0x04, 0x43, 0x01, 0x01, 0x71])])
        payload = iso_tp.read_response(bus, timeout=0.2)
        assert payload == bytes([0x43, 0x01, 0x01, 0x71])

    def test_request_builds_single_frame(self):
        bus = _FakeBus([(0x7E8, [0x03, 0x41, 0x0C, 0x1A])])
        iso_tp.request(bus, [0x01, 0x0C], timeout=0.2)
        # First sent message is the request: [len=2, 0x01, 0x0C, pad...]
        req = bus.sent[0]
        assert req.arbitration_id == 0x7DF
        assert req.data[:3] == [0x02, 0x01, 0x0C]
        assert len(req.data) == 8

    def test_no_response_returns_none(self):
        bus = _FakeBus([])
        assert iso_tp.read_response(bus, timeout=0.05) is None

    def test_out_of_range_ids_ignored(self):
        bus = _FakeBus([(0x100, [0x04, 0x43, 0x01, 0x01, 0x71])])
        assert iso_tp.read_response(bus, timeout=0.05) is None


class TestMultiFrame:
    """VIN (Mode 09 PID 02): First Frame + Consecutive Frames, gated on Flow
    Control. VIN 'SAJEA51D44XD39283' split across 3 frames."""

    def _vin_frames(self):
        return [
            # FF: total len 0x14 (20) = [0x49,0x02,count] + 17 VIN bytes
            (0x7E8, [0x10, 0x14, 0x49, 0x02, 0x01,
                     ord('S'), ord('A'), ord('J')]),
            (0x7E8, [0x21, ord('E'), ord('A'), ord('5'),
                     ord('1'), ord('D'), ord('4'), ord('4')]),
            (0x7E8, [0x22, ord('X'), ord('D'), ord('3'),
                     ord('9'), ord('2'), ord('8'), ord('3')]),
        ]

    def test_reassembles_full_payload(self):
        bus = _FakeBus(self._vin_frames())
        payload = iso_tp.read_response(bus, timeout=1.0)
        assert payload[:3] == bytes([0x49, 0x02, 0x01])
        assert bytes(payload[3:]) == b'SAJEA51D44XD39283'
        assert len(payload) == 20

    def test_flow_control_sent_to_the_responding_ecu(self):
        bus = _FakeBus(self._vin_frames())
        iso_tp.read_response(bus, timeout=1.0)
        # Exactly one FC frame, addressed to physical request id 0x7E0
        # (response 0x7E8 - 8), clear-to-send (0x30).
        fcs = [m for m in bus.sent if m.data and m.data[0] == 0x30]
        assert len(fcs) == 1
        assert fcs[0].arbitration_id == 0x7E0

    def test_consecutive_frames_from_other_ecu_ignored(self):
        frames = self._vin_frames()
        # Inject a stray CF from a different ECU between the real ones.
        frames.insert(1, (0x7E9, [0x21, 0xDE, 0xAD, 0xBE, 0xEF, 0, 0, 0]))
        bus = _FakeBus(frames)
        payload = iso_tp.read_response(bus, timeout=1.0)
        assert bytes(payload[3:]) == b'SAJEA51D44XD39283'

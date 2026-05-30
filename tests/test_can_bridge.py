# tests/test_can_bridge.py
"""
Tests for CAN bridge OBD-II decode functions.
The `can` library is mocked so these run without hardware.
"""
import sys
from unittest.mock import MagicMock

# Mock the `can` module before importing can_bridge
sys.modules['can'] = MagicMock()
sys.path.insert(0, 'src')

from can_bridge import decode_dtc, decode_obd_response, PIDS, TWO_BYTE_PIDS


# ── decode_dtc ──

class TestDecodeDtc:
    def test_zero_bytes_returns_none(self):
        assert decode_dtc(0x00, 0x00) is None

    def test_p0171_lean_bank1(self):
        # P0171: prefix=P (bits[7:6]=00), digit2=0 (bits[5:4]=00),
        #        digit3=1 (bits[3:0]=0001), digit4=7 (byte2>>4), digit5=1 (byte2&0F)
        # byte1 = 0b00000001 = 0x01, byte2 = 0x71
        assert decode_dtc(0x01, 0x71) == 'P0171'

    def test_p0174_lean_bank2(self):
        assert decode_dtc(0x01, 0x74) == 'P0174'

    def test_p0301_misfire_cyl1(self):
        # P0301: byte1=0x03, byte2=0x01
        assert decode_dtc(0x03, 0x01) == 'P0301'

    def test_prefix_c_chassis(self):
        # C prefix: bits[7:6] of byte1 = 01
        # byte1 = 0b01000000 = 0x40
        result = decode_dtc(0x40, 0x01)
        assert result.startswith('C')

    def test_prefix_b_body(self):
        # B prefix: bits[7:6] = 10
        result = decode_dtc(0x80, 0x01)
        assert result.startswith('B')

    def test_prefix_u_network(self):
        # U prefix: bits[7:6] = 11
        result = decode_dtc(0xC0, 0x01)
        assert result.startswith('U')

    def test_dtc_has_five_chars(self):
        # All valid DTCs are 5 characters: prefix + 4 hex/decimal digits
        for b1 in [0x01, 0x03, 0x40, 0x80, 0xC0]:
            for b2 in [0x00, 0x01, 0xFF]:
                if b1 == 0 and b2 == 0:
                    continue
                result = decode_dtc(b1, b2)
                assert result is None or len(result) == 5, f"decode_dtc(0x{b1:02X}, 0x{b2:02X}) = {result!r}"

    def test_nonzero_b2_only(self):
        # byte1=0 (but byte2 nonzero) still encodes a valid DTC
        result = decode_dtc(0x00, 0x01)
        # byte1=0 and byte2=0 → None, but byte1=0, byte2=1 is valid (P0001)
        # Actually the check is "if byte1 == 0 AND byte2 == 0"
        assert result == 'P0001'


# ── decode_obd_response ──

class TestDecodeObdResponse:
    """Tests decode_obd_response using a mock can.Message."""

    def _msg(self, arb_id, data):
        msg = MagicMock()
        msg.arbitration_id = arb_id
        msg.data = bytes(data)
        return msg

    def test_rpm_decodes_correctly(self):
        # Mode 01 PID 0x0C (RPM): value = ((A*256)+B)/4
        # 3000 RPM → raw = 12000 → A=0x2E, B=0xE0
        msg = self._msg(0x7E8, [0x04, 0x41, 0x0C, 0x2E, 0xE0, 0x00, 0x00, 0x00])
        pid, value = decode_obd_response(msg)
        assert pid == 0x0C
        assert abs(value - 3000.0) < 0.1

    def test_coolant_decodes_correctly(self):
        # PID 0x05 (coolant): value = A - 40; 90°C → A=130=0x82
        msg = self._msg(0x7E8, [0x03, 0x41, 0x05, 0x82, 0x00, 0x00, 0x00, 0x00])
        pid, value = decode_obd_response(msg)
        assert pid == 0x05
        assert value == 90

    def test_speed_decodes_correctly(self):
        # PID 0x0D (speed km/h): value = A; 80 km/h → A=0x50
        msg = self._msg(0x7E8, [0x03, 0x41, 0x0D, 0x50, 0x00, 0x00, 0x00, 0x00])
        pid, value = decode_obd_response(msg)
        assert pid == 0x0D
        assert value == 80

    def test_wrong_mode_byte_returns_none(self):
        # Mode byte is 0x42 (not 0x41)
        msg = self._msg(0x7E8, [0x04, 0x42, 0x0C, 0x2E, 0xE0, 0x00, 0x00, 0x00])
        assert decode_obd_response(msg) is None

    def test_out_of_range_arb_id_returns_none(self):
        msg = self._msg(0x100, [0x04, 0x41, 0x0C, 0x2E, 0xE0, 0x00, 0x00, 0x00])
        assert decode_obd_response(msg) is None

    def test_too_short_returns_none(self):
        msg = self._msg(0x7E8, [0x02, 0x41, 0x0C])
        assert decode_obd_response(msg) is None

    def test_unknown_pid_returns_none(self):
        msg = self._msg(0x7E8, [0x03, 0x41, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00])
        assert decode_obd_response(msg) is None

    def test_stft_decodes_percent(self):
        # PID 0x06 (STFT1): value = (A/1.28) - 100; 0% → A=128=0x80
        msg = self._msg(0x7E8, [0x03, 0x41, 0x06, 0x80, 0x00, 0x00, 0x00, 0x00])
        pid, value = decode_obd_response(msg)
        assert pid == 0x06
        assert abs(value - 0.0) < 0.5


# ── Regression: _consecutive_failures must be declared global in main() ──

class TestMainGlobals:
    """Guards the UnboundLocalError that crashed drifter-canbridge on
    2026-05-08: main() reads _consecutive_failures at the polling-loop
    health-check, but assigns to it on the recovery branch, so without
    a `global` declaration Python treats it as a local and the first
    read raises UnboundLocalError before any frame is sent."""

    def test_consecutive_failures_is_global_in_main(self):
        import can_bridge
        code = can_bridge.main.__code__
        # If `global _consecutive_failures` is missing, the name appears
        # in co_varnames (locals) instead of co_names (module-globals).
        assert '_consecutive_failures' not in code.co_varnames, (
            "main() treats _consecutive_failures as a local — add "
            "`global _consecutive_failures` at the top of main()"
        )
        assert '_consecutive_failures' in code.co_names, (
            "main() never references the module global "
            "_consecutive_failures — the recovery path is dead code"
        )

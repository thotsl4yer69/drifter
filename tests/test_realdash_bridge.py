#!/usr/bin/env python3
"""
MZ1312 DRIFTER — RealDash Bridge Tests
Tests frame packing functions produce valid RealDash CAN protocol frames.
Run: pytest tests/test_realdash_bridge.py -v
UNCAGED TECHNOLOGY — EST 1991
"""

import struct
import pytest

import realdash_bridge as rb


REALDASH_HEADER = bytes([0x44, 0x33, 0x22, 0x11])

# Actual frame IDs from realdash_bridge.py pack functions
ENGINE_FRAME_ID = 0x110
VEHICLE_FRAME_ID = 0x120
EXTENDED_FRAME_ID = 0x130
ALERT_FRAME_ID = 0x300
TPMS_FRAME_ID = 0x140
TPMS_TEMP_FRAME_ID = 0x150
EXTRA_ENGINE_FRAME_ID = 0x160
VEHICLE_EXTRA_FRAME_ID = 0x170


@pytest.fixture(autouse=True)
def reset_latest():
    """Reset the latest dict to defaults (all zeros) before each test."""
    for key in rb.latest:
        rb.latest[key] = 0
    rb.alert_message = ""
    yield


class TestFramePacking:
    """Verify each frame packer produces a valid 16-byte RealDash CAN frame."""

    def _validate_frame(self, frame_bytes, expected_frame_id):
        assert len(frame_bytes) == 16, f"Frame should be 16 bytes, got {len(frame_bytes)}"
        assert frame_bytes[:4] == REALDASH_HEADER, "Missing RealDash header"
        frame_id = struct.unpack_from('<I', frame_bytes, 4)[0]
        assert frame_id == expected_frame_id, (
            f"Expected frame ID 0x{expected_frame_id:04X}, got 0x{frame_id:04X}"
        )

    def test_engine_frame_structure(self):
        """Engine frame should encode RPM, coolant, STFT1, STFT2."""
        rb.latest['rpm'] = 800.0
        rb.latest['coolant'] = 90.0
        rb.latest['stft1'] = 2.5
        rb.latest['stft2'] = -1.0
        frame = rb.pack_engine_frame()
        self._validate_frame(frame, ENGINE_FRAME_ID)

    def test_vehicle_frame_structure(self):
        """Vehicle frame should encode speed, throttle, load, voltage."""
        rb.latest['speed'] = 60.0
        rb.latest['throttle'] = 25.0
        rb.latest['load'] = 35.0
        rb.latest['voltage'] = 14.2
        frame = rb.pack_vehicle_frame()
        self._validate_frame(frame, VEHICLE_FRAME_ID)

    def test_extended_frame_structure(self):
        """Extended frame should encode LTFT1, LTFT2, IAT, MAF."""
        rb.latest['ltft1'] = 3.0
        rb.latest['ltft2'] = -2.0
        rb.latest['iat'] = 30.0
        rb.latest['maf'] = 15.0
        frame = rb.pack_extended_frame()
        self._validate_frame(frame, EXTENDED_FRAME_ID)

    def test_alert_frame_structure(self):
        """Alert frame should encode alert level."""
        rb.latest['alert_level'] = 2
        frame = rb.pack_alert_frame()
        self._validate_frame(frame, ALERT_FRAME_ID)

    def test_empty_values_produce_valid_frames(self):
        """Packing with all zeros should still produce valid frames."""
        for pack_fn, fid in [
            (rb.pack_engine_frame, ENGINE_FRAME_ID),
            (rb.pack_vehicle_frame, VEHICLE_FRAME_ID),
            (rb.pack_extended_frame, EXTENDED_FRAME_ID),
            (rb.pack_alert_frame, ALERT_FRAME_ID),
            (rb.pack_extra_engine_frame, EXTRA_ENGINE_FRAME_ID),
            (rb.pack_vehicle_extra_frame, VEHICLE_EXTRA_FRAME_ID),
        ]:
            frame = pack_fn()
            assert len(frame) == 16
            self._validate_frame(frame, fid)

    def test_extra_engine_frame_structure(self):
        """Extra engine frame should encode O2 B1S1, O2 B2S1, timing, baro."""
        rb.latest['o2_b1s1'] = 0.78
        rb.latest['o2_b2s1'] = 0.82
        rb.latest['timing'] = 12.0
        rb.latest['baro'] = 101.0
        frame = rb.pack_extra_engine_frame()
        self._validate_frame(frame, EXTRA_ENGINE_FRAME_ID)

    def test_vehicle_extra_frame_structure(self):
        """Vehicle extra frame should encode fuel level and engine run time."""
        rb.latest['fuel_lvl'] = 63.0
        rb.latest['run_time'] = 4320
        frame = rb.pack_vehicle_extra_frame()
        self._validate_frame(frame, VEHICLE_EXTRA_FRAME_ID)

    def test_tpms_frame_structure(self):
        """TPMS frame should encode 4 tire pressures."""
        rb.latest['tpms_fl_psi'] = 32.0
        rb.latest['tpms_fr_psi'] = 31.5
        rb.latest['tpms_rl_psi'] = 30.0
        rb.latest['tpms_rr_psi'] = 29.5
        frame = rb.pack_tpms_frame()
        self._validate_frame(frame, TPMS_FRAME_ID)

    def test_tpms_temp_frame_structure(self):
        """TPMS temp frame should encode 4 tire temperatures."""
        rb.latest['tpms_fl_temp'] = 35.0
        rb.latest['tpms_fr_temp'] = 36.0
        rb.latest['tpms_rl_temp'] = 34.0
        rb.latest['tpms_rr_temp'] = 33.0
        frame = rb.pack_tpms_temp_frame()
        self._validate_frame(frame, TPMS_TEMP_FRAME_ID)


class TestRPMEncoding:
    """Verify RPM encodes differently at different values."""

    def test_rpm_encoding_800(self):
        """Idle RPM (800) should produce non-zero data in the engine frame."""
        rb.latest['rpm'] = 800.0
        frame = rb.pack_engine_frame()
        # RPM is first 2 bytes of data section (big-endian), raw value = rpm * 4
        rpm_raw = struct.unpack_from('>H', frame, 8)[0]
        assert rpm_raw == 800 * 4  # 3200

    def test_rpm_encoding_6000(self):
        """High RPM (6000) should encode differently from idle."""
        rb.latest['rpm'] = 6000.0
        frame_high = rb.pack_engine_frame()
        rb.latest['rpm'] = 800.0
        frame_low = rb.pack_engine_frame()
        assert frame_high[8:10] != frame_low[8:10]  # RPM bytes differ

    def test_coolant_encoding(self):
        """Coolant 90°C should encode as (90+40)*10 = 1300."""
        rb.latest['coolant'] = 90.0
        frame = rb.pack_engine_frame()
        coolant_raw = struct.unpack_from('>h', frame, 10)[0]
        assert coolant_raw == int((90 + 40) * 10)


class TestExtraEngineEncoding:
    """Pin the scaling maths for pack_extra_engine_frame (0x160)."""

    def test_o2_b1_encoding(self):
        """O2 B1S1 0.78V → 7800 raw (value × 10000)."""
        rb.latest['o2_b1s1'] = 0.78
        frame = rb.pack_extra_engine_frame()
        assert struct.unpack_from('>H', frame, 8)[0] == 7800

    def test_o2_b2_encoding(self):
        """O2 B2S1 0.82V → 8200 raw."""
        rb.latest['o2_b2s1'] = 0.82
        frame = rb.pack_extra_engine_frame()
        assert struct.unpack_from('>H', frame, 10)[0] == 8200

    def test_timing_encoding_positive(self):
        """Timing +12° → (12+64)*100 = 7600 raw."""
        rb.latest['timing'] = 12.0
        frame = rb.pack_extra_engine_frame()
        assert struct.unpack_from('>H', frame, 12)[0] == 7600

    def test_timing_encoding_negative(self):
        """Negative timing (-10° retard) must not underflow."""
        rb.latest['timing'] = -10.0
        frame = rb.pack_extra_engine_frame()
        assert struct.unpack_from('>H', frame, 12)[0] == (-10 + 64) * 100

    def test_baro_encoding(self):
        """Barometric 101 kPa → 1010 raw (kPa × 10)."""
        rb.latest['baro'] = 101.0
        frame = rb.pack_extra_engine_frame()
        assert struct.unpack_from('>H', frame, 14)[0] == 1010

    def test_o2_over_range_clamped(self):
        """An out-of-spec O2 reading must clamp to uint16 max, not wrap."""
        rb.latest['o2_b1s1'] = 10.0  # absurd — 10.0 × 10000 = 100000
        frame = rb.pack_extra_engine_frame()
        assert struct.unpack_from('>H', frame, 8)[0] == 65535


class TestVehicleExtraEncoding:
    """Pin the scaling maths for pack_vehicle_extra_frame (0x170)."""

    def test_fuel_encoding(self):
        """Fuel 63% → 6300 raw (percent × 100)."""
        rb.latest['fuel_lvl'] = 63.0
        frame = rb.pack_vehicle_extra_frame()
        assert struct.unpack_from('>H', frame, 8)[0] == 6300

    def test_fuel_full_tank(self):
        """100% fuel → 10000 raw, well below the 65535 clamp."""
        rb.latest['fuel_lvl'] = 100.0
        frame = rb.pack_vehicle_extra_frame()
        assert struct.unpack_from('>H', frame, 8)[0] == 10000

    def test_run_time_encoding(self):
        """Run time passes through unscaled."""
        rb.latest['run_time'] = 4320
        frame = rb.pack_vehicle_extra_frame()
        assert struct.unpack_from('>H', frame, 10)[0] == 4320

    def test_run_time_clamped_at_uint16_max(self):
        """Engine run >18h (65535s) clamps rather than wraps."""
        rb.latest['run_time'] = 70000
        frame = rb.pack_vehicle_extra_frame()
        assert struct.unpack_from('>H', frame, 10)[0] == 65535

    def test_vehicle_extra_data_padding(self):
        """Only 4 bytes of payload — remaining 4 data bytes must be zero-padded."""
        rb.latest['fuel_lvl'] = 50.0
        rb.latest['run_time'] = 1234
        frame = rb.pack_vehicle_extra_frame()
        assert frame[12:16] == b'\x00\x00\x00\x00'

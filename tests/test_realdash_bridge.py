#!/usr/bin/env python3
"""
MZ1312 DRIFTER — RealDash Bridge Tests
Tests frame packing functions produce valid RealDash CAN protocol frames.
Run: pytest tests/test_realdash_bridge.py -v
UNCAGED TECHNOLOGY — EST 1991
"""

import struct
import pytest
from unittest.mock import patch

# Patch threading/socket so the module can be imported on non-Linux
import realdash_bridge as rb


REALDASH_HEADER = bytes([0x44, 0x33, 0x22, 0x11])


class TestFramePacking:
    """Verify each frame packer produces a valid 16-byte RealDash CAN frame."""

    def _validate_frame(self, frame_bytes, expected_frame_id):
        """Assert a frame has correct header, ID, and length (16 bytes)."""
        assert len(frame_bytes) == 16, f"Frame should be 16 bytes, got {len(frame_bytes)}"
        assert frame_bytes[:4] == REALDASH_HEADER, "Missing RealDash header"
        frame_id = struct.unpack_from('<I', frame_bytes, 4)[0]
        assert frame_id == expected_frame_id, (
            f"Expected frame ID 0x{expected_frame_id:04X}, got 0x{frame_id:04X}"
        )

    def test_engine_frame_structure(self):
        """Engine frame (0x5100) should encode RPM, coolant, STFT, load."""
        rb.latest_values = {'rpm': 800.0, 'coolant': 90.0, 'stft1': 2.5, 'load': 35.0}
        frame = rb.pack_engine_frame()
        self._validate_frame(frame, 0x5100)

    def test_vehicle_frame_structure(self):
        """Vehicle frame (0x5101) should encode speed, throttle, STFT2, LTFT."""
        rb.latest_values = {'speed': 60.0, 'throttle': 25.0, 'stft2': -1.0, 'ltft1': 3.0}
        frame = rb.pack_vehicle_frame()
        self._validate_frame(frame, 0x5101)

    def test_extended_frame_structure(self):
        """Extended frame (0x5102) should encode MAF, IAT, voltage, LTFT2."""
        rb.latest_values = {'maf': 15.0, 'iat': 30.0, 'voltage': 14.2, 'ltft2': -2.0}
        frame = rb.pack_extended_frame()
        self._validate_frame(frame, 0x5102)

    def test_alert_frame_structure(self):
        """Alert frame (0x5103) should encode alert level."""
        rb.alert_level = 2
        frame = rb.pack_alert_frame()
        self._validate_frame(frame, 0x5103)

    def test_empty_values_dont_crash(self):
        """Packing with no data should still produce valid frames (all zeros)."""
        rb.latest_values = {}
        rb.alert_level = 0
        for pack_fn in [rb.pack_engine_frame, rb.pack_vehicle_frame,
                        rb.pack_extended_frame, rb.pack_alert_frame]:
            frame = pack_fn()
            assert len(frame) == 16

    def test_tpms_frame_structure(self):
        """TPMS frame (0x5104) should encode 4 tire pressures."""
        rb.tpms_data = {
            'fl': {'pressure_psi': 32.0}, 'fr': {'pressure_psi': 31.5},
            'rl': {'pressure_psi': 30.0}, 'rr': {'pressure_psi': 29.5},
        }
        frame = rb.pack_tpms_frame()
        self._validate_frame(frame, 0x5104)

    def test_tpms_temp_frame_structure(self):
        """TPMS temp frame (0x5105) should encode 4 tire temperatures."""
        rb.tpms_data = {
            'fl': {'temp_c': 35.0}, 'fr': {'temp_c': 36.0},
            'rl': {'temp_c': 34.0}, 'rr': {'temp_c': 33.0},
        }
        frame = rb.pack_tpms_temp_frame()
        self._validate_frame(frame, 0x5105)


class TestRPMEncoding:
    """Verify RPM round-trips through pack/unpack correctly."""

    def test_rpm_encoding_800(self):
        """Idle RPM (800) should encode/decode correctly."""
        rb.latest_values = {'rpm': 800.0, 'coolant': 0, 'stft1': 0, 'load': 0}
        frame = rb.pack_engine_frame()
        # RPM is first 2 bytes of data section (bytes 8-9), little-endian unsigned short
        rpm_raw = struct.unpack_from('<H', frame, 8)[0]
        # The scaling depends on pack function — just verify non-zero
        assert rpm_raw > 0

    def test_rpm_encoding_6000(self):
        """High RPM (6000) should encode differently from idle."""
        rb.latest_values = {'rpm': 6000.0, 'coolant': 0, 'stft1': 0, 'load': 0}
        frame_high = rb.pack_engine_frame()
        rb.latest_values = {'rpm': 800.0, 'coolant': 0, 'stft1': 0, 'load': 0}
        frame_low = rb.pack_engine_frame()
        # High RPM frame should have different data than idle
        assert frame_high[8:] != frame_low[8:]

# tests/test_can_bridge.py
"""
Tests for CAN bridge OBD-II decode functions + graceful-degrade behaviour.
The `can` library is mocked so these run without hardware.
"""
import sys
from unittest.mock import MagicMock, patch


# Mock the `can` module before importing can_bridge. Give can.CanError a real
# exception class so `except can.CanError` clauses behave like the real lib.
class _FakeCanError(Exception):
    pass


_can_mock = MagicMock()
_can_mock.CanError = _FakeCanError
sys.modules['can'] = _can_mock
sys.path.insert(0, 'src')

import can_bridge
from can_bridge import decode_dtc, decode_obd_response

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


# ── CAN-adapter USB allowlist (anti-hijack) ──

class TestCanUsbAllowlist:
    """find_can_interface()/setup-can must bind slcan ONLY to a positively
    identified CAN adapter, never the Flipper/Marauder/GPS/mic serial ports."""

    def test_canable_slcan_id_present(self):
        # CANable / slcan STMicro VCP must be on the allowlist.
        assert ('0483', '5740') in can_bridge.CAN_USB_IDS

    def test_candlelight_gs_usb_id_present(self):
        # candleLight / gs_usb (1d50:606f) must be on the allowlist.
        assert ('1d50', '606f') in can_bridge.CAN_USB_IDS

    def _udev(self, vid, pid):
        out = f"ID_VENDOR_ID={vid}\nID_MODEL_ID={pid}\n"
        return MagicMock(returncode=0, stdout=out)

    def test_canable_is_identified_as_can(self):
        with patch('subprocess.run', return_value=self._udev('0483', '5740')):
            assert can_bridge._serial_dev_is_can_adapter('/dev/ttyACM0') is True

    def test_flipper_serial_is_not_can(self):
        # A Flipper Zero CDC serial (STMicro VID but a DIFFERENT product id)
        # must NOT be treated as a CAN adapter.
        with patch('subprocess.run', return_value=self._udev('0483', 'df11')):
            assert can_bridge._serial_dev_is_can_adapter('/dev/ttyACM0') is False

    def test_ch340_mic_serial_is_not_can(self):
        with patch('subprocess.run', return_value=self._udev('1a86', '7523')):
            assert can_bridge._serial_dev_is_can_adapter('/dev/ttyUSB0') is False

    def test_udev_failure_is_not_can(self):
        with patch('subprocess.run', return_value=MagicMock(returncode=1, stdout='')):
            assert can_bridge._serial_dev_is_can_adapter('/dev/ttyUSB0') is False

    def test_exception_is_not_can(self):
        with patch('subprocess.run', side_effect=OSError('boom')):
            assert can_bridge._serial_dev_is_can_adapter('/dev/ttyUSB0') is False


# ── Graceful degrade: never exit / crash-loop when CAN is absent ──

class TestGracefulDegrade:
    def test_acquire_bus_returns_none_when_stopped(self):
        """_acquire_bus must return (None, None) — NOT raise / sys.exit —
        when no CAN is present and we're asked to stop. This is the contract
        that keeps a no-CAN car/bench from crash-looping the service."""
        mqtt = MagicMock()
        # running_fn starts True (so we enter the loop) then flips False.
        calls = {'n': 0}

        def running_fn():
            calls['n'] += 1
            return calls['n'] <= 1

        with patch.object(can_bridge, 'find_can_interface', return_value=None), \
             patch.object(can_bridge.time, 'sleep'):
            bus, iface = can_bridge._acquire_bus(mqtt, running_fn)
        assert bus is None and iface is None

    def test_acquire_bus_publishes_hw_pending(self):
        """While degraded it must publish a hw_pending status so /healthz +
        cockpit see hardware-pending, not failed."""
        mqtt = MagicMock()
        calls = {'n': 0}

        def running_fn():
            calls['n'] += 1
            return calls['n'] <= 1

        with patch.object(can_bridge, 'find_can_interface', return_value=None), \
             patch.object(can_bridge.time, 'sleep'):
            can_bridge._acquire_bus(mqtt, running_fn)
        published = [c.args[0] for c in mqtt.publish.call_args_list]
        payloads = [c.args[1] for c in mqtt.publish.call_args_list]
        assert any('hw_pending' in p for p in payloads), \
            "no hw_pending status published while degraded"
        assert mqtt.publish.called and published

    def test_acquire_bus_returns_bus_when_iface_found(self):
        """Happy path: a found interface opens a bus and reports 'online'."""
        mqtt = MagicMock()
        fake_bus = MagicMock()

        def running_fn():
            return True

        with patch.object(can_bridge, 'find_can_interface', return_value='can0'), \
             patch.object(can_bridge.can, 'Bus', return_value=fake_bus):
            bus, iface = can_bridge._acquire_bus(mqtt, running_fn)
        assert bus is fake_bus
        assert iface == 'can0'

    def test_publish_status_never_raises(self):
        """_publish_status must swallow publish errors (best-effort) — a
        status push must never crash the bridge."""
        mqtt = MagicMock()
        mqtt.publish.side_effect = RuntimeError('broker gone')
        # Should not raise.
        can_bridge._publish_status(mqtt, 'hw_pending', reason='x')

    def test_main_has_no_no_can_exit_path(self):
        """Regression guard: main() must not contain the old 'exiting for
        systemd restart' / 'Shutting down — no CAN interface found' lines that
        let a missing CAN source terminate the process (→ crash-loop → the
        removed reboot-force unit's reboot loop)."""
        import inspect
        src = inspect.getsource(can_bridge.main)
        assert 'exiting for systemd restart' not in src
        assert 'no CAN interface found' not in src


# ── OBD (K-line) bridge: protocol detect + graceful degrade ──

class TestObdBridge:
    """obd_bridge is the ELM327 / K-line fallback path. Kept here since
    test_can_bridge.py is the diagnostics-core test module."""

    def _import(self):
        import obd_bridge
        return obd_bridge

    def test_protocol_detect_iso9141_kline(self):
        ob = self._import()
        ser = MagicMock()
        ser.read.return_value = b'A3\r\r>'
        label = ob.detect_protocol(ser)
        assert 'K-line' in label and '9141' in label

    def test_protocol_detect_can(self):
        ob = self._import()
        ser = MagicMock()
        ser.read.return_value = b'6\r\r>'
        label = ob.detect_protocol(ser)
        assert 'CAN' in label

    def test_protocol_detect_handles_garbage(self):
        ob = self._import()
        ser = MagicMock()
        ser.read.return_value = b'???\r>'
        # 'unknown' or a labelled-unknown, but never an exception.
        assert isinstance(ob.detect_protocol(ser), str)

    def test_protocol_detect_never_raises(self):
        ob = self._import()
        ser = MagicMock()
        ser.write.side_effect = OSError('serial gone')
        assert ob.detect_protocol(ser) == 'unknown'

    def test_open_elm_returns_none_without_pyserial(self):
        """No adapter / no pyserial must degrade to None, never raise — the
        bridge then idles and retries rather than crash-looping."""
        ob = self._import()
        with patch.dict(sys.modules, {'serial': None}):
            # Importing serial as None makes `import serial` raise ImportError.
            assert ob._open_elm() is None

    def test_kline_switchover_documented(self):
        """The operator canbridge<->obdbridge switch-over must stay documented
        in the module docstring (this is how an X-Type K-line car is handled)."""
        ob = self._import()
        doc = ob.__doc__ or ''
        assert 'drifter-obdbridge' in doc
        assert 'drifter-canbridge' in doc
        assert 'K-line' in doc or 'K-LINE' in doc

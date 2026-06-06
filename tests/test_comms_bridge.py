"""Test that comms_bridge serialises modem access under _serial_lock.

The modem serial port is shared between the inbound reader thread and
_send_sms (MQTT callback thread). Without the lock an outbound SOS can have
its +CMGS/OK response stolen by the reader and be misreported as failed.
"""
from __future__ import annotations

import sys

sys.path.insert(0, 'src')

import comms_bridge


class _LockAssertingSerial:
    """Fake serial that asserts _serial_lock is held on every access."""

    def __init__(self, read_value=b'+CMGS: 1\r\nOK\r\n'):
        self._read_value = read_value
        self.accesses = 0

    def write(self, _data):
        assert comms_bridge._serial_lock.locked(), "write outside _serial_lock"
        self.accesses += 1

    def read(self, _n=1):
        assert comms_bridge._serial_lock.locked(), "read outside _serial_lock"
        self.accesses += 1
        return self._read_value


def test_send_sms_holds_lock_for_all_serial_io(monkeypatch):
    monkeypatch.setattr(comms_bridge.time, 'sleep', lambda *_a, **_k: None)
    ser = _LockAssertingSerial()
    ok = comms_bridge._send_sms(ser, '+15551234567', 'test')
    assert ok is True
    assert ser.accesses > 0
    # Lock must be released again once the send completes.
    assert not comms_bridge._serial_lock.locked()


def test_send_sms_reports_failure_without_ok(monkeypatch):
    monkeypatch.setattr(comms_bridge.time, 'sleep', lambda *_a, **_k: None)
    ser = _LockAssertingSerial(read_value=b'ERROR\r\n')
    assert comms_bridge._send_sms(ser, '+15551234567', 'test') is False
    assert not comms_bridge._serial_lock.locked()

#!/usr/bin/env python3
"""
MZ1312 DRIFTER — ISO-TP (ISO 15765-2) reassembly for the raw SocketCAN path.

Multi-frame OBD-II responses (VIN via Mode 09, a long DTC list via Mode 03/07)
are transported with ISO-TP framing: a First Frame declares the total length and
carries the first 6 bytes, then the tester MUST send a Flow Control frame before
the ECU will emit the Consecutive Frames. can_bridge / vehicle_id previously read
the raw frames without ever sending Flow Control and hand-parsed them — which
works by luck on ECUs that stream anyway, but drops data on any strict ISO-TP
stack (i.e. most modern cars). This module does it correctly:

  * builds and sends the Single-Frame request,
  * on a First Frame, sends Flow Control (clear-to-send, no block limit),
  * collects Consecutive Frames from the *same* responding ECU in order,
  * returns the reassembled service payload (starting at the response service
    byte, e.g. 0x49 for a Mode 09 reply).

11-bit addressing only — the raw bridge is 11-bit CAN; 29-bit / K-line / J1850
go through the ELM327 bridge (obd_bridge.py), which handles their framing itself.

Dependency-light: ``can`` + stdlib + config defaults. Never raises on a bus
error — returns ``None`` so the caller degrades instead of crashing.
UNCAGED TECHNOLOGY — EST 1991
"""
from __future__ import annotations

import logging
import time

import can

from config import OBD_REQUEST_ID, OBD_RESPONSE_BASE, OBD_RESPONSE_END

log = logging.getLogger(__name__)

# ISO-TP PCI (Protocol Control Information) frame types — the high nibble of
# byte 0.
_SF = 0x00   # Single Frame:      0x0L, L = payload length (1-7)
_FF = 0x10   # First Frame:       0x1X X.. = 12-bit total length
_CF = 0x20   # Consecutive Frame: 0x2N, N = sequence number (wraps 0..15)
_FC = 0x30   # Flow Control:      0x30 (clear-to-send) BS STmin

# Physical request id for an ECU response id. OBD 11-bit responses live at
# 0x7E8-0x7EF; the matching physical request is response-8 (0x7E0-0x7E7). Flow
# Control must be addressed to that specific ECU, not the 0x7DF functional
# broadcast.
_RESP_TO_REQ_OFFSET = 8


def _send_flow_control(bus, fc_id: int) -> bool:
    """Send a clear-to-send Flow Control frame (block size 0 = send everything,
    STmin 0 = as fast as possible). Best-effort."""
    try:
        bus.send(can.Message(
            arbitration_id=fc_id,
            data=[_FC, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00],
            is_extended_id=False,
        ))
        return True
    except Exception as e:  # pragma: no cover - defensive
        log.debug(f"flow-control send failed: {e}")
        return False


def read_response(bus, resp_lo: int = OBD_RESPONSE_BASE,
                  resp_hi: int = OBD_RESPONSE_END, timeout: float = 1.0):
    """Read one ISO-TP response (single- or multi-frame) already requested.

    Returns the reassembled service payload bytes (e.g. starts at 0x41/0x43/0x49
    for a Mode 01/03/09 reply), or ``None`` if nothing decodable arrived before
    ``timeout``. Sends Flow Control when a First Frame appears.
    """
    deadline = time.monotonic() + timeout
    expected_len: int | None = None
    buf = bytearray()
    resp_from: int | None = None
    while time.monotonic() < deadline:
        try:
            msg = bus.recv(timeout=0.1)
        except can.CanError:
            break
        if msg is None:
            if expected_len is not None and len(buf) >= expected_len:
                break
            continue
        if msg.arbitration_id < resp_lo or msg.arbitration_id > resp_hi:
            continue
        d = bytes(msg.data)
        if not d:
            continue
        pci = d[0] & 0xF0
        if pci == _SF:
            length = d[0] & 0x0F
            return d[1:1 + length]
        if pci == _FF:
            expected_len = ((d[0] & 0x0F) << 8) | d[1]
            buf.extend(d[2:8])
            resp_from = msg.arbitration_id
            _send_flow_control(bus, resp_from - _RESP_TO_REQ_OFFSET)
            continue
        if pci == _CF:
            # Only accept consecutive frames from the ECU that sent the FF.
            if resp_from is not None and msg.arbitration_id != resp_from:
                continue
            buf.extend(d[1:8])
            if expected_len is not None and len(buf) >= expected_len:
                return bytes(buf[:expected_len])
            continue
        # Flow-control / unknown frames from the ECU are ignored.
    if expected_len is not None and buf:
        return bytes(buf[:expected_len])
    return bytes(buf) if buf else None


def request(bus, service_bytes, req_id: int = OBD_REQUEST_ID,
            resp_lo: int = OBD_RESPONSE_BASE, resp_hi: int = OBD_RESPONSE_END,
            timeout: float = 1.0):
    """Send an OBD-II request as a Single Frame and return the ISO-TP-reassembled
    response payload. ``service_bytes`` is the service + optional PID, e.g.
    ``[0x09, 0x02]`` for VIN or ``[0x03]`` for stored DTCs. Returns ``None`` on
    a send error or no response."""
    n = len(service_bytes)
    frame = [n, *list(service_bytes)]
    frame += [0x00] * (8 - len(frame))  # pad to the 8-byte CAN payload
    try:
        bus.send(can.Message(arbitration_id=req_id, data=frame,
                             is_extended_id=False))
    except Exception as e:
        log.debug(f"ISO-TP request send failed: {e}")
        return None
    return read_response(bus, resp_lo, resp_hi, timeout)

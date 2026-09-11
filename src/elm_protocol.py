#!/usr/bin/env python3
"""MZ1312 DRIFTER — ELM327 command framing and read-only OBD decoding.
Prompt-delimited transactions shared by USB, Bluetooth Classic and Wi-Fi.
UNCAGED TECHNOLOGY — EST 1991
"""
from __future__ import annotations

import re
import time


class ELMError(OSError):
    """The adapter stream is unusable and must be reopened."""


def command(stream, request: str, timeout: float = 3.0) -> str:
    """Finish one command before sending another; never accept partial replies.

    A timeout loses framing: callers must reconnect rather than discard a late
    response and accidentally attribute its bytes to the next requested PID.
    """
    stream.write((request + '\r').encode('ascii'))
    deadline = time.monotonic() + timeout
    buf = bytearray()
    while time.monotonic() < deadline:
        # Serial.read(128) waits to fill all 128 bytes; read a single byte when
        # no buffered data is available. SocketStream.read returns one recv.
        waiting = getattr(stream, 'in_waiting', 0)
        size = min(max(waiting, 1), 4096) if isinstance(waiting, int) else 1
        chunk = stream.read(size)
        if chunk:
            buf.extend(chunk)
            if len(buf) > 16384:
                raise ELMError('ELM response exceeds 16 KiB')
            if b'>' in chunk:
                return bytes(buf).split(b'>', 1)[0].decode('ascii', errors='replace')
        else:
            time.sleep(0.005)
    raise ELMError(f'ELM prompt timeout after {request} ({timeout:g}s)')


def initialize(stream) -> None:
    """Prove an adapter is answering before marking its link connected."""
    raw = command(stream, 'ATZ', timeout=5.0).upper()
    if not any(name in raw for name in ('ELM', 'STN', 'OBDLINK')):
        raise ELMError('ATZ did not identify an ELM/STN-compatible adapter')
    for request in ('ATE0', 'ATH0', 'ATL0', 'ATS1', 'ATSP0'):
        response = command(stream, request)
        if 'OK' not in response.upper().split():
            raise ELMError(f'{request} rejected: {response.strip()[:80]}')


def payloads(raw: str, response_mode: int) -> list[bytes]:
    """Decode headerless ELM replies, preserving separate ECU responses.

    Accept both spaced and compact bytes, echoes/search banners, and ELM's
    numbered multiline CAN output. A declared CAN length bounds padding.
    """
    result: list[bytes] = []
    parts = bytearray()
    expected_index = 0
    declared_length = None

    def finish():
        nonlocal parts, declared_length, expected_index
        if parts and parts[0] == response_mode:
            if declared_length is None or len(parts) >= declared_length:
                result.append(bytes(parts[:declared_length]))
        parts = bytearray()
        declared_length = None
        expected_index = 0

    for line in re.split(r'[\r\n>]+', raw.upper()):
        line = re.sub(r'^\s*(?:SEARCHING\.*|BUS INIT:\s*\.?(?:OK)?)\s*', '', line).strip()
        if not line:
            continue
        indexed = re.fullmatch(r'([0-9A-F]+):\s*([0-9A-F ]+)', line)
        if indexed:
            index = int(indexed[1], 16)
            if index == 0 and parts:
                finish()
            if index != expected_index:
                raise ELMError('Out-of-order ELM multiline response')
            try:
                parts.extend(bytes.fromhex(indexed[2]))
            except ValueError as exc:
                raise ELMError('Malformed ELM multiline bytes') from exc
            expected_index += 1
            continue
        compact = ''.join(line.split())
        if re.fullmatch(r'[0-9A-F]{3}', compact):
            finish()
            declared_length = int(compact, 16)
            continue
        if not re.fullmatch(r'(?:[0-9A-F]{2})+', compact):
            continue
        data = bytes.fromhex(compact)
        if data[0] == response_mode:
            finish()
            result.append(data)
    finish()
    return result


def pid_data(raw: str, pid: int, nbytes: int = 1) -> list[int] | None:
    """Only return a complete reply to the requested Mode-01 PID."""
    for data in payloads(raw, 0x41):
        if len(data) >= 2 + nbytes and data[1] == pid:
            return list(data[2:])
    return None


def dtc_codes(raw: str, mode: int, *, can_protocol: bool) -> list[str] | None:
    """None means unanswered; [] means an explicit successful empty result."""
    replies = payloads(raw, mode + 0x40)
    if not replies:
        return None
    codes = set()
    for reply in replies:
        body = reply[1:]
        if can_protocol:
            if not body:
                return None
            count, body = body[0], body[1:]
            if len(body) < count * 2:
                return None
            body = body[:count * 2]
        if len(body) % 2:
            return None
        for a, b in zip(body[::2], body[1::2], strict=True):
            if a or b:
                codes.add(f'{"PCBU"[a >> 6]}{(a >> 4) & 3}{a & 15:X}{b:02X}')
    return sorted(codes)


def vin_from_reply(raw: str) -> str | None:
    messages = [p for p in payloads(raw, 0x49) if len(p) >= 3 and p[1] == 2]
    if not messages:
        return None
    # K-line uses numbered 49 02 nn chunks; CAN is normally one assembled
    # 49 02 01 payload. Reject conflicting sequence numbers (multiple ECUs).
    chunks = {}
    for p in messages:
        if p[2] in chunks and chunks[p[2]] != p[3:]:
            return None
        chunks[p[2]] = p[3:]
    if sorted(chunks) != list(range(1, len(chunks) + 1)):
        return None
    value = b''.join(chunks[k] for k in sorted(chunks)).strip(b'\x00').decode('ascii', errors='replace')
    return value if re.fullmatch(r'[A-HJ-NPR-Z0-9]{17}', value) else None

"""Tests for fleet_server JWT decode hardening.

Malformed/attacker-supplied tokens must yield a clean auth failure (None),
never an unhandled exception that turns into a 500.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import sys
import time

sys.path.insert(0, 'src')

import fleet_server

SECRET = "test-secret"


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b'=').decode()


def test_valid_token_roundtrips():
    tok = fleet_server._jwt_encode({'sub': 'admin', 'exp': time.time() + 60}, SECRET)
    out = fleet_server._jwt_decode(tok, SECRET)
    assert out and out['sub'] == 'admin'


def test_expired_token_rejected():
    tok = fleet_server._jwt_encode({'sub': 'admin', 'exp': time.time() - 1}, SECRET)
    assert fleet_server._jwt_decode(tok, SECRET) is None


def test_wrong_secret_rejected():
    tok = fleet_server._jwt_encode({'sub': 'admin', 'exp': time.time() + 60}, SECRET)
    assert fleet_server._jwt_decode(tok, 'other-secret') is None


def test_wrong_segment_count_returns_none():
    assert fleet_server._jwt_decode('only.two', SECRET) is None


def test_malformed_base64_does_not_raise():
    # '@@@' is not valid urlsafe base64 -> binascii.Error (ValueError subclass)
    assert fleet_server._jwt_decode('@@@.@@@.@@@', SECRET) is None


def test_valid_sig_but_non_json_payload_does_not_raise():
    # Payload is valid base64 but decodes to non-JSON bytes; signature is valid
    # so we reach json.loads — it must be caught, not 500.
    h = _b64(b'{"alg":"HS256"}')
    p = _b64(b'\xff\xfe not json at all')
    sig = hmac.new(SECRET.encode(), f"{h}.{p}".encode(), hashlib.sha256).digest()
    token = f"{h}.{p}.{_b64(sig)}"
    assert fleet_server._jwt_decode(token, SECRET) is None

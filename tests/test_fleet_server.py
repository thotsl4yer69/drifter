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
from unittest.mock import MagicMock

import pytest

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


@pytest.mark.parametrize('payload', [[], 'admin', {'sub': 'admin', 'exp': 'tomorrow'},
                                    {'sub': 'admin', 'exp': float('nan')},
                                    {'sub': 'admin', 'exp': float('inf')},
                                    {'exp': 99999999999}])
def test_malformed_claims_are_auth_failures(payload):
    token = fleet_server._jwt_encode(payload, SECRET)
    assert fleet_server._jwt_decode(token, SECRET) is None


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv('FLEET_ADMIN_USERNAME', 'bench-admin')
    monkeypatch.setenv('FLEET_ADMIN_PASSWORD', 'bench-test-only-password')
    return fleet_server.build_app(SECRET, MagicMock())


def test_login_refuses_unconfigured_auth(app, monkeypatch):
    monkeypatch.delenv('FLEET_ADMIN_PASSWORD')
    response = app.test_client().post('/api/auth/login', json={})
    assert response.status_code == 503


@pytest.mark.parametrize('body', [{}, [], {'username': 'any', 'password': 'anything'},
                                 {'username': 'bench-admin', 'password': 123}])
def test_arbitrary_credentials_do_not_issue_a_token(app, body):
    response = app.test_client().post('/api/auth/login', json=body)
    assert response.status_code == 401
    assert 'token' not in response.json


def test_configured_credentials_authorize_protected_api(app):
    client = app.test_client()
    assert client.get('/api/vehicles').status_code == 401
    response = client.post('/api/auth/login', json={
        'username': 'bench-admin', 'password': 'bench-test-only-password'})
    assert response.status_code == 200
    assert client.get('/api/vehicles', headers={'Authorization': 'Bearer ' + response.json['token']}).status_code == 200


def test_fleet_websocket_requires_auth_before_registering(app):
    ws = MagicMock()
    with app.test_request_context('/ws/fleet'):
        app.view_functions['fleet_ws'].__wrapped__(ws)
    ws.close.assert_called_once()
    ws.receive.assert_not_called()
    assert ws not in fleet_server._ws_clients

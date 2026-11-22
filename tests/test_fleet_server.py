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
from pathlib import Path

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


# ── Login fails closed (no "any caller gets a token") ──

import sqlite3 as _sqlite3

import pytest as _pytest


def _users_db():
    conn = _sqlite3.connect(':memory:', check_same_thread=False)
    conn.execute("CREATE TABLE users (username TEXT PRIMARY KEY, "
                 "password_hash TEXT, role TEXT DEFAULT 'viewer')")
    return conn


def test_password_hash_roundtrip():
    h = fleet_server.hash_password('correct horse battery')
    assert h.startswith('scrypt$')
    assert fleet_server.verify_password('correct horse battery', h)
    assert not fleet_server.verify_password('wrong', h)


@_pytest.mark.parametrize('stored', [None, '', 'plain', 'md5$x$y', 'scrypt$1$2'])
def test_verify_rejects_malformed(stored):
    assert not fleet_server.verify_password('anything', stored)


def test_set_and_lookup_user():
    db = _users_db()
    fleet_server.set_user(db, 'admin', 'a-long-password', 'admin')
    pw_hash, role = fleet_server.lookup_user(db, 'admin')
    assert role == 'admin'
    assert fleet_server.verify_password('a-long-password', pw_hash)
    assert fleet_server.lookup_user(db, 'nobody') is None


def _client(db):
    flask = _pytest.importorskip('flask')  # noqa: F841
    _pytest.importorskip('flask_sock')

    class _M:
        def publish(self, *a, **k):
            pass

    app = fleet_server.build_app(SECRET, _M(), db)
    return app.test_client()


def test_login_rejects_unknown_and_wrong_password():
    db = _users_db()
    fleet_server.set_user(db, 'admin', 'a-long-password', 'admin')
    c = _client(db)
    assert c.post('/api/auth/login', json={'username': 'anon', 'password': 'x'}).status_code == 401
    assert c.post('/api/auth/login', json={'username': 'admin', 'password': 'x'}).status_code == 401
    assert c.post('/api/auth/login', json={}).status_code == 401


def test_login_accepts_valid_user_and_token_works():
    db = _users_db()
    fleet_server.set_user(db, 'admin', 'a-long-password', 'admin')
    c = _client(db)
    r = c.post('/api/auth/login', json={'username': 'admin', 'password': 'a-long-password'})
    assert r.status_code == 200
    tok = r.get_json()['token']
    assert c.get('/api/vehicles').status_code == 401
    assert c.get('/api/vehicles', headers={'Authorization': f'Bearer {tok}'}).status_code == 200


def test_login_with_no_users_configured_is_refused():
    c = _client(_users_db())
    assert c.post('/api/auth/login', json={'username': 'admin', 'password': 'x'}).status_code == 401


# ── Bundled dashboard must match the fail-closed server ──

_DASH = (Path(__file__).resolve().parent.parent / 'src' / 'fleet_dashboard.html').read_text()


def test_dashboard_has_no_hardcoded_credentials():
    assert "password: 'admin'" not in _DASH
    assert "username: 'fleet'" not in _DASH


def test_dashboard_websocket_carries_token():
    assert '/ws/fleet?token=${encodeURIComponent(token)}' in _DASH


def test_dashboard_does_not_retry_login_on_401():
    assert 'await login(); return fetchVehicles()' not in _DASH


def test_ws_query_token_authenticates():
    db = _users_db()
    fleet_server.set_user(db, 'admin', 'a-long-password', 'admin')
    c = _client(db)
    tok = c.post('/api/auth/login', json={'username': 'admin', 'password': 'a-long-password'}).get_json()['token']
    assert c.get(f'/api/vehicles?token={tok}').status_code == 200
    assert c.get('/api/vehicles?token=garbage').status_code == 401

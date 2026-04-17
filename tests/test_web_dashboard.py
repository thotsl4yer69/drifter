# tests/test_web_dashboard.py
"""Tests for the HTTP hardening in web_dashboard.py.

We avoid standing up a real HTTP server — instead we construct a partial
DashboardHandler with just enough plumbing to exercise _read_json_body and
the DTC validation regex.
"""
import io
import json
import sys

import pytest

sys.path.insert(0, 'src')

import web_dashboard


class FakeWFile(io.BytesIO):
    def flush(self):
        pass


def _make_handler(body: bytes, content_length: int):
    """Build a DashboardHandler without invoking BaseHTTPRequestHandler.__init__."""
    handler = web_dashboard.DashboardHandler.__new__(
        web_dashboard.DashboardHandler
    )
    handler.headers = {'Content-Length': str(content_length)}
    handler.rfile = io.BytesIO(body)
    handler.wfile = FakeWFile()
    handler._errors = []
    def fake_send_error(code, msg=None):
        handler._errors.append((code, msg))
    handler.send_error = fake_send_error
    return handler


def test_dtc_regex_accepts_valid_codes():
    assert web_dashboard._DTC_RE.match('P0301')
    assert web_dashboard._DTC_RE.match('C1234')
    assert web_dashboard._DTC_RE.match('B0ABC')
    assert web_dashboard._DTC_RE.match('U0100')


def test_dtc_regex_rejects_junk():
    """Regression: the endpoint used to pass user input straight through."""
    assert not web_dashboard._DTC_RE.match('p0301')           # lowercase
    assert not web_dashboard._DTC_RE.match('P030')            # too short
    assert not web_dashboard._DTC_RE.match('P030111')          # too long
    assert not web_dashboard._DTC_RE.match('../../etc/passwd')
    assert not web_dashboard._DTC_RE.match('P030G')           # non-hex
    assert not web_dashboard._DTC_RE.match('X0301')           # bad prefix
    assert not web_dashboard._DTC_RE.match('')


def test_read_json_body_rejects_oversize():
    h = _make_handler(b'{}', web_dashboard.MAX_POST_BODY + 1)
    assert h._read_json_body() is None
    assert h._errors and h._errors[0][0] == 413


def test_read_json_body_rejects_zero_length():
    h = _make_handler(b'', 0)
    assert h._read_json_body() is None
    assert h._errors and h._errors[0][0] == 400


def test_read_json_body_rejects_invalid_json():
    payload = b'not-json-at-all'
    h = _make_handler(payload, len(payload))
    assert h._read_json_body() is None
    assert h._errors and h._errors[0][0] == 400


def test_read_json_body_rejects_non_object():
    payload = b'["list", "not", "object"]'
    h = _make_handler(payload, len(payload))
    assert h._read_json_body() is None
    assert h._errors and h._errors[0][0] == 400


def test_read_json_body_accepts_valid_object():
    payload = b'{"query": "why is it hot"}'
    h = _make_handler(payload, len(payload))
    body = h._read_json_body()
    assert body == {'query': 'why is it hot'}
    assert h._errors == []


def test_read_json_body_rejects_non_integer_content_length():
    h = _make_handler(b'{}', 0)
    h.headers = {'Content-Length': 'banana'}
    assert h._read_json_body() is None
    assert h._errors and h._errors[0][0] == 400

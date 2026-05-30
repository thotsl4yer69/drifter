# tests/test_status.py
"""Regression tests for src/status.py."""
import sys
sys.path.insert(0, 'src')

import status


class FakeMsg:
    def __init__(self, topic, payload):
        self.topic = topic
        self.payload = payload


def setup_function():
    status.collected.clear()


def test_on_message_accepts_dict_payload():
    msg = FakeMsg('drifter/engine/rpm', b'{"value": 1200}')
    status.on_message(None, None, msg)
    assert status.collected['drifter/engine/rpm'] == {'value': 1200}


def test_on_message_ignores_non_dict_json():
    """Regression: downstream .get() calls would crash on bare JSON primitives."""
    for payload in (b'42', b'"a string"', b'[1,2,3]', b'null', b'true'):
        status.collected.clear()
        status.on_message(None, None, FakeMsg('drifter/weird', payload))
        assert status.collected == {}


def test_on_message_ignores_invalid_json():
    status.on_message(None, None, FakeMsg('drifter/x', b'not-json'))
    assert status.collected == {}

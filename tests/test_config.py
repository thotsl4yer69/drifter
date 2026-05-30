# tests/test_config.py
"""Regression tests for config.make_mqtt_client and related helpers."""
import sys
sys.path.insert(0, 'src')

import paho.mqtt.client as _mqtt
import config


def test_make_mqtt_client_returns_client():
    client = config.make_mqtt_client("test-client")
    assert isinstance(client, _mqtt.Client)


def test_make_mqtt_client_uses_v2_callback_api():
    """All drifter services must use the paho v2 API to silence the
    deprecation warning and match the new callback signatures."""
    if not hasattr(_mqtt, 'CallbackAPIVersion'):
        # paho < 2.0: nothing to verify.
        return
    client = config.make_mqtt_client("test-client")
    # paho exposes the selected API version on _callback_api_version.
    assert getattr(client, '_callback_api_version', None) == \
        _mqtt.CallbackAPIVersion.VERSION2


def test_make_mqtt_client_accepts_kwargs():
    client = config.make_mqtt_client("test-client", clean_session=True)
    assert isinstance(client, _mqtt.Client)

"""Every MQTT client must survive an exception in its own callbacks.

paho re-raises callback exceptions by default; under loop_start() that kills
the network thread while the systemd unit stays active (a deaf service)."""
import ast
import logging
import sys
from pathlib import Path

import paho.mqtt.client as mqtt

SRC = Path(__file__).resolve().parent.parent / 'src'
sys.path.insert(0, str(SRC))
import config  # noqa: E402

# one-shot reporter that never runs a network loop
_EXEMPT = {'config.py', 'boot_reason.py'}


def test_make_mqtt_client_suppresses_callback_exceptions():
    c = config.make_mqtt_client('drifter-test')
    assert c.suppress_exceptions is True


def test_suppressed_on_message_exception_is_logged_not_raised(caplog):
    c = config.make_mqtt_client('drifter-test')

    def bad(client, userdata, msg):
        raise AttributeError("'int' object has no attribute 'get'")

    c.on_message = bad
    msg = mqtt.MQTTMessage(topic=b'drifter/engine/rpm')
    msg.payload = b'42'
    with caplog.at_level(logging.ERROR):
        c._handle_on_message(msg)  # must not raise
    assert any('Caught exception in on_message' in r.getMessage() for r in caplog.records)


def test_every_raw_client_construction_is_hardened():
    missing = []
    for p in sorted(SRC.glob('*.py')):
        if p.name in _EXEMPT:
            continue
        tree = ast.parse(p.read_text())
        hardened = {
            ast.unparse(n.targets[0].value)
            for n in ast.walk(tree)
            if isinstance(n, ast.Assign) and len(n.targets) == 1
            and isinstance(n.targets[0], ast.Attribute)
            and n.targets[0].attr == 'suppress_exceptions'
        }
        for n in ast.walk(tree):
            if (isinstance(n, ast.Assign) and isinstance(n.value, ast.Call)
                    and isinstance(n.value.func, ast.Attribute) and n.value.func.attr == 'Client'
                    and isinstance(n.value.func.value, ast.Name)
                    and n.value.func.value.id in ('mqtt', '_mqtt')):
                name = ast.unparse(n.targets[0])
                if name not in hardened:
                    missing.append(f'{p.name}:{n.lineno} {name}')
    assert not missing, f'mqtt.Client without suppress_exceptions: {missing}'

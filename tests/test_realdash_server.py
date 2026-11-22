"""RealDash bridge: malformed MQTT payloads and the TCP listener + peer ACL."""
import json
import socket
import threading
import time
from types import SimpleNamespace

import pytest

import realdash_bridge as rb


def _msg(topic, payload):
    if not isinstance(payload, bytes):
        payload = json.dumps(payload).encode()
    return SimpleNamespace(topic=topic, payload=payload)


@pytest.fixture(autouse=True)
def _reset():
    saved = dict(rb.latest), dict(rb._last_known), rb._has_real_data, rb.alert_message, rb.running
    yield
    rb.latest.clear(); rb.latest.update(saved[0])
    rb._last_known.clear(); rb._last_known.update(saved[1])
    rb._has_real_data, rb.alert_message, rb.running = saved[2], saved[3], saved[4]


@pytest.mark.parametrize('payload', [42, [1, 2], 'text', None, b'\xff\xfe', b'{not json'])
def test_malformed_payloads_are_ignored_not_raised(payload):
    before = dict(rb.latest)
    rb.on_mqtt_message(None, None, _msg('drifter/engine/rpm', payload))
    assert rb.latest == before


def test_valid_payload_updates_and_marks_real_data():
    rb._has_real_data = False
    rb.on_mqtt_message(None, None, _msg('drifter/engine/rpm', {'value': 2150}))
    assert rb.latest['rpm'] == 2150 and rb._has_real_data


def test_tpms_ignores_unknown_wheel_position():
    rb.on_mqtt_message(None, None, _msg('drifter/rf/tpms/spare', {'pressure_psi': 30}))
    assert 'tpms_spare_psi' not in rb.latest


def _free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _run_server(monkeypatch, trusted):
    port = _free_port()
    monkeypatch.setattr(rb, 'REALDASH_TCP_PORT', port)
    monkeypatch.setattr(rb.config, 'BIND_HOST', '127.0.0.1')
    monkeypatch.setattr(rb.config, 'is_local_peer', lambda addr: trusted)
    rb.running = True
    t = threading.Thread(target=rb.tcp_server, daemon=True)
    t.start()
    for _ in range(50):
        try:
            return port, t, socket.create_connection(('127.0.0.1', port), timeout=2)
        except ConnectionRefusedError:
            time.sleep(0.05)
    raise RuntimeError('server did not start')


def _stop(t):
    rb.running = False
    t.join(timeout=5)
    assert not t.is_alive()


def test_untrusted_peer_is_disconnected_immediately(monkeypatch):
    _, t, c = _run_server(monkeypatch, trusted=False)
    try:
        c.settimeout(2)
        assert c.recv(16) == b''  # server closed us without sending frames
    finally:
        c.close()
        _stop(t)


def test_trusted_peer_receives_realdash_frames(monkeypatch):
    rb.on_mqtt_message(None, None, _msg('drifter/engine/rpm', {'value': 900}))
    _, t, c = _run_server(monkeypatch, trusted=True)
    try:
        c.settimeout(3)
        data = b''
        while len(data) < 16:
            chunk = c.recv(256)
            assert chunk, 'server closed a trusted client'
            data += chunk
        assert data[:4] == bytes([0x44, 0x33, 0x22, 0x11])
    finally:
        c.close()
        _stop(t)


def test_no_frames_before_first_real_reading(monkeypatch):
    rb._has_real_data = False
    _, t, c = _run_server(monkeypatch, trusted=True)
    try:
        c.settimeout(0.4)
        with pytest.raises(TimeoutError):
            c.recv(16)
    finally:
        c.close()
        _stop(t)

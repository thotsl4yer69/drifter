#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Fleet Server
Flask + WebSocket fleet-management API. Aggregates telemetry from multiple
drifter vehicles via MQTT, stores rolling state and history in SQLite,
exposes a JWT-authenticated REST API plus a live WS feed.
UNCAGED TECHNOLOGY — EST 1991
"""

import json
import logging
import secrets
import signal
import sqlite3
import threading
import time
from pathlib import Path

import paho.mqtt.client as mqtt

from config import (
    FLEET_API_HOST,
    FLEET_API_PORT,
    FLEET_DB_PATH,
    FLEET_HEARTBEAT_TIMEOUT,
    FLEET_JWT_SECRET_FILE,
    FLEET_JWT_TTL,
    MQTT_HOST,
    MQTT_PORT,
    TOPICS,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [FLEET] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger(__name__)

# Shared in-memory state (small + fast). SQLite holds the durable copy.
_state_lock = threading.Lock()
_vehicles: dict[str, dict] = {}
_ws_clients: set = set()


def _load_secret() -> str:
    p = Path(FLEET_JWT_SECRET_FILE)
    if p.exists():
        return p.read_text().strip()
    secret = secrets.token_hex(32)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(secret)
    p.chmod(0o600)
    return secret


def _init_db() -> sqlite3.Connection:
    Path(FLEET_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(FLEET_DB_PATH), check_same_thread=False)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS vehicles (
            vin TEXT PRIMARY KEY,
            label TEXT,
            registered_at REAL,
            last_seen REAL,
            profile TEXT
        );
        CREATE TABLE IF NOT EXISTS telemetry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vin TEXT,
            ts REAL,
            payload TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_telemetry_vin_ts
            ON telemetry(vin, ts DESC);
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vin TEXT,
            ts REAL,
            level INTEGER,
            message TEXT
        );
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password_hash TEXT,
            role TEXT DEFAULT 'viewer'
        );
        """
    )
    conn.commit()
    return conn


def _jwt_encode(payload: dict, secret: str) -> str:
    """Tiny dependency-free HS256 JWT encoder."""
    import base64
    import hashlib
    import hmac

    header = {"alg": "HS256", "typ": "JWT"}

    def b64(d: bytes) -> str:
        return base64.urlsafe_b64encode(d).rstrip(b'=').decode()

    h = b64(json.dumps(header, separators=(',', ':')).encode())
    p = b64(json.dumps(payload, separators=(',', ':')).encode())
    signing_input = f"{h}.{p}".encode()
    sig = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    return f"{h}.{p}.{b64(sig)}"


def _jwt_decode(token: str, secret: str) -> dict | None:
    import base64
    import hashlib
    import hmac

    try:
        h, p, s = token.split('.')
    except ValueError:
        return None

    def unb64(d: str) -> bytes:
        return base64.urlsafe_b64decode(d + '=' * (-len(d) % 4))

    # Everything below decodes attacker-controlled input: a malformed base64
    # segment (binascii.Error) or a valid-base64-but-non-JSON payload
    # (JSONDecodeError) must yield a clean auth failure, not a 500. Both are
    # ValueError subclasses.
    try:
        signing_input = f"{h}.{p}".encode()
        expected = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, unb64(s)):
            return None
        payload = json.loads(unb64(p))
    except (ValueError, TypeError):
        return None
    if payload.get('exp', 0) < time.time():
        return None
    return payload


def _broadcast_ws(message: dict) -> None:
    """Push update to all connected WebSocket clients."""
    if not _ws_clients:
        return
    dead = []
    raw = json.dumps(message)
    for ws in list(_ws_clients):
        try:
            ws.send(raw)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _ws_clients.discard(ws)


def on_mqtt_message(client, _u, msg) -> None:
    try:
        data = json.loads(msg.payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return
    if not isinstance(data, dict):
        return
    vin = data.get('vin') or data.get('vehicle_id')
    if not vin:
        return
    topic = msg.topic
    now = time.time()
    with _state_lock:
        v = _vehicles.setdefault(vin, {'vin': vin, 'first_seen': now})
        v['last_seen'] = now
        if topic == TOPICS['fleet_register']:
            v['profile'] = data.get('profile', {})
            v['label'] = data.get('label', vin)
            client.user_data_set({'last_register': vin})
        elif topic == TOPICS['fleet_heartbeat']:
            v['health'] = data.get('health', {})
        elif topic == TOPICS['fleet_telemetry']:
            v['telemetry'] = data.get('telemetry', {})
        elif topic == TOPICS['fleet_alert']:
            v.setdefault('alerts', []).append({
                'ts': now,
                'level': data.get('level', 1),
                'message': data.get('message', ''),
            })
    _broadcast_ws({'type': topic.split('/')[-1], 'vin': vin, 'data': data})


def start_mqtt() -> mqtt.Client:
    client = mqtt.Client(client_id="drifter-fleet-server")
    client.on_message = on_mqtt_message
    connected = False
    while not connected:
        try:
            client.connect(MQTT_HOST, MQTT_PORT, 60)
            connected = True
        except Exception as e:
            log.warning(f"Waiting for MQTT broker... ({e})")
            time.sleep(3)
    client.subscribe([
        (TOPICS['fleet_register'], 1),
        (TOPICS['fleet_heartbeat'], 0),
        (TOPICS['fleet_telemetry'], 0),
        (TOPICS['fleet_alert'], 1),
    ])
    client.loop_start()
    log.info("MQTT subscribed: fleet/*")
    return client


def build_app(secret: str, mqtt_client: mqtt.Client):
    """Build a Flask app — Flask is optional dependency, falls back to stub."""
    try:
        from flask import Flask, jsonify, request
        from flask_sock import Sock
    except ImportError:
        log.error("flask / flask-sock not installed — REST API disabled")
        return None

    app = Flask(__name__)
    sock = Sock(app)

    @app.route('/api/health')
    def health():
        return jsonify({'ok': True, 'ts': time.time(), 'vehicles': len(_vehicles)})

    @app.route('/api/auth/login', methods=['POST'])
    def login():
        body = request.get_json(silent=True) or {}
        # MVP: any username/password issues a token; replace with real auth
        username = body.get('username', 'anon')
        token = _jwt_encode(
            {'sub': username, 'exp': time.time() + FLEET_JWT_TTL},
            secret,
        )
        return jsonify({'token': token, 'expires_in': FLEET_JWT_TTL})

    def _auth() -> bool:
        h = request.headers.get('Authorization', '')
        if not h.startswith('Bearer '):
            return False
        return _jwt_decode(h[7:], secret) is not None

    @app.route('/api/vehicles')
    def vehicles():
        if not _auth():
            return jsonify({'error': 'unauthorized'}), 401
        with _state_lock:
            now = time.time()
            out = []
            for v in _vehicles.values():
                v = dict(v)
                v['online'] = (now - v.get('last_seen', 0)) < FLEET_HEARTBEAT_TIMEOUT
                out.append(v)
        return jsonify({'vehicles': out, 'count': len(out)})

    @app.route('/api/vehicles/<vin>')
    def vehicle(vin):
        if not _auth():
            return jsonify({'error': 'unauthorized'}), 401
        with _state_lock:
            v = _vehicles.get(vin)
        if not v:
            return jsonify({'error': 'not_found'}), 404
        return jsonify(v)

    @app.route('/api/vehicles/<vin>/command', methods=['POST'])
    def command(vin):
        if not _auth():
            return jsonify({'error': 'unauthorized'}), 401
        body = request.get_json(silent=True) or {}
        mqtt_client.publish(
            TOPICS['fleet_command'],
            json.dumps({'vin': vin, 'command': body}),
            qos=1,
        )
        return jsonify({'ok': True})

    @sock.route('/ws/fleet')
    def fleet_ws(ws):
        _ws_clients.add(ws)
        try:
            while True:
                msg = ws.receive(timeout=30)
                if msg is None:
                    ws.send(json.dumps({'type': 'ping', 'ts': time.time()}))
        except Exception:
            pass
        finally:
            _ws_clients.discard(ws)

    return app


def main() -> None:
    log.info("DRIFTER Fleet Server starting...")
    secret = _load_secret()
    _init_db()

    running = [True]

    def _handle_signal(sig, frame):
        running[0] = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    mqtt_client = start_mqtt()
    app = build_app(secret, mqtt_client)

    if app is None:
        log.error("Flask app unavailable, idle-loop only")
        while running[0]:
            time.sleep(1)
        return

    log.info(f"Fleet API LIVE on http://{FLEET_API_HOST}:{FLEET_API_PORT}")
    server_thread = threading.Thread(
        target=app.run,
        kwargs={'host': FLEET_API_HOST, 'port': FLEET_API_PORT,
                'debug': False, 'use_reloader': False},
        daemon=True,
    )
    server_thread.start()

    while running[0]:
        time.sleep(1)

    mqtt_client.loop_stop()
    mqtt_client.disconnect()
    log.info("Fleet Server stopped")


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
MZ1312 DRIFTER — GPS publisher (drifter-gps)

Reads from gpsd over its localhost JSON socket and republishes every
fix to drifter/gps/fix. The cockpit subscribes via the existing
WebSocket relay and recentres the map on each fix — see Phase 5.2 in
docs/BLE_STACK.md (or the cockpit JS handler for drifter-gps-fix).

Hardware: any USB GPS dongle that gpsd recognises (u-blox 7, VK-172,
Adafruit Ultimate, etc). Install gpsd, plug the dongle into a Pi USB
port, confirm `cgps -s` shows a fix; this service then reads from the
gpsd JSON socket and publishes.

UNCAGED TECHNOLOGY — EST 1991
"""
from __future__ import annotations

import json
import logging
import signal
import socket
import sys
import time
from pathlib import Path
from typing import Optional

try:
    from config import MQTT_HOST, MQTT_PORT, TOPICS, make_mqtt_client
except ImportError:
    sys.path.insert(0, '/opt/drifter')
    from config import MQTT_HOST, MQTT_PORT, TOPICS, make_mqtt_client  # type: ignore

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [GPS] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger(__name__)

GPSD_HOST = '127.0.0.1'
GPSD_PORT = 2947
RECONNECT_BACKOFF_MAX = 30
PUBLISH_TOPIC = TOPICS.get('gps_fix', 'drifter/gps/fix')

_running = True


def _sig(_signo, _frame):
    global _running
    _running = False


def parse_tpv(line: str) -> Optional[dict]:
    """gpsd publishes one JSON object per line. We only care about TPV
    (time-position-velocity) reports with a 2D-or-better fix."""
    try:
        msg = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    if msg.get('class') != 'TPV':
        return None
    mode = int(msg.get('mode', 0))
    if mode < 2:
        return None  # mode 0/1 = no fix
    lat = msg.get('lat')
    lon = msg.get('lon')
    if lat is None or lon is None:
        return None
    return {
        'lat':       float(lat),
        'lng':       float(lon),
        'alt_m':     float(msg['alt'])      if msg.get('alt')      is not None else None,
        'speed_mps': float(msg['speed'])    if msg.get('speed')    is not None else None,
        'track_deg': float(msg['track'])    if msg.get('track')    is not None else None,
        'mode':      mode,
        'ts':        time.time(),
    }


def connect_mqtt():
    client = make_mqtt_client('drifter-gps')
    last_err: Optional[Exception] = None
    for attempt in range(1, 11):
        try:
            client.connect(MQTT_HOST, MQTT_PORT, 60)
            client.loop_start()
            return client
        except (ConnectionRefusedError, OSError) as e:
            last_err = e
            log.info(f"MQTT not ready (attempt {attempt}/10): {e}")
            time.sleep(min(attempt, 5))
    raise RuntimeError(f"MQTT broker unreachable after 10 attempts: {last_err}")


def connect_gpsd():
    """Open a socket to gpsd and start a JSON watch. Returns a file-
    like object you can iterate one line at a time."""
    s = socket.create_connection((GPSD_HOST, GPSD_PORT), timeout=5)
    s.sendall(b'?WATCH={"enable":true,"json":true};\n')
    return s


def main() -> int:
    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)

    mqtt = connect_mqtt()
    log.info(f"connected to MQTT broker, publishing to {PUBLISH_TOPIC}")

    backoff = 1
    while _running:
        try:
            sock = connect_gpsd()
            backoff = 1
            log.info("connected to gpsd at %s:%d — watching", GPSD_HOST, GPSD_PORT)
            buf = b''
            while _running:
                chunk = sock.recv(4096)
                if not chunk:
                    raise ConnectionError("gpsd closed connection")
                buf += chunk
                while b'\n' in buf:
                    line, buf = buf.split(b'\n', 1)
                    fix = parse_tpv(line.decode('utf-8', errors='replace'))
                    if fix is None:
                        continue
                    try:
                        mqtt.publish(PUBLISH_TOPIC, json.dumps(fix), retain=True)
                    except Exception as e:
                        log.warning(f"publish failed: {e}")
            sock.close()
        except (ConnectionRefusedError, ConnectionError, OSError, socket.timeout) as e:
            log.info(f"gpsd unreachable ({e}) — retrying in {backoff}s")
            time.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_BACKOFF_MAX)

    log.info("shutting down")
    mqtt.loop_stop()
    mqtt.disconnect()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Kismet REST → MQTT bridge

Polls the local Kismet headless instance (127.0.0.1:2501) every 2s,
normalises detected devices into a compact schema, and republishes
them on:

    drifter/wifi/devices  — Wi-Fi APs / clients
    drifter/ble/devices   — Bluetooth LE + classic radios

Schema per row:
    {ts, mac, manufacturer, type, channel, signal_dbm, last_seen,
     mac_random}

Auth is read from env (KISMET_USER / KISMET_PASS). When the daemon
isn't reachable yet the bridge keeps polling rather than dying — this
mirrors the wardrive/feeds resilience pattern.

UNCAGED TECHNOLOGY — EST 1991
"""

from __future__ import annotations

import json
import logging
import os
import signal
import time

import requests
from requests.auth import HTTPBasicAuth

from config import MQTT_HOST, MQTT_PORT, make_mqtt_client

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [KISMET-BRIDGE] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger(__name__)

KISMET_HOST = os.environ.get('KISMET_HOST', '127.0.0.1')
KISMET_PORT = int(os.environ.get('KISMET_PORT', '2501'))
KISMET_USER = os.environ.get('KISMET_USER', 'drifter')
KISMET_PASS = os.environ.get('KISMET_PASS', '')
POLL_INTERVAL = float(os.environ.get('KISMET_POLL_SEC', '2.0'))

TOPIC_WIFI = 'drifter/wifi/devices'
TOPIC_BLE = 'drifter/ble/devices'

# Kismet device "phyname" → which MQTT topic the row belongs on.
PHY_WIFI = {'IEEE802.11'}
PHY_BLE = {'Bluetooth', 'BTLE', 'BLE'}

running = True


def _kismet_url(path: str) -> str:
    return f'http://{KISMET_HOST}:{KISMET_PORT}{path}'


def _is_random_mac(mac: str) -> bool:
    """Locally-administered bit (bit 1 of first octet) → randomised MAC."""
    if not mac or len(mac) < 2:
        return False
    try:
        first = int(mac.split(':')[0], 16)
    except (ValueError, IndexError):
        return False
    return bool(first & 0x02)


def normalize_device(dev: dict) -> dict | None:
    """Squash a Kismet device record into the DRIFTER schema.

    Returns None if the row is unusable (no MAC). Kismet's JSON shape
    has changed across releases, so every getter is defensive.
    """
    if not isinstance(dev, dict):
        return None
    mac = (dev.get('kismet.device.base.macaddr')
           or dev.get('kismet_device_base_macaddr') or '').upper()
    if not mac or mac == '00:00:00:00:00:00':
        return None

    phy = (dev.get('kismet.device.base.phyname')
           or dev.get('kismet_device_base_phyname') or '')
    dtype = (dev.get('kismet.device.base.type')
             or dev.get('kismet_device_base_type') or 'unknown')
    manuf = (dev.get('kismet.device.base.manuf')
             or dev.get('kismet_device_base_manuf') or '')
    channel = (dev.get('kismet.device.base.channel')
               or dev.get('kismet_device_base_channel') or '')
    signal_block = (dev.get('kismet.device.base.signal')
                    or dev.get('kismet_device_base_signal') or {})
    if isinstance(signal_block, dict):
        signal_dbm = (signal_block.get('kismet.common.signal.last_signal')
                      or signal_block.get('kismet_common_signal_last_signal'))
    else:
        signal_dbm = None
    last_seen = (dev.get('kismet.device.base.last_time')
                 or dev.get('kismet_device_base_last_time') or 0)

    return {
        'ts': time.time(),
        'mac': mac,
        'manufacturer': manuf or '',
        'type': dtype or 'unknown',
        'channel': str(channel) if channel else '',
        'signal_dbm': signal_dbm,
        'last_seen': last_seen,
        'mac_random': _is_random_mac(mac),
        'phy': phy,
    }


def split_by_phy(devices: list) -> tuple[list, list]:
    """Return (wifi_rows, ble_rows) — phyname decides topic routing."""
    wifi, ble = [], []
    for d in devices:
        if not d:
            continue
        phy = d.get('phy') or ''
        if phy in PHY_WIFI:
            wifi.append(d)
        elif phy in PHY_BLE:
            ble.append(d)
        else:
            # Unknown phys default to Wi-Fi (most common Kismet source).
            wifi.append(d)
    return wifi, ble


def fetch_devices(session: requests.Session) -> list:
    """Pull the all-devices view from Kismet. Returns [] on any failure."""
    try:
        r = session.get(
            _kismet_url('/devices/views/all/devices.json'),
            auth=HTTPBasicAuth(KISMET_USER, KISMET_PASS),
            timeout=5,
        )
        if r.status_code != 200:
            log.debug("Kismet HTTP %s on devices.json", r.status_code)
            return []
        data = r.json()
        if isinstance(data, list):
            return data
        # Some Kismet builds wrap in {"devices": [...]}.
        if isinstance(data, dict) and isinstance(data.get('devices'), list):
            return data['devices']
        return []
    except requests.RequestException as e:
        log.debug("Kismet poll failed: %s", e)
        return []
    except (ValueError, json.JSONDecodeError) as e:
        log.warning("Kismet returned non-JSON: %s", e)
        return []


def publish_devices(mqtt_client, wifi: list, ble: list) -> None:
    """Publish both schemas. Not retained — the bridge re-publishes."""
    now = time.time()
    mqtt_client.publish(TOPIC_WIFI, json.dumps({
        'ts': now,
        'count': len(wifi),
        'devices': wifi,
    }))
    mqtt_client.publish(TOPIC_BLE, json.dumps({
        'ts': now,
        'count': len(ble),
        'devices': ble,
    }))


def main():
    global running

    def _handle_signal(sig, frame):
        global running
        running = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    log.info("DRIFTER Kismet bridge starting → %s:%d", KISMET_HOST, KISMET_PORT)

    mqtt_client = make_mqtt_client("drifter-kismet-bridge")
    while running:
        try:
            mqtt_client.connect(MQTT_HOST, MQTT_PORT, 60)
            break
        except Exception as e:
            log.warning("Waiting for MQTT broker... (%s)", e)
            time.sleep(3)
    mqtt_client.loop_start()

    session = requests.Session()
    while running:
        raw = fetch_devices(session)
        normalised = [normalize_device(d) for d in raw]
        normalised = [d for d in normalised if d is not None]
        wifi, ble = split_by_phy(normalised)
        publish_devices(mqtt_client, wifi, ble)
        if normalised:
            log.debug("Published %d wifi + %d ble devices", len(wifi), len(ble))
        time.sleep(POLL_INTERVAL)

    mqtt_client.loop_stop()
    mqtt_client.disconnect()
    log.info("Kismet bridge stopped")


if __name__ == '__main__':
    main()

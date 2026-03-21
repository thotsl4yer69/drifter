#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Wardrive Monitor
Passive Wi-Fi and Bluetooth scanning using Kali Linux tools.
Logs all detected networks and devices per drive session.
No active connections, no packet injection — passive listen only.
UNCAGED TECHNOLOGY — EST 1991
"""

import json
import re
import time
import signal
import logging
import subprocess
import threading
from pathlib import Path
from collections import OrderedDict

import paho.mqtt.client as mqtt

from config import (
    MQTT_HOST, MQTT_PORT, TOPICS,
    WARDRIVE_LOG_DIR, WIFI_SCAN_INTERVAL,
    BT_SCAN_INTERVAL, BT_SCAN_DURATION,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [WARDRIVE] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

running = True

# ── Session state ──
session_networks = OrderedDict()    # bssid → network dict
session_bt_devices = OrderedDict()  # addr → device dict
session_start = time.time()
latest_wifi_scan = []
latest_bt_scan = []

# MAC address pattern
MAC_RE = re.compile(
    r'([0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}'
    r':[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2})'
)


# ═══════════════════════════════════════════════════════════════════
#  Wi-Fi Scanning
# ═══════════════════════════════════════════════════════════════════

def parse_nmcli_wifi(output: str) -> list:
    """Parse nmcli terse multiline output into a list of network dicts."""
    networks = []
    current = {}
    for raw_line in output.strip().splitlines() + ['']:
        line = raw_line.strip()
        if not line:
            if current.get('bssid'):
                networks.append({**current, 'ts': time.time()})
            current = {}
            continue
        if ':' not in line:
            continue
        colon = line.index(':')
        key = line[:colon].lstrip('*').strip().lower()
        value = line[colon + 1:].replace('\\:', ':').strip()
        if key == 'ssid':
            current['ssid'] = value or '<hidden>'
        elif key == 'bssid':
            current['bssid'] = value.upper()
        elif key == 'signal':
            try:
                pct = int(value)
                current['signal_pct'] = pct
                current['signal_dbm'] = round((pct / 2) - 100)
            except ValueError:
                pass
        elif key == 'chan':
            current['channel'] = value
        elif key == 'security':
            current['security'] = value
    return networks


def scan_wifi() -> list:
    """Run a passive Wi-Fi scan via nmcli. Returns list of network dicts."""
    try:
        result = subprocess.run(
            ['nmcli', '-t', '-m', 'multiline', '-f',
             'SSID,BSSID,SIGNAL,CHAN,SECURITY',
             'dev', 'wifi', 'list', '--rescan', 'yes'],
            capture_output=True, text=True, timeout=25
        )
        return parse_nmcli_wifi(result.stdout)
    except FileNotFoundError:
        log.warning("nmcli not found — Wi-Fi scanning unavailable")
    except subprocess.TimeoutExpired:
        log.warning("Wi-Fi scan timed out")
    except Exception as e:
        log.warning(f"Wi-Fi scan error: {e}")
    return []


# ═══════════════════════════════════════════════════════════════════
#  Bluetooth Scanning
# ═══════════════════════════════════════════════════════════════════

def parse_hcitool_classic(output: str) -> list:
    """Parse hcitool scan output into device dicts."""
    devices = []
    for line in output.splitlines():
        m = MAC_RE.search(line)
        if m:
            addr = m.group(1).upper()
            name = line[m.end():].strip() or '(unknown)'
            devices.append({'addr': addr, 'name': name, 'type': 'classic',
                            'ts': time.time()})
    return devices


def parse_hcitool_le(output: str) -> list:
    """Parse hcitool lescan output into device dicts (deduplicated)."""
    seen = {}
    for line in output.splitlines():
        m = MAC_RE.search(line)
        if m:
            addr = m.group(1).upper()
            name = line[m.end():].strip().strip('()') or 'unknown'
            if addr not in seen:
                seen[addr] = {'addr': addr, 'name': name, 'type': 'ble',
                              'ts': time.time()}
    return list(seen.values())


def scan_bluetooth() -> list:
    """Scan for classic BT and BLE devices. Returns combined list."""
    devices = []

    # Classic Bluetooth (inquiry, ~8 seconds)
    try:
        r = subprocess.run(
            ['hcitool', 'scan', '--length', '4'],
            capture_output=True, text=True, timeout=20
        )
        devices.extend(parse_hcitool_classic(r.stdout))
    except FileNotFoundError:
        log.debug("hcitool not available — Bluetooth scanning disabled")
        return []
    except Exception as e:
        log.debug(f"BT classic scan error: {e}")

    # BLE scan (passive, time-bounded)
    try:
        r = subprocess.run(
            ['timeout', str(BT_SCAN_DURATION), 'hcitool', 'lescan', '--duplicates'],
            capture_output=True, text=True, timeout=BT_SCAN_DURATION + 5
        )
        devices.extend(parse_hcitool_le(r.stdout))
    except Exception as e:
        log.debug(f"BLE scan error: {e}")

    return devices


# ═══════════════════════════════════════════════════════════════════
#  Session Logging
# ═══════════════════════════════════════════════════════════════════

def update_session(wifi_networks: list, bt_devices: list):
    """Merge scan results into session state."""
    global latest_wifi_scan, latest_bt_scan
    if wifi_networks:
        latest_wifi_scan = wifi_networks
    if bt_devices:
        latest_bt_scan = bt_devices
    now = time.time()
    for net in wifi_networks:
        bssid = net.get('bssid', '')
        if bssid:
            session_networks[bssid] = {**net, 'last_seen': now}
    for dev in bt_devices:
        addr = dev.get('addr', '')
        if addr:
            session_bt_devices[addr] = {**dev, 'last_seen': now}


def save_session_log():
    """Persist session summary to disk on shutdown."""
    try:
        WARDRIVE_LOG_DIR.mkdir(parents=True, exist_ok=True)
        sid = time.strftime('%Y%m%d_%H%M%S', time.localtime(session_start))
        path = WARDRIVE_LOG_DIR / f'wardrive_{sid}.json'
        data = {
            'session_start': session_start,
            'session_end': time.time(),
            'duration_s': round(time.time() - session_start),
            'unique_ssids': len(session_networks),
            'unique_bt': len(session_bt_devices),
            'wifi': list(session_networks.values()),
            'bluetooth': list(session_bt_devices.values()),
        }
        path.write_text(json.dumps(data, indent=2))
        log.info(f"Session log saved: {path.name} "
                 f"({data['unique_ssids']} SSIDs, {data['unique_bt']} BT devices)")
    except Exception as e:
        log.warning(f"Failed to save session log: {e}")


# ═══════════════════════════════════════════════════════════════════
#  MQTT Publishers
# ═══════════════════════════════════════════════════════════════════

def do_wifi_scan(mqtt_client):
    """Scan Wi-Fi and publish results."""
    networks = scan_wifi()
    update_session(networks, [])
    mqtt_client.publish(TOPICS['wardrive_wifi'], json.dumps({
        'scan': networks,
        'session_total': len(session_networks),
        'ts': time.time(),
    }), retain=True)
    if networks:
        log.info(f"Wi-Fi: {len(networks)} visible, "
                 f"{len(session_networks)} unique this session")


def do_bt_scan(mqtt_client):
    """Scan Bluetooth and publish results."""
    devices = scan_bluetooth()
    update_session([], devices)
    classic = [d for d in devices if d['type'] == 'classic']
    ble = [d for d in devices if d['type'] == 'ble']
    mqtt_client.publish(TOPICS['wardrive_bt'], json.dumps({
        'devices': devices,
        'classic_count': len(classic),
        'ble_count': len(ble),
        'session_total': len(session_bt_devices),
        'ts': time.time(),
    }), retain=True)
    if devices:
        log.info(f"Bluetooth: {len(classic)} classic + {len(ble)} BLE, "
                 f"{len(session_bt_devices)} unique this session")


def publish_snapshot(mqtt_client):
    """Publish session summary."""
    mqtt_client.publish(TOPICS['wardrive_snapshot'], json.dumps({
        'session_start': session_start,
        'duration_s': round(time.time() - session_start),
        'unique_ssids': len(session_networks),
        'unique_bt': len(session_bt_devices),
        'top_networks': sorted(
            session_networks.values(),
            key=lambda n: n.get('signal_dbm', -999),
            reverse=True,
        )[:10],
        'ts': time.time(),
    }), retain=True)


# ═══════════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════════

def main():
    global running

    log.info("DRIFTER Wardrive Monitor starting...")

    def _handle_signal(sig, frame):
        global running
        running = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    mqtt_client = mqtt.Client(client_id="drifter-wardrive")

    connected = False
    while not connected and running:
        try:
            mqtt_client.connect(MQTT_HOST, MQTT_PORT, 60)
            connected = True
        except Exception as e:
            log.warning(f"Waiting for MQTT broker... ({e})")
            time.sleep(3)

    mqtt_client.loop_start()

    mqtt_client.publish(TOPICS['wardrive_status'], json.dumps({
        'state': 'online',
        'ts': time.time(),
    }), retain=True)

    log.info("Wardrive Monitor is LIVE")
    log.info(f"Wi-Fi scan every {WIFI_SCAN_INTERVAL}s | "
             f"Bluetooth scan every {BT_SCAN_INTERVAL}s")

    last_wifi = 0
    last_bt = 0
    last_snapshot = 0

    while running:
        now = time.time()

        if now - last_wifi >= WIFI_SCAN_INTERVAL:
            threading.Thread(
                target=do_wifi_scan, args=(mqtt_client,), daemon=True
            ).start()
            last_wifi = now

        if now - last_bt >= BT_SCAN_INTERVAL:
            threading.Thread(
                target=do_bt_scan, args=(mqtt_client,), daemon=True
            ).start()
            last_bt = now

        if now - last_snapshot >= 60:
            publish_snapshot(mqtt_client)
            last_snapshot = now

        time.sleep(1)

    # Persist session on shutdown
    save_session_log()
    mqtt_client.publish(TOPICS['wardrive_status'], json.dumps({
        'state': 'offline',
        'ts': time.time(),
    }), retain=True)
    mqtt_client.loop_stop()
    mqtt_client.disconnect()
    log.info("Wardrive Monitor stopped")


if __name__ == '__main__':
    main()

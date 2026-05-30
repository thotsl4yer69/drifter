#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Presence Detect
Vehicle WiFi-based arrival / departure detection. Watches the local
ARP / MAC table for known devices (driver's phone, key fob, satellite
ESP32) and publishes presence events when devices enter / leave the
hotspot's coverage.
UNCAGED TECHNOLOGY — EST 1991
"""

import json
import logging
import signal
import subprocess
import time
from pathlib import Path
from typing import Optional

import paho.mqtt.client as mqtt

from config import (
    MQTT_HOST, MQTT_PORT, TOPICS,
    PRESENCE_SCAN_INTERVAL, PRESENCE_DEPARTURE_GRACE,
    PRESENCE_KNOWN_DEVICES_FILE,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [PRESENCE] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger(__name__)


def _load_known() -> dict[str, str]:
    p = Path(PRESENCE_KNOWN_DEVICES_FILE)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception as e:
        log.warning(f"known devices load failed: {e}")
        return {}


def _save_known(known: dict[str, str]) -> None:
    p = Path(PRESENCE_KNOWN_DEVICES_FILE)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(known, indent=2))


def _scan_arp() -> set[str]:
    """Return set of MAC addresses seen on local interfaces."""
    macs = set()
    try:
        out = subprocess.run(
            ['ip', 'neigh', 'show'],
            capture_output=True, text=True, timeout=5,
        ).stdout
        for line in out.splitlines():
            parts = line.split()
            for i, tok in enumerate(parts):
                if tok == 'lladdr' and i + 1 < len(parts):
                    macs.add(parts[i + 1].lower())
    except Exception:
        # Windows fallback: `arp -a`
        try:
            out = subprocess.run(['arp', '-a'], capture_output=True, text=True, timeout=5).stdout
            for line in out.splitlines():
                for tok in line.split():
                    if '-' in tok and len(tok) == 17:
                        macs.add(tok.replace('-', ':').lower())
                    elif ':' in tok and len(tok) == 17:
                        macs.add(tok.lower())
        except Exception as e:
            log.debug(f"arp scan failed: {e}")
    return macs


def main() -> None:
    log.info("DRIFTER Presence Detect starting...")
    known = _load_known()
    if not known:
        log.warning(f"no known devices — populate {PRESENCE_KNOWN_DEVICES_FILE} with {{mac: label}}")

    running = [True]

    def _handle_signal(sig, frame):
        running[0] = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    client = mqtt.Client(client_id="drifter-presence")
    connected = False
    while not connected and running[0]:
        try:
            client.connect(MQTT_HOST, MQTT_PORT, 60)
            connected = True
        except Exception as e:
            log.warning(f"Waiting for MQTT broker... ({e})")
            time.sleep(3)

    if not running[0]:
        return

    client.loop_start()
    client.publish(TOPICS['presence_status'], json.dumps({
        'status': 'up', 'tracked': len(known), 'ts': time.time(),
    }), retain=True)
    log.info(f"Presence LIVE — tracking {len(known)} devices, scan every {PRESENCE_SCAN_INTERVAL}s")

    last_seen: dict[str, float] = {}
    presence_state: dict[str, bool] = {}  # mac -> present

    while running[0]:
        macs = _scan_arp()
        now = time.time()
        for mac, label in known.items():
            mac_lc = mac.lower()
            here = mac_lc in macs
            if here:
                last_seen[mac_lc] = now
                if not presence_state.get(mac_lc, False):
                    presence_state[mac_lc] = True
                    log.info(f"+ {label} ({mac_lc}) arrived")
                    client.publish(TOPICS['presence_event'], json.dumps({
                        'event': 'arrived', 'mac': mac_lc, 'label': label, 'ts': now,
                    }))
            else:
                last = last_seen.get(mac_lc, 0)
                if presence_state.get(mac_lc, False) and (now - last) > PRESENCE_DEPARTURE_GRACE:
                    presence_state[mac_lc] = False
                    log.info(f"- {label} ({mac_lc}) departed")
                    client.publish(TOPICS['presence_event'], json.dumps({
                        'event': 'departed', 'mac': mac_lc, 'label': label, 'ts': now,
                    }))
        # heartbeat with present roster
        present = [known[m] for m in presence_state if presence_state[m] and m in known]
        client.publish(TOPICS['presence_status'], json.dumps({
            'status': 'scanning', 'present': present, 'ts': now,
        }))
        # sleep in short slices so SIGTERM is responsive
        for _ in range(PRESENCE_SCAN_INTERVAL):
            if not running[0]:
                break
            time.sleep(1)

    client.publish(TOPICS['presence_status'], json.dumps({'status': 'down', 'ts': time.time()}), retain=True)
    client.loop_stop()
    client.disconnect()
    log.info("Presence Detect stopped")


if __name__ == '__main__':
    main()

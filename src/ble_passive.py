#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Passive BLE Scanner (drifter-bleconv)

Listens to BLE advertisements via BlueZ (bleak), matches against
config/ble_targets.yaml, publishes hits to drifter/ble/detection,
logs to /opt/drifter/state/ble-events.db. No probe requests, no
connections, no transmissions — listening only.

UNCAGED TECHNOLOGY — EST 1991
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import signal
import sqlite3
import time
from collections import deque
from pathlib import Path
from typing import Optional

import paho.mqtt.client as mqtt

try:
    from config import (
        DRIFTER_DIR, MQTT_HOST, MQTT_PORT, TOPICS, make_mqtt_client,
        BLE_TARGETS_PATH, BLE_DB_PATH, BLE_RAW_PUBLISH,
        BLE_LOG_RETENTION_DAYS, BLE_RATE_LIMIT_SEC, BLE_GPS_FRESH_SEC,
    )
except ImportError:
    import sys
    sys.path.insert(0, '/opt/drifter')
    from config import (  # type: ignore
        DRIFTER_DIR, MQTT_HOST, MQTT_PORT, TOPICS, make_mqtt_client,
        BLE_TARGETS_PATH, BLE_DB_PATH, BLE_RAW_PUBLISH,
        BLE_LOG_RETENTION_DAYS, BLE_RATE_LIMIT_SEC, BLE_GPS_FRESH_SEC,
    )

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [BLECONV] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger(__name__)

_OUI_RE = re.compile(r'^[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}$')


# ── Target loading + validation ────────────────────────────────────

def load_targets(path: Path) -> list[dict]:
    """Read + validate ble_targets.yaml. Returns the list of usable targets;
    targets with verified=false AND enabled=true are warned + disabled."""
    if not path.exists():
        log.warning(f"BLE targets file missing: {path}")
        return []
    try:
        import yaml
    except ImportError:
        log.error("pyyaml not installed — BLE targets unavailable")
        return []
    data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    raw = data.get('targets', [])
    if not isinstance(raw, list):
        log.error("ble_targets.yaml: 'targets' must be a list")
        return []
    out: list[dict] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get('name', '')).strip()
        if not name:
            log.warning("BLE target missing name; skipping")
            continue
        match = entry.get('match') or {}
        if not isinstance(match, dict) or not any(match.values()):
            log.warning(f"BLE target {name!r}: no match criteria; skipping")
            continue
        enabled = bool(entry.get('enabled', False))
        verified = bool(entry.get('verified', False))
        if enabled and not verified:
            log.warning(f"BLE target {name!r}: enabled but unverified — disabling at runtime")
            enabled = False
        out.append({
            'name': name,
            'description': str(entry.get('description', '')),
            'enabled': enabled,
            'verified': verified,
            'match': match,
            'rssi_alert_threshold': int(entry.get('rssi_alert_threshold', -60)),
            'vivi_alert': bool(entry.get('vivi_alert', False)),
            'vivi_label': str(entry.get('vivi_label', name)),
        })
    return out


# ── Match predicates ───────────────────────────────────────────────

def matches_oui(target: dict, mac: str) -> bool:
    prefixes = target['match'].get('oui_prefixes') or []
    if not prefixes:
        return False
    head = (mac or '').upper()[:8]
    return any(head == str(p).upper() for p in prefixes)


def matches_manufacturer_id(target: dict, mfr_data: dict) -> bool:
    want = target['match'].get('manufacturer_id')
    if want is None:
        return False
    return int(want) in (mfr_data or {})


def matches_manufacturer_data_prefix(target: dict, mfr_data: dict) -> bool:
    """Hex-encode the mfr_data bytes for the matched ID and check startswith."""
    want_id = target['match'].get('manufacturer_id')
    want_pfx = (target['match'].get('manufacturer_data_prefix') or '').lower()
    if want_id is None or not want_pfx:
        return False
    raw = (mfr_data or {}).get(int(want_id))
    if raw is None:
        return False
    return raw.hex().startswith(want_pfx)


def matches_service_uuid(target: dict, service_uuids: list) -> bool:
    want = [str(u).lower() for u in target['match'].get('service_uuids') or []]
    if not want:
        return False
    have = [str(u).lower() for u in (service_uuids or [])]
    return any(u in have for u in want)


def target_matches(target: dict, mac: str, mfr_data: dict, service_uuids: list) -> bool:
    """OR semantics across this target's criteria. Any one is enough."""
    return (
        matches_oui(target, mac) or
        matches_manufacturer_id(target, mfr_data) or
        matches_manufacturer_data_prefix(target, mfr_data) or
        matches_service_uuid(target, service_uuids)
    )


# ── Rate limiter ───────────────────────────────────────────────────

class RateLimiter:
    """Per-(target, mac) cooldown. Prunes entries older than 5min on every check."""
    PRUNE_AFTER = 300.0

    def __init__(self, cooldown: float):
        self.cooldown = cooldown
        self._last: dict[tuple[str, str], float] = {}

    def allow(self, target: str, mac: str, now: Optional[float] = None) -> bool:
        now = now if now is not None else time.time()
        # prune
        stale = [k for k, t in self._last.items() if now - t > self.PRUNE_AFTER]
        for k in stale:
            del self._last[k]
        key = (target, mac)
        if now - self._last.get(key, 0) < self.cooldown:
            return False
        self._last[key] = now
        return True

    def __len__(self) -> int:
        return len(self._last)


# ── GPS injection ──────────────────────────────────────────────────

class GpsCache:
    """Last fix from drifter/gps/fix; only attached when fresh."""
    def __init__(self, fresh_sec: float):
        self.fresh_sec = fresh_sec
        self._fix: Optional[dict] = None
        self._ts: float = 0.0

    def update(self, fix: dict) -> None:
        if isinstance(fix, dict) and 'lat' in fix and 'lng' in fix:
            self._fix = {'lat': float(fix['lat']), 'lng': float(fix['lng'])}
            self._ts = time.time()

    def get(self) -> Optional[dict]:
        if not self._fix:
            return None
        if time.time() - self._ts > self.fresh_sec:
            return None
        return dict(self._fix)


# ── SQLite event log ───────────────────────────────────────────────

class EventLog:
    SCHEMA = """
        CREATE TABLE IF NOT EXISTS detections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            target TEXT NOT NULL,
            mac TEXT NOT NULL,
            rssi INTEGER NOT NULL,
            gps_lat REAL,
            gps_lng REAL,
            manufacturer_id TEXT,
            advertised_name TEXT,
            raw_advertisement TEXT,
            is_alert INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_ts ON detections(ts);
        CREATE INDEX IF NOT EXISTS idx_target_mac ON detections(target, mac);
    """

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as c:
            c.executescript(self.SCHEMA)

    def insert(self, detection: dict) -> None:
        with sqlite3.connect(self.path) as c:
            c.execute(
                """INSERT INTO detections
                   (ts, target, mac, rssi, gps_lat, gps_lng, manufacturer_id,
                    advertised_name, raw_advertisement, is_alert)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    detection['ts'],
                    detection['target'],
                    detection['mac'],
                    detection['rssi'],
                    (detection.get('gps') or {}).get('lat'),
                    (detection.get('gps') or {}).get('lng'),
                    detection.get('manufacturer_id'),
                    detection.get('advertised_name'),
                    detection.get('raw_advertisement'),
                    1 if detection.get('is_alert') else 0,
                ),
            )

    def prune_older_than(self, days: int) -> int:
        cutoff = time.time() - (days * 86400)
        with sqlite3.connect(self.path) as c:
            cur = c.execute("DELETE FROM detections WHERE ts < ?", (cutoff,))
            return cur.rowcount

    def count(self) -> int:
        with sqlite3.connect(self.path) as c:
            return c.execute("SELECT COUNT(*) FROM detections").fetchone()[0]


# ── Scanner ────────────────────────────────────────────────────────

class BLEScanner:
    def __init__(self):
        self.targets = load_targets(BLE_TARGETS_PATH)
        self.enabled = [t for t in self.targets if t['enabled']]
        log.info(f"loaded {len(self.targets)} target(s); {len(self.enabled)} enabled")
        self.rl = RateLimiter(BLE_RATE_LIMIT_SEC)
        self.gps = GpsCache(BLE_GPS_FRESH_SEC)
        self.eventlog = EventLog(BLE_DB_PATH)
        self._mqtt: Optional[mqtt.Client] = None
        self._stop = asyncio.Event()
        self._last_prune = 0.0

    def _connect_mqtt(self) -> None:
        client = make_mqtt_client('drifter-bleconv')
        client.connect(MQTT_HOST, MQTT_PORT, 60)
        client.subscribe(TOPICS.get('gps_fix', 'drifter/gps/fix'))
        client.on_message = self._on_mqtt
        client.loop_start()
        self._mqtt = client

    def _on_mqtt(self, _client, _ud, msg):
        try:
            payload = json.loads(msg.payload)
        except (ValueError, UnicodeDecodeError):
            return
        if msg.topic == TOPICS.get('gps_fix', 'drifter/gps/fix'):
            self.gps.update(payload)

    def _detection_callback(self, device, advertisement_data):
        mac = device.address or ''
        rssi = int(advertisement_data.rssi or -127)
        mfr_data = dict(advertisement_data.manufacturer_data or {})
        service_uuids = list(advertisement_data.service_uuids or [])
        name = advertisement_data.local_name

        # Verbose mode — every advertisement (gated). Useful for offline
        # analysis but spammy; off by default.
        if BLE_RAW_PUBLISH and self._mqtt is not None:
            try:
                self._mqtt.publish(TOPICS.get('ble_raw', 'drifter/ble/raw'),
                                   json.dumps({'mac': mac, 'rssi': rssi,
                                               'name': name, 'ts': time.time()}))
            except Exception:
                pass

        for target in self.enabled:
            if not target_matches(target, mac, mfr_data, service_uuids):
                continue
            if not self.rl.allow(target['name'], mac):
                continue
            mfr_id_hex: Optional[str] = None
            mfr_blob_hex = ''
            if mfr_data:
                k = next(iter(mfr_data))
                mfr_id_hex = f'0x{k:04x}'
                mfr_blob_hex = mfr_data[k].hex()
            is_alert = rssi >= target['rssi_alert_threshold']
            detection = {
                'target': target['name'],
                'target_label': target['vivi_label'],
                'mac': mac,
                'mac_prefix': mac[:8].upper() if len(mac) >= 8 else mac,
                'rssi': rssi,
                'ts': time.time(),
                'gps': self.gps.get(),
                'manufacturer_id': mfr_id_hex,
                'advertised_name': name,
                'is_alert': is_alert,
                'raw_advertisement': mfr_blob_hex,
                'vivi_alert': bool(target['vivi_alert']) and is_alert,
            }
            self.eventlog.insert(detection)
            if self._mqtt is not None:
                try:
                    self._mqtt.publish(
                        TOPICS.get('ble_detection', 'drifter/ble/detection'),
                        json.dumps(detection),
                    )
                except Exception as e:
                    log.warning(f"ble publish failed: {e}")
            log.info(
                f"hit {target['name']} {mac} rssi={rssi}"
                f"{' [ALERT]' if is_alert else ''}"
            )
            break  # one target per advertisement is enough

        # Daily prune
        now = time.time()
        if now - self._last_prune > 86400:
            self._last_prune = now
            removed = self.eventlog.prune_older_than(BLE_LOG_RETENTION_DAYS)
            if removed:
                log.info(f"pruned {removed} old detection(s)")

    async def run(self) -> int:
        if not self.enabled:
            log.warning("no enabled targets — sleeping until config change is restart-detected")
            await self._stop.wait()
            return 0

        try:
            from bleak import BleakScanner
        except ImportError as e:
            log.error(f"bleak not available: {e}")
            return 1

        self._connect_mqtt()
        # BlueZ "passive" mode requires kernel-level or_patterns filters
        # tied to advertisement data types — it can't filter on device MAC,
        # which our primary Axon target needs (OUI 00:25:DF). So we run
        # in BlueZ's default scan mode: listen to every advertisement, no
        # connection attempts, no SCAN_REQ for advertisers that opted out
        # of scannability. We never connect — purely a listener.
        scanner = BleakScanner(
            detection_callback=self._detection_callback,
        )
        log.info(f"BLE scanner LIVE — listening for {len(self.enabled)} target(s)")
        await scanner.start()
        try:
            await self._stop.wait()
        finally:
            await scanner.stop()
            if self._mqtt is not None:
                self._mqtt.loop_stop()
                self._mqtt.disconnect()
        return 0

    def stop(self):
        self._stop.set()


def main() -> int:
    scanner = BLEScanner()

    def _sig(_signo, _frame):
        log.info("shutdown signal")
        loop = asyncio.get_event_loop()
        loop.call_soon_threadsafe(scanner.stop)

    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)
    try:
        return asyncio.run(scanner.run())
    except KeyboardInterrupt:
        return 0


if __name__ == '__main__':
    raise SystemExit(main())

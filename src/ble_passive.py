"""
MZ1312 DRIFTER — Passive BLE surveillance scanner v2

OUI-classified surveillance detection. Targets:
  0025df → axon-class       (Axon Enterprise: body cams, TASER, Signal)
  003044 → cradlepoint-class (Cradlepoint mobile router — police vehicle network)
  00170d → cradlepoint-class (Cradlepoint mobile router — police vehicle network)
  f8e71e → ruckus-class      (Ruckus AP — common in police facilities)

Apple Find My (manufacturer_id 0x004C, type byte 0x12) → airtag-class.

Persists to /opt/drifter/state/ble_history.db (WAL). Schema is migrated in
place via ALTER TABLE — never dropped — so existing dashboards/tests keep
working through the v1→v2 transition.

MQTT topics:
  drifter/ble/detection       qos 0  every classified hit
  drifter/ble/alert/<target>  qos 1  60s per-MAC cooldown
  drifter/ble/persist         qos 1  retained — same MAC ≥2 drives in 30 days
  drifter/ble/stats           qos 1  retained — hourly target-count rollup

Config (env):
  DRIFTER_MQTT_HOST/PORT/USER/PASS
  DRIFTER_BLE_DB                  default /opt/drifter/state/ble_history.db
  DRIFTER_BLE_SCAN_SECS           default 8
  DRIFTER_BLE_ALERT_COOLDOWN      default 60
  DRIFTER_STATE_DIR               default /opt/drifter/state

UNCAGED TECHNOLOGY — EST 1991
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sqlite3
import sys
import time
from pathlib import Path
from typing import Optional

try:
    from bleak import BleakScanner
except ImportError:  # pragma: no cover — bleak is required at runtime
    print("FATAL: bleak not installed", file=sys.stderr)
    sys.exit(1)

import paho.mqtt.client as mqtt


# ── Config ─────────────────────────────────────────────────────────

MQTT_HOST  = os.environ.get('DRIFTER_MQTT_HOST', 'localhost')
MQTT_PORT  = int(os.environ.get('DRIFTER_MQTT_PORT', '1883'))
MQTT_USER  = os.environ.get('DRIFTER_MQTT_USER') or None
MQTT_PASS  = os.environ.get('DRIFTER_MQTT_PASS') or None

STATE_DIR  = Path(os.environ.get('DRIFTER_STATE_DIR', '/opt/drifter/state'))
DB_PATH    = Path(os.environ.get('DRIFTER_BLE_DB', str(STATE_DIR / 'ble_history.db')))
GPS_PATH   = STATE_DIR / 'gps.json'
DRIVE_PATH_LEGACY = STATE_DIR / 'current_drive_id'
DRIVE_PATH_NEW    = STATE_DIR / 'current_drive'

SCAN_SECS         = float(os.environ.get('DRIFTER_BLE_SCAN_SECS', '8'))
ALERT_COOLDOWN_S  = float(os.environ.get('DRIFTER_BLE_ALERT_COOLDOWN', '60'))
STATS_INTERVAL_S  = 3600
PERSIST_WINDOW_S  = 30 * 86400

# OUI longest-prefix table — lowercase, no separators, hex.
OUI_RULES: list[tuple[str, str, str, str]] = [
    ('0025df', 'axon-class',         'high',
        'Axon Enterprise (police body cam, TASER, Signal)'),
    ('003044', 'cradlepoint-class',  'high',
        'Cradlepoint mobile router (police vehicle network)'),
    ('00170d', 'cradlepoint-class',  'high',
        'Cradlepoint mobile router (police vehicle network)'),
    ('f8e71e', 'ruckus-class',       'medium',
        'Ruckus AP (used in police facilities)'),
]
OUI_RULES.sort(key=lambda r: -len(r[0]))   # longest-prefix-wins

APPLE_MFR_ID    = 0x004C
APPLE_FINDMY_TY = 0x12

TOPIC_DETECTION = 'drifter/ble/detection'
TOPIC_ALERT_FMT = 'drifter/ble/alert/{target}'
TOPIC_PERSIST   = 'drifter/ble/persist'
TOPIC_STATS     = 'drifter/ble/stats'


# ── Logging ────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [BLE] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger('drifter.ble')


# ── DB ─────────────────────────────────────────────────────────────

# Columns the v2 daemon writes. Existing v1 columns (manufacturer_id,
# adv_name, lng, is_alert) are preserved and dual-written so legacy
# readers (web_dashboard_handlers.py, tests, ble_history.py) keep working.
V2_COLUMNS = {
    'name':              'TEXT',
    'severity':          'TEXT',
    'description':       'TEXT',
    'manufacturer_data': 'TEXT',
    'service_uuids':     'TEXT',
    'lon':               'REAL',
}


def open_db(path: Path) -> sqlite3.Connection:
    """Open + WAL + create-if-missing + ALTER ADD any missing v2 columns."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS detections (
          id              INTEGER PRIMARY KEY AUTOINCREMENT,
          ts              REAL NOT NULL,
          target          TEXT NOT NULL,
          mac             TEXT NOT NULL,
          rssi            INTEGER,
          name            TEXT,
          severity        TEXT,
          description     TEXT,
          manufacturer_data TEXT,
          service_uuids   TEXT,
          lat             REAL,
          lon             REAL,
          drive_id        TEXT NOT NULL DEFAULT 'unknown'
        )
    ''')
    have = {row[1] for row in conn.execute('PRAGMA table_info(detections)')}
    for col, sqltype in V2_COLUMNS.items():
        if col not in have:
            conn.execute(f'ALTER TABLE detections ADD COLUMN {col} {sqltype}')
            log.info(f'schema: ADD COLUMN {col} {sqltype}')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_det_mac        ON detections(mac)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_det_target_ts  ON detections(target, ts)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_det_drive_id   ON detections(drive_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_det_ts         ON detections(ts)')
    conn.commit()
    return conn


def insert_detection(conn: sqlite3.Connection, d: dict) -> None:
    """Insert with dual-write to legacy columns (lng, adv_name, is_alert,
    manufacturer_id) where they exist, so v1 readers keep functioning."""
    cols = {row[1] for row in conn.execute('PRAGMA table_info(detections)')}
    fields = ['ts', 'target', 'mac', 'rssi', 'name', 'severity',
              'description', 'manufacturer_data', 'service_uuids',
              'lat', 'lon', 'drive_id']
    values = [d.get(k) for k in fields]
    if 'lng' in cols:
        fields.append('lng');             values.append(d.get('lon'))
    if 'adv_name' in cols:
        fields.append('adv_name');        values.append(d.get('name'))
    if 'is_alert' in cols:
        fields.append('is_alert')
        values.append(1 if d.get('severity') in ('high', 'medium') else 0)
    if 'manufacturer_id' in cols:
        fields.append('manufacturer_id'); values.append(d.get('_legacy_mfr_hex'))

    placeholders = ','.join('?' * len(fields))
    conn.execute(
        f'INSERT INTO detections ({",".join(fields)}) VALUES ({placeholders})',
        values,
    )
    conn.commit()


# ── Helpers ────────────────────────────────────────────────────────

def normalise_mac(mac: str) -> str:
    return (mac or '').upper()


def oui_of(mac: str) -> str:
    """First 6 hex chars of the MAC, lowercase, no separators."""
    return mac.lower().replace(':', '').replace('-', '')[:6]


def classify_oui(mac: str) -> Optional[tuple[str, str, str]]:
    """Return (target, severity, description) or None for non-target OUIs."""
    o = oui_of(mac)
    for prefix, target, severity, desc in OUI_RULES:
        if o.startswith(prefix):
            return (target, severity, desc)
    return None


def is_apple_findmy(mfr: dict[int, bytes]) -> bool:
    """0x004C with first payload byte == 0x12."""
    if APPLE_MFR_ID not in mfr:
        return False
    payload = mfr[APPLE_MFR_ID]
    return bool(payload) and payload[0] == APPLE_FINDMY_TY


def serialise_mfr(mfr: dict[int, bytes]) -> str:
    """JSON map of company_id (0xNNNN) → hex payload string."""
    return json.dumps({f'0x{cid:04X}': payload.hex() for cid, payload in mfr.items()})


def first_legacy_mfr_hex(mfr: dict[int, bytes]) -> Optional[str]:
    if not mfr:
        return None
    cid = next(iter(mfr))
    return f'0x{cid:04X}'


def read_gps() -> tuple[Optional[float], Optional[float]]:
    """Read /opt/drifter/state/gps.json. Format: {lat, lon, fix, ts}."""
    try:
        j = json.loads(GPS_PATH.read_text())
        if not j.get('fix'):
            return (None, None)
        if time.time() - float(j.get('ts', 0)) > 30:
            return (None, None)
        return (float(j['lat']), float(j['lon']))
    except (FileNotFoundError, json.JSONDecodeError, KeyError, ValueError, TypeError):
        return (None, None)


def read_drive_id() -> str:
    """Read current drive id. Try spec path first, then v1 path."""
    for p in (DRIVE_PATH_NEW, DRIVE_PATH_LEGACY):
        try:
            v = p.read_text().strip()
            if v:
                return v
        except FileNotFoundError:
            continue
    return 'no-drive'


# ── MQTT ───────────────────────────────────────────────────────────

def make_mqtt() -> mqtt.Client:
    """Create + connect MQTT client. paho v1/v2 compatible."""
    try:
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id='drifter-bleconv',
        )
    except AttributeError:
        client = mqtt.Client(client_id='drifter-bleconv')
    if MQTT_USER:
        client.username_pw_set(MQTT_USER, MQTT_PASS or '')
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
    client.loop_start()
    return client


# ── Detection processing ───────────────────────────────────────────

class Bleconv:
    def __init__(self, conn: sqlite3.Connection, mqttc: mqtt.Client) -> None:
        self.conn = conn
        self.mqtt = mqttc
        self._cooldown: dict[str, float] = {}
        self._stats_window: dict[str, int] = {}
        self._stats_started_at = time.time()
        self._stop = asyncio.Event()

    def _cooldown_ok(self, mac: str) -> bool:
        now = time.time()
        if now - self._cooldown.get(mac, 0.0) >= ALERT_COOLDOWN_S:
            self._cooldown[mac] = now
            return True
        return False

    def _check_persistent(self, mac: str) -> Optional[dict]:
        cutoff = time.time() - PERSIST_WINDOW_S
        row = self.conn.execute(
            '''SELECT COUNT(DISTINCT drive_id), MIN(ts), MAX(ts)
               FROM detections WHERE mac = ? AND ts >= ?''',
            (mac, cutoff),
        ).fetchone()
        if not row or row[0] is None:
            return None
        n_drives, first_ts, last_ts = row
        if n_drives >= 2:
            return {
                'mac': mac,
                'unique_drives': int(n_drives),
                'first_seen': float(first_ts),
                'last_seen': float(last_ts),
                'window_days': 30,
            }
        return None

    def _publish_detection(self, d: dict) -> None:
        self.mqtt.publish(TOPIC_DETECTION, json.dumps(d), qos=0, retain=False)

    def _publish_alert(self, target: str, d: dict) -> None:
        self.mqtt.publish(
            TOPIC_ALERT_FMT.format(target=target),
            json.dumps(d), qos=1, retain=False,
        )

    def _publish_persist(self, summary: dict) -> None:
        self.mqtt.publish(TOPIC_PERSIST, json.dumps(summary), qos=1, retain=True)

    def _publish_stats(self) -> None:
        snap = {
            'window_started_ts': self._stats_started_at,
            'window_ended_ts':   time.time(),
            'counts': dict(self._stats_window),
        }
        self.mqtt.publish(TOPIC_STATS, json.dumps(snap), qos=1, retain=True)
        self._stats_window = {}
        self._stats_started_at = time.time()
        log.info('stats: %s', snap['counts'] or '(empty)')

    def _process(self, mac_raw: str, rssi: Optional[int], adv) -> None:
        mac = normalise_mac(mac_raw)
        cls = classify_oui(mac)
        mfr = dict(getattr(adv, 'manufacturer_data', None) or {})
        is_findmy = (cls is None) and is_apple_findmy(mfr)

        if cls is None and not is_findmy:
            return

        if cls is not None:
            target, severity, description = cls
        else:
            target, severity, description = (
                'airtag-class', 'medium',
                'Apple Find My beacon (AirTag/AirPods/Find My-paired item)',
            )

        lat, lon = read_gps()
        drive_id = read_drive_id()
        name = getattr(adv, 'local_name', None) or None
        service_uuids = list(getattr(adv, 'service_uuids', None) or [])
        ts = time.time()

        det = {
            'ts': ts,
            'target': target,
            'mac': mac,
            'rssi': int(rssi) if rssi is not None else None,
            'name': name,
            'severity': severity,
            'description': description,
            'manufacturer_data': serialise_mfr(mfr),
            'service_uuids': json.dumps(service_uuids),
            'lat': lat,
            'lon': lon,
            'drive_id': drive_id,
            '_legacy_mfr_hex': first_legacy_mfr_hex(mfr),
        }
        try:
            insert_detection(self.conn, det)
        except sqlite3.Error as e:
            log.warning('db insert failed: %s', e)
            return
        det.pop('_legacy_mfr_hex', None)

        self._publish_detection(det)
        self._stats_window[target] = self._stats_window.get(target, 0) + 1
        log.info('hit: %s %s rssi=%s name=%s drive=%s',
                 target, mac, det['rssi'], name or '?', drive_id)

        if self._cooldown_ok(mac):
            self._publish_alert(target, det)
            persist = self._check_persistent(mac)
            if persist:
                persist.update({
                    'target': target,
                    'severity': severity,
                    'description': description,
                    'last_seen_ts': ts,
                })
                self._publish_persist(persist)
                log.info('PERSISTENT: %s seen across %d drives',
                         mac, persist['unique_drives'])

    def detection_callback(self, device, advertisement_data) -> None:
        try:
            self._process(device.address, advertisement_data.rssi, advertisement_data)
        except Exception:
            log.exception('callback error')

    async def run(self) -> None:
        scanner = BleakScanner(detection_callback=self.detection_callback)
        log.info('DRIFTER bleconv v2 starting — %d OUI rules + Apple Find My',
                 len(OUI_RULES))
        log.info('mqtt %s:%s · db %s · scan %.0fs · cooldown %.0fs',
                 MQTT_HOST, MQTT_PORT, DB_PATH, SCAN_SECS, ALERT_COOLDOWN_S)
        await scanner.start()
        next_stats = time.time() + STATS_INTERVAL_S
        try:
            while not self._stop.is_set():
                await asyncio.sleep(SCAN_SECS)
                if time.time() >= next_stats:
                    self._publish_stats()
                    next_stats = time.time() + STATS_INTERVAL_S
        finally:
            await scanner.stop()
            log.info('scanner stopped')

    def stop(self) -> None:
        self._stop.set()


# ── Entry ──────────────────────────────────────────────────────────

def main() -> int:
    conn = open_db(DB_PATH)
    mqttc = make_mqtt()
    bc = Bleconv(conn, mqttc)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _shutdown(*_a):
        log.info('signal received — stopping')
        bc.stop()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT,  _shutdown)

    try:
        loop.run_until_complete(bc.run())
    except Exception:
        log.exception('bleconv crashed')
        return 1
    finally:
        try:
            mqttc.loop_stop()
            mqttc.disconnect()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass
        log.info('bleconv stopped')
    return 0


if __name__ == '__main__':
    sys.exit(main())

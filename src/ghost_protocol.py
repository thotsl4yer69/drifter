#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Ghost Protocol (Counter-Surveillance)
Passive counter-surveillance correlator. Does NOT drive radios directly —
it subscribes to the existing detection feeds (BLE passive scanner, RF
monitor, GPS) and raises alerts when the pattern looks like surveillance:

  - BLE tracker follower   AirTag / Tile / Samsung SmartTag seen across
                           multiple drive locations (someone tagged you)
  - Stingray / IMSI-catcher anomalous strong signal in cellular bands
                           from the RF spectrum sweep
  - ALPR awareness         plate-reader activity near you (from alpr feed
                           + known camera geofences)
  - Anomalous RF           unexpected energy in surveillance-associated
                           bands

Publishes to drifter/ghost/* (see TOPICS). All processing is local.
UNCAGED TECHNOLOGY — EST 1991
"""
from __future__ import annotations

import json
import logging
import signal
import time
from collections import defaultdict

from config import (
    MQTT_HOST,
    MQTT_PORT,
    TOPICS,
    make_mqtt_client,
)

try:
    from ble_identity import compute_identity
except Exception:  # pragma: no cover - ble_identity is always present in-tree
    def compute_identity(detection: dict) -> tuple[str, float]:
        return str(detection.get('mac', 'unknown')), 0.2

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [GHOST] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger(__name__)

# ── Tracker fingerprints ──
# Bluetooth SIG company identifiers (manufacturer-specific data) and the
# service UUIDs each tracker family advertises. We match on either.
TRACKER_COMPANY_IDS = {
    0x004C: 'apple-findmy',   # Apple — AirTag / Find My network
    0x0075: 'samsung-smarttag',
    0x0157: 'tile',
}
TRACKER_SERVICE_UUIDS = {
    'fd5a': 'samsung-smarttag',
    'feed': 'tile',
    'feec': 'tile',
    'fe9f': 'google-findmydevice',
}
# `target` labels that ble_passive/ble_identity already classify as trackers.
TRACKER_TARGETS = {'airtag', 'find-my', 'tile', 'smarttag', 'findmy'}

# A tracked identity that turns up at this many distinct ~100 m location
# cells within one session is treated as a follower (it's moving with you).
FOLLOWER_MIN_LOCATIONS = 3
# Coarse geocell size in degrees (~110 m at the equator) for "distinct place".
GEOCELL_DEG = 0.001
# Drop follower state after this much silence (single drive correlation).
FOLLOWER_TTL_SEC = 3600

# ── Surveillance RF bands ──
# IMSI-catchers ("Stingrays") force handsets onto a rogue cell, which shows
# up as an unusually strong, persistent carrier in cellular uplink/downlink
# bands. We can't decode it (no transmit, no cellular demod) — we only flag
# anomalous energy in these ranges from the rtl_power sweep. Heuristic only.
SURVEILLANCE_BANDS_MHZ = [
    {'name': 'GSM900-DL', 'lo': 935.0, 'hi': 960.0},
    # 1805–1880 MHz is shared by GSM1800 DL and LTE Band 3 DL — one physical
    # range, one entry. Listing it twice made every anomalous peak there fire
    # the IMSI-catcher alert twice.
    {'name': 'DL1800-GSM/LTE-B3', 'lo': 1805.0, 'hi': 1880.0},
    {'name': 'LTE-B28-DL', 'lo': 758.0, 'hi': 803.0},
]
# dBm above the rolling band median that counts as anomalous.
STINGRAY_DELTA_DB = 18.0

# ── State ──
# identity -> {'family', 'cells': set, 'first': ts, 'last': ts, 'alerted': bool}
_followers: dict = {}
_last_gps: dict = {'lat': None, 'lon': None, 'ts': 0.0}
_band_baseline: dict = defaultdict(list)


def _geocell(lat: float, lon: float) -> tuple[int, int]:
    return (int(lat / GEOCELL_DEG), int(lon / GEOCELL_DEG))


def _classify_tracker(detection: dict) -> str | None:
    """Return a tracker family label if the detection looks like a tracker."""
    target = str(detection.get('target', '')).lower()
    if target in TRACKER_TARGETS:
        return target
    mfr = detection.get('manufacturer_id')
    if mfr is not None:
        try:
            mfr_int = int(str(mfr), 0) if isinstance(mfr, str) else int(mfr)
            if mfr_int in TRACKER_COMPANY_IDS:
                return TRACKER_COMPANY_IDS[mfr_int]
        except (ValueError, TypeError):
            pass
    uuids = detection.get('service_uuids') or detection.get('uuids') or []
    if isinstance(uuids, str):
        uuids = [uuids]
    for u in uuids:
        key = str(u).lower().replace('0x', '')[-4:]
        if key in TRACKER_SERVICE_UUIDS:
            return TRACKER_SERVICE_UUIDS[key]
    return None


def _handle_ble(client, detection: dict) -> None:
    family = _classify_tracker(detection)
    if family is None:
        return
    identity, conf = compute_identity(detection)
    now = time.time()
    st = _followers.get(identity)
    if st is None:
        st = {'family': family, 'cells': set(), 'first': now, 'last': now,
              'alerted': False, 'confidence': conf}
        _followers[identity] = st
    st['last'] = now
    st['family'] = family
    if _last_gps['lat'] is not None and now - _last_gps['ts'] < 30:
        st['cells'].add(_geocell(_last_gps['lat'], _last_gps['lon']))

    if len(st['cells']) >= FOLLOWER_MIN_LOCATIONS and not st['alerted']:
        st['alerted'] = True
        log.warning("FOLLOWER: %s tracker '%s' seen at %d locations this drive",
                    family, identity, len(st['cells']))
        client.publish(TOPICS['ghost_tracker'], json.dumps({
            'family': family, 'identity': identity, 'confidence': conf,
            'locations': len(st['cells']), 'first_seen': st['first'], 'ts': now,
        }), retain=False)
        client.publish(TOPICS['ghost_alert'], json.dumps({
            'kind': 'tracker_follower', 'severity': 'amber', 'family': family,
            'message': f"{family} tracker following you ({len(st['cells'])} locations)",
            'ts': now,
        }))


def _handle_gps(detection: dict) -> None:
    lat = detection.get('lat')
    lon = detection.get('lon')
    if lat is None or lon is None:
        return
    try:
        _last_gps['lat'] = float(lat)
        _last_gps['lon'] = float(lon)
        _last_gps['ts'] = time.time()
    except (ValueError, TypeError):
        pass


def _handle_spectrum(client, payload: dict) -> None:
    """Flag anomalous energy in surveillance-associated cellular bands."""
    bins = payload.get('bins') or payload.get('spectrum') or []
    if not bins:
        return
    for band in SURVEILLANCE_BANDS_MHZ:
        powers = []
        for b in bins:
            try:
                freq = float(b.get('freq_mhz', b.get('freq', 0)))
                power = float(b.get('power_db', b.get('db', b.get('power', -999))))
            except (ValueError, TypeError, AttributeError):
                continue
            if band['lo'] <= freq <= band['hi']:
                powers.append(power)
        if not powers:
            continue
        peak = max(powers)
        baseline = _band_baseline[band['name']]
        baseline.append(peak)
        if len(baseline) > 30:
            baseline.pop(0)
        median = sorted(baseline)[len(baseline) // 2]
        if len(baseline) >= 5 and peak - median >= STINGRAY_DELTA_DB:
            log.warning("RF anomaly in %s: peak %.1f dB vs median %.1f dB",
                        band['name'], peak, median)
            client.publish(TOPICS['ghost_stingray'], json.dumps({
                'band': band['name'], 'peak_db': round(peak, 1),
                'median_db': round(median, 1), 'delta_db': round(peak - median, 1),
                'ts': time.time(),
            }))
            client.publish(TOPICS['ghost_alert'], json.dumps({
                'kind': 'imsi_catcher_suspect', 'severity': 'amber',
                'message': f"Anomalous carrier in {band['name']} "
                           f"(+{peak - median:.0f} dB) — possible IMSI-catcher",
                'ts': time.time(),
            }))


def _handle_alpr(client, payload: dict) -> None:
    """ALPR awareness — plate-reader activity nearby is an exposure event."""
    plate = payload.get('plate') or payload.get('text')
    if not plate:
        return
    client.publish(TOPICS['ghost_alpr'], json.dumps({
        'kind': 'alpr_activity', 'plate': str(plate)[:16],
        'confidence': payload.get('confidence'),
        'lat': _last_gps['lat'], 'lon': _last_gps['lon'], 'ts': time.time(),
    }))


def _prune(now: float) -> None:
    stale = [k for k, st in _followers.items() if now - st['last'] > FOLLOWER_TTL_SEC]
    for k in stale:
        del _followers[k]


def _on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code != 0:
        log.warning("MQTT connect reason_code=%s", reason_code)
        return
    for key in ('ble_detection', 'ble_raw', 'gps_fix', 'rf_spectrum',
                'rf_signal', 'alpr_plate'):
        client.subscribe(TOPICS[key])
    log.info("subscribed to BLE / GPS / RF / ALPR feeds")


def _on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode('utf-8', errors='ignore'))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return
    if not isinstance(payload, dict):
        return
    topic = msg.topic
    if topic in (TOPICS['ble_detection'], TOPICS['ble_raw']):
        _handle_ble(client, payload)
    elif topic == TOPICS['gps_fix']:
        _handle_gps(payload)
    elif topic in (TOPICS['rf_spectrum'], TOPICS['rf_signal']):
        _handle_spectrum(client, payload)
    elif topic == TOPICS['alpr_plate']:
        _handle_alpr(client, payload)


def main() -> None:
    running = [True]

    def _handle_signal(sig, frame):
        running[0] = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    client = make_mqtt_client("drifter-ghost")
    client.on_connect = _on_connect
    client.on_message = _on_message

    connected = False
    while not connected and running[0]:
        try:
            client.connect(MQTT_HOST, MQTT_PORT, 60)
            connected = True
        except Exception as e:
            log.warning("Waiting for MQTT broker... (%s)", e)
            time.sleep(3)
    if not running[0]:
        return

    client.loop_start()
    log.info("Ghost Protocol LIVE — passive counter-surveillance")
    client.publish(TOPICS['ghost_status'], json.dumps({
        'status': 'up', 'ts': time.time(),
    }), retain=True)

    last_status = 0.0
    while running[0]:
        now = time.time()
        if now - last_status >= 30:
            _prune(now)
            client.publish(TOPICS['ghost_status'], json.dumps({
                'status': 'up',
                'tracked': len(_followers),
                'followers': sum(1 for st in _followers.values() if st['alerted']),
                'ts': now,
            }), retain=True)
            last_status = now
        time.sleep(1)

    client.publish(TOPICS['ghost_status'], json.dumps({'status': 'down', 'ts': time.time()}),
                   retain=True)
    client.loop_stop()
    client.disconnect()
    log.info("Ghost Protocol stopped")


if __name__ == '__main__':
    main()

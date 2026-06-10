#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Vivi proactive sentinel (ported from retired v1)

The police-adjacent / counter-surveillance heads-up path. Maps a detection
message on one of the watched topics to a short, casual spoken line. Pure
classifier — the caller (vivi_v2) speaks the line via its proactive-comment
mechanism, which applies the per-reason cooldown so a flood of hits produces
one heads-up, not a stream.

Watched detections (topics already in config.TOPICS):
  - ble_detection   : axon-class BLE (officer body-cam/vehicle) at close range
                      (RSSI >= -70) → "Cop nearby."; other vivi-flagged BLE
                      alerts → a longer "<label> nearby" line.
  - adsb_police     : police-helicopter watcher feed → "Helicopter overhead."
  - drone_detection : RF drone pipeline → "Drone signal detected."
  - rf_adsb         : any aircraft below 1500 ft → "Low aircraft overhead."
                      (stale retained payloads >120 s are skipped so it
                      doesn't re-fire on every reconnect).

The casual phrasing is deliberate — the operator asks for detail if they
want it. (v1 lived inside vivi.py; restored here on the v2 brain.)

UNCAGED TECHNOLOGY — EST 1991
"""
from __future__ import annotations

import time

# Aircraft at/under this altitude (ft) surface as a heads-up.
LOW_AIRCRAFT_FT = 1500
# Axon close-range threshold (RSSI dBm); >= this counts as "close".
AXON_CLOSE_RSSI = -70
# Skip retained ADS-B payloads older than this (s) so reconnects don't re-fire.
ADSB_STALE_S = 120


def classify_detection(topic_key: str, payload: dict) -> tuple[str, str] | None:
    """Map a detection message to (cooldown_reason, heads-up line), or None.

    `topic_key` is the config.TOPICS key (e.g. 'ble_detection'). Pure: the
    caller speaks the line and owns the cooldown (keyed by the returned
    reason, so distinct threat types don't suppress each other)."""
    if not isinstance(payload, dict):
        return None

    if topic_key == 'ble_detection':
        target = str(payload.get('target', '')).strip()
        try:
            rssi = int(payload.get('rssi', 0) or 0)
        except (TypeError, ValueError):
            rssi = 0
        # Axon-class hardware at close range = officer with body cam / vehicle.
        if target in ('axon', 'axon-class') and rssi >= AXON_CLOSE_RSSI:
            return ('police_ble', 'Cop nearby.')
        # Other vivi-flagged BLE alerts get the longer configured line.
        if payload.get('vivi_alert') and payload.get('is_alert'):
            label = str(payload.get('target_label') or target).strip()
            return ('ble_alert', f"BLE detection: {label} nearby (RSSI {rssi})")
        return None

    if topic_key == 'adsb_police':
        return ('police_heli', 'Helicopter overhead.')

    if topic_key == 'drone_detection':
        return ('drone', 'Drone signal detected.')

    if topic_key == 'rf_adsb':
        try:
            payload_ts = float(payload.get('ts', 0) or 0)
        except (TypeError, ValueError):
            payload_ts = 0.0
        if payload_ts and (time.time() - payload_ts) > ADSB_STALE_S:
            return None
        for a in payload.get('aircraft') or []:
            if not isinstance(a, dict) or a.get('altitude') is None:
                continue
            try:
                alt = float(a.get('altitude', 0))
            except (TypeError, ValueError):
                continue
            if 0 < alt < LOW_AIRCRAFT_FT:
                return ('low_aircraft', 'Low aircraft overhead.')
        return None

    return None

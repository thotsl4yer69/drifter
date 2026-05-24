#!/usr/bin/env python3
"""
MZ1312 DRIFTER — RF Environment Baseline

Captures a 24h baseline of RF activity at a given GPS location and then
flags signals that diverge from it. Publishes "novel signal" events to
drifter/rf/novel so the cockpit RF tile can paint an amber pulsing dot.

Baseline shape (per location_hash):
  {
    'location_hash': str,
    'lat': float, 'lon': float,
    'captured_ts': float,
    'window_s': 86400,
    'known_signals': [
      {'frequency_mhz': float, 'protocol': str,
       'rssi_mean': float, 'rssi_stddev': float,
       'hit_rate_per_hour': float},
      ...
    ],
  }

Persisted as /opt/drifter/state/rf_baseline_<location_hash>.json.

UNCAGED TECHNOLOGY — EST 1991
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import signal
import time
from collections import defaultdict
from pathlib import Path
from typing import Optional

import paho.mqtt.client as mqtt

from config import MQTT_HOST, MQTT_PORT, TOPICS, make_mqtt_client
import gps_helper

logging.basicConfig(level=logging.INFO, format='%(asctime)s [RF-BASELINE] %(message)s',
                    datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

STATE_DIR = Path('/opt/drifter/state')
BASELINE_WINDOW_SEC = 24 * 3600
NOVEL_TOPIC = 'drifter/rf/novel'

# Round lat/lon to ~1 km cells (≈ 0.01°) for the location hash so a
# vehicle parked in the same spot across days picks up the same baseline.
_LOCATION_PRECISION = 2

# Bucket width for grouping nearby frequency observations — 0.5 MHz is
# small enough to keep different services apart, large enough to absorb
# rtl_433 jitter.
_FREQ_BUCKET_MHZ = 0.5

# Z-threshold above which an in-baseline signal RSSI is treated as novel.
_RSSI_Z_NOVEL = 3.0


def _location_hash(lat: float, lon: float) -> str:
    key = f"{round(lat, _LOCATION_PRECISION):.{_LOCATION_PRECISION}f}," \
          f"{round(lon, _LOCATION_PRECISION):.{_LOCATION_PRECISION}f}"
    return hashlib.sha1(key.encode()).hexdigest()[:12]


def _baseline_path(loc_hash: str) -> Path:
    return STATE_DIR / f'rf_baseline_{loc_hash}.json'


def _bucket_freq(freq_mhz: float) -> float:
    return round(freq_mhz / _FREQ_BUCKET_MHZ) * _FREQ_BUCKET_MHZ


def load_baseline(loc_hash: str) -> Optional[dict]:
    path = _baseline_path(loc_hash)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            return None
        return data
    except (OSError, json.JSONDecodeError):
        return None


def save_baseline(baseline: dict) -> bool:
    loc_hash = baseline.get('location_hash')
    if not loc_hash:
        return False
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        path = _baseline_path(loc_hash)
        tmp = path.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(baseline, default=str, indent=2))
        tmp.replace(path)
        return True
    except OSError as e:
        log.warning("baseline save failed: %s", e)
        return False


def build_baseline(samples: list, lat: float, lon: float,
                   captured_ts: Optional[float] = None,
                   window_s: int = BASELINE_WINDOW_SEC) -> dict:
    """Aggregate per-sample observations into a baseline dict.

    Each sample is a dict shaped {frequency_mhz, rssi, protocol?}. Samples
    are bucketed by (freq_bucket, protocol) so e.g. a TPMS sensor at
    433.92 with protocol "Ford" lands in a separate bucket from a generic
    433.92 ISM ping.
    """
    captured_ts = captured_ts if captured_ts is not None else time.time()
    by_key: dict = defaultdict(list)
    for s in samples:
        try:
            freq = float(s.get('frequency_mhz'))
            rssi = float(s.get('rssi'))
        except (TypeError, ValueError, AttributeError):
            continue
        proto = s.get('protocol') or 'unknown'
        bucket = _bucket_freq(freq)
        by_key[(bucket, proto)].append(rssi)

    hours = max(window_s / 3600.0, 1e-6)
    known = []
    for (bucket, proto), rssis in by_key.items():
        n = len(rssis)
        if n == 0:
            continue
        mean = sum(rssis) / n
        var = sum((r - mean) ** 2 for r in rssis) / n
        stddev = math.sqrt(var)
        known.append({
            'frequency_mhz': float(bucket),
            'protocol': proto,
            'rssi_mean': round(mean, 2),
            'rssi_stddev': round(max(stddev, 0.5), 2),
            'hit_rate_per_hour': round(n / hours, 3),
            'sample_count': n,
        })
    known.sort(key=lambda k: k['frequency_mhz'])
    return {
        'location_hash': _location_hash(lat, lon),
        'lat': lat,
        'lon': lon,
        'captured_ts': captured_ts,
        'window_s': window_s,
        'known_signals': known,
    }


def is_novel(observation: dict, baseline: dict) -> bool:
    """Return True if `observation` is not in the baseline OR exceeds
    rssi_mean + 3*stddev for its matching bucket.
    """
    try:
        freq = float(observation.get('frequency_mhz'))
        rssi = float(observation.get('rssi'))
    except (TypeError, ValueError, AttributeError):
        return False
    proto = observation.get('protocol') or 'unknown'
    bucket = _bucket_freq(freq)
    if not isinstance(baseline, dict):
        return True
    for k in baseline.get('known_signals', []) or []:
        if abs(k.get('frequency_mhz', -1) - bucket) < 1e-6 and k.get('protocol') == proto:
            mean = float(k.get('rssi_mean', 0))
            stddev = max(float(k.get('rssi_stddev', 0.5)), 0.5)
            return rssi > (mean + _RSSI_Z_NOVEL * stddev)
    return True


class RFBaseline:
    """Long-running collector + live novelty detector."""

    def __init__(self):
        self.running = True
        # Per-location capture buffers — {loc_hash: [samples]}
        self._capture_buffers: dict = defaultdict(list)
        self._capture_started: dict = {}     # loc_hash -> start_ts
        self._loaded_baselines: dict = {}    # loc_hash -> baseline dict
        self._last_gps_check_ts = 0.0
        self._current_loc_hash: Optional[str] = None
        self._last_published_novel: dict = {}  # (bucket, proto) -> ts (dedupe)

        self.client = make_mqtt_client('drifter-rf-baseline')
        self.client.on_message = self._on_message
        self.client.on_connect = self._on_connect

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        log.info("MQTT connected rc=%s", rc)
        for t in (
            TOPICS.get('rf_signal', 'drifter/rf/signals'),
            TOPICS.get('rf_spectrum', 'drifter/rf/spectrum'),
            TOPICS.get('rf_emergency', 'drifter/rf/emergency'),
        ):
            client.subscribe(t)

    def _on_message(self, client, userdata, msg):
        topic = msg.topic
        try:
            data = json.loads(msg.payload)
        except (json.JSONDecodeError, ValueError):
            return
        # Normalise per-message into a list of observations.
        observations = self._extract_observations(topic, data)
        if not observations:
            return
        # We need a fix to bucket by location. Without GPS, drop the data
        # rather than poison the wrong baseline.
        loc_hash = self._current_location_hash()
        if loc_hash is None:
            return
        baseline = self._get_or_init_baseline(loc_hash)
        # Always feed observations into the capture buffer (so the baseline
        # auto-updates if it doesn't exist yet).
        self._capture_buffers[loc_hash].extend(observations)
        if loc_hash not in self._capture_started:
            self._capture_started[loc_hash] = time.time()
        # If we already have a finalised baseline, evaluate novelty.
        if baseline is not None:
            self._evaluate_novelty(observations, baseline)
        else:
            self._maybe_finalise(loc_hash)

    def _extract_observations(self, topic: str, data) -> list:
        out = []
        if isinstance(data, dict):
            if 'frequency_mhz' in data and 'rssi' in data:
                out.append({
                    'frequency_mhz': data.get('frequency_mhz'),
                    'rssi': data.get('rssi'),
                    'protocol': data.get('protocol') or data.get('model'),
                })
            elif isinstance(data.get('signals'), list):
                for s in data['signals']:
                    if isinstance(s, dict):
                        out.append({
                            'frequency_mhz': s.get('frequency_mhz') or s.get('freq_mhz'),
                            'rssi': s.get('rssi'),
                            'protocol': s.get('protocol') or s.get('model'),
                        })
        return out

    def _current_location_hash(self) -> Optional[str]:
        now = time.time()
        # Recompute the location hash every 30s; otherwise reuse.
        if self._current_loc_hash is not None and (now - self._last_gps_check_ts) < 30:
            return self._current_loc_hash
        fix = gps_helper.current_fix()
        if fix is None:
            return None
        self._last_gps_check_ts = now
        self._current_loc_hash = _location_hash(fix['lat'], fix['lon'])
        return self._current_loc_hash

    def _get_or_init_baseline(self, loc_hash: str) -> Optional[dict]:
        if loc_hash in self._loaded_baselines:
            return self._loaded_baselines[loc_hash]
        b = load_baseline(loc_hash)
        if b is not None:
            self._loaded_baselines[loc_hash] = b
        return b

    def _maybe_finalise(self, loc_hash: str):
        start_ts = self._capture_started.get(loc_hash)
        if start_ts is None:
            return
        if (time.time() - start_ts) < BASELINE_WINDOW_SEC:
            return
        fix = gps_helper.current_fix()
        if fix is None:
            return
        baseline = build_baseline(
            self._capture_buffers[loc_hash], fix['lat'], fix['lon'],
            captured_ts=time.time(), window_s=BASELINE_WINDOW_SEC,
        )
        if save_baseline(baseline):
            self._loaded_baselines[loc_hash] = baseline
            log.info("Baseline FINALISED loc_hash=%s signals=%d",
                     loc_hash, len(baseline['known_signals']))
        # Clear the buffer to bound memory now that the baseline is in place.
        self._capture_buffers[loc_hash] = []

    def _evaluate_novelty(self, observations: list, baseline: dict):
        now = time.time()
        for obs in observations:
            if not is_novel(obs, baseline):
                continue
            try:
                freq = float(obs.get('frequency_mhz'))
            except (TypeError, ValueError):
                continue
            bucket = _bucket_freq(freq)
            proto = obs.get('protocol') or 'unknown'
            key = (bucket, proto)
            last = self._last_published_novel.get(key, 0)
            # Dedupe to one publish per 60s per (bucket, protocol). The
            # cockpit just needs the badge — flooding helps no one.
            if (now - last) < 60.0:
                continue
            self._last_published_novel[key] = now
            payload = {
                'ts': now,
                'frequency_mhz': freq,
                'rssi': obs.get('rssi'),
                'protocol_guess': proto,
                'baseline_location_hash': baseline.get('location_hash'),
            }
            try:
                self.client.publish(NOVEL_TOPIC, json.dumps(payload, default=str))
            except Exception as e:
                log.debug("novel publish failed: %s", e)

    def start(self):
        log.info("RF Baseline starting...")
        connected = False
        while not connected and self.running:
            try:
                self.client.connect(MQTT_HOST, MQTT_PORT, 60)
                connected = True
            except Exception as e:
                log.warning("MQTT connect failed: %s", e)
                time.sleep(3)
        self.client.loop_start()
        log.info("RF Baseline LIVE")
        while self.running:
            time.sleep(10)
            # Periodic finalisation check so a baseline lands even on a
            # quiet bus.
            for loc_hash in list(self._capture_started.keys()):
                if loc_hash not in self._loaded_baselines:
                    self._maybe_finalise(loc_hash)
        self.client.loop_stop()
        self.client.disconnect()


def main():
    rb = RFBaseline()

    def _stop(sig, frame):
        rb.running = False

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    rb.start()


if __name__ == '__main__':
    main()

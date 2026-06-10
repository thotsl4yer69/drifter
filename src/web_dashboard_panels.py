"""RF / CAN / airspace process-local caches for the web dashboard.

Extracted verbatim from web_dashboard_handlers.py (non-security HUD helper
group). The handler module re-imports these names so the public API at
web_dashboard_handlers.X is unchanged.
"""
from __future__ import annotations

import json
import logging
import threading as _threading
import time

import web_dashboard_state as state

log = logging.getLogger(__name__)

# Process-local ring buffer of the last N URH classifications published on
# drifter/rf/classification. The cockpit's "Signal Intel" sub-tile polls
# /api/rf/classification and renders the last 5; bound so a noisy band
# can't pin memory.
_RF_CLASSIFICATION_RING_MAX = 50
_rf_classifications: list = []
_rf_classifications_lock = _threading.Lock()


def _record_rf_classification(payload: dict) -> None:
    """Push a classifier payload (newest first) onto the ring."""
    if not isinstance(payload, dict):
        return
    with _rf_classifications_lock:
        _rf_classifications.insert(0, payload)
        del _rf_classifications[_RF_CLASSIFICATION_RING_MAX:]


def _snapshot_rf_classifications(limit: int = 50) -> list:
    with _rf_classifications_lock:
        return list(_rf_classifications[:max(0, int(limit))])


# Same ring for CaringCaribou discovery responses (drifter/can/discovery).
_CAN_DISCOVERY_RING_MAX = 25
_can_discoveries: list = []
_can_discoveries_lock = _threading.Lock()


def _record_can_discovery(payload: dict) -> None:
    if not isinstance(payload, dict):
        return
    with _can_discoveries_lock:
        _can_discoveries.insert(0, payload)
        del _can_discoveries[_CAN_DISCOVERY_RING_MAX:]


def _snapshot_can_discoveries(limit: int = 25) -> list:
    with _can_discoveries_lock:
        return list(_can_discoveries[:max(0, int(limit))])


# Airspace enrichment cache — populated by the background fetcher that
# polls tar1090's aircraft.json every 10s. /api/airspace/aircraft returns
# whatever the last poll captured (or {} when tar1090 hasn't answered).
_AIRSPACE_CACHE: dict = {'ts': 0.0, 'payload': {}}
_AIRSPACE_CACHE_LOCK = _threading.Lock()
_AIRSPACE_TAR1090_URL = 'http://localhost:8504/data/aircraft.json'
_AIRSPACE_POLL_INTERVAL_S = 10.0
_AIRSPACE_EMERGENCY_SQUAWKS = {'7500', '7600', '7700'}


def _update_airspace_cache(payload: dict) -> None:
    with _AIRSPACE_CACHE_LOCK:
        _AIRSPACE_CACHE['ts'] = time.time()
        _AIRSPACE_CACHE['payload'] = payload or {}


def _snapshot_airspace() -> dict:
    with _AIRSPACE_CACHE_LOCK:
        return {
            'ts': _AIRSPACE_CACHE['ts'],
            'aircraft': (_AIRSPACE_CACHE['payload'] or {}).get('aircraft', []),
            'source': 'tar1090',
            'raw': _AIRSPACE_CACHE['payload'],
        }


def _airspace_poller() -> None:
    """Background loop — fetch tar1090's aircraft.json, refresh the cache,
    and republish to drifter/airspace/aircraft so the WS fan-out picks it up."""
    import urllib.error
    import urllib.request
    while True:
        payload = None
        try:
            with urllib.request.urlopen(_AIRSPACE_TAR1090_URL, timeout=4) as resp:
                payload = json.loads(resp.read())
        except (urllib.error.URLError, OSError, ValueError, TimeoutError):
            payload = None
        except Exception as e:
            log.debug("airspace poll failed: %s", e)
            payload = None
        if payload is not None:
            _update_airspace_cache(payload)
            if state.mqtt_client is not None:
                try:
                    state.mqtt_client.publish(
                        'drifter/airspace/aircraft',
                        json.dumps({'ts': time.time(),
                                    'aircraft': payload.get('aircraft', [])}),
                    )
                except Exception as e:
                    log.debug("airspace publish failed: %s", e)
        time.sleep(_AIRSPACE_POLL_INTERVAL_S)


_AIRSPACE_THREAD_STARTED = {'v': False}


def start_airspace_poller() -> None:
    """Idempotent kick-off. Called once from web_dashboard.main()."""
    if _AIRSPACE_THREAD_STARTED['v']:
        return
    _AIRSPACE_THREAD_STARTED['v'] = True
    t = _threading.Thread(target=_airspace_poller, daemon=True,
                          name='airspace-poller')
    t.start()

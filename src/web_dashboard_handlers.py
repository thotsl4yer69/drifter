"""HTTP handler for the web dashboard.

DashboardHandler is a SimpleHTTPRequestHandler subclass that dispatches
requests through a lookup table instead of the 100-branch if/elif chain
we used to have inline in web_dashboard.py. Each endpoint is a short
method with a clear name, which is easier to scan, test, and extend.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml

import ble_history
import ble_persistence
import web_dashboard_state as state
from ble_map_html import BLE_MAP_HTML
from config import (
    DEFAULT_MODE,
    MODE_STATE_PATH,
    MODES,
    SERVICES,
    SETTINGS_SCHEMA,
    SETTINGS_SECTIONS,
    TOPICS,
    XTYPE_DTC_LOOKUP,
    load_settings,
    save_settings,
    validate_settings_payload,
)
from corpus import corpus_search, dtc_lookup
from hw_probe import probe_rtl_sdr, probe_speaker
from web_dashboard_hardware import check_hardware

log = logging.getLogger(__name__)

# Hard cap on POST request body size. Stops a hostile client from stalling
# a handler thread by announcing a giant Content-Length.
MAX_POST_BODY = 64 * 1024

# Maximum accepted browser-geolocation accuracy radius. Real phone GPS
# typically reports 5–30m even on a moving vehicle; Wi-Fi triangulation
# adds another ~20m. Anything coarser is almost certainly IP geolocation,
# which is useless as a vehicle position and historically poisoned the
# entire feeds pipeline with a phantom origin.
GPS_MAX_ACCURACY_M = 100.0

# OBD-II / manufacturer DTC format — P/C/B/U followed by four hex digits.
# Anything else on /api/mechanic/dtc/:code is rejected.
_DTC_RE = re.compile(r'^[PCBU][0-9A-F]{4}$')

# /healthz cache — systemctl is cheap but we hit it 15× per probe.
# The fleet contract pings /healthz frequently; cache for 2s.
_HEALTHZ_TTL = 2.0
_healthz_cache: dict = {'ts': 0.0, 'payload': None, 'http_status': 200}

# Services whose systemd active-state is necessary but not sufficient: their
# inner loop can degrade (mic disappears, CAN drops) while the unit stays
# "active". Each service writes a heartbeat file from inside its working loop;
# /healthz overrides the systemctl reading with the heartbeat freshness.
_CAPABILITY_HEARTBEATS: dict = {
    'drifter-voicein': ('/opt/drifter/voicein.heartbeat', 90.0),
}


def _systemctl_active(unit: str) -> bool:
    """Return True if `systemctl is-active <unit>` reports 'active'."""
    try:
        r = subprocess.run(
            ['systemctl', 'is-active', unit],
            capture_output=True, text=True, timeout=2,
        )
        return r.stdout.strip() == 'active'
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return False


def _heartbeat_fresh(path: str, max_age_s: float, now: float) -> bool:
    try:
        return (now - Path(path).stat().st_mtime) < max_age_s
    except OSError:
        return False


def _healthz_payload() -> tuple[dict, int]:
    """Build the /healthz payload + HTTP status. Cached for _HEALTHZ_TTL."""
    now = time.time()
    if (_healthz_cache['payload'] is not None
            and now - _healthz_cache['ts'] < _HEALTHZ_TTL):
        return _healthz_cache['payload'], _healthz_cache['http_status']

    services = {svc: _systemctl_active(svc) for svc in SERVICES}
    for svc, (hb_path, max_age) in _CAPABILITY_HEARTBEATS.items():
        if services.get(svc) and not _heartbeat_fresh(hb_path, max_age, now):
            services[svc] = False
    # Mode-aware failure: only services the current mode wants running count
    # toward the "failed" list. Drive-only services being inactive in FOOT mode
    # is the correct state, not a degradation.
    try:
        mode = (Path(MODE_STATE_PATH).read_text(encoding='utf-8').strip()
                or DEFAULT_MODE)
    except OSError:
        mode = DEFAULT_MODE
    expected = MODES.get(mode, set(SERVICES))
    # Hardware-optional services crash-loop cleanly until their dongle is
    # plugged in. Canbridge waits for USB2CANFD, rf for RTL-SDR, voicein
    # for the mic, vivi for Ollama+Piper. These should warn (status:
    # degraded), not fail the healthz contract (HTTP 503).
    _HW_OPTIONAL = {
        'drifter-canbridge', 'drifter-rf', 'drifter-vivi',
        'drifter-voicein', 'drifter-flipper', 'drifter-bleconv',
        'drifter-gps',
        # Community-tool services pending external installs / allowlist
        # config. They go inactive cleanly until their dependency is met
        # (urh/caringcaribou/kismet/bettercap/fly catcher model) rather
        # than failing the healthz contract.
        'drifter-can-discovery', 'drifter-fly-catcher',
        'drifter-kismet', 'drifter-kismet-bridge', 'drifter-wifi-audit',
        'drifter-rf-baseline', 'drifter-session-recorder',
    }
    failed = [s for s, ok in services.items()
              if s in expected and not ok and s not in _HW_OPTIONAL]
    degraded = [s for s, ok in services.items()
                if s in expected and not ok and s in _HW_OPTIONAL]

    mqtt_ok = state.mqtt_client is not None and getattr(
        state.mqtt_client, 'is_connected', lambda: False)()

    # Telemetry freshness: any topic updated in the last 30s = bus alive.
    last_seen = state.latest_state.get('_last_update', 0)
    telemetry_fresh = (now - last_seen) < 30 if last_seen else False

    if failed:
        status_str = 'degraded'
    elif degraded:
        status_str = 'ok-hw-pending'  # pi is healthy, dongles aren't plugged in yet
    else:
        status_str = 'ok'
    # System hostname — the cockpit wordmark reads this instead of a
    # baked-in node id. Cached because /etc/hostname is read-mostly.
    try:
        node_id = Path('/etc/hostname').read_text(encoding='utf-8').strip()
    except OSError:
        node_id = 'unknown'
    payload = {
        'status':              status_str,
        'mode':                mode,
        'ts':                  now,
        'node_id':             node_id,
        'services':            services,
        'services_failed':     failed,
        'services_hw_pending': degraded,
        'mqtt_connected':      mqtt_ok,
        'telemetry_fresh':     telemetry_fresh,
        'ws_clients':          len(state.ws_clients),
    }
    # Healthz contract: 200 = OS-side healthy, 503 = a NON-hardware service
    # is failing. Hardware-pending state still returns 200 so the deploy
    # contract doesn't block on a bench unit waiting for OBD-II.
    http_status = 200 if not failed else 503
    _healthz_cache.update(ts=now, payload=payload, http_status=http_status)
    return payload, http_status

# Static files served with no extra rewriting. All live at /opt/drifter/*.
_STATIC_FILES = {
    '/realdash.xml': ('/opt/drifter/drifter_channels.xml', 'application/xml',
                       'attachment; filename="drifter_channels.xml"'),
    # Vendored Leaflet 1.9.4 — used by /map/ble. Tethered phones can't
    # reach unpkg through the hotspot, so we serve everything locally.
    '/static/leaflet/leaflet.css': ('/opt/drifter/static/leaflet/leaflet.css',
                                     'text/css', None),
    '/static/leaflet/leaflet.js':  ('/opt/drifter/static/leaflet/leaflet.js',
                                     'application/javascript', None),
    '/static/leaflet/marker-icon.png':
        ('/opt/drifter/static/leaflet/marker-icon.png', 'image/png', None),
    '/static/leaflet/marker-icon-2x.png':
        ('/opt/drifter/static/leaflet/marker-icon-2x.png', 'image/png', None),
    '/static/leaflet/marker-shadow.png':
        ('/opt/drifter/static/leaflet/marker-shadow.png', 'image/png', None),
}


_BLE_HISTORY_DB = '/opt/drifter/state/ble_history.db'

_RFAUDIO_ACTIONS = {'start', 'stop', 'scan', 'test_tone', 'list_bands'}

# rf_monitor's on_message() command allowlist. Mirrors the if/elif chain in
# src/rf_monitor.py so the dashboard surface can't smuggle arbitrary commands
# onto drifter/rf/command — the bridge there has no further validation.
_RF_COMMANDS = {
    'tpms_learn_start', 'tpms_learn_stop',
    'tpms_auto_assign', 'tpms_assign',
    'pause_rtl_433', 'resume_rtl_433',
    'force_spectrum',
    'tpms_harvest_start', 'tpms_harvest_stop',
    'tpms_assign_corner', 'tpms_clear_assignments',
    'tpms_delta_capture',
}

# Mechanic chat ring buffer — process-local, last N {ts, role, content} turns
# prepended to the LLM prompt so the model has conversation context.
# Capped at MECHANIC_HISTORY_TURNS (5 user + 5 assistant) and trimmed
# until the joined char-count fits in MECHANIC_HISTORY_CHAR_BUDGET, which
# approximates the 2000-token ceiling at ~4 chars/token.
MECHANIC_HISTORY_TURNS = 10
MECHANIC_HISTORY_CHAR_BUDGET = 8000

import threading as _threading
from collections import deque as _deque

_mechanic_history: _deque = _deque(maxlen=MECHANIC_HISTORY_TURNS)
_mechanic_history_lock = _threading.Lock()


def _mechanic_history_append(role: str, content: str) -> None:
    """Append a turn to the ring buffer and trim to char budget."""
    if not isinstance(content, str) or not content.strip():
        return
    with _mechanic_history_lock:
        _mechanic_history.append({
            'ts': time.time(),
            'role': role,
            'content': content.strip(),
        })
        # Trim oldest turns while the budget is exceeded. Char-count
        # is the proxy for token-count (≈ 4 chars/token).
        total = sum(len(t.get('content') or '') for t in _mechanic_history)
        while total > MECHANIC_HISTORY_CHAR_BUDGET and len(_mechanic_history) > 1:
            dropped = _mechanic_history.popleft()
            total -= len(dropped.get('content') or '')


def _mechanic_history_reset() -> None:
    with _mechanic_history_lock:
        _mechanic_history.clear()


def _mechanic_history_snapshot() -> list:
    with _mechanic_history_lock:
        return list(_mechanic_history)


def _mechanic_history_block() -> str:
    """Render the ring as a CONVERSATION HISTORY context block."""
    turns = _mechanic_history_snapshot()
    if not turns:
        return ''
    lines = []
    for t in turns:
        role = (t.get('role') or '').upper()
        content = (t.get('content') or '').strip()
        if not content:
            continue
        lines.append(f"{role}: {content}")
    return "\n".join(lines)

# flipper_bridge's on_message() command allowlist. Same fail-closed posture
# as _RF_COMMANDS — only commands the cockpit surface emits.
#
# Add-on aware workflows: WIFI_PASSIVE_COMMANDS (Marauder) and
# SUBGHZ_PRESET_COMMANDS (CC1101) are appended below the base set. DEAUTH/
# BEACON SPAM/EVIL TWIN are deliberately omitted — the firmware can do them
# but the cockpit must not surface them per the operator's revised spec.
_FLIPPER_COMMANDS = {
    'subghz_monitor_start', 'subghz_monitor_stop',
    'confirm', 'subghz_replay',
    # Wi-Fi passive (TASK 2.2)
    'wifi_scan_ap', 'wifi_scan_sta', 'ble_scan',
    'packet_monitor', 'probe_capture', 'pwnagotchi_passive',
    # Sub-GHz preset (TASK 2.3)
    'freq_analyzer', 'raw_capture', 'read_protocol',
}

# can_discovery's on_message() command allowlist. Cockpit's CAN DISCOVERY
# drawer is the only intended emitter; can_discovery.py has its own
# allowlist check as the second gate.
_CAN_COMMANDS = {
    'discover_ecus', 'list_services', 'dump_dids', 'fuzz_range',
}

# CSV capture directory shared with can_discovery.py. /api/can/captures
# lists files here and /api/can/captures/<name> serves them back.
_CAN_CAPTURE_DIR = Path('/opt/drifter/state/can_captures')

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


# Per-corner TPMS assignment file (written by rf_monitor.tpms_assign_corner).
# GET /api/tpms/assignments reads this verbatim so the cockpit shows the
# persisted pairing without depending on a live MQTT round-trip.
_TPMS_ASSIGNMENTS_PATH = Path('/opt/drifter/state/tpms_assignments.json')

# Local flipper .sub artifact cache, mirroring flipper_bridge.FLIPPER_CAPTURE_DIR.
# /api/flipper/captures merges these into each ring-buffer row so the cockpit
# can present a real REPLAY button.
_FLIPPER_CAPTURE_DIR = Path('/opt/drifter/state/flipper_captures')

# Phone-as-GPS sink. The tethered phone POSTs its browser-geolocation
# fix here; we drop it at the same path drifter-gps writes to so
# feeds.origin() sees it without any wiring change.
_GPS_STATE_PATH = Path('/opt/drifter/state/gps.json')

# Driver profile — read by Vivi at the top of every turn (see
# config/driver.yaml). The cockpit's topbar pill and drawer foot
# read /api/driver to patch the operator name and reg plate at boot
# instead of carrying a hardcoded "MAZ" label.
_DRIVER_YAML_PATH = Path('/opt/drifter/driver.yaml')


def _is_local_peer(peer: str) -> bool:
    """Hotspot-only ACL — same gate Phase 4.5 used for /api/ble/recent.
    BLE detection data shouldn't leak past 127.0.0.1 + the 10.42.0.0/24
    Wi-Fi hotspot."""
    return peer == '127.0.0.1' or peer.startswith('10.42.0.')


# ─── /api/arsenal aggregate (BE-3) ────────────────────────────────────
# One descriptor per foot-mode tool surfaced on the launcher board. The
# aggregate route folds these into a single payload the cockpit's
# openArsenal() overlay polls every 3s. `present` is derived HONESTLY:
#   * `unit` — the systemd unit whose `is-active` state (via the shared
#     /healthz services map) is a necessary condition for presence; None
#     for tools with no dedicated unit (e.g. ghost, which has no service
#     shipped here — it stays present:false until one exists).
#   * `hw_key` — when set, a latest_state key whose payload carries a
#     hardware-presence signal. A tool is only `present` when BOTH the
#     unit is active AND (if hw_key is set) the hardware probe agrees.
#     This is what stops the UI from ever rendering a green/up card for a
#     tool whose unit is dead or whose dongle/ESP32/camera is unplugged.
#   * `state_key` — latest_state key for the live status payload; its
#     `state` field (or a tool-specific honest default) is surfaced.
#   * `actions` — verbs the tool *would* expose (metadata only; the
#     START/STOP route is BE-4, not built here — the UI renders them
#     aria-disabled this stage).
_ARSENAL_TOOLS = [
    {'name': 'kismet',    'unit': 'drifter-kismet',       'tab': 'kismet',
     'state_key': 'wifi_devices',  'hw_key': None,
     'actions': ['start', 'stop']},
    {'name': 'marauder',  'unit': 'drifter-marauder',     'tab': 'marauder',
     'state_key': 'marauder_status', 'hw_key': 'marauder_status',
     'actions': ['start', 'stop']},
    {'name': 'flipper',   'unit': 'drifter-flipper',      'tab': 'flipper',
     'state_key': 'flipper_status', 'hw_key': 'flipper_status',
     'actions': ['start', 'stop']},
    {'name': 'wardrive',  'unit': 'drifter-wardrive',     'tab': 'wardrive',
     'state_key': 'wardrive_status', 'hw_key': None,
     'actions': ['start', 'stop']},
    {'name': 'wifi_audit', 'unit': 'drifter-wifi-audit',  'tab': 'wardrive',
     'state_key': 'wifi_audit', 'hw_key': None,
     'actions': ['start', 'stop']},
    {'name': 'flycatcher', 'unit': 'drifter-fly-catcher', 'tab': 'flycatcher',
     'state_key': 'state_fly_catcher', 'hw_key': None,
     'actions': ['start', 'stop']},
    {'name': 'ghost',     'unit': None,                   'tab': 'ghost',
     'state_key': 'ghost_status', 'hw_key': None,
     'actions': []},
    {'name': 'alpr',      'unit': 'drifter-alpr',         'tab': 'alpr',
     'state_key': 'alpr_plate', 'hw_key': None,
     'actions': ['start', 'stop']},
    {'name': 'vision',    'unit': 'drifter-vision',       'tab': 'vision',
     'state_key': 'vision_status', 'hw_key': 'vision_status',
     'actions': ['start', 'stop']},
    {'name': 'sentry',    'unit': 'drifter-sentry',       'tab': 'sentry',
     'state_key': 'sentry_status', 'hw_key': None,
     'actions': ['start', 'stop']},
    {'name': 'rf',        'unit': 'drifter-rf',           'tab': 'rf',
     'state_key': 'rf_spectrum', 'hw_key': None,
     'actions': ['start', 'stop']},
    {'name': 'rfaudio',   'unit': 'drifter-rfaudio',      'tab': 'rf',
     'state_key': 'rfaudio_status', 'hw_key': None,
     'actions': ['start', 'stop']},
]


def _arsenal_hw_present(tool: dict) -> bool:
    """Honest hardware-presence check for an arsenal tool.

    Returns True when the tool declares no hardware gate (`hw_key` is
    None) — unit-active is then sufficient. When a hw_key is set, the
    matching latest_state payload must NOT signal absent hardware:
      * marauder publishes state:'no_hardware' when no ESP32 is attached;
      * flipper/vision publish nothing (or an empty/disconnected status)
        until their device enumerates.
    Never fabricates presence — a missing payload reads as absent."""
    hw_key = tool.get('hw_key')
    if not hw_key:
        return True
    payload = state.latest_state.get(hw_key)
    if not isinstance(payload, dict) or not payload:
        return False
    st = str(payload.get('state', '')).lower()
    if st in ('no_hardware', 'absent', 'disconnected', 'not_connected'):
        return False
    # flipper publishes connected/state fields; treat an explicit
    # disconnected/false as absent without inventing a 'connected' default.
    if payload.get('connected') is False:
        return False
    return True


def _arsenal_state_str(tool: dict, present: bool) -> str:
    """Live status string for an arsenal tool — honest, never faked.

    Prefers the tool's published `state` field. Falls back to 'idle' only
    when the unit is present (active + hardware) but hasn't published a
    state yet; 'absent' when not present. No tool is ever reported 'up'
    while its unit is dead or its hardware is missing."""
    payload = state.latest_state.get(tool.get('state_key'))
    if isinstance(payload, dict):
        st = payload.get('state') or payload.get('status')
        if isinstance(st, str) and st.strip():
            return st.strip()
    return 'idle' if present else 'absent'


class DashboardHandler(SimpleHTTPRequestHandler):
    """Route HTTP requests to one of the small endpoint methods below."""

    # ─── GET ──────────────────────────────────────────────────────────
    # HEAD requests fall through SimpleHTTPRequestHandler.do_HEAD by
    # default, which only knows how to serve files — it doesn't see
    # the _EXACT_GET_ROUTES table, so /api/* and /map/* return 404 to
    # `curl -I`. Route HEAD through the same dispatcher; the response
    # body is sent (technically wasteful) but every client that uses
    # HEAD ignores it, and the alternative is duplicating every route.
    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        # Exact-path routes first (cheapest lookup).
        handler = self._EXACT_GET_ROUTES.get(parsed.path)
        if handler is not None:
            handler(self, parsed)
            return
        # Prefix routes for endpoints that carry a parameter in the path.
        if parsed.path.startswith('/api/mechanic/dtc/'):
            self._serve_dtc_lookup(parsed)
            return
        if parsed.path.startswith('/api/can/captures/'):
            self._serve_can_capture_file(parsed.path[len('/api/can/captures/'):])
            return
        # Vivi avatar assets — the 3D viewer page fetches its GLB plus any
        # texture / animation bundles from here. Path-traversal guarded in
        # _serve_avatar_asset.
        if parsed.path.startswith('/assets/'):
            self._serve_avatar_asset(parsed.path[len('/assets/'):])
            return
        # Static files served straight from disk.
        if parsed.path in _STATIC_FILES:
            self._serve_static(parsed.path)
            return
        self.send_error(404)

    # Route bodies — one method per endpoint. Most are one-liners.
    def _serve_dashboard_page(self, parsed):
        """Front door. Serves the cockpit (formerly /preview/cockpit) from
        disk so design iterations land without a service restart.
        Local-network only — same ACL as before."""
        peer = self.client_address[0] if self.client_address else ''
        if not _is_local_peer(peer):
            self.send_error(403, 'cockpit: local network only')
            return
        path = Path('/opt/drifter/ui/cockpit-preview.html')
        if not path.exists():
            self.send_error(503, 'cockpit not deployed')
            return
        try:
            self._serve_html(path.read_text(encoding='utf-8'))
        except OSError as e:
            self.send_error(500, f'cockpit read error: {e}')

    def _redirect_to_root(self, parsed):
        """Permanent redirect for URLs whose surface moved into the
        cockpit: /settings (now the cockpit's inline overlay) and the
        previous /preview/cockpit alias. Operator bookmarks survive."""
        self.send_response(301)
        self.send_header('Location', '/')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def _serve_preview_cockpit(self, parsed):
        """Deprecated alias — kept for backward compatibility on any
        client that bypasses the redirect (curl, etc.)."""
        peer = self.client_address[0] if self.client_address else ''
        if not _is_local_peer(peer):
            self.send_error(403, 'preview: local network only')
            return
        path = Path('/opt/drifter/ui/cockpit-preview.html')
        if not path.exists():
            self.send_error(404, 'preview not deployed')
            return
        try:
            self._serve_html(path.read_text(encoding='utf-8'))
        except OSError as e:
            self.send_error(500, f'preview read error: {e}')
    def _get_settings(self, parsed):          self._serve_json(load_settings())

    def _get_settings_schema(self, parsed):
        """Operator-facing settings schema. Drives the cockpit overlay
        render. Excludes internal-state flags (e.g. setup_complete) so
        they don't appear as user-toggleable controls. The full key set
        — including those flags — remains on GET /api/settings for the
        onboarding flow."""
        self._serve_json({
            'sections': SETTINGS_SECTIONS,
            'fields': SETTINGS_SCHEMA,
        })
    def _get_state(self, parsed):             self._serve_json(state.latest_state)
    def _get_hardware(self, parsed):          self._serve_json(check_hardware())
    def _get_rfaudio_status(self, parsed):
        """rfaudio status + live hardware-presence echo (BE-2).

        Start from the retained rfaudio status (verbatim, including any
        `error`), then merge in fresh RTL-SDR / speaker presence so the
        cockpit can gate tune/scan/test-tone honestly. Build a COPY —
        never mutate latest_state (it's the WS fan-out source)."""
        status = dict(state.latest_state.get('rfaudio_status', {}))
        sdr = probe_rtl_sdr()
        spk = probe_speaker()
        status['sdr_present'] = bool(sdr['connected'])
        status['speaker_present'] = bool(spk['connected'])
        status['sdr_action'] = sdr.get('action', '')
        status['speaker_action'] = spk.get('action', '')
        self._serve_json(status)

    def _get_recent_alerts(self, parsed):
        """Return the in-memory ring of recent alert messages, newest first."""
        self._serve_json({'alerts': list(reversed(state.recent_alerts))})

    def _get_recent_aircraft(self, parsed):
        """Aircraft snapshot from drifter-feeds, captured into latest_state.

        Returns the raw snapshot payload (aircraft list + origin + count)
        so the drawer can render whatever the ADS-B source last produced.
        Empty object if no snapshot has landed yet.
        """
        self._serve_json(state.latest_state.get('feeds_aircraft_snapshot', {}))

    def _get_rf_spectrum(self, parsed):
        """Latest spectrum sweep from drifter-rf (RTL-SDR)."""
        self._serve_json(state.latest_state.get('rf_spectrum', {}))

    def _get_rf_spectrum_summary(self, parsed):
        """Downsampled spectrum (≤256 bins) for the cockpit WS surface."""
        self._serve_json(state.latest_state.get('rf_spectrum_summary', {}))

    def _get_mechanic_history(self, parsed):
        """Return the current Mechanic chat ring buffer.

        Shape: {"turns": [{ts, role, content}, ...], "max_turns": N}.
        Always 200 — an empty ring is a legitimate state.
        """
        self._serve_json({
            'turns': _mechanic_history_snapshot(),
            'max_turns': MECHANIC_HISTORY_TURNS,
        })

    def _get_rf_adsb(self, parsed):
        """Latest RTL-SDR ADS-B scan (separate from /api/aircraft/recent
        which is the feeds.py ADSB.lol path)."""
        self._serve_json(state.latest_state.get('rf_adsb', {}))

    def _get_rf_classification(self, parsed):
        """Most recent N URH-NG classifier results (newest first).

        Drives the cockpit's "Signal Intel" sub-tile inside the RF panel.
        Empty list when no unknown signal has been classified yet — the
        cockpit renders "AWAITING UNKNOWN SIGNAL" in that state.

        Query: ?limit=<int> (default 50, capped at the ring size).
        """
        peer = self.client_address[0] if self.client_address else ''
        if not _is_local_peer(peer):
            self.send_error(403, 'rf classification: local network only')
            return
        try:
            qs = parse_qs(parsed.query or '')
            limit = int(qs.get('limit', [50])[0])
        except (ValueError, TypeError):
            limit = 50
        self._serve_json({
            'classifications': _snapshot_rf_classifications(limit),
            'ring_max': _RF_CLASSIFICATION_RING_MAX,
        })

    def _get_can_discovery(self, parsed):
        """Most recent CaringCaribou discovery responses (newest first)."""
        peer = self.client_address[0] if self.client_address else ''
        if not _is_local_peer(peer):
            self.send_error(403, 'can discovery: local network only')
            return
        self._serve_json({
            'discoveries': _snapshot_can_discoveries(),
            'ring_max': _CAN_DISCOVERY_RING_MAX,
        })

    def _get_can_captures(self, parsed):
        """List SavvyCAN-compatible CSV captures from
        /opt/drifter/state/can_captures/. Each entry: {name, size, ts}."""
        peer = self.client_address[0] if self.client_address else ''
        if not _is_local_peer(peer):
            self.send_error(403, 'can captures: local network only')
            return
        entries = []
        try:
            if _CAN_CAPTURE_DIR.exists():
                for p in sorted(_CAN_CAPTURE_DIR.glob('*.csv'),
                                key=lambda x: x.stat().st_mtime, reverse=True):
                    try:
                        st = p.stat()
                        entries.append({
                            'name': p.name,
                            'size': st.st_size,
                            'ts': st.st_mtime,
                        })
                    except OSError:
                        continue
        except OSError as e:
            log.debug("can_captures listing failed: %s", e)
        self._serve_json({'captures': entries})

    def _serve_can_capture_file(self, name: str):
        """Serve one CSV from the can_captures dir. Path-traversal-safe."""
        peer = self.client_address[0] if self.client_address else ''
        if not _is_local_peer(peer):
            self.send_error(403, 'can capture: local network only')
            return
        # Strict allowlist: only the basename, must match <digits>.csv.
        if not re.match(r'^\d{1,20}\.csv$', name or ''):
            self.send_error(400, 'invalid capture name')
            return
        path = _CAN_CAPTURE_DIR / name
        if not path.exists() or not path.is_file():
            self.send_error(404)
            return
        try:
            data = path.read_bytes()
        except OSError:
            self.send_error(500)
            return
        self.send_response(200)
        self.send_header('Content-Type', 'text/csv')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Content-Disposition',
                         f'attachment; filename="{name}"')
        self.end_headers()
        self.wfile.write(data)

    def _get_airspace_aircraft(self, parsed):
        """Enriched tar1090 aircraft snapshot. Falls through cleanly to {}
        when tar1090 is offline — the cockpit then falls back to
        /api/aircraft/recent."""
        peer = self.client_address[0] if self.client_address else ''
        if not _is_local_peer(peer):
            self.send_error(403, 'airspace: local network only')
            return
        self._serve_json(_snapshot_airspace())

    # ─── Arsenal read-side routes (BE-2, foot-mode toolkit) ───────────
    # All GET, all _is_local_peer gated, all read state.latest_state from
    # the `drifter/#` subscription. Each returns the live snapshot or an
    # HONEST empty/`no_hardware` shape — NEVER a fabricated 'up'/'idle'
    # status or demo rows when the tool/hardware is absent.

    def _get_kismet_devices(self, parsed):
        """Kismet Wi-Fi + BLE device snapshot.

        Reads drifter/wifi/devices + drifter/ble/devices. Honest empty
        shape {wifi:[],ble:[],ts:null} when neither has published — an
        empty REST poll is a legitimate state, not a fault."""
        peer = self.client_address[0] if self.client_address else ''
        if not _is_local_peer(peer):
            self.send_error(403, 'kismet: local network only')
            return
        wifi = state.latest_state.get('wifi_devices')
        ble = state.latest_state.get('ble_devices')
        if not isinstance(wifi, list):
            wifi = list(wifi.get('devices', [])) if isinstance(wifi, dict) else []
        if not isinstance(ble, list):
            ble = list(ble.get('devices', [])) if isinstance(ble, dict) else []
        ts = None
        for src in (state.latest_state.get('wifi_devices'),
                    state.latest_state.get('ble_devices')):
            if isinstance(src, dict) and src.get('ts') is not None:
                ts = src.get('ts')
        self._serve_json({'wifi': wifi, 'ble': ble, 'ts': ts})

    def _get_marauder_status(self, parsed):
        """ESP32 Marauder bridge status (retained drifter/marauder/status).

        Honest {state:'no_hardware'} when no ESP32 has ever announced —
        never fabricate a connected/idle state for absent hardware."""
        peer = self.client_address[0] if self.client_address else ''
        if not _is_local_peer(peer):
            self.send_error(403, 'marauder: local network only')
            return
        status = state.latest_state.get('marauder_status')
        if not isinstance(status, dict) or not status:
            status = {'state': 'no_hardware'}
        self._serve_json(status)

    def _get_marauder_scan(self, parsed):
        """Live Marauder scan ring for a single stream.

        ?stream=ap|sta|probe (default ap) & ?n=<int> (cap the returned
        rows, newest-kept). Reads drifter/marauder/scan/<stream>. Empty
        rows list when no scan has run — honest, never demo rows."""
        peer = self.client_address[0] if self.client_address else ''
        if not _is_local_peer(peer):
            self.send_error(403, 'marauder scan: local network only')
            return
        qs = parse_qs(parsed.query or '') if parsed is not None else {}
        stream = (qs.get('stream', ['ap'])[0] or 'ap').lower()
        if stream not in ('ap', 'sta', 'probe'):
            stream = 'ap'
        try:
            n = int(qs.get('n', [0])[0])
        except (ValueError, TypeError):
            n = 0
        snap = state.latest_state.get('marauder_scan_' + stream)
        if isinstance(snap, dict):
            rows = snap.get('rows') or snap.get('devices') or []
        elif isinstance(snap, list):
            rows = snap
        else:
            rows = []
        rows = list(rows)
        if n > 0:
            rows = rows[-n:]
        self._serve_json({'stream': stream, 'rows': rows})

    def _get_flycatcher_aircraft(self, parsed):
        """IMSI-catcher / fly-catcher airspace classification.

        Reads drifter/airspace/aircraft_classified + drifter/state/fly_catcher.
        Honest empty lists + null state when the detector is idle/absent."""
        peer = self.client_address[0] if self.client_address else ''
        if not _is_local_peer(peer):
            self.send_error(403, 'flycatcher: local network only')
            return
        classified = state.latest_state.get('airspace_aircraft_classified')
        if isinstance(classified, dict):
            classified = classified.get('aircraft') or classified.get('classified') or []
        elif not isinstance(classified, list):
            classified = []
        fc = state.latest_state.get('state_fly_catcher')
        if isinstance(fc, dict):
            aircraft = fc.get('aircraft') if isinstance(fc.get('aircraft'), list) else []
        else:
            aircraft = []
            fc = None
        self._serve_json({
            'aircraft': aircraft,
            'classified': list(classified),
            'state': fc,
        })

    def _get_ghost_status(self, parsed):
        """Counter-surveillance (ghost_protocol) posture.

        Reads drifter/ghost/{status,tracker,stingray,alpr,rf}. ghost has
        no service unit shipped here, so this returns honest empty lists +
        null status until a drifter-ghost.service exists — never faked."""
        peer = self.client_address[0] if self.client_address else ''
        if not _is_local_peer(peer):
            self.send_error(403, 'ghost: local network only')
            return

        def _aslist(key):
            v = state.latest_state.get(key)
            if isinstance(v, list):
                return v
            if isinstance(v, dict):
                inner = v.get(key.split('_', 1)[-1]) or v.get('items')
                return inner if isinstance(inner, list) else [v]
            return []

        status = state.latest_state.get('ghost_status')
        if not isinstance(status, dict):
            status = None
        self._serve_json({
            'status': status,
            'trackers': _aslist('ghost_tracker'),
            'stingray': _aslist('ghost_stingray'),
            'alpr': _aslist('ghost_alpr'),
            'rf': _aslist('ghost_rf'),
        })

    def _get_alpr_plates(self, parsed):
        """ALPR plate-read ring (drifter/vision/alpr/plate).

        Honest empty list when no camera/model has produced a read."""
        peer = self.client_address[0] if self.client_address else ''
        if not _is_local_peer(peer):
            self.send_error(403, 'alpr: local network only')
            return
        snap = state.latest_state.get('alpr_plate')
        if isinstance(snap, dict):
            plates = snap.get('plates') if isinstance(snap.get('plates'), list) else [snap]
        elif isinstance(snap, list):
            plates = snap
        else:
            plates = []
        self._serve_json({'plates': list(plates)})

    def _get_vision_status(self, parsed):
        """Vision pipeline status + object detections.

        Reads drifter/vision/{status,object}. Honest null status + empty
        objects when no camera/model is running."""
        peer = self.client_address[0] if self.client_address else ''
        if not _is_local_peer(peer):
            self.send_error(403, 'vision: local network only')
            return
        status = state.latest_state.get('vision_status')
        if not isinstance(status, dict):
            status = None
        objs = state.latest_state.get('vision_object')
        if isinstance(objs, dict):
            objects = objs.get('objects') if isinstance(objs.get('objects'), list) else [objs]
        elif isinstance(objs, list):
            objects = objs
        else:
            objects = []
        self._serve_json({'status': status, 'objects': list(objects)})

    def _get_sentry_status(self, parsed):
        """Sentry (impact/tamper) status (retained drifter/sentry/status).

        Honest empty shape with null fields when the sentry has never
        published — never fabricate an armed/disarmed posture."""
        peer = self.client_address[0] if self.client_address else ''
        if not _is_local_peer(peer):
            self.send_error(403, 'sentry: local network only')
            return
        status = state.latest_state.get('sentry_status')
        if not isinstance(status, dict) or not status:
            status = {'armed': None, 'threshold_g': None,
                      'auto_arm': None, 'ts': None}
        self._serve_json(status)

    def _get_arsenal(self, parsed):
        """Foot-mode arsenal aggregate (BE-3).

        {ts, mode, tools:[{name, unit, present, state, live_meta, actions}]}

        `present` is derived from the SAME service map /healthz computes
        (we call the shared _healthz_payload() so service-active detection
        is never reimplemented differently) AND from hardware-presence
        signals already in latest_state. A tool is present ONLY when its
        unit is active and its hardware (when it has any) agrees — so the
        UI renders a disabled card for an inactive/absent tool and NEVER a
        fabricated 'up'. `actions` is metadata only; the START/STOP route
        is BE-4 and is not built here."""
        peer = self.client_address[0] if self.client_address else ''
        if not _is_local_peer(peer):
            self.send_error(403, 'arsenal: local network only')
            return
        # Reuse /healthz's authoritative services map + mode rather than
        # re-running systemctl with different logic.
        payload, _ = _healthz_payload()
        services = payload.get('services', {}) or {}
        mode = payload.get('mode', DEFAULT_MODE)

        tools = []
        for spec in _ARSENAL_TOOLS:
            unit = spec.get('unit')
            # No-unit tools (e.g. ghost) can never be present until a
            # service ships; unit-bearing tools follow the healthz reading.
            unit_active = bool(services.get(unit)) if unit else False
            hw_ok = _arsenal_hw_present(spec)
            present = unit_active and hw_ok
            tools.append({
                'name':    spec['name'],
                'unit':    unit,
                'tab':     spec.get('tab'),
                'present': present,
                'state':   _arsenal_state_str(spec, present),
                'live_meta': {
                    'unit_active':  unit_active,
                    'hardware_ok':  hw_ok,
                },
                'actions': list(spec.get('actions', [])),
            })
        self._serve_json({
            'ts':    payload.get('ts', time.time()),
            'mode':  mode,
            'tools': tools,
        })

    def _get_rf_emergency(self, parsed):
        """Latest emergency-band activity scan."""
        self._serve_json(state.latest_state.get('rf_emergency', {}))

    def _get_flipper_status(self, parsed):
        """Flipper Zero connection + firmware status."""
        self._serve_json(state.latest_state.get('flipper_status', {}))

    def _get_flipper_hardware(self, parsed):
        """Latest add-on hardware detection from flipper_bridge.

        Source of truth is flipper_bridge.hardware_state, populated by the
        periodic probe_hardware() call. We also fall back to the retained
        drifter/flipper/hardware MQTT payload via state.latest_state so a
        dashboard reload survives a bridge restart.
        """
        payload = None
        try:
            from flipper_bridge import get_hardware_state
            payload = get_hardware_state()
        except Exception as e:
            log.debug(f"flipper hardware import failed: {e}")
        if not payload or not payload.get('ts'):
            payload = state.latest_state.get('flipper_hardware') or {
                'ts': 0.0, 'module': 'none', 'capabilities': [],
            }
        self._serve_json(payload)

    def _get_flipper_captures(self, parsed):
        """Recent sub-GHz captures from the Flipper monitor, newest first.

        Each entry is whatever flipper_bridge.run_subghz_monitor
        published: frequency, modulation, raw frame text, ts.

        Augmentation: every row gets `local_sub_path`/`on_flipper_path`
        if a matching .sub artifact exists on disk. The cockpit uses these
        fields to enable the REPLAY button (no path → no replay).
        """
        # Live ring buffer rows.
        live = [dict(c) for c in reversed(state.recent_flipper_captures)]
        # Persisted artifacts keyed by id — merge into matching live rows
        # and surface the rest as standalone history entries.
        persisted_by_id = {}
        persisted_extras = []
        try:
            from flipper_bridge import list_persisted_captures
            for art in list_persisted_captures():
                persisted_by_id[art['id']] = art
        except Exception as e:
            log.debug(f"persisted-captures lookup failed: {e}")

        seen_ids = set()
        for row in live:
            row_id = row.get('id')
            if row_id and row_id in persisted_by_id:
                art = persisted_by_id[row_id]
                row['local_sub_path'] = art.get('local_sub_path')
                row['on_flipper_path'] = art.get('on_flipper_path')
                seen_ids.add(row_id)
        # Any persisted .sub not present in the live ring shows up as a
        # standalone history row (after a restart the ring is empty but
        # the captures on disk persist).
        for art_id, art in persisted_by_id.items():
            if art_id in seen_ids:
                continue
            persisted_extras.append({
                'id': art_id,
                'freq_hz': art.get('freq_hz'),
                'frequency': (f"{art['freq_hz']/1e6:.3f} MHz"
                              if art.get('freq_hz') else None),
                'ts': art.get('ts'),
                'local_sub_path': art.get('local_sub_path'),
                'on_flipper_path': art.get('on_flipper_path'),
            })
        captures = live + persisted_extras
        captures.sort(key=lambda c: c.get('ts') or 0, reverse=True)
        self._serve_json({'captures': captures})

    def _get_tpms_assignments(self, parsed):
        """Read /opt/drifter/state/tpms_assignments.json verbatim.

        Returns the persisted {FL|FR|RL|RR: sensor_id} mapping the
        rf_monitor.tpms_assign_corner handler writes. Empty object before
        any sensor has been paired.
        """
        if not _TPMS_ASSIGNMENTS_PATH.exists():
            self._serve_json({})
            return
        try:
            self._serve_json(json.loads(
                _TPMS_ASSIGNMENTS_PATH.read_text()))
        except (OSError, json.JSONDecodeError):
            self._serve_json({})

    def _get_flipper_results(self, parsed):
        """Recent command outcomes from the Flipper bridge — needed to
        surface HIGH-risk confirmation prompts and command success."""
        self._serve_json({'results': list(reversed(state.recent_flipper_results))})

    def _get_recent_dtcs(self, parsed):
        """Current DTCs (Diagnostic Trouble Codes) from drifter-diag.

        Returns the latest payload from drifter/diag/dtc — typically a
        list of {code, severity, ts, description?} entries. Empty if
        no OBD scan has run or no faults present.
        """
        self._serve_json(state.latest_state.get('diag_dtc', {}))

    def _get_recent_trip(self, parsed):
        """Live trip-computer state from drifter-trip.

        Returns the three trip topics merged into a single payload:
        stats (distance/duration/avg consumption/speed), fuel (current
        and average L/100km), cost (cumulative + fuel price). Empty
        fields if drifter-trip hasn't published yet.
        """
        self._serve_json({
            'stats': state.latest_state.get('trip_stats', {}),
            'fuel':  state.latest_state.get('trip_fuel', {}),
            'cost':  state.latest_state.get('trip_cost', {}),
        })

    def _get_recent_tpms(self, parsed):
        """4-corner TPMS snapshot from drifter-rf.

        Returns the rf_monitor.TpmsState.get_snapshot() payload — one
        entry per position (fl/fr/rl/rr) with pressure_psi, temp_c, ts,
        stale flag. Empty object if no snapshot has landed yet (no RTL-SDR
        plugged in, or no sensors learned).
        """
        self._serve_json(state.latest_state.get('rf_tpms_snapshot', {}))
    def _get_report(self, parsed):            self._serve_json(state.latest_report)

    def _get_feeds_summary(self, parsed):
        """Read /opt/drifter/state/feeds_summary.json (written every 30s
        by drifter-feeds). Returns {} if absent so the dashboard can render
        a clean empty state before the first poll cycle lands."""
        path = Path('/opt/drifter/state/feeds_summary.json')
        if not path.exists():
            self._serve_json({})
            return
        try:
            self._serve_json(json.loads(path.read_text()))
        except (OSError, json.JSONDecodeError):
            self._serve_json({})

    def _get_radar_gif(self, parsed):
        """Serve /opt/drifter/state/radar.gif written by drifter-feeds."""
        path = Path('/opt/drifter/state/radar.gif')
        if not path.exists():
            self.send_error(404, 'radar not yet fetched')
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', 'image/gif')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


    def _get_ble_recent(self, parsed):
        """Last N BLE detections from the Phase 4.7 ble_history.db.
        Same hotspot-only ACL as /api/ble/history."""
        peer = self.client_address[0] if self.client_address else ''
        if not _is_local_peer(peer):
            self.send_error(403, 'BLE recent: local network only')
            return
        try:
            limit = int(parse_qs(parsed.query).get('limit', ['20'])[0])
        except ValueError:
            limit = 20
        limit = max(1, min(limit, 200))
        rows = self._ble_query(limit=limit)
        if rows is None:
            self._serve_json({'detections': []})
            return
        # Preserve the Phase 4.5 wire shape (gps as nested object,
        # advertised_name field) so the existing dashboard tile keeps
        # rendering without a JS edit.
        out = [{
            'ts':                r['ts'],
            'target':            r['target'],
            'mac':               r['mac'],
            'rssi':              r['rssi'],
            'gps': ({'lat': r['lat'], 'lng': r['lng']}
                    if r['lat'] is not None else None),
            'manufacturer_id':   r['manufacturer_id'],
            'advertised_name':   r['adv_name'],
            'is_alert':          r['is_alert'],
        } for r in rows]
        self._serve_json({'detections': out})

    def _ble_query(self, **kwargs):
        """Open the history DB read-only, run query_history, close.
        Returns None if the DB doesn't exist yet (fresh install before
        any detections)."""
        from pathlib import Path as _P
        if not _P(_BLE_HISTORY_DB).exists():
            return None
        try:
            conn = ble_history.open_db(_P(_BLE_HISTORY_DB))
            try:
                return ble_history.query_history(conn, **kwargs)
            finally:
                conn.close()
        except Exception as e:
            log.warning(f"ble_history query failed: {e}")
            return None

    def _get_ble_history(self, parsed):
        """Filterable history read. Hotspot-only.
        Query params: target, since, until, drive_id, limit (default 200,
        cap 2000)."""
        peer = self.client_address[0] if self.client_address else ''
        if not _is_local_peer(peer):
            self.send_error(403, 'BLE history: local network only')
            return
        q = parse_qs(parsed.query)
        kw: dict = {}
        for key in ('target', 'drive_id'):
            v = q.get(key, [None])[0]
            if v:
                kw[key] = v
        for key in ('since', 'until'):
            v = q.get(key, [None])[0]
            if v:
                try:
                    kw[key] = float(v)
                except ValueError:
                    self.send_error(400, f'invalid {key}')
                    return
        try:
            kw['limit'] = int(q.get('limit', ['200'])[0])
        except ValueError:
            kw['limit'] = 200
        rows = self._ble_query(**kw)
        if rows is None:
            self._serve_json({'detections': [], 'count': 0,
                              'drive_id': ble_history.current_drive_id()
                                          if Path(_BLE_HISTORY_DB).parent.exists()
                                          else None})
            return
        self._serve_json({
            'detections': rows,
            'count': len(rows),
            'drive_id': ble_history.current_drive_id(),
        })

    def _get_ble_drives(self, parsed):
        """Per-drive summary."""
        peer = self.client_address[0] if self.client_address else ''
        if not _is_local_peer(peer):
            self.send_error(403, 'BLE drives: local network only')
            return
        from pathlib import Path as _P
        if not _P(_BLE_HISTORY_DB).exists():
            self._serve_json({'drives': []})
            return
        try:
            conn = ble_history.open_db(_P(_BLE_HISTORY_DB))
            try:
                drives = ble_history.query_drives(conn)
            finally:
                conn.close()
        except Exception as e:
            log.warning(f"ble_history drives query failed: {e}")
            self._serve_json({'drives': [], 'error': str(e)})
            return
        self._serve_json({'drives': drives})

    def _get_ble_persistent(self, parsed):
        """Phase 4.8 — persistent-contact (follower) analysis. Hotspot-only.
        Query params:
          window=24h|7d|30d|all (default 7d)
          min_tier=weak|medium|high (default weak)
        Compute on demand. Logs a warning if window=30d takes >2s."""
        peer = self.client_address[0] if self.client_address else ''
        if not _is_local_peer(peer):
            self.send_error(403, 'BLE persistent: local network only')
            return
        q = parse_qs(parsed.query)
        window = q.get('window', ['7d'])[0]
        min_tier = q.get('min_tier', ['weak'])[0]

        windows = {'24h': 86400.0, '7d': 7 * 86400.0,
                   '30d': 30 * 86400.0, 'all': None}
        if window not in windows:
            self.send_error(400, 'invalid window')
            return
        if min_tier not in ('weak', 'medium', 'high'):
            self.send_error(400, 'invalid min_tier')
            return

        from pathlib import Path as _P
        now = time.time()
        if not _P(_BLE_HISTORY_DB).exists():
            self._serve_json({
                'window': window, 'computed_at': now,
                'contacts': [], 'count': 0, 'noise_excluded': 0,
            })
            return

        since = (now - windows[window]) if windows[window] is not None else None
        t0 = time.time()
        try:
            conn = ble_history.open_db(_P(_BLE_HISTORY_DB))
            try:
                contacts, noise = ble_persistence.score_persistent_contacts(
                    conn, since_ts=since, until_ts=now,
                )
            finally:
                conn.close()
        except Exception as e:
            log.warning(f"persistent-contacts compute failed: {e}")
            self._serve_json({
                'window': window, 'computed_at': now,
                'contacts': [], 'count': 0, 'noise_excluded': 0,
                'error': str(e),
            })
            return
        elapsed = time.time() - t0
        if window == '30d' and elapsed > 2.0:
            log.warning(
                f"/api/ble/persistent {window} took {elapsed:.2f}s — "
                "consider caching"
            )

        tier_rank = {'weak': 0, 'medium': 1, 'high': 2}
        threshold = tier_rank[min_tier]
        filtered = [c for c in contacts if tier_rank[c['tier']] >= threshold]
        self._serve_json({
            'window':         window,
            'computed_at':    now,
            'contacts':       filtered,
            'count':          len(filtered),
            'noise_excluded': noise,
        })

    def _get_ble_map(self, parsed):
        """Self-contained Leaflet map of the last 24h of BLE detections.
        Hotspot-only — same ACL as the API endpoints."""
        peer = self.client_address[0] if self.client_address else ''
        if not _is_local_peer(peer):
            self.send_error(403, 'BLE map: local network only')
            return
        self._serve_html(BLE_MAP_HTML)

    def _get_mechanic_advice(self, parsed):
        """Alert-click handler: feeds the alert text into the corpus and
        returns the top 3 ranked passages. The dashboard renders the
        passage bodies as bullet lines under the alert banner."""
        msg = parse_qs(parsed.query).get('alert', [''])[0]
        hits = corpus_search(msg, k=3, min_similarity=0.4) if msg else []
        advice = [{
            'text':   (h.get('content') or '').strip().splitlines()[0][:200],
            'source': h.get('source'),
            'topic':  h.get('topic'),
            'score':  round(h.get('score', 0), 3),
        } for h in hits]
        self._serve_json({'alert': msg, 'advice': advice})

    def _get_sessions(self, parsed):
        try:
            import db as _db
            self._serve_json(_db.get_recent_sessions(10))
        except Exception:
            self._serve_json([])

    def _get_reports(self, parsed):
        try:
            import db as _db
            self._serve_json(_db.get_recent_reports(10))
        except Exception:
            self._serve_json([])

    def _get_wardrive(self, parsed):
        self._serve_json({
            'wifi':      state.latest_state.get('wardrive_wifi', {}),
            'bluetooth': state.latest_state.get('wardrive_bt', {}),
            'adsb':      state.latest_state.get('rf_adsb', {}),
        })

    def _get_healthz(self, parsed):
        """Fleet contract healthz: 200 if all services active, 503 if any failed."""
        payload, http_status = _healthz_payload()
        body = json.dumps(payload, default=str).encode()
        self.send_response(http_status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_dtc_lookup(self, parsed):
        """DTC click handler — corpus first (full description, ECU action,
        likely causes), legacy XTYPE_DTC_LOOKUP as a tiny built-in fallback
        for codes the corpus hasn't been rebuilt with."""
        code = parsed.path.rsplit('/', 1)[-1].upper()
        if not _DTC_RE.match(code):
            self.send_error(400, 'Invalid DTC code')
            return
        hit = dtc_lookup(code)
        if hit:
            self._serve_json({
                'code':    code,
                'topic':   hit.get('topic'),
                'content': (hit.get('content') or '').strip(),
                'source':  hit.get('source'),
            })
            return
        info = XTYPE_DTC_LOOKUP.get(code, {})
        self._serve_json({'code': code, **info})

    def _get_driver(self, parsed):
        """Driver profile for the cockpit topbar/foot.

        Reads /opt/drifter/driver.yaml and returns ONLY a small whitelist
        of fields (preferred_name, name, registration_plate). Other keys
        — e.g. home_postcode, address, phone — must never leak into the
        dashboard surface. Returns 200 with null values on missing/bad
        file rather than 500-ing the cockpit page-init fetch.
        """
        payload = {'preferred_name': None, 'name': None, 'registration_plate': None}
        try:
            if _DRIVER_YAML_PATH.exists():
                doc = yaml.safe_load(_DRIVER_YAML_PATH.read_text(encoding='utf-8'))
                if isinstance(doc, dict):
                    pn = doc.get('preferred_name')
                    nm = doc.get('name')
                    rp = doc.get('registration_plate')
                    payload['preferred_name'] = pn if isinstance(pn, str) and pn.strip() else None
                    payload['name'] = nm if isinstance(nm, str) and nm.strip() else None
                    payload['registration_plate'] = (
                        rp if isinstance(rp, str) and rp.strip() else None
                    )
        except (OSError, yaml.YAMLError) as e:
            log.debug("driver.yaml read failed: %s", e)
        self._serve_json(payload)

    def _serve_static(self, path):
        disk_path, content_type, disposition = _STATIC_FILES[path]
        f = Path(disk_path)
        if not f.exists():
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        if disposition:
            self.send_header('Content-Disposition', disposition)
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(f.read_bytes())

    # ─── Vivi avatar viewer (3D Three.js page + GLB assets) ──────────
    # The viewer is a single HTML file in src/. Its model + textures live
    # under assets/. In dev the repo sits under the user's home; in
    # production install.sh drops a copy at /opt/drifter/{src,assets}.
    def _serve_avatar_page(self, parsed):
        candidates = [
            Path(__file__).resolve().parent / 'vivi_avatar.html',
            Path('/opt/drifter/src/vivi_avatar.html'),
        ]
        for p in candidates:
            if p.exists() and p.is_file():
                try:
                    self._serve_html(p.read_text(encoding='utf-8'))
                except OSError as e:
                    self.send_error(500, f'avatar read error: {e}')
                return
        self.send_error(404, 'vivi_avatar.html not deployed')

    def _serve_avatar_asset(self, rel):
        # Path-traversal guard: reject empty, absolute, or '..' segments.
        if not rel or rel.startswith('/') or '..' in rel.split('/'):
            self.send_error(400, 'bad asset path')
            return
        mime = {
            '.glb':  'model/gltf-binary',
            '.gltf': 'model/gltf+json',
            '.bin':  'application/octet-stream',
            '.png':  'image/png',
            '.jpg':  'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.webp': 'image/webp',
            '.wav':  'audio/wav',
            '.mp3':  'audio/mpeg',
            '.js':   'application/javascript',
            '.css':  'text/css',
            '.json': 'application/json',
        }
        bases = [
            Path(__file__).resolve().parent.parent / 'assets',
            Path('/opt/drifter/assets'),
        ]
        for base in bases:
            try:
                base_resolved = base.resolve()
            except (FileNotFoundError, OSError):
                continue
            candidate = (base / rel).resolve()
            # Stay inside the base after symlink resolution.
            if not str(candidate).startswith(str(base_resolved)):
                continue
            if candidate.exists() and candidate.is_file():
                ct = mime.get(candidate.suffix.lower(), 'application/octet-stream')
                data = candidate.read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', ct)
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'public, max-age=3600')
                self.end_headers()
                self.wfile.write(data)
                return
        self.send_error(404, f'asset not found: {rel}')

    # ─── POST ─────────────────────────────────────────────────────────
    def do_POST(self) -> None:
        if self.path == '/api/analyse':
            self._post_analyse()
            return
        if self.path == '/api/query':
            self._post_query()
            return
        if self.path == '/api/query/stream':
            self._post_query_stream()
            return
        if self.path == '/api/settings':
            self._post_settings()
            return
        if self.path.startswith('/api/mode/'):
            self._post_mode(self.path[len('/api/mode/'):])
            return
        if self.path == '/api/vivi/reset':
            self._post_vivi_reset()
            return
        if self.path == '/api/vivi/conversation_mode':
            self._post_vivi_conversation_mode()
            return
        if self.path == '/api/vivi/query':
            self._post_vivi_query()
            return
        if self.path == '/api/flipper/command':
            self._post_flipper_command()
            return
        if self.path == '/api/rfaudio/command':
            self._post_rfaudio_command()
            return
        if self.path == '/api/rf/command':
            self._post_rf_command()
            return
        if self.path == '/api/can/command':
            self._post_can_command()
            return
        if self.path == '/api/gps/manual':
            self._post_gps_manual()
            return
        if self.path == '/api/voice/listen_now':
            self._post_voice_listen_now()
            return
        self.send_error(404)

    def _post_voice_listen_now(self):
        """Trigger an immediate listen-once cycle in drifter-voicein by
        publishing to drifter/voice/listen_now (the topic voice_input.py
        already subscribes to and which drifter-vivi already uses in
        conversation mode). Body is ignored — an optional {ts} may be
        supplied but the handler always stamps its own.

        Local-network only (hotspot ACL). 503 when MQTT is offline so the
        operator UI can flash a failure indicator instead of silently
        "succeeding".
        """
        peer = self.client_address[0] if self.client_address else ''
        if not _is_local_peer(peer):
            self.send_error(403, 'voice: local network only')
            return
        if state.mqtt_client is None:
            self.send_error(503, 'mqtt offline')
            return
        topic = TOPICS.get('voice_listen_now', 'drifter/voice/listen_now')
        try:
            state.mqtt_client.publish(
                topic,
                json.dumps({'ts': time.time()}),
                qos=0,
                retain=False,
            )
        except Exception as e:
            log.warning("voice listen_now publish failed: %s", e)
            self.send_error(503, 'publish failed')
            return
        self._serve_json({'ok': True})

    def _post_gps_manual(self):
        """Accept a browser-geolocation fix from the tethered phone and
        drop it into /opt/drifter/state/gps.json so feeds.origin() and
        every downstream consumer treat it as the authoritative position.

        Body: {"lat": float, "lng": float, "accuracy_m": float?, "ts": float?}
        Local-network only; the hotspot is the only intended client.

        Also republishes to drifter/gps/fix so the cockpit's existing
        MQTT-driven map follow path fires immediately, without waiting
        for the 30s feeds-summary cycle.
        """
        peer = self.client_address[0] if self.client_address else ''
        if not _is_local_peer(peer):
            self.send_error(403, 'gps manual: local network only')
            return
        try:
            length = int(self.headers.get('Content-Length') or 0)
            length = min(length, MAX_POST_BODY)
            raw = self.rfile.read(length) if length else b'{}'
            body = json.loads(raw or b'{}')
        except (ValueError, json.JSONDecodeError):
            self.send_error(400, 'invalid JSON body')
            return
        if not isinstance(body, dict):
            self.send_error(400, 'body must be a JSON object')
            return
        try:
            lat = float(body['lat'])
            lng = float(body.get('lng', body.get('lon')))
        except (KeyError, TypeError, ValueError):
            self.send_error(400, 'body requires numeric lat and lng')
            return
        if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lng <= 180.0):
            self.send_error(400, 'lat/lng out of range')
            return
        # Accuracy gate. Browsers without a GPS chip (or with location
        # disabled) fall back to Wi-Fi/IP geolocation, which reports
        # 1km–50km error. A 25 km IP-fix was being accepted as a real
        # position and downstream consumers (feeds, cockpit map) treated
        # it as the vehicle's location — fabricating ADS-B and map
        # context for a city the Pi has never been in.
        try:
            accuracy_m = float(body['accuracy_m'])
        except (KeyError, TypeError, ValueError):
            self.send_error(400, 'body requires numeric accuracy_m')
            return
        if not (0.0 < accuracy_m <= GPS_MAX_ACCURACY_M):
            self.send_error(400,
                f'accuracy {accuracy_m:.0f}m exceeds {GPS_MAX_ACCURACY_M:.0f}m '
                'threshold — not a real fix (likely IP-based geolocation)')
            return
        now = time.time()
        fix = {
            'lat': lat,
            'lng': lng,
            'lon': lng,
            'fix': True,
            'mode': 2,
            'ts': now,
            'source': 'browser',
            'accuracy_m': accuracy_m,
        }
        try:
            tmp = _GPS_STATE_PATH.with_suffix('.json.tmp')
            tmp.write_text(json.dumps(fix))
            tmp.replace(_GPS_STATE_PATH)
        except OSError as e:
            log.warning("gps manual write failed: %s", e)
            self.send_error(500, 'failed to persist fix')
            return
        if state.mqtt_client is not None:
            try:
                state.mqtt_client.publish(
                    'drifter/gps/fix', json.dumps(fix), retain=True)
            except Exception as e:
                log.warning("gps manual mqtt publish failed: %s", e)
        self._serve_json({'ok': True, 'lat': lat, 'lng': lng, 'ts': now})

    def _post_flipper_command(self):
        """Forward a JSON body to drifter/flipper/command via MQTT.

        The bridge runs its own risk classifier (LOW/MEDIUM/HIGH/BLOCKED)
        and gates HIGH-risk commands behind a {command:'confirm', id:...}
        round-trip. This handler does not re-classify — it relays. The
        only thing it enforces here is the local-network ACL.

        Body shape:
          {"command": "subghz tx_from_file /ext/subghz/test.sub",
           "id": "client-generated-uuid"}
          {"command": "subghz_monitor_start"}
          {"command": "subghz_monitor_stop"}
          {"command": "confirm", "id": "<pending-id>"}
        """
        peer = self.client_address[0] if self.client_address else ''
        if not _is_local_peer(peer):
            self.send_error(403, 'flipper: local network only')
            return
        try:
            length = int(self.headers.get('Content-Length') or 0)
            length = min(length, MAX_POST_BODY)
            raw = self.rfile.read(length) if length else b'{}'
            body = json.loads(raw or b'{}')
        except (ValueError, json.JSONDecodeError):
            self.send_error(400, 'invalid JSON body')
            return
        if not isinstance(body, dict) or not body.get('command'):
            self.send_error(400, 'body requires a non-empty "command" string')
            return
        cmd = body.get('command')
        # Bare CLI commands (e.g. "hw info") still pass through to the bridge
        # — only structured workflow commands are allowlisted. The bridge's
        # risk classifier is the second gate.
        if (' ' not in cmd) and (cmd not in _FLIPPER_COMMANDS):
            self.send_error(400, 'command')
            return
        if cmd == 'subghz_replay':
            capture_id = body.get('capture_id')
            if not isinstance(capture_id, str) or not capture_id.strip():
                self.send_error(400, 'capture_id')
                return
        ok = False
        if state.mqtt_client is not None:
            try:
                state.mqtt_client.publish(
                    'drifter/flipper/command',
                    json.dumps(body),
                    qos=1,
                )
                ok = True
            except Exception as e:
                log.warning("flipper command publish failed: %s", e)
        self._serve_json({'ok': ok, 'published': 'drifter/flipper/command'})

    def _post_rfaudio_command(self):
        """Forward a JSON body to drifter/rfaudio/command via MQTT.
        Body shape matches the rfaudio.py command contract:
          {"action": "start", "freq_mhz": 476.525, "mode": "nfm", "gain": 0}
          {"action": "stop"} | {"action": "scan"} |
          {"action": "test_tone"} | {"action": "list_bands"}
        Returns {"ok": bool, "published": <topic>}."""
        peer = self.client_address[0] if self.client_address else ''
        if not _is_local_peer(peer):
            self.send_error(403, 'rfaudio: local network only')
            return
        try:
            length = int(self.headers.get('Content-Length') or 0)
            length = min(length, MAX_POST_BODY)
            raw = self.rfile.read(length) if length else b'{}'
            body = json.loads(raw or b'{}')
        except (ValueError, json.JSONDecodeError):
            self.send_error(400, 'invalid JSON body')
            return
        if not isinstance(body, dict) or 'action' not in body:
            self.send_error(400, 'body must be a JSON object with an "action" field')
            return
        if body.get('action') not in _RFAUDIO_ACTIONS:
            self.send_error(400, 'unknown action')
            return
        ok = False
        if state.mqtt_client is not None:
            try:
                state.mqtt_client.publish(
                    'drifter/rfaudio/command',
                    json.dumps(body),
                )
                ok = True
            except Exception as e:
                log.warning("rfaudio command publish failed: %s", e)
        self._serve_json({'ok': ok, 'published': 'drifter/rfaudio/command'})

    def _post_rf_command(self):
        """Forward a JSON body to drifter/rf/command via MQTT.

        rf_monitor.on_message() consumes the published payload and routes by
        the 'command' field. We re-validate the allowlist here so the only
        thing the WAN-facing surface can publish to that topic is the small
        set of commands the cockpit's preset buttons emit.

        Body shape:
          {"command": "tpms_learn_start"}
          {"command": "tpms_learn_stop"}
          {"command": "tpms_auto_assign"}
          {"command": "tpms_assign", "assignments": {"<sensor_id>": "fl", ...}}
          {"command": "pause_rtl_433"}
          {"command": "resume_rtl_433"}
        Returns {"ok": bool, "published": <topic>}."""
        peer = self.client_address[0] if self.client_address else ''
        if not _is_local_peer(peer):
            self.send_error(403, 'rf command: local network only')
            return
        try:
            length = int(self.headers.get('Content-Length') or 0)
            length = min(length, MAX_POST_BODY)
            raw = self.rfile.read(length) if length else b'{}'
            body = json.loads(raw or b'{}')
        except (ValueError, json.JSONDecodeError):
            self.send_error(400, 'invalid JSON body')
            return
        if not isinstance(body, dict) or 'command' not in body:
            self.send_error(400, 'command')
            return
        cmd = body.get('command')
        if cmd not in _RF_COMMANDS:
            self.send_error(400, 'command')
            return
        if cmd == 'tpms_assign':
            assignments = body.get('assignments')
            if not isinstance(assignments, dict) or not assignments:
                self.send_error(400, 'assignments')
                return
        if cmd == 'tpms_assign_corner':
            sid = body.get('sensor_id')
            corner = (body.get('corner') or '').upper()
            if not isinstance(sid, str) or not sid.strip():
                self.send_error(400, 'sensor_id')
                return
            if corner not in {'FL', 'FR', 'RL', 'RR'}:
                self.send_error(400, 'corner')
                return
        ok = False
        if state.mqtt_client is not None:
            try:
                state.mqtt_client.publish(
                    'drifter/rf/command',
                    json.dumps(body),
                )
                ok = True
            except Exception as e:
                log.warning("rf command publish failed: %s", e)
        self._serve_json({'ok': ok, 'published': 'drifter/rf/command'})

    def _post_can_command(self):
        """Forward a JSON body to drifter/can/command via MQTT.

        can_discovery.py consumes the published payload and routes by the
        'command' field. Same fail-closed allowlist + JSON validation
        pattern as _post_rf_command — the dashboard surface can only
        publish the small set the cockpit drawer emits.

        Body shape:
          {"command": "discover_ecus"}
          {"command": "list_services", "ecu_id": 2016}    # 0x7E0
          {"command": "dump_dids",     "ecu_id": 2016}
          {"command": "fuzz_range", "id_start": 1792, "id_end": 2047}
        Returns {"ok": bool, "published": <topic>}.
        """
        peer = self.client_address[0] if self.client_address else ''
        if not _is_local_peer(peer):
            self.send_error(403, 'can command: local network only')
            return
        try:
            length = int(self.headers.get('Content-Length') or 0)
            length = min(length, MAX_POST_BODY)
            raw = self.rfile.read(length) if length else b'{}'
            body = json.loads(raw or b'{}')
        except (ValueError, json.JSONDecodeError):
            self.send_error(400, 'invalid JSON body')
            return
        if not isinstance(body, dict) or 'command' not in body:
            self.send_error(400, 'command')
            return
        cmd = body.get('command')
        if cmd not in _CAN_COMMANDS:
            self.send_error(400, 'command')
            return
        # Per-command shape checks. We accept integer IDs as either an int
        # or a hex/decimal string; can_discovery.py normalises further.
        def _int_ok(v):
            if isinstance(v, int):
                return True
            if isinstance(v, str) and v.strip():
                try:
                    int(v, 0)
                    return True
                except ValueError:
                    try:
                        int(v, 16)
                        return True
                    except ValueError:
                        return False
            return False
        if cmd in ('list_services', 'dump_dids'):
            if not _int_ok(body.get('ecu_id')):
                self.send_error(400, 'ecu_id')
                return
        if cmd == 'fuzz_range':
            if not _int_ok(body.get('id_start')) or not _int_ok(body.get('id_end')):
                self.send_error(400, 'id_start/id_end')
                return
        ok = False
        if state.mqtt_client is not None:
            try:
                state.mqtt_client.publish(
                    'drifter/can/command',
                    json.dumps(body),
                )
                ok = True
            except Exception as e:
                log.warning("can command publish failed: %s", e)
        self._serve_json({'ok': ok, 'published': 'drifter/can/command'})

    def _post_vivi_reset(self):
        """Tell Vivi to drop her conversation history. Publishes
        drifter/vivi/control={"action":"reset"} which Vivi's MQTT
        subscriber consumes and clears _history + mints a new session id.

        Also clears the Mechanic chat ring buffer so /api/mechanic/history
        returns [] after a reset — the cockpit treats the two surfaces
        as the same conversation from the operator's perspective.
        """
        ok = False
        if state.mqtt_client is not None:
            try:
                state.mqtt_client.publish(
                    'drifter/vivi/control',
                    json.dumps({'action': 'reset', 'ts': time.time()}),
                )
                ok = True
            except Exception as e:
                log.warning("vivi reset publish failed: %s", e)
        _mechanic_history_reset()
        self._serve_json({'ok': ok})

    def _post_vivi_conversation_mode(self):
        """Toggle conversation mode. Body: {"enabled": bool}.
        Publishes RETAINED to drifter/vivi/conversation_mode so the
        state survives drifter-vivi restarts. drifter-vivi's subscriber
        flips a flag; on every subsequent /api/query response, vivi
        publishes drifter/voice/listen_now and drifter-voicein records
        a follow-up turn without waiting for the wake-word."""
        try:
            length = int(self.headers.get('Content-Length') or 0)
            length = min(length, MAX_POST_BODY)
            raw = self.rfile.read(length) if length else b'{}'
            body = json.loads(raw or b'{}')
        except (ValueError, json.JSONDecodeError):
            self.send_error(400, 'invalid JSON body')
            return
        enabled = bool(body.get('enabled', False))
        ok = False
        if state.mqtt_client is not None:
            try:
                state.mqtt_client.publish(
                    'drifter/vivi/conversation_mode',
                    json.dumps({'enabled': enabled, 'ts': time.time()}),
                    qos=0, retain=True,
                )
                ok = True
            except Exception as e:
                log.warning("conversation_mode publish failed: %s", e)
        self._serve_json({'ok': ok, 'enabled': enabled})

    def _get_mode(self, parsed):
        try:
            mode = (Path(MODE_STATE_PATH).read_text(encoding='utf-8').strip()
                    or DEFAULT_MODE)
        except OSError:
            mode = DEFAULT_MODE
        self._serve_json({'mode': mode, 'choices': sorted(MODES)})

    def _post_mode(self, target: str):
        if target not in MODES:
            self.send_error(400, f'unknown mode {target!r}')
            return
        # systemd-run spawns the switch as a transient unit OUTSIDE this
        # dashboard's cgroup. Required for the foot→drive case where the
        # opsec dashboard initiates a switch that disables drifter-opsec
        # mid-call: systemctl SIGTERMs the cgroup, which would kill any
        # subprocess.Popen child of opsec even with start_new_session.
        r = subprocess.run(
            ['sudo', '-n', '/usr/bin/systemd-run', '--no-block',
             '--unit=drifter-mode-switch', '/usr/local/bin/drifter', 'mode', target],
            capture_output=True, text=True, timeout=10,
        )
        _healthz_cache.update(ts=0.0, payload=None, http_status=200)
        self._serve_json({
            'requested': target,
            'status':    'dispatched' if r.returncode == 0 else 'failed',
            'rc':        r.returncode,
            'stderr':    r.stderr.strip(),
        })

    def _post_analyse(self):
        try:
            if state.mqtt_client is not None:
                state.mqtt_client.publish('drifter/analysis/request', '{}')
        except Exception:
            pass
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(b'{"status": "triggered"}')

    def _post_vivi_query(self):
        """Avatar viewer text input → publish on TOPICS['vivi_query'].
        Drives both vivi.py and vivi_v2.py since both subscribe there."""
        body = self._read_json_body()
        if body is None:
            return
        query = (body.get('query') or body.get('text') or '').strip()
        if not query:
            self.send_error(400, 'query required')
            return
        topic = TOPICS.get('vivi_query', 'drifter/vivi/query')
        payload = json.dumps({'query': query, 'ts': time.time()})
        try:
            if state.mqtt_client is None:
                self.send_error(503, 'mqtt unavailable')
                return
            state.mqtt_client.publish(topic, payload)
        except Exception as e:
            log.warning("vivi query publish failed: %s", e)
            self.send_error(503, 'mqtt publish failed')
            return
        self._serve_json({'status': 'queued', 'query': query})

    def _post_query(self):
        body = self._read_json_body()
        if body is None:
            return
        try:
            query = (body.get('query') or '').strip()
            if not query:
                self.send_error(400, 'Missing query')
                return
            # Record the user turn BEFORE building the prompt so the
            # history block in the next /api/mechanic/history GET is
            # already up-to-date.
            _mechanic_history_append('user', query)
            prompt = self._build_query_context(query)
            import llm_client
            result = llm_client.query_chat(prompt)
            text = result['text']
            _mechanic_history_append('assistant', text)
            # Phase 5.3 grounding validator — second line of defence
            # after the prompt-side NO DATA tags. Catches the case where
            # the model still reads a static-spec range out of the KB
            # and reports it as a live reading.
            try:
                from vivi_grounding import no_data_from_state, validate
                no_data = no_data_from_state(state.latest_state,
                                              _query_telemetry_keys())
                text, intercepted = validate(text, no_data)
                if intercepted:
                    log.warning("Vivi /api/query grounding intercept "
                                "(sensor=%s, query=%r)", intercepted, query[:80])
            except Exception as e:
                log.debug("grounding validator skipped: %s", e)
            self._serve_json({
                'response': text,
                'model':    result['model'],
                'tokens':   result['tokens'],
            })
        except Exception as e:
            log.warning("Query error: %s", e)
            self._serve_json({'error': str(e)})

    def _post_query_stream(self):
        body = self._read_json_body()
        if body is None:
            return
        try:
            query = (body.get('query') or '').strip()
            if not query:
                self.send_error(400, 'Missing query')
                return
            # Record the user turn before generating; the assistant turn
            # is recorded after the stream completes (see done block).
            _mechanic_history_append('user', query)
            prompt = self._build_query_context(query)
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Connection', 'keep-alive')
            self.end_headers()
            import llm_client
            buffered = []
            for chunk in llm_client.stream_chat_ollama(prompt):
                if chunk.get('token'):
                    buffered.append(chunk['token'])
                payload = json.dumps(chunk, default=str)
                self.wfile.write(f"data: {payload}\n\n".encode())
                self.wfile.flush()
                # Phase 5.3 — when the stream completes, validate the
                # buffered text. If the model hallucinated a NO DATA
                # sensor reading, emit a final replace event so the
                # client can overwrite the rendered tokens with the
                # canonical no-reading reply.
                if chunk.get('done'):
                    # Persist the full streamed text into the Mechanic
                    # ring buffer so the next /api/mechanic/history GET
                    # reflects the just-completed turn.
                    try:
                        _mechanic_history_append('assistant',
                                                 ''.join(buffered))
                    except Exception as e:
                        log.debug("history append (stream) failed: %s", e)
                    try:
                        from vivi_grounding import no_data_from_state, validate
                        full = ''.join(buffered)
                        no_data = no_data_from_state(
                            state.latest_state, _query_telemetry_keys())
                        safe, intercepted = validate(full, no_data)
                        if intercepted:
                            log.warning(
                                "Vivi /api/query/stream grounding "
                                "intercept (sensor=%s, query=%r)",
                                intercepted, query[:80])
                            replace = json.dumps(
                                {'replace_text': safe,
                                 'intercepted_sensor': intercepted})
                            self.wfile.write(
                                f"data: {replace}\n\n".encode())
                            self.wfile.flush()
                    except Exception as e:
                        log.debug(
                            "stream grounding validator skipped: %s", e)
        except Exception as e:
            log.warning("Stream query error: %s", e)
            try:
                err = json.dumps({"error": str(e)})
                self.wfile.write(f"data: {err}\n\n".encode())
                self.wfile.flush()
            except Exception:
                pass

    def _post_settings(self):
        body = self._read_json_body()
        if body is None:
            return
        cleaned, err = validate_settings_payload(body)
        if err is not None:
            self.send_error(400, err)
            return
        try:
            ok = save_settings(cleaned)
            self._serve_json({'ok': ok})
        except Exception as e:
            log.warning("Settings save error: %s", e)
            self._serve_json({'ok': False, 'error': str(e)})

    # ─── Body / response helpers ──────────────────────────────────────
    def _read_json_body(self):
        """Read + parse a JSON request body with a size cap.

        Returns the parsed dict, or None after sending an error response.
        Callers MUST return immediately on None.
        """
        try:
            length = int(self.headers.get('Content-Length', 0))
        except (TypeError, ValueError):
            self.send_error(400, 'Invalid Content-Length')
            return None
        if length <= 0:
            self.send_error(400, 'Missing request body')
            return None
        if length > MAX_POST_BODY:
            self.send_error(413, 'Request body too large')
            return None
        try:
            body = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, ValueError):
            self.send_error(400, 'Invalid JSON')
            return None
        if not isinstance(body, dict):
            self.send_error(400, 'Expected JSON object')
            return None
        return body

    def _serve_html(self, html: str):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(html.encode())

    def _serve_json(self, data):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str).encode())

    # Silence default access log; systemd journald already captures relevant info.
    def log_message(self, format, *args):
        pass

    # ─── LLM prompt assembly ──────────────────────────────────────────
    def _build_query_context(self, query: str) -> str:
        """Bundle live telemetry + top KB hits into a single LLM prompt."""
        return build_query_context(query)


_TELEMETRY_LINES = [
    ('engine_rpm',      'RPM',      '{:.0f}'),
    ('engine_coolant',  'Coolant',  '{:.1f}°C'),
    ('vehicle_speed',   'Speed',    '{:.0f} km/h'),
    ('engine_stft1',    'STFT B1',  '{:+.1f}%'),
    ('engine_stft2',    'STFT B2',  '{:+.1f}%'),
    ('engine_ltft1',    'LTFT B1',  '{:+.1f}%'),
    ('engine_ltft2',    'LTFT B2',  '{:+.1f}%'),
    ('power_voltage',   'Battery',  '{:.1f}V'),
    ('engine_load',     'Load',     '{:.0f}%'),
    ('vehicle_throttle','Throttle', '{:.0f}%'),
    ('engine_iat',      'IAT',      '{:.0f}°C'),
    ('engine_maf',      'MAF',      '{:.1f} g/s'),
]


def _query_telemetry_keys():
    """Return [(state_key, label), ...] — single source of truth shared
    between build_query_context and the grounding validator."""
    return [(k, label) for k, label, _ in _TELEMETRY_LINES]


def build_query_context(query: str) -> str:
    """Assemble the prompt the LLM sees when you ask a question in the UI.

    Exposed at module scope so tests can reuse it without instantiating
    a handler.
    """
    def _v(key):
        d = state.latest_state.get(key, {})
        return d.get('value') if isinstance(d, dict) else None

    TELEMETRY_LINES = _TELEMETRY_LINES

    telem_lines = []
    for key, label, fmt in TELEMETRY_LINES:
        v = _v(key)
        if v is not None:
            telem_lines.append(f"{label}: {fmt.format(v)}")
        else:
            # Explicit NO DATA — the model must SEE the absence rather
            # than infer one. Closes the hallucination class where the
            # LLM invented values to satisfy its mechanic persona.
            telem_lines.append(f"{label}: NO DATA")

    dtc_data = state.latest_state.get('diag_dtc', {})
    if isinstance(dtc_data, dict):
        if dtc_data.get('stored'):
            telem_lines.append(f"Active DTCs: {', '.join(dtc_data['stored'])}")
        if dtc_data.get('pending'):
            telem_lines.append(f"Pending DTCs: {', '.join(dtc_data['pending'])}")

    alert_d = state.latest_state.get('alert_message', {})
    if isinstance(alert_d, dict):
        alert_msg = alert_d.get('message', '')
        if alert_msg and alert_msg != 'Systems nominal':
            telem_lines.append(f"Active alert: {alert_msg}")

    context_parts = []
    # Conversation history — the last few user/assistant turns from this
    # session so follow-up questions resolve correctly ("what about the
    # second one?") without re-stating context every turn.
    history_block = _mechanic_history_block()
    if history_block:
        context_parts.append(
            "CONVERSATION HISTORY (most recent turns; use to resolve "
            "pronouns and follow-ups, not as a source of telemetry):\n"
            + history_block
        )
    # Telemetry is always emitted with explicit NO DATA markers — the
    # model must see absent sensors rather than have to infer their
    # absence from a vague "car may be off" line.
    context_parts.append(
        "CURRENT VEHICLE STATE (NO DATA = no current reading; do NOT "
        "invent, estimate, or infer a value for any sensor marked "
        "NO DATA):\n" + "\n".join(telem_lines)
    )

    # Live public-data feeds — same source the cockpit reads. We pull the
    # vivi helper to keep the format identical between the voice path
    # (vivi.py) and the dashboard query path (here). A None means the
    # feeds aggregator is offline / stale (>10 min) and we omit cleanly.
    try:
        import vivi as _vivi
        feed_block = _vivi._format_feed_context()
        if feed_block:
            context_parts.append("LIVE EXTERIOR CONTEXT (use these numbers verbatim "
                                 "— do not invent or refer to coolant/engine):\n"
                                 + feed_block)
    except Exception as e:
        log.debug(f"feed-context build failed: {e}")

    # Corpus retrieval — top 3 chunks ranked by cosine similarity.
    kb_lines = []
    for hit in corpus_search(query, k=3, min_similarity=0.4):
        topic = hit.get('topic') or hit.get('section') or 'reference'
        body = (hit.get('content') or '').strip().replace('\n', ' ')[:400]
        kb_lines.append(f"{topic}: {body}")
    if kb_lines:
        context_parts.append("RELEVANT KNOWLEDGE:\n" + "\n---\n".join(kb_lines))

    # Recency-attended reminder — qwen2.5 weights instructions later in
    # the prompt more strongly. The static-spec loophole was real:
    # 1.5b read "normal coolant range 85-100°C" from the corpus and
    # answered "Your coolant is at 95°C". The reminder now explicitly
    # forbids quoting a number for a NO DATA sensor even if the
    # knowledge base documents a normal range.
    context_parts.append(
        "REMINDER: If a sensor in the CURRENT VEHICLE STATE block above "
        "shows NO DATA, you MUST respond that you don't have a current "
        "reading for it. Do NOT state any specific number for that "
        "sensor — not from a normal range, not from a static spec, "
        "not from a knowledge-base reference. Never estimate, infer, "
        "or invent sensor values."
    )

    return query + ("\n\n---\n\n" + "\n\n".join(context_parts) if context_parts else "")


# Populate the exact-match route table AFTER the methods exist.
DashboardHandler._EXACT_GET_ROUTES = {
    '/':                          DashboardHandler._serve_dashboard_page,
    '/index.html':                DashboardHandler._serve_dashboard_page,
    '/settings':                  DashboardHandler._redirect_to_root,
    '/healthz':                   DashboardHandler._get_healthz,
    '/api/settings':              DashboardHandler._get_settings,
    '/api/settings/schema':       DashboardHandler._get_settings_schema,
    '/api/state':                 DashboardHandler._get_state,
    '/api/hardware':              DashboardHandler._get_hardware,
    '/api/rfaudio/status':        DashboardHandler._get_rfaudio_status,
    '/api/alerts/recent':         DashboardHandler._get_recent_alerts,
    '/api/aircraft/recent':       DashboardHandler._get_recent_aircraft,
    '/api/tpms/recent':           DashboardHandler._get_recent_tpms,
    '/api/trip/recent':           DashboardHandler._get_recent_trip,
    '/api/dtcs/recent':           DashboardHandler._get_recent_dtcs,
    '/api/rf/spectrum':           DashboardHandler._get_rf_spectrum,
    '/api/rf/spectrum/summary':   DashboardHandler._get_rf_spectrum_summary,
    '/api/mechanic/history':      DashboardHandler._get_mechanic_history,
    '/api/rf/adsb':               DashboardHandler._get_rf_adsb,
    '/api/rf/emergency':          DashboardHandler._get_rf_emergency,
    '/api/flipper/status':        DashboardHandler._get_flipper_status,
    '/api/flipper/hardware':      DashboardHandler._get_flipper_hardware,
    '/api/flipper/captures':      DashboardHandler._get_flipper_captures,
    '/api/tpms/assignments':      DashboardHandler._get_tpms_assignments,
    '/api/flipper/results':       DashboardHandler._get_flipper_results,
    '/api/report':                DashboardHandler._get_report,
    '/api/reports':               DashboardHandler._get_reports,
    '/api/sessions':              DashboardHandler._get_sessions,
    '/api/wardrive':              DashboardHandler._get_wardrive,
    '/api/mechanic/advice':       DashboardHandler._get_mechanic_advice,
    '/api/ble/recent':            DashboardHandler._get_ble_recent,
    '/api/ble/history':           DashboardHandler._get_ble_history,
    '/api/ble/drives':            DashboardHandler._get_ble_drives,
    '/api/ble/persistent':        DashboardHandler._get_ble_persistent,
    '/api/feeds/summary':         DashboardHandler._get_feeds_summary,
    '/api/radar.gif':             DashboardHandler._get_radar_gif,
    '/map/ble':                   DashboardHandler._get_ble_map,
    '/api/mode':                  DashboardHandler._get_mode,
    '/api/driver':                DashboardHandler._get_driver,
    # RF / CAN / Airspace expansion (Agent A)
    '/api/rf/classification':     DashboardHandler._get_rf_classification,
    '/api/can/discovery':         DashboardHandler._get_can_discovery,
    '/api/can/captures':          DashboardHandler._get_can_captures,
    '/api/airspace/aircraft':     DashboardHandler._get_airspace_aircraft,
    # Arsenal read-side routes (BE-2, foot-mode toolkit)
    '/api/kismet/devices':        DashboardHandler._get_kismet_devices,
    '/api/marauder/status':       DashboardHandler._get_marauder_status,
    '/api/marauder/scan':         DashboardHandler._get_marauder_scan,
    '/api/flycatcher/aircraft':   DashboardHandler._get_flycatcher_aircraft,
    '/api/ghost/status':          DashboardHandler._get_ghost_status,
    '/api/alpr/plates':           DashboardHandler._get_alpr_plates,
    '/api/vision/status':         DashboardHandler._get_vision_status,
    '/api/sentry/status':         DashboardHandler._get_sentry_status,
    # Arsenal aggregate (BE-3) — present derived from /healthz + hardware
    '/api/arsenal':               DashboardHandler._get_arsenal,
    '/preview/cockpit':           DashboardHandler._redirect_to_root,
    # Vivi 3D avatar viewer
    '/avatar':                    DashboardHandler._serve_avatar_page,
    '/vivi-avatar':               DashboardHandler._serve_avatar_page,
}

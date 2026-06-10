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
import hid_ducky
import hid_inject
import web_dashboard_state as state
from ble_map_html import BLE_MAP_HTML
from config import (
    ARSENAL_SERVICE_UNITS,
    DEFAULT_MODE,
    DRIVE_ONLY_SERVICES,
    FOOT_ONLY_SERVICES,
    GOOGLE_MAPS_API_KEY,
    MARAUDER_COMMANDS,
    MODE_STATE_PATH,
    MODES,
    SENTRY_COMMANDS,
    SETTINGS_SCHEMA,
    SETTINGS_SECTIONS,
    SHARED_SERVICES,
    TOPICS,
    XTYPE_DTC_LOOKUP,
    load_settings,
    save_settings,
    validate_settings_payload,
)
from corpus import corpus_search_best, dtc_lookup_static
from hw_probe import probe_rtl_sdr, probe_speaker
from web_dashboard_hardware import check_hardware

# Non-security HUD helper groups extracted into focused sibling modules.
# Re-imported here so the public API is byte-for-byte unchanged: every name
# below still resolves at web_dashboard_handlers.X and the DashboardHandler
# methods keep calling them as bare names.
from web_dashboard_health import (  # noqa: F401
    _CAPABILITY_HEARTBEATS,
    _HEALTHZ_TTL,
    _healthz_cache,
    _healthz_payload,
    _heartbeat_fresh,
    _systemctl_active,
)
from web_dashboard_mechanic import (  # noqa: F401
    _TELEMETRY_LINES,
    MECHANIC_HISTORY_CHAR_BUDGET,
    MECHANIC_HISTORY_TURNS,
    _format_feed_context,
    _mechanic_history_append,
    _mechanic_history_block,
    _mechanic_history_reset,
    _mechanic_history_snapshot,
    _query_telemetry_keys,
    build_query_context,
)
from web_dashboard_panels import (  # noqa: F401
    _AIRSPACE_EMERGENCY_SQUAWKS,
    _CAN_DISCOVERY_RING_MAX,
    _RF_CLASSIFICATION_RING_MAX,
    _airspace_poller,
    _record_can_discovery,
    _record_rf_classification,
    _snapshot_airspace,
    _snapshot_can_discoveries,
    _snapshot_rf_classifications,
    _update_airspace_cache,
    start_airspace_poller,
)

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

# _systemctl_active, _heartbeat_fresh, _healthz_payload and their cache
# globals (_HEALTHZ_TTL, _healthz_cache, _CAPABILITY_HEARTBEATS) now live in
# web_dashboard_health.py and are re-imported at the top of this module.

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
    # Brand / PWA assets — the cockpit is phone-tethered, so it should install
    # to the home screen with proper MZ1312 branding. The SVG/PNG mark already
    # ships in the canonical palette (static/icons/drifter-cockpit.*).
    '/favicon.svg': ('/opt/drifter/static/icons/drifter-cockpit.svg',
                     'image/svg+xml', None),
    '/favicon.ico': ('/opt/drifter/static/icons/drifter-cockpit.png',
                     'image/png', None),
    '/apple-touch-icon.png': ('/opt/drifter/static/icons/drifter-cockpit.png',
                              'image/png', None),
    '/manifest.webmanifest': ('/opt/drifter/static/icons/manifest.webmanifest',
                              'application/manifest+json', None),
}


_BLE_HISTORY_DB = '/opt/drifter/state/ble_history.db'

_RFAUDIO_ACTIONS = {'start', 'stop', 'scan', 'test_tone', 'list_bands'}

# ─── Arsenal service control (BE-4) ───────────────────────────────────
# POST /api/service/<unit> drives `sudo -n systemctl <action> <unit>` for
# foot-mode arsenal tools. The unit allowlist is the arsenal subset
# (ARSENAL_SERVICE_UNITS) intersected with the modes a foot-mode tool is
# allowed to live in (FOOT_ONLY ∪ SHARED). A DRIVE_ONLY unit can therefore
# NEVER appear here even if it slipped into ARSENAL_SERVICE_UNITS — the
# intersection is fail-closed. Unknown / arbitrary units are rejected.
_SERVICE_ACTIONS = {'start', 'stop', 'restart'}
_SERVICE_UNITS = (
    (set(FOOT_ONLY_SERVICES) | set(SHARED_SERVICES))
    & set(ARSENAL_SERVICE_UNITS)
)
# Defence in depth: an explicit deny-set of every drive-only unit, asserted
# disjoint from the computed allowlist at import so a future edit that lets a
# drive unit through trips the test suite instead of the vehicle.
_DRIVE_ONLY_UNITS = set(DRIVE_ONLY_SERVICES)
assert not (_SERVICE_UNITS & _DRIVE_ONLY_UNITS), \
    "arsenal service allowlist must never include a DRIVE_ONLY unit"

# systemctl call timeout — start/stop of a small unit is sub-second; cap so a
# wedged unit can't pin the handler thread.
_SERVICE_CTL_TIMEOUT = 15.0

# Append-only audit trail for every service-control attempt (peer, unit,
# action, rc, result). Mirrors the ducky/marauder audit requirement.
_ARSENAL_AUDIT_LOG = Path('/opt/drifter/state/arsenal_audit.log')

# Marauder / sentry command relays. The bridge re-validates and (for HIGH-risk
# marauder ops) runs its own confirm-token round-trip — these are thin relays.
_MARAUDER_COMMANDS = set(MARAUDER_COMMANDS)
_SENTRY_COMMANDS = set(SENTRY_COMMANDS)

# ─── Rubber Ducky / BadUSB HID (BE-1) ─────────────────────────────────
# POST /api/hid/command allowlist. The drifter-hid service is the
# authoritative SECOND gate (ARM→CONFIRM→RUN); for the Flipper backend a
# THIRD gate is the bridge's HIGH-risk classifier. This handler RELAYS
# only — it never injects and never bypasses CONFIRM.
_HID_COMMANDS = {'hid_arm', 'hid_confirm', 'hid_cancel'}
_HID_BACKENDS = {'native', 'flipper'}
# Payload storage shared with hid_inject.HID_PAYLOAD_DIR. The API compiles +
# persists here; the service reads from the same dir at ARM/RUN time.
_HID_PAYLOAD_DIR = Path('/opt/drifter/state/hid_payloads')
# Append-only audit shared with hid_inject.HID_AUDIT_LOG. The API records
# UPLOAD/DELETE here (operator events) with peer IP and also publishes
# drifter/hid/audit so the cockpit + logger see it.
_HID_AUDIT_LOG = Path('/opt/drifter/state/hid_audit.log')


def _hid_audit(event: str, peer: str, **fields) -> None:
    """Append one JSONL record to the HID audit log AND publish it.

    Mirrors hid_inject.audit so UPLOAD/DELETE (which happen API-side, not
    in the service) land in the SAME append-only trail with the peer IP.
    Never raises — a failed audit write must not break the route."""
    record = {'ts': time.time(), 'event': event, 'peer': peer}
    record.update(fields)
    try:
        _HID_AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _HID_AUDIT_LOG.open('a', encoding='utf-8') as fh:
            fh.write(json.dumps(record, default=str) + '\n')
    except OSError as e:
        log.warning("hid audit write failed: %s", e)
    if state.mqtt_client is not None:
        try:
            state.mqtt_client.publish(
                TOPICS.get('hid_audit', 'drifter/hid/audit'),
                json.dumps(record, default=str), qos=1, retain=False)
        except Exception as e:
            log.warning("hid audit publish failed: %s", e)
    log.info("hid-audit %s", json.dumps(record, default=str))


def _hid_payload_id_ok(payload_id: str) -> bool:
    """Path-traversal guard for a payload id used in a filesystem path."""
    return bool(payload_id) and not (
        '/' in payload_id or '\\' in payload_id or '..' in payload_id)


def _hid_list_payloads() -> list:
    """Enumerate stored payload metas newest-first. Honest empty list when
    none exist — never fabricated rows."""
    out = []
    if not _HID_PAYLOAD_DIR.exists():
        return out
    try:
        for meta_path in _HID_PAYLOAD_DIR.glob('*.meta.json'):
            try:
                meta = json.loads(meta_path.read_text(encoding='utf-8'))
            except (OSError, json.JSONDecodeError):
                continue
            out.append(meta)
    except OSError as e:
        log.warning("hid payload listing error: %s", e)
    out.sort(key=lambda m: m.get('created_ts') or 0, reverse=True)
    return out


def _arsenal_audit(event: str, peer: str, **fields) -> None:
    """Append one JSONL record to the arsenal audit log AND log a line.

    Never raises — a failed audit write must not break the control path,
    but it is logged at WARNING so a broken audit surface is visible."""
    record = {'ts': time.time(), 'event': event, 'peer': peer}
    record.update(fields)
    try:
        _ARSENAL_AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _ARSENAL_AUDIT_LOG.open('a', encoding='utf-8') as fh:
            fh.write(json.dumps(record, default=str) + '\n')
    except OSError as e:
        log.warning("arsenal audit write failed: %s", e)
    log.info("arsenal-audit %s", json.dumps(record, default=str))

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

# The mechanic chat ring buffer (_mechanic_history*, MECHANIC_HISTORY_*) now
# lives in web_dashboard_mechanic.py and is re-imported at the top of this
# module.

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

# The RF/CAN/airspace process-local caches (_record_rf_classification,
# _snapshot_rf_classifications, _record_can_discovery,
# _snapshot_can_discoveries, _update_airspace_cache, _snapshot_airspace,
# _airspace_poller, start_airspace_poller) and their backing globals now live
# in web_dashboard_panels.py and are re-imported at the top of this module.

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
        if parsed.path.startswith('/api/hid/payloads/'):
            self._serve_hid_payload(parsed.path[len('/api/hid/payloads/'):])
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

    def _get_mapconfig(self, parsed):
        """Map config for the cockpit's MZ1312 Google basemap.

        Local-only — the Maps JS key never leaves the hotspot. An empty key
        makes the cockpit hide the mz1312 basemap and fall back to dark/sat."""
        peer = self.client_address[0] if self.client_address else ''
        if not _is_local_peer(peer):
            self.send_error(403, 'mapconfig: local network only')
            return
        self._serve_json({
            'google_maps_key': GOOGLE_MAPS_API_KEY or '',
            'has_google': bool(GOOGLE_MAPS_API_KEY),
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

    # ─── Rubber Ducky / BadUSB HID (BE-1) ────────────────────────────
    def _get_hid_status(self, parsed):
        """Backend readiness + currently-armed entry (if any).

        {native:{dr_mode,hidg0_present,bound,boot_profile,...},
         flipper:{connected,badusb_ready}, armed:{...}|null}

        native readiness is read HONESTLY from /proc/device-tree dr_mode —
        on this host it is 'host' so native reports not-configured and is
        NEVER faked ready. flipper readiness is merged from the live
        retained drifter/flipper/status snapshot. `armed` reflects the
        most recent ARMED preview the service published on drifter/hid/status
        (the service is the authority; the API does not invent state)."""
        peer = self.client_address[0] if self.client_address else ''
        if not _is_local_peer(peer):
            self.send_error(403, 'hid: local network only')
            return
        native = hid_inject.native_status()
        # Honest flipper readiness from the retained status topic.
        fstatus = state.latest_state.get('flipper_status')
        if isinstance(fstatus, dict) and fstatus:
            connected = (fstatus.get('state') == 'connected'
                         or fstatus.get('connected') is True)
        else:
            connected = False
        flipper = {
            'connected': connected,
            # BadUSB-app readiness needs the bridge to confirm at fire time;
            # we do not fabricate it — connected is the honest precondition.
            'badusb_ready': connected,
        }
        # Armed snapshot from the service's last drifter/hid/status publish.
        armed = None
        hid_status = state.latest_state.get('hid_status')
        if isinstance(hid_status, dict):
            armed = hid_status.get('armed')
        self._serve_json({'native': native, 'flipper': flipper,
                          'armed': armed, 'ts': time.time()})

    def _get_hid_payloads(self, parsed):
        """List stored payload metas, newest-first. Honest empty list."""
        peer = self.client_address[0] if self.client_address else ''
        if not _is_local_peer(peer):
            self.send_error(403, 'hid: local network only')
            return
        self._serve_json(_hid_list_payloads())

    def _serve_hid_payload(self, payload_id: str):
        """Single payload {meta, script} for the editor. 404 when absent."""
        peer = self.client_address[0] if self.client_address else ''
        if not _is_local_peer(peer):
            self.send_error(403, 'hid: local network only')
            return
        if not _hid_payload_id_ok(payload_id):
            self.send_error(400, 'bad payload id')
            return
        loaded = hid_inject.load_payload(payload_id)
        if loaded is None:
            self.send_error(404, 'payload not found')
            return
        self._serve_json({'meta': loaded['meta'], 'script': loaded['script']})

    def _post_hid_payload(self):
        """Compile a DuckyScript payload, persist .txt + .meta.json, audit
        UPLOAD. 400 {ok:false,error,line} on a parse error — the payload is
        NOT stored if it cannot compile.

        Body: {name, script, layout?, id?}. Compilation here is the same
        compiler the service uses at ARM time, so a stored payload is known
        to compile. NO injection happens — this only validates + persists."""
        peer = self.client_address[0] if self.client_address else ''
        if not _is_local_peer(peer):
            self.send_error(403, 'hid: local network only')
            return
        body = self._read_post_body()
        if body is None:
            return
        if not isinstance(body, dict):
            self.send_error(400, 'body must be a JSON object')
            return
        script = body.get('script')
        name = body.get('name') or 'unnamed'
        layout = body.get('layout') or 'us'
        if not isinstance(script, str) or not script.strip():
            self.send_error(400, 'body requires a non-empty "script" string')
            return
        # Validate by compiling. A parse error blocks persistence.
        try:
            compiled = hid_ducky.compile_ducky(script, layout=layout)
        except hid_ducky.DuckyParseError as e:
            self.send_response(400)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            self.wfile.write(json.dumps({
                'ok': False, 'error': str(e),
                'line': getattr(e, 'line', None)}).encode())
            return
        # Persist. id defaults to ducky-<unix_ts> (mirrors capture ids).
        payload_id = body.get('id') or f'ducky-{int(time.time())}'
        if not _hid_payload_id_ok(payload_id):
            self.send_error(400, 'bad payload id')
            return
        sha = hid_ducky.sha256_source(script)
        meta = {
            'id': payload_id,
            'name': str(name)[:200],
            'created_ts': time.time(),
            'line_count': compiled.line_count,
            'keystrokes': compiled.keystrokes,
            'sha256': sha,
            'backend_hint': body.get('backend_hint'),
            'target_layout': layout,
        }
        try:
            _HID_PAYLOAD_DIR.mkdir(parents=True, exist_ok=True)
            (_HID_PAYLOAD_DIR / f'{payload_id}.txt').write_text(
                script, encoding='utf-8')
            (_HID_PAYLOAD_DIR / f'{payload_id}.meta.json').write_text(
                json.dumps(meta), encoding='utf-8')
        except OSError as e:
            log.warning("hid payload persist failed: %s", e)
            self.send_error(500, 'failed to persist payload')
            return
        _hid_audit('UPLOAD', peer, id=payload_id, name=meta['name'],
                   line_count=compiled.line_count, sha256=sha, layout=layout)
        self._serve_json({'ok': True, 'id': payload_id, 'meta': meta})

    def _delete_hid_payload(self, payload_id: str):
        """Remove a stored payload (.txt + .meta.json). Audited DELETE."""
        peer = self.client_address[0] if self.client_address else ''
        if not _is_local_peer(peer):
            self.send_error(403, 'hid: local network only')
            return
        if not _hid_payload_id_ok(payload_id):
            self.send_error(400, 'bad payload id')
            return
        txt = _HID_PAYLOAD_DIR / f'{payload_id}.txt'
        meta = _HID_PAYLOAD_DIR / f'{payload_id}.meta.json'
        if not txt.exists() and not meta.exists():
            self.send_error(404, 'payload not found')
            return
        ok = True
        for p in (txt, meta):
            try:
                if p.exists():
                    p.unlink()
            except OSError as e:
                log.warning("hid payload delete failed: %s", e)
                ok = False
        _hid_audit('DELETE', peer, id=payload_id, ok=ok)
        self._serve_json({'ok': ok})

    def _post_hid_command(self):
        """Relay an allowlisted command to drifter/hid/command.

        The drifter-hid service is the authoritative SECOND gate
        (ARM→CONFIRM→RUN); for the Flipper backend the bridge's HIGH-risk
        classifier is a THIRD gate. This handler validates the allowlist
        and stamps the peer IP onto the relayed message so the service
        records it in the audit trail — there is NO upload→inject path here
        that skips CONFIRM.

        Allowlist (_HID_COMMANDS):
          {"command":"hid_arm","payload_id":"ducky-…","backend":"native"|"flipper"}
          {"command":"hid_confirm","id":"arm-…"}
          {"command":"hid_cancel","id":"arm-…"}
        503 if mqtt offline; 403 off-peer; 400 on a bad/unknown command."""
        peer = self.client_address[0] if self.client_address else ''
        if not _is_local_peer(peer):
            self.send_error(403, 'hid: local network only')
            return
        body = self._read_post_body()
        if body is None:
            return
        if not isinstance(body, dict):
            self.send_error(400, 'body must be a JSON object')
            return
        command = body.get('command')
        if command not in _HID_COMMANDS:
            self.send_error(400, 'command')
            return
        if command == 'hid_arm':
            payload_id = body.get('payload_id')
            backend = body.get('backend')
            if not isinstance(payload_id, str) or not payload_id.strip():
                self.send_error(400, 'payload_id')
                return
            if backend not in _HID_BACKENDS:
                self.send_error(400, 'backend')
                return
        else:  # hid_confirm / hid_cancel
            arm_id = body.get('id')
            if not isinstance(arm_id, str) or not arm_id.strip():
                self.send_error(400, 'id')
                return
        if state.mqtt_client is None:
            self.send_error(503, 'mqtt offline')
            return
        # Stamp the peer IP so the service audits the operator's address.
        relay = dict(body)
        relay['peer'] = peer
        ok = False
        try:
            state.mqtt_client.publish(
                TOPICS.get('hid_command', 'drifter/hid/command'),
                json.dumps(relay), qos=1)
            ok = True
        except Exception as e:
            log.warning("hid command publish failed: %s", e)
        self._serve_json({'ok': ok, 'published': 'drifter/hid/command'})

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
        passage bodies as bullet lines under the alert banner.

        In this embed-free dashboard process corpus_search_best falls back to
        a torch-free lexical search over the corpus (semantic retrieval, which
        needs the embedding model, runs in the torch-owning services) — so the
        HUD still surfaces relevant passages without loading torch."""
        msg = parse_qs(parsed.query).get('alert', [''])[0]
        hits = corpus_search_best(msg, k=3, min_similarity=0.4) if msg else []
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
        for codes the corpus hasn't been rebuilt with.

        Uses the torch-free static lookup (dtc_lookup_static) — NOT the
        semantic-fallback dtc_lookup — so this always-on, memory-capped HUD
        process never loads sentence-transformers/torch (would blow
        MemoryMax=512M and OOM-kill the dashboard). A static dtc/<code>.md
        miss falls through to the built-in XTYPE_DTC_LOOKUP table below."""
        code = parsed.path.rsplit('/', 1)[-1].upper()
        if not _DTC_RE.match(code):
            self.send_error(400, 'Invalid DTC code')
            return
        hit = dtc_lookup_static(code)
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

    # ─── DELETE ───────────────────────────────────────────────────────
    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith('/api/hid/payloads/'):
            self._delete_hid_payload(parsed.path[len('/api/hid/payloads/'):])
            return
        self.send_error(404)

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
        if self.path.startswith('/api/service/'):
            self._post_service(self.path[len('/api/service/'):])
            return
        if self.path == '/api/marauder/command':
            self._post_marauder_command()
            return
        if self.path == '/api/sentry/command':
            self._post_sentry_command()
            return
        if self.path == '/api/hid/payload':
            self._post_hid_payload()
            return
        if self.path == '/api/hid/command':
            self._post_hid_command()
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

    def _read_post_body(self):
        """Read + JSON-decode the request body, capped at MAX_POST_BODY.

        Returns the decoded object, or None on a malformed body (the caller
        has already not sent any response in that case — but we send the 400
        here so callers don't each repeat it). On None the caller must return
        immediately."""
        try:
            length = int(self.headers.get('Content-Length') or 0)
            length = min(length, MAX_POST_BODY)
            raw = self.rfile.read(length) if length else b'{}'
            return json.loads(raw or b'{}')
        except (ValueError, json.JSONDecodeError):
            self.send_error(400, 'invalid JSON body')
            return None

    def _post_service(self, unit: str):
        """Start/stop/restart a foot-mode arsenal systemd unit.

        Hard safety model (the whole point of this stage):
          * _is_local_peer gated — 403 off 127.0.0.1 / 10.42.0.0/24.
          * action ∈ {start,stop,restart}.
          * unit ∈ _SERVICE_UNITS, the arsenal subset of
            (FOOT_ONLY ∪ SHARED). A DRIVE_ONLY or arbitrary unit is rejected
            403 (fail-closed) — never operated.
          * REFUSE with 409 when the node is in 'drive' mode (foot-gate at
            the route, mirroring the UI mode-gate). Read the mode the same
            way /healthz / _get_arsenal do.
          * Execute `sudo -n systemctl <action> <unit>` (timeout, captured).
          * Audit EVERY call (peer IP, unit, action, rc, result) to the
            JSONL log + a log line.
        Returns {ok, unit, action, rc}.
        """
        peer = self.client_address[0] if self.client_address else ''
        if not _is_local_peer(peer):
            self.send_error(403, 'service: local network only')
            return
        # Strip any stray path component / query — unit is the path tail.
        unit = (unit or '').split('/', 1)[0].split('?', 1)[0].strip()
        body = self._read_post_body()
        if body is None:
            return
        if not isinstance(body, dict):
            self.send_error(400, 'body must be a JSON object')
            return
        action = body.get('action')
        # Action allowlist first — cheapest reject, no audit noise needed
        # for a malformed verb.
        if action not in _SERVICE_ACTIONS:
            self.send_error(400, 'action')
            return
        # Unit allowlist — fail-closed. A DRIVE_ONLY or unknown unit never
        # reaches systemctl. Audit the refusal so a probe is visible.
        if unit not in _SERVICE_UNITS:
            _arsenal_audit('BLOCKED', peer, unit=unit, action=action,
                           reason='unit not in arsenal allowlist')
            self.send_error(403, 'unit not in arsenal allowlist')
            return
        # Foot-gate AT THE ROUTE: refuse start/stop while driving. The mode
        # is read from the same authoritative marker /healthz uses.
        try:
            mode = (Path(MODE_STATE_PATH).read_text(encoding='utf-8').strip()
                    or DEFAULT_MODE)
        except OSError:
            mode = DEFAULT_MODE
        if mode == 'drive':
            _arsenal_audit('REFUSED', peer, unit=unit, action=action,
                           reason='node in drive mode')
            self.send_error(409, 'arsenal service control disabled in drive mode')
            return
        # Execute. sudo -n so a missing NOPASSWD rule fails fast instead of
        # hanging on a password prompt.
        rc = None
        ok = False
        err = ''
        try:
            r = subprocess.run(
                ['sudo', '-n', 'systemctl', action, unit],
                capture_output=True, text=True, timeout=_SERVICE_CTL_TIMEOUT,
            )
            rc = r.returncode
            ok = (rc == 0)
            if not ok:
                err = (r.stderr or r.stdout or '').strip()[:200]
        except subprocess.TimeoutExpired:
            err = 'systemctl timed out'
        except (subprocess.SubprocessError, FileNotFoundError, OSError) as e:
            err = str(e)[:200]
        _arsenal_audit('RUN' if ok else 'FAIL', peer, unit=unit,
                       action=action, rc=rc, error=err or None)
        out = {'ok': ok, 'unit': unit, 'action': action, 'rc': rc}
        if err:
            out['error'] = err
        self._serve_json(out)

    def _post_marauder_command(self):
        """Thin relay to drifter/marauder/cmd — the marauder_bridge's risk
        classifier + ConfirmRegistry is the authoritative second gate.

        Body: {op, ...params}. `op` is the operation name (validated against
        _MARAUDER_COMMANDS, which mirrors the bridge's action names). The
        bridge dispatch reads its 'command' field, so we relay `op` as
        `command` and pass through any `id`, `args`, and (crucially) any
        `confirm_token` so the HIGH-risk confirm round-trip works END TO END
        without the cockpit ever reimplementing tiers or auto-confirming.

        _is_local_peer gated; 503 if mqtt offline. HIGH-risk ops are in the
        allowlist ONLY so they can be relayed WITH a confirm token — the
        bridge still refuses them without one.
        """
        peer = self.client_address[0] if self.client_address else ''
        if not _is_local_peer(peer):
            self.send_error(403, 'marauder: local network only')
            return
        body = self._read_post_body()
        if body is None:
            return
        if not isinstance(body, dict):
            self.send_error(400, 'body must be a JSON object')
            return
        op = body.get('op') or body.get('command')
        if op not in _MARAUDER_COMMANDS:
            self.send_error(400, 'op')
            return
        if state.mqtt_client is None:
            self.send_error(503, 'mqtt offline')
            return
        # Build the bridge-shaped payload. Preserve operator-supplied id /
        # args / confirm_token verbatim; the bridge owns confirm semantics.
        relay = {'command': op}
        if isinstance(body.get('args'), dict):
            relay['args'] = body['args']
        if body.get('id') is not None:
            relay['id'] = body['id']
        if body.get('confirm_token') is not None:
            relay['confirm_token'] = body['confirm_token']
        topic = TOPICS.get('marauder_cmd', 'drifter/marauder/cmd')
        ok = False
        try:
            state.mqtt_client.publish(topic, json.dumps(relay), qos=1)
            ok = True
        except Exception as e:
            log.warning("marauder command publish failed: %s", e)
        # Audit HIGH-risk relays (and confirms) with peer IP per the spec.
        _arsenal_audit('MARAUDER', peer, op=op,
                       confirm=bool(body.get('confirm_token')), ok=ok)
        self._serve_json({'ok': ok, 'published': topic})

    def _post_sentry_command(self):
        """Thin relay to drifter/sentry/event — arm/disarm the sentry.

        Body: {action: 'arm'|'disarm'}. _is_local_peer gated; allowlist
        {arm,disarm}; 503 if mqtt offline.
        """
        peer = self.client_address[0] if self.client_address else ''
        if not _is_local_peer(peer):
            self.send_error(403, 'sentry: local network only')
            return
        body = self._read_post_body()
        if body is None:
            return
        if not isinstance(body, dict):
            self.send_error(400, 'body must be a JSON object')
            return
        action = body.get('action')
        if action not in _SENTRY_COMMANDS:
            self.send_error(400, 'action')
            return
        if state.mqtt_client is None:
            self.send_error(503, 'mqtt offline')
            return
        topic = TOPICS.get('sentry_event', 'drifter/sentry/event')
        ok = False
        try:
            state.mqtt_client.publish(
                topic,
                json.dumps({'action': action, 'ts': time.time()}),
                qos=1,
            )
            ok = True
        except Exception as e:
            log.warning("sentry command publish failed: %s", e)
        _arsenal_audit('SENTRY', peer, action=action, ok=ok)
        self._serve_json({'ok': ok, 'published': topic})

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
            import llm_client_v2
            result = llm_client_v2.query(prompt, CHAT_SYSTEM_PROMPT)
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
            import llm_client_v2
            buffered = []

            # v2 streams via an on_token callback (Claude SSE, with a safe
            # non-streaming fallback) rather than yielding chunks. We adapt
            # back to the same per-token SSE event shape the client expects.
            def _emit_token(tok: str) -> None:
                if not tok:
                    return
                buffered.append(tok)
                payload = json.dumps({'token': tok}, default=str)
                self.wfile.write(f"data: {payload}\n\n".encode())
                self.wfile.flush()

            result = llm_client_v2.stream(
                prompt, CHAT_SYSTEM_PROMPT, on_token=_emit_token)
            # If the backend never streamed token-by-token (e.g. the cascade
            # served a non-streaming Ollama/Groq backend), on_token never
            # fired — emit the full text now so the client still renders it.
            if not buffered:
                _emit_token(result.get('text', ''))

            # Final done event mirrors the old generator's terminal chunk.
            done = json.dumps({
                'done': True,
                'model': result.get('model', '?'),
                'tokens': result.get('tokens', 0),
                'text': ''.join(buffered),
            }, default=str)
            self.wfile.write(f"data: {done}\n\n".encode())
            self.wfile.flush()

            # Persist the full streamed text into the Mechanic ring buffer
            # so the next /api/mechanic/history GET reflects the just-
            # completed turn.
            try:
                _mechanic_history_append('assistant', ''.join(buffered))
            except Exception as e:
                log.debug("history append (stream) failed: %s", e)
            # Phase 5.3 — validate the buffered text. If the model
            # hallucinated a NO DATA sensor reading, emit a final replace
            # event so the client can overwrite the rendered tokens with the
            # canonical no-reading reply.
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


# _TELEMETRY_LINES and _query_telemetry_keys now live in
# web_dashboard_mechanic.py and are re-imported at the top of this module.


# Conversational "Ask Mechanic" system prompt. Moved here from the retired
# llm_client v1 shim (query_chat hard-wired this exact text); the v2 client
# takes the system prompt explicitly via query()/stream(). This is the sole
# caller now.
CHAT_SYSTEM_PROMPT = """You are an expert diagnostic technician and mechanic specialising in the \
2004 Jaguar X-Type 2.5L V6 (AJ-V6 engine). This is an Australian-delivered, \
right-hand-drive, AWD vehicle with the Jatco JF506E 5-speed automatic.

You are running on DRIFTER — a vehicle intelligence system on Raspberry Pi 5 \
(Kali Linux) with live OBD-II/CAN bus telemetry. You may be given live sensor \
readings and knowledge base context alongside each question.

Your approach:
- Be direct, practical, and experienced. Answer conversationally like a real mechanic.
- Reference the live telemetry values when relevant ("Your coolant is at 95°C which suggests...")
- Cite known X-Type failure modes when applicable (thermostat, coil packs, MAF, vacuum leaks)
- Give actionable advice with difficulty ratings and AUD cost estimates
- ALWAYS prioritise safety — flag anything dangerous immediately
- Keep responses concise — the driver may be reading on a phone mounted in the car

Do NOT return JSON. Respond in clear, readable text.

VEHICLE CONTEXT:
- Known history: valve cover gasket oil leak, prior spark plug overtorque failure
- Current symptoms: P0303 cylinder 3 misfire, cruise control disabled, rough idle
- Suspected: vacuum leaks (PCV hose, IMT valve O-ring, brake booster hose)
"""

# _FEEDS_SUMMARY_PATH, _format_feed_context and build_query_context now live
# in web_dashboard_mechanic.py and are re-imported at the top of this module.


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
    '/api/mapconfig':             DashboardHandler._get_mapconfig,
    '/api/alpr/plates':           DashboardHandler._get_alpr_plates,
    '/api/vision/status':         DashboardHandler._get_vision_status,
    '/api/sentry/status':         DashboardHandler._get_sentry_status,
    # Arsenal aggregate (BE-3) — present derived from /healthz + hardware
    '/api/arsenal':               DashboardHandler._get_arsenal,
    # Rubber Ducky / BadUSB HID (BE-1) — Flipper backend; native=stage6
    '/api/hid/status':            DashboardHandler._get_hid_status,
    '/api/hid/payloads':          DashboardHandler._get_hid_payloads,
    '/preview/cockpit':           DashboardHandler._redirect_to_root,
    # Vivi 3D avatar viewer
    '/avatar':                    DashboardHandler._serve_avatar_page,
    '/vivi-avatar':               DashboardHandler._serve_avatar_page,
}

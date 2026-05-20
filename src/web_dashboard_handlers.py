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
from urllib.parse import urlparse, parse_qs

from config import (
    load_settings, save_settings, XTYPE_DTC_LOOKUP, SERVICES,
    MODES, MODE_STATE_PATH, DEFAULT_MODE,
)
from corpus import corpus_search, dtc_lookup
from ble_map_html import BLE_MAP_HTML
import ble_history
import ble_persistence
from web_dashboard_hardware import check_hardware
import web_dashboard_state as state

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
    payload = {
        'status':              status_str,
        'mode':                mode,
        'ts':                  now,
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

# Phone-as-GPS sink. The tethered phone POSTs its browser-geolocation
# fix here; we drop it at the same path drifter-gps writes to so
# feeds.origin() sees it without any wiring change.
_GPS_STATE_PATH = Path('/opt/drifter/state/gps.json')


def _is_local_peer(peer: str) -> bool:
    """Hotspot-only ACL — same gate Phase 4.5 used for /api/ble/recent.
    BLE detection data shouldn't leak past 127.0.0.1 + the 10.42.0.0/24
    Wi-Fi hotspot."""
    return peer == '127.0.0.1' or peer.startswith('10.42.0.')


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
    def _get_state(self, parsed):             self._serve_json(state.latest_state)
    def _get_hardware(self, parsed):          self._serve_json(check_hardware())
    def _get_rfaudio_status(self, parsed):    self._serve_json(state.latest_state.get('rfaudio_status', {}))

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
        if self.path == '/api/rfaudio/command':
            self._post_rfaudio_command()
            return
        if self.path == '/api/gps/manual':
            self._post_gps_manual()
            return
        self.send_error(404)

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

    def _post_vivi_reset(self):
        """Tell Vivi to drop her conversation history. Publishes
        drifter/vivi/control={"action":"reset"} which Vivi's MQTT
        subscriber consumes and clears _history + mints a new session id."""
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

    def _post_query(self):
        body = self._read_json_body()
        if body is None:
            return
        try:
            query = (body.get('query') or '').strip()
            if not query:
                self.send_error(400, 'Missing query')
                return
            prompt = self._build_query_context(query)
            import llm_client
            result = llm_client.query_chat(prompt)
            text = result['text']
            # Phase 5.3 grounding validator — second line of defence
            # after the prompt-side NO DATA tags. Catches the case where
            # the model still reads a static-spec range out of the KB
            # and reports it as a live reading.
            try:
                from vivi_grounding import validate, no_data_from_state
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
                    try:
                        from vivi_grounding import (
                            validate, no_data_from_state)
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
        try:
            ok = save_settings(body)
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
    '/api/state':                 DashboardHandler._get_state,
    '/api/hardware':              DashboardHandler._get_hardware,
    '/api/rfaudio/status':        DashboardHandler._get_rfaudio_status,
    '/api/alerts/recent':         DashboardHandler._get_recent_alerts,
    '/api/aircraft/recent':       DashboardHandler._get_recent_aircraft,
    '/api/tpms/recent':           DashboardHandler._get_recent_tpms,
    '/api/trip/recent':           DashboardHandler._get_recent_trip,
    '/api/dtcs/recent':           DashboardHandler._get_recent_dtcs,
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
    '/preview/cockpit':           DashboardHandler._redirect_to_root,
}

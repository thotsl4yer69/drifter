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
from web_dashboard_html import DASHBOARD_HTML, SETTINGS_HTML
from ble_map_html import BLE_MAP_HTML
import ble_history
import ble_persistence
from web_dashboard_hardware import check_hardware
import web_dashboard_state as state

log = logging.getLogger(__name__)

# Hard cap on POST request body size. Stops a hostile client from stalling
# a handler thread by announcing a giant Content-Length.
MAX_POST_BODY = 64 * 1024

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
    def _serve_dashboard_page(self, parsed):  self._serve_html(DASHBOARD_HTML)
    def _serve_settings_page(self, parsed):   self._serve_html(SETTINGS_HTML)
    def _get_settings(self, parsed):          self._serve_json(load_settings())
    def _get_state(self, parsed):             self._serve_json(state.latest_state)
    def _get_hardware(self, parsed):          self._serve_json(check_hardware())
    def _get_report(self, parsed):            self._serve_json(state.latest_report)

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
        self.send_error(404)

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
            self._serve_json({
                'response': result['text'],
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
            for chunk in llm_client.stream_chat_ollama(prompt):
                payload = json.dumps(chunk, default=str)
                self.wfile.write(f"data: {payload}\n\n".encode())
                self.wfile.flush()
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


def build_query_context(query: str) -> str:
    """Assemble the prompt the LLM sees when you ask a question in the UI.

    Exposed at module scope so tests can reuse it without instantiating
    a handler.
    """
    def _v(key):
        d = state.latest_state.get(key, {})
        return d.get('value') if isinstance(d, dict) else None

    # Map of (state key, label, unit-format) so adding a new telemetry line
    # is one table row instead of three. The format string controls both
    # precision and unit — kept tight for LLM context economy.
    TELEMETRY_LINES = [
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

    telem_lines = []
    for key, label, fmt in TELEMETRY_LINES:
        v = _v(key)
        if v is not None:
            telem_lines.append(f"{label}: {fmt.format(v)}")

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
    if telem_lines:
        context_parts.append("CURRENT VEHICLE STATE:\n" + "\n".join(telem_lines))
    else:
        context_parts.append("CURRENT VEHICLE STATE: No live telemetry — car may be off")

    # Corpus retrieval — top 3 chunks ranked by cosine similarity.
    kb_lines = []
    for hit in corpus_search(query, k=3, min_similarity=0.4):
        topic = hit.get('topic') or hit.get('section') or 'reference'
        body = (hit.get('content') or '').strip().replace('\n', ' ')[:400]
        kb_lines.append(f"{topic}: {body}")
    if kb_lines:
        context_parts.append("RELEVANT KNOWLEDGE:\n" + "\n---\n".join(kb_lines))

    return query + ("\n\n---\n\n" + "\n\n".join(context_parts) if context_parts else "")


# Populate the exact-match route table AFTER the methods exist.
DashboardHandler._EXACT_GET_ROUTES = {
    '/':                          DashboardHandler._serve_dashboard_page,
    '/index.html':                DashboardHandler._serve_dashboard_page,
    '/settings':                  DashboardHandler._serve_settings_page,
    '/healthz':                   DashboardHandler._get_healthz,
    '/api/settings':              DashboardHandler._get_settings,
    '/api/state':                 DashboardHandler._get_state,
    '/api/hardware':              DashboardHandler._get_hardware,
    '/api/report':                DashboardHandler._get_report,
    '/api/reports':               DashboardHandler._get_reports,
    '/api/sessions':              DashboardHandler._get_sessions,
    '/api/wardrive':              DashboardHandler._get_wardrive,
    '/api/mechanic/advice':       DashboardHandler._get_mechanic_advice,
    '/api/ble/recent':            DashboardHandler._get_ble_recent,
    '/api/ble/history':           DashboardHandler._get_ble_history,
    '/api/ble/drives':            DashboardHandler._get_ble_drives,
    '/api/ble/persistent':        DashboardHandler._get_ble_persistent,
    '/map/ble':                   DashboardHandler._get_ble_map,
    '/api/mode':                  DashboardHandler._get_mode,
}

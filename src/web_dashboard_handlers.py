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
)
from mechanic import (
    search as mechanic_search, VEHICLE_SPECS, COMMON_PROBLEMS,
    SERVICE_SCHEDULE, EMERGENCY_PROCEDURES, TORQUE_SPECS, FUSE_REFERENCE,
    get_advice_for_alert,
)
from web_dashboard_html import DASHBOARD_HTML, MECHANIC_HTML, SETTINGS_HTML
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
    failed = [s for s, ok in services.items() if not ok]

    mqtt_ok = state.mqtt_client is not None and getattr(
        state.mqtt_client, 'is_connected', lambda: False)()

    # Telemetry freshness: any topic updated in the last 30s = bus alive.
    last_seen = state.latest_state.get('_last_update', 0)
    telemetry_fresh = (now - last_seen) < 30 if last_seen else False

    payload = {
        'status':           'ok' if not failed else 'degraded',
        'ts':               now,
        'services':         services,
        'services_failed':  failed,
        'mqtt_connected':   mqtt_ok,
        'telemetry_fresh':  telemetry_fresh,
        'ws_clients':       len(state.ws_clients),
    }
    http_status = 200 if not failed else 503
    _healthz_cache.update(ts=now, payload=payload, http_status=http_status)
    return payload, http_status

# Static files served with no extra rewriting. All live at /opt/drifter/*.
_STATIC_FILES = {
    '/screen':       ('/opt/drifter/screen_dash.html',    'text/html; charset=utf-8',       None),
    '/screen.html':  ('/opt/drifter/screen_dash.html',    'text/html; charset=utf-8',       None),
    '/realdash.xml': ('/opt/drifter/drifter_channels.xml', 'application/xml',
                       'attachment; filename="drifter_channels.xml"'),
}


class DashboardHandler(SimpleHTTPRequestHandler):
    """Route HTTP requests to one of the small endpoint methods below."""

    # ─── GET ──────────────────────────────────────────────────────────
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
    def _serve_mechanic_page(self, parsed):   self._serve_html(MECHANIC_HTML)
    def _serve_settings_page(self, parsed):   self._serve_html(SETTINGS_HTML)
    def _get_settings(self, parsed):          self._serve_json(load_settings())
    def _get_state(self, parsed):             self._serve_json(state.latest_state)
    def _get_hardware(self, parsed):          self._serve_json(check_hardware())
    def _get_report(self, parsed):            self._serve_json(state.latest_report)
    def _get_specs(self, parsed):             self._serve_json(VEHICLE_SPECS)
    def _get_problems(self, parsed):          self._serve_json(COMMON_PROBLEMS)
    def _get_service(self, parsed):           self._serve_json(SERVICE_SCHEDULE)
    def _get_emergency(self, parsed):         self._serve_json(EMERGENCY_PROCEDURES)
    def _get_torque(self, parsed):            self._serve_json(TORQUE_SPECS)
    def _get_fuses(self, parsed):             self._serve_json(FUSE_REFERENCE)

    def _get_mechanic_search(self, parsed):
        q = parse_qs(parsed.query).get('q', [''])[0]
        self._serve_json({'query': q, 'results': mechanic_search(q)})

    def _get_mechanic_advice(self, parsed):
        msg = parse_qs(parsed.query).get('alert', [''])[0]
        self._serve_json({'alert': msg, 'advice': get_advice_for_alert(msg) or []})

    def _get_training(self, parsed):
        # TRAINING_MODULES is optional — absent in the current KB.
        try:
            from mechanic import TRAINING_MODULES  # type: ignore
            self._serve_json(TRAINING_MODULES)
        except ImportError:
            self._serve_json([])

    def _get_tsb(self, parsed):
        try:
            from mechanic import TECHNICAL_BULLETINS  # type: ignore
            self._serve_json(TECHNICAL_BULLETINS)
        except ImportError:
            self._serve_json([])

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
        code = parsed.path.rsplit('/', 1)[-1].upper()
        if not _DTC_RE.match(code):
            self.send_error(400, 'Invalid DTC code')
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
        self.send_error(404)

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

    Exposed at module scope so tests and the standalone llm_mechanic
    service can reuse it without instantiating a handler.
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

    # Knowledge-base top-5 hits, reformatted into short blocks.
    kb_lines = []
    for r in mechanic_search(query)[:5]:
        if r.get('type') == 'problem':
            p = r['data']
            kb_lines.append(
                f"KNOWN ISSUE: {p['title']}\n"
                f"Cause: {p.get('cause', '')}\n"
                f"Fix: {p.get('fix', '')}\n"
                f"Cost: {p.get('cost', 'Unknown')}"
            )
        elif r.get('type') == 'dtc':
            d = r['data']
            kb_lines.append(
                f"DTC: {d.get('code', '')} — {d.get('desc', '')}\n"
                f"Causes: {', '.join(d.get('causes', []))}"
            )
        elif r.get('type') == 'telemetry_guide':
            kb_lines.append(f"GUIDE: {r.get('title', '')}")
    if kb_lines:
        context_parts.append("RELEVANT KNOWLEDGE:\n" + "\n---\n".join(kb_lines))

    return query + ("\n\n---\n\n" + "\n\n".join(context_parts) if context_parts else "")


# Populate the exact-match route table AFTER the methods exist.
DashboardHandler._EXACT_GET_ROUTES = {
    '/':                          DashboardHandler._serve_dashboard_page,
    '/index.html':                DashboardHandler._serve_dashboard_page,
    '/mechanic':                  DashboardHandler._serve_mechanic_page,
    '/settings':                  DashboardHandler._serve_settings_page,
    '/healthz':                   DashboardHandler._get_healthz,
    '/api/settings':              DashboardHandler._get_settings,
    '/api/state':                 DashboardHandler._get_state,
    '/api/hardware':              DashboardHandler._get_hardware,
    '/api/report':                DashboardHandler._get_report,
    '/api/reports':               DashboardHandler._get_reports,
    '/api/sessions':              DashboardHandler._get_sessions,
    '/api/wardrive':              DashboardHandler._get_wardrive,
    '/api/mechanic/search':       DashboardHandler._get_mechanic_search,
    '/api/mechanic/advice':       DashboardHandler._get_mechanic_advice,
    '/api/mechanic/specs':        DashboardHandler._get_specs,
    '/api/mechanic/problems':     DashboardHandler._get_problems,
    '/api/mechanic/service':      DashboardHandler._get_service,
    '/api/mechanic/emergency':    DashboardHandler._get_emergency,
    '/api/mechanic/torque':       DashboardHandler._get_torque,
    '/api/mechanic/fuses':        DashboardHandler._get_fuses,
    '/api/mechanic/training':     DashboardHandler._get_training,
    '/api/mechanic/tsb':          DashboardHandler._get_tsb,
}

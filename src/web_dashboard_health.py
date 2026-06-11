"""/healthz payload + systemd/heartbeat helpers for the web dashboard.

Extracted verbatim from web_dashboard_handlers.py (non-security HUD helper
group). The handler module re-imports these names so the public API at
web_dashboard_handlers.X is unchanged.

_healthz_payload resolves _systemctl_active / _heartbeat_fresh /
MODE_STATE_PATH through the web_dashboard_handlers module at call time so the
test-suite's monkeypatch.setattr(h, ...) keeps working after the split. The
import is lazy (inside the function) to avoid a load-time import cycle —
web_dashboard_handlers imports this module at the top.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

import web_dashboard_state as state
from config import (
    DEFAULT_MODE,
    MODES,
    SERVICES,
)

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
    # Resolve the patchable dependencies through the handler module so the
    # test suite's monkeypatch.setattr(h, '_systemctl_active'|'_heartbeat_fresh'
    # |'MODE_STATE_PATH', ...) still affects this function after extraction.
    import web_dashboard_handlers as _wdh
    _systemctl_active_fn = _wdh._systemctl_active
    _heartbeat_fresh_fn = _wdh._heartbeat_fresh
    _mode_state_path = _wdh.MODE_STATE_PATH

    now = time.time()
    if (_healthz_cache['payload'] is not None
            and now - _healthz_cache['ts'] < _HEALTHZ_TTL):
        return _healthz_cache['payload'], _healthz_cache['http_status']

    services = {svc: _systemctl_active_fn(svc) for svc in SERVICES}
    for svc, (hb_path, max_age) in _CAPABILITY_HEARTBEATS.items():
        if services.get(svc) and not _heartbeat_fresh_fn(hb_path, max_age, now):
            services[svc] = False
    # Mode-aware failure: only services the current mode wants running count
    # toward the "failed" list. Drive-only services being inactive in FOOT mode
    # is the correct state, not a degradation.
    try:
        mode = (Path(_mode_state_path).read_text(encoding='utf-8').strip()
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
        # In-car SPI LCD: inactive (exits hw-pending) until /dev/fb1 is wired.
        # (fbmirror, the old mutually-exclusive fb0->fb1 mirror, was retired in
        # favour of lcd as the sole dash service.)
        'drifter-lcd',
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

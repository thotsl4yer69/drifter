#!/usr/bin/env python3
"""
MZ1312 DRIFTER — AI Diagnostics (Tier 2)

On-demand Claude-backed diagnosis. Triggers when:
  - a safety alert reaches AMBER+,
  - an active DTC appears,
  - or a Vivi/UI client asks for "diagnose now".

Pulls a fresh window from telemetry_batcher, looks up X-Type DTC context,
and asks the LLM cascade for a structured diagnosis. Result is published
back on TOPICS['ai_diag_response'].

Threading model: MQTT thread (paho loop) handles inbound messages and
schedules diagnoses on a single worker thread via a 1-slot queue. We do
not spawn an unbounded number of threads — one in-flight diagnosis at a
time, with a cooldown and a daily token budget cap.

UNCAGED TECHNOLOGY — EST 1991
"""

import json
import logging
import os
import queue
import signal
import socket
import threading
import time

import paho.mqtt.client as mqtt

import llm_client_v2
from config import (
    LEVEL_AMBER,
    MQTT_HOST,
    MQTT_PORT,
    TOPICS,
    VEHICLE,
    VEHICLE_ENGINE,
    VEHICLE_YEAR,
    XTYPE_DTC_LOOKUP,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [AIDIAG] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

# ── Tunables ──
MIN_INTERVAL_S = 20.0           # min seconds between non-manual diagnoses
MAX_PROMPT_CHARS = 12000        # cap prompt size before LLM call
MAX_RESPONSE_TOKENS = 900       # per-call ceiling
DAILY_TOKEN_BUDGET = 200_000    # soft cap — log warning above, refuse new auto requests
DEDUP_WINDOW_S = 60             # ignore identical (reason, dtc-fingerprint) within window

# ── Shared state (guarded by _state_lock) ──
_state_lock = threading.RLock()
_last_window: dict = {}
_active_dtcs: list = []
_pending_dtcs: list = []
_last_alert: dict = {}
_last_weather: dict = {}   # latest drifter/weather/current snapshot
_mqtt_connected = False

# Token usage tracking — per UTC day
_tokens_used = 0
_tokens_day = time.gmtime().tm_yday

# Dedup cache: {fingerprint: ts}
_recent_fingerprints: dict[str, float] = {}

# Single-slot queue — newest pending request wins. A worker thread pulls
# from this. Manual requests skip cooldown but still queue.
_request_queue: "queue.Queue[tuple[str, bool]]" = queue.Queue(maxsize=4)
_worker_running = threading.Event()

SYSTEM_PROMPT = (
    f"You are the in-vehicle diagnostic specialist for a {VEHICLE}. "
    "You receive a 60s telemetry window plus any DTCs, current weather, and the "
    "safety event that triggered the request. Factor weather into your reasoning — "
    "this engine runs richer when cold, is prone to heat-soak in high ambient temps, "
    "and humidity/IAT shifts move fuel trims. Respond with JSON only — no prose "
    "around it.\n\n"
    "Schema:\n"
    "{\n"
    '  "primary_suspect": {"diagnosis": str, "confidence": int 0-100, '
    '"evidence": str, "confirm_with": str},\n'
    '  "secondary_suspects": [{"diagnosis": str, "confidence": int, "evidence": str}],\n'
    '  "watch_items": [str], "action_items": [str], "safety_critical": bool, "safety_note": str\n'
    "}\n"
    "Cite specific telemetry values. Lead with safety-critical issues."
)


def _budget_check_and_account(estimated_tokens: int, manual: bool) -> bool:
    """Roll the daily token counter and decide whether to proceed.

    Manual requests are always allowed (we don't refuse a user asking).
    Automatic requests refuse once the daily cap is exceeded.
    Returns True if the request may proceed.
    """
    global _tokens_used, _tokens_day
    with _state_lock:
        today = time.gmtime().tm_yday
        if today != _tokens_day:
            _tokens_day = today
            _tokens_used = 0
        if not manual and _tokens_used + estimated_tokens > DAILY_TOKEN_BUDGET:
            log.warning(
                f"Daily token budget reached "
                f"({_tokens_used}/{DAILY_TOKEN_BUDGET}) — skipping auto diagnosis"
            )
            return False
    return True


def _account_tokens(tokens: int) -> None:
    global _tokens_used
    if not tokens:
        return
    with _state_lock:
        _tokens_used += int(tokens)


def _dtc_fingerprint() -> str:
    """Stable signature of the current DTC + alert set for dedup."""
    with _state_lock:
        return "|".join([
            ",".join(sorted(_active_dtcs)),
            ",".join(sorted(_pending_dtcs)),
            str(_last_alert.get('key', '')),
        ])


def _is_duplicate(reason: str) -> bool:
    """Suppress repeat work — same reason + DTC fingerprint within window."""
    fp = f"{reason}::{_dtc_fingerprint()}"
    now = time.time()
    with _state_lock:
        # GC old entries
        stale = [k for k, ts in _recent_fingerprints.items()
                 if now - ts > DEDUP_WINDOW_S]
        for k in stale:
            _recent_fingerprints.pop(k, None)
        last = _recent_fingerprints.get(fp)
        if last is not None and now - last < DEDUP_WINDOW_S:
            return True
        _recent_fingerprints[fp] = now
    return False


def _build_prompt(reason: str) -> str:
    """Build the user-prompt portion (snapshot + DTCs + alert).

    Holds the lock briefly to snapshot state, then renders outside the lock.
    """
    with _state_lock:
        window = dict(_last_window) if isinstance(_last_window, dict) else {}
        active = list(_active_dtcs)
        pending = list(_pending_dtcs)
        alert = dict(_last_alert) if isinstance(_last_alert, dict) else {}
        weather = dict(_last_weather) if isinstance(_last_weather, dict) else {}

    metrics = window.get('metrics') or {}
    if not isinstance(metrics, dict):
        metrics = {}

    metrics_lines = []
    for k in sorted(metrics):
        v = metrics[k]
        if not isinstance(v, dict):
            continue
        metrics_lines.append(
            f"  {k}: mean={v.get('mean')} min={v.get('min')} "
            f"max={v.get('max')} stddev={v.get('stddev')} last={v.get('last')}"
        )

    dtc_lines: list[str] = []
    for code in active[:5]:
        info = XTYPE_DTC_LOOKUP.get(code)
        if info:
            cause = (info.get('cause') or '')[:120]
            dtc_lines.append(
                f"  {code} ({info.get('severity', '?')}): {info.get('desc', '?')} — {cause}"
            )
        else:
            dtc_lines.append(f"  {code}: (no X-Type lookup)")
    for code in pending[:3]:
        info = XTYPE_DTC_LOOKUP.get(code)
        dtc_lines.append(
            f"  PENDING {code}: {info.get('desc', '(unknown)') if info else '(unknown)'}"
        )

    parts = [
        f"VEHICLE: {VEHICLE_YEAR} {VEHICLE} ({VEHICLE_ENGINE})",
        f"REASON: {reason}",
        "",
        "TELEMETRY WINDOW (last 60s):",
        '\n'.join(metrics_lines) or "  (no data)",
    ]
    if dtc_lines:
        parts.append("")
        parts.append("DIAGNOSTIC CODES:")
        parts.extend(dtc_lines)
    if alert:
        msg = str(alert.get('message', ''))[:300]
        parts.append("")
        parts.append(f"TRIGGERING ALERT: {msg}")
    if weather:
        parts.append("")
        parts.append(
            "WEATHER: "
            f"{weather.get('description', weather.get('condition', '?'))}, "
            f"{weather.get('temp_c')}°C (feels {weather.get('feels_like_c')}°C), "
            f"humidity {weather.get('humidity')}%, "
            f"wind {weather.get('wind_kph')} km/h"
        )

    text = '\n'.join(parts)
    if len(text) > MAX_PROMPT_CHARS:
        text = text[:MAX_PROMPT_CHARS] + "\n... [truncated]"
    return text


def _run_diagnosis(client: mqtt.Client, reason: str, manual: bool) -> None:
    """Execute one diagnosis end-to-end. Publishes status + response."""
    # Rough token estimate: ~1 token per 4 chars for prompt + headroom for response.
    estimated = (len(SYSTEM_PROMPT) + MAX_PROMPT_CHARS) // 4 + MAX_RESPONSE_TOKENS
    if not _budget_check_and_account(estimated, manual):
        try:
            client.publish(TOPICS['ai_diag_status'], json.dumps({
                'state': 'budget_exceeded',
                'reason': reason,
                'ts': time.time(),
            }))
        except Exception:
            pass
        return

    try:
        client.publish(TOPICS['ai_diag_status'], json.dumps({
            'state': 'running',
            'reason': reason,
            'ts': time.time(),
        }))
    except Exception as e:
        log.warning(f"status publish failed: {e}")

    try:
        prompt = _build_prompt(reason)
        result = llm_client_v2.query_json(
            prompt, SYSTEM_PROMPT, max_tokens=MAX_RESPONSE_TOKENS
        )
        _account_tokens(int(result.get('tokens') or 0))

        diagnosis = result.get('json')
        if diagnosis is not None and not isinstance(diagnosis, dict):
            diagnosis = None

        payload = {
            'reason': reason,
            'manual': manual,
            'ts': time.time(),
            'model': result.get('model'),
            'backend': result.get('backend'),
            'tokens': result.get('tokens'),
            'cached': result.get('cached', False),
            'parse_error': result.get('parse_error'),
            'diagnosis': diagnosis,
            'raw': result.get('text') if result.get('parse_error') else None,
        }
        try:
            client.publish(TOPICS['ai_diag_response'],
                           json.dumps(payload), retain=True)
        except Exception as e:
            log.error(f"response publish failed: {e}")

        if diagnosis:
            primary = diagnosis.get('primary_suspect') or {}
            log.info(
                f"Diagnosis: {primary.get('diagnosis', '?')} "
                f"({primary.get('confidence', '?')}%) "
                f"[{payload['backend']}, {payload['tokens']} tok]"
            )
        else:
            log.warning("LLM response did not parse as JSON")
    except Exception as e:
        log.error(f"Diagnosis failed: {e}")
        try:
            client.publish(TOPICS['ai_diag_response'], json.dumps({
                'error': str(e)[:300],
                'reason': reason,
                'ts': time.time(),
            }))
        except Exception:
            pass
    finally:
        try:
            client.publish(TOPICS['ai_diag_status'], json.dumps({
                'state': 'idle',
                'ts': time.time(),
            }))
        except Exception:
            pass


def _worker_loop(client: mqtt.Client) -> None:
    """Pull requests off the queue one at a time and run them sequentially."""
    last_auto_run = 0.0
    while _worker_running.is_set():
        try:
            reason, manual = _request_queue.get(timeout=0.5)
        except queue.Empty:
            continue

        now = time.time()
        if not manual and now - last_auto_run < MIN_INTERVAL_S:
            log.info("Cooldown active — dropping auto diagnosis request")
            continue
        if _is_duplicate(reason):
            log.info(f"Dedup hit — skipping repeat diagnosis: {reason[:60]}")
            continue
        try:
            _run_diagnosis(client, reason, manual)
        except Exception as e:
            log.error(f"worker loop crashed: {e}")
        if not manual:
            last_auto_run = time.time()


def _enqueue(reason: str, manual: bool) -> None:
    """Non-blocking enqueue — drop new if queue is full."""
    try:
        _request_queue.put_nowait((reason, manual))
    except queue.Full:
        log.info("Diagnosis queue full — dropping request")


def on_message(client, userdata, msg) -> None:
    global _last_window, _active_dtcs, _pending_dtcs, _last_alert, _last_weather
    try:
        data = json.loads(msg.payload)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
        return

    topic = msg.topic
    if topic == TOPICS['telemetry_window']:
        if isinstance(data, dict):
            with _state_lock:
                _last_window = data
        return

    if topic == TOPICS['weather_current']:
        if isinstance(data, dict):
            with _state_lock:
                _last_weather = data
        return

    if topic == TOPICS['dtc'] and isinstance(data, dict):
        stored = data.get('stored') or []
        pending = data.get('pending') or []
        if not isinstance(stored, list):
            stored = []
        if not isinstance(pending, list):
            pending = []
        with _state_lock:
            _active_dtcs = [str(c) for c in stored][:20]
            _pending_dtcs = [str(c) for c in pending][:20]
        if _active_dtcs:
            _enqueue(f"DTC: {','.join(_active_dtcs[:3])}", manual=False)
        return

    if topic == TOPICS['alert_message'] and isinstance(data, dict):
        with _state_lock:
            _last_alert = data
        try:
            level = int(data.get('level', 0))
        except (TypeError, ValueError):
            level = 0
        if level >= LEVEL_AMBER:
            msg_text = str(data.get('message', ''))[:80]
            _enqueue(f"alert: {msg_text}", manual=False)
        return

    if topic == TOPICS['ai_diag_request']:
        if isinstance(data, dict):
            reason = str(data.get('reason', 'manual'))[:120]
        else:
            reason = 'manual'
        _enqueue(reason, manual=True)


def on_connect(client, userdata, flags, rc) -> None:
    global _mqtt_connected
    if rc != 0:
        log.warning(f"MQTT connect failed rc={rc}")
        return
    with _state_lock:
        _mqtt_connected = True
    client.subscribe([
        (TOPICS['telemetry_window'], 0),
        (TOPICS['dtc'], 1),
        (TOPICS['alert_message'], 1),
        (TOPICS['ai_diag_request'], 1),
        (TOPICS['weather_current'], 0),
    ])
    log.info("MQTT connected — subscriptions active")


def on_disconnect(client, userdata, rc) -> None:
    global _mqtt_connected
    with _state_lock:
        _mqtt_connected = False
    if rc != 0:
        log.warning(f"MQTT disconnected unexpectedly rc={rc} — paho will reconnect")


def _make_client_id() -> str:
    return f"drifter-aidiag-{socket.gethostname()}-{os.getpid()}"


def main() -> None:
    log.info("DRIFTER AI Diagnostics starting...")

    running = True

    def _handle_signal(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    try:
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION1,  # type: ignore[attr-defined]
            client_id=_make_client_id(),
        )
    except AttributeError:
        client = mqtt.Client(client_id=_make_client_id())

    client.on_message = on_message
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect

    client.will_set(
        TOPICS['ai_diag_status'],
        json.dumps({'state': 'offline', 'ts': time.time()}),
        qos=0,
        retain=True,
    )

    connected = False
    while not connected and running:
        try:
            client.connect(MQTT_HOST, MQTT_PORT, 60)
            connected = True
        except Exception as e:
            log.warning(f"Waiting for MQTT broker... ({e})")
            time.sleep(3)

    if not running:
        return

    client.loop_start()

    _worker_running.set()
    worker = threading.Thread(
        target=_worker_loop, args=(client,), name="aidiag-worker", daemon=True
    )
    worker.start()

    try:
        client.publish(TOPICS['ai_diag_status'], json.dumps({
            'state': 'idle', 'ts': time.time(),
        }), retain=True)
    except Exception as e:
        log.warning(f"initial status publish failed: {e}")
    log.info("AI Diagnostics LIVE")

    while running:
        time.sleep(0.5)

    log.info("Stopping worker...")
    _worker_running.clear()
    worker.join(timeout=5)

    try:
        client.publish(TOPICS['ai_diag_status'], json.dumps({
            'state': 'offline', 'ts': time.time(),
        }), retain=True)
    except Exception:
        pass

    client.loop_stop()
    try:
        client.disconnect()
    except Exception:
        pass
    log.info("AI Diagnostics stopped")


if __name__ == '__main__':
    main()

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
UNCAGED TECHNOLOGY — EST 1991
"""

import json
import logging
import signal
import threading
import time
from typing import Optional

import paho.mqtt.client as mqtt

import llm_client_v2
from config import (
    MQTT_HOST, MQTT_PORT, TOPICS,
    XTYPE_DTC_LOOKUP, VEHICLE, VEHICLE_YEAR, VEHICLE_ENGINE,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [AIDIAG] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

# ── State ──
_last_window: dict = {}
_active_dtcs: list = []
_pending_dtcs: list = []
_last_alert: dict = {}
_inflight = False
_inflight_lock = threading.Lock()
_last_response_ts = 0.0
MIN_INTERVAL_S = 20.0

SYSTEM_PROMPT = (
    "You are the in-vehicle diagnostic specialist for a {vehicle}. "
    "You receive a 60s telemetry window plus any DTCs and the safety event that "
    "triggered the request. Respond with JSON only — no prose around it.\n\n"
    "Schema:\n"
    "{{\n"
    '  "primary_suspect": {{"diagnosis": str, "confidence": int 0-100, '
    '"evidence": str, "confirm_with": str}},\n'
    '  "secondary_suspects": [{{"diagnosis": str, "confidence": int, "evidence": str}}],\n'
    '  "watch_items": [str], "action_items": [str], "safety_critical": bool, "safety_note": str\n'
    "}}\n"
    "Cite specific telemetry values. Lead with safety-critical issues."
).format(vehicle=VEHICLE)


def _build_prompt(reason: str) -> str:
    metrics = _last_window.get('metrics', {})
    metrics_lines = [
        f"  {k}: mean={v['mean']} min={v['min']} max={v['max']} stddev={v['stddev']} last={v['last']}"
        for k, v in sorted(metrics.items())
    ]

    dtc_lines = []
    for code in _active_dtcs[:5]:
        info = XTYPE_DTC_LOOKUP.get(code)
        if info:
            dtc_lines.append(f"  {code} ({info['severity']}): {info['desc']} — {info['cause'][:120]}")
        else:
            dtc_lines.append(f"  {code}: (no X-Type lookup)")
    for code in _pending_dtcs[:3]:
        info = XTYPE_DTC_LOOKUP.get(code)
        dtc_lines.append(f"  PENDING {code}: {info['desc'] if info else '(unknown)'}")

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
    if _last_alert:
        parts.append("")
        parts.append(f"TRIGGERING ALERT: {_last_alert.get('message', '')[:300]}")
    return '\n'.join(parts)


def _run_diagnosis(client: mqtt.Client, reason: str) -> None:
    global _inflight, _last_response_ts
    with _inflight_lock:
        if _inflight:
            log.info("Diagnosis already in flight — skipping")
            return
        if time.time() - _last_response_ts < MIN_INTERVAL_S:
            log.info("Diagnosis cooldown active — skipping")
            return
        _inflight = True
    client.publish(TOPICS['ai_diag_status'], json.dumps({
        'state': 'running',
        'reason': reason,
        'ts': time.time(),
    }))
    try:
        prompt = _build_prompt(reason)
        result = llm_client_v2.query_json(prompt, SYSTEM_PROMPT, max_tokens=900)
        payload = {
            'reason': reason,
            'ts': time.time(),
            'model': result.get('model'),
            'backend': result.get('backend'),
            'tokens': result.get('tokens'),
            'parse_error': result.get('parse_error'),
            'diagnosis': result.get('json'),
            'raw': result.get('text') if result.get('parse_error') else None,
        }
        client.publish(TOPICS['ai_diag_response'], json.dumps(payload), retain=True)
        _last_response_ts = time.time()
        if payload['diagnosis']:
            primary = payload['diagnosis'].get('primary_suspect', {})
            log.info(f"Diagnosis: {primary.get('diagnosis', '?')} "
                     f"({primary.get('confidence', '?')}%)")
        else:
            log.warning("LLM response did not parse as JSON")
    except Exception as e:
        log.error(f"Diagnosis failed: {e}")
        client.publish(TOPICS['ai_diag_response'], json.dumps({
            'error': str(e),
            'reason': reason,
            'ts': time.time(),
        }))
    finally:
        with _inflight_lock:
            _inflight = False
        client.publish(TOPICS['ai_diag_status'], json.dumps({
            'state': 'idle',
            'ts': time.time(),
        }))


def on_message(client, userdata, msg) -> None:
    global _last_window, _active_dtcs, _pending_dtcs, _last_alert
    try:
        data = json.loads(msg.payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return

    topic = msg.topic
    if topic == TOPICS['telemetry_window']:
        if isinstance(data, dict):
            _last_window = data
    elif topic == TOPICS['dtc']:
        _active_dtcs = data.get('stored', []) or []
        _pending_dtcs = data.get('pending', []) or []
        if _active_dtcs:
            threading.Thread(
                target=_run_diagnosis, args=(client, f"DTC: {','.join(_active_dtcs[:3])}"),
                daemon=True).start()
    elif topic == TOPICS['alert_message']:
        if isinstance(data, dict):
            _last_alert = data
            level = data.get('level', 0)
            if level >= 2:  # AMBER or RED
                threading.Thread(
                    target=_run_diagnosis,
                    args=(client, f"alert: {data.get('message', '')[:80]}"),
                    daemon=True).start()
    elif topic == TOPICS['ai_diag_request']:
        reason = "manual" if not isinstance(data, dict) else data.get('reason', 'manual')
        threading.Thread(target=_run_diagnosis, args=(client, reason), daemon=True).start()


def main() -> None:
    log.info("DRIFTER AI Diagnostics starting...")

    running = True

    def _handle_signal(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    client = mqtt.Client(client_id="drifter-aidiag")
    client.on_message = on_message

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

    client.subscribe([
        (TOPICS['telemetry_window'], 0),
        (TOPICS['dtc'], 0),
        (TOPICS['alert_message'], 0),
        (TOPICS['ai_diag_request'], 0),
    ])
    client.loop_start()
    client.publish(TOPICS['ai_diag_status'], json.dumps({
        'state': 'idle', 'ts': time.time(),
    }), retain=True)
    log.info("AI Diagnostics LIVE")

    while running:
        time.sleep(0.5)

    client.loop_stop()
    client.disconnect()
    log.info("AI Diagnostics stopped")


if __name__ == '__main__':
    main()

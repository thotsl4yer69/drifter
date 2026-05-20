"""Shared state for the web dashboard.

Gathers every piece of module-level mutable state used by the server
(telemetry dict, client sets, asyncio loop handle) into one place so the
HTTP / WebSocket / MQTT halves can share it without importing each other
circularly.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from typing import Optional

log = logging.getLogger(__name__)

# ── Runtime flags & global singletons ────────────────────────────────
running: bool = True

# Latest value per MQTT topic, keyed by the topic with 'drifter/' stripped
# and slashes replaced by underscores (e.g. 'engine_rpm').
latest_state: dict = {}

# Most recent analysis report published on drifter/analysis/report.
latest_report: dict = {}

# Sets of asyncio.Queue instances — one per connected WebSocket client.
ws_clients: set = set()
audio_ws_clients: set = set()

# Bounded ring of the most recent alert_message payloads, oldest-first.
# alert_engine publishes the highest-priority active condition each cycle on
# drifter/alert/message; this deque keeps the last 50 so the cockpit drawer
# can render a real timeline instead of just the current-top alert.
recent_alerts: deque = deque(maxlen=50)

# Bounded ring of recent Flipper Zero sub-GHz captures. flipper_bridge
# publishes on drifter/flipper/subghz when its monitor catches a frame
# (key-fob, garage door, weather station, TPMS sensor, etc.). The deque
# keeps the last 50 so the cockpit can show a capture log and offer
# replay against any captured frame.
recent_flipper_captures: deque = deque(maxlen=50)

# Bounded ring of recent flipper_result payloads — every command sent to
# the Flipper produces a result (success/fail, risk class, HIGH-risk
# confirmation prompts, etc.). UI polls this to know what the bridge
# did with the last command.
recent_flipper_results: deque = deque(maxlen=50)

# Paho MQTT client — set by web_dashboard.main().
mqtt_client = None

# The asyncio loop running the WebSocket servers.  Set in web_dashboard.main()
# so the MQTT callback thread can call loop.call_soon_threadsafe() to
# enqueue broadcasts without touching asyncio internals directly.
_ws_loop: Optional[asyncio.AbstractEventLoop] = None


def set_ws_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Register the running asyncio loop for cross-thread broadcasts."""
    global _ws_loop
    _ws_loop = loop


def set_mqtt_client(client) -> None:
    """Register the paho client so HTTP handlers can publish commands."""
    global mqtt_client
    mqtt_client = client


def stop() -> None:
    """Signal the main loop to exit."""
    global running
    running = False


# ── MQTT message handling ────────────────────────────────────────────

def _broadcast_sync(msg: str) -> None:
    """Enqueue ``msg`` on every connected WS client's queue.

    Must run on the asyncio loop (scheduled via call_soon_threadsafe).
    A client with a full queue silently drops the frame — the dashboard
    reconnects on stall, so losing a frame is better than blocking.
    """
    for queue in list(ws_clients):
        try:
            queue.put_nowait(msg)
        except Exception:
            pass


def on_message(client, userdata, msg) -> None:
    """paho on_message callback: fan MQTT data out to state + websockets.

    Runs on paho's network thread. Pure reads of latest_state from other
    threads are safe under the GIL; iteration is done on snapshots (list()).
    """
    global latest_report
    try:
        data = json.loads(msg.payload)
        topic = msg.topic

        if topic == 'drifter/analysis/report':
            latest_report = data
            log.info("New diagnostic report received")
            return

        # Capture Flipper sub-GHz frames into a ring buffer for the RF
        # overlay's capture log. Every catch gets appended (we want the
        # full timeline, not just unique entries — replay is per-capture).
        if topic == 'drifter/flipper/subghz' and isinstance(data, dict):
            recent_flipper_captures.append(data)

        # Capture flipper_result so the cockpit can show command outcomes
        # (success/fail responses, HIGH-risk confirmation prompts).
        if topic == 'drifter/flipper/result' and isinstance(data, dict):
            recent_flipper_results.append(data)

        # Capture alert messages into a ring buffer for the Incidents tab.
        # Only append when the message text actually changes — alert_engine
        # republishes the same retained payload on every cooldown tick and
        # we'd otherwise pile up duplicates.
        if topic == 'drifter/alert/message' and isinstance(data, dict):
            msg = data.get('message')
            if msg and (not recent_alerts or recent_alerts[-1].get('message') != msg):
                recent_alerts.append({
                    'ts': data.get('ts') or time.time(),
                    'level': data.get('level'),
                    'name': data.get('name'),
                    'message': msg,
                })

        key = topic.replace('drifter/', '').replace('/', '_')
        latest_state[key] = data
        latest_state['_last_update'] = time.time()

        ws_msg = json.dumps({'topic': topic, 'data': data, 'ts': time.time()})
        if _ws_loop is not None:
            _ws_loop.call_soon_threadsafe(_broadcast_sync, ws_msg)
    except (json.JSONDecodeError, RuntimeError):
        pass

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

        key = topic.replace('drifter/', '').replace('/', '_')
        latest_state[key] = data
        latest_state['_last_update'] = time.time()

        ws_msg = json.dumps({'topic': topic, 'data': data, 'ts': time.time()})
        if _ws_loop is not None:
            _ws_loop.call_soon_threadsafe(_broadcast_sync, ws_msg)
    except (json.JSONDecodeError, RuntimeError):
        pass

#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Web Dashboard & Audio Bridge
Self-hosted live vehicle dashboard served over Wi-Fi hotspot.

Phone connects to MZ1312_DRIFTER Wi-Fi → opens browser → 10.42.0.1:8080
Live telemetry via WebSocket, zero apps needed.

Also serves TTS audio alerts as WAV over WebSocket for phone speaker output,
so your Jag speaks through the Pioneer via Android Auto / phone audio.

This module is the entry point / wiring layer only. The real work lives
in the sibling modules:

    web_dashboard_state.py      — shared state + MQTT callback
    web_dashboard_handlers.py   — HTTP DashboardHandler
    web_dashboard_html.py       — dashboard / mechanic / settings pages
    web_dashboard_hardware.py   — hardware & service probes
    web_dashboard_audio.py      — piper / espeak TTS → WAV

UNCAGED TECHNOLOGY — EST 1991
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import signal
import threading
import time

import paho.mqtt.client as mqtt

try:
    import websockets
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False

from config import MQTT_HOST, MQTT_PORT
from http.server import HTTPServer

import web_dashboard_state as state
from web_dashboard_handlers import DashboardHandler
from web_dashboard_audio import generate_audio_wav

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [DASHBOARD] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger(__name__)

WEB_PORT = 8080
WS_PORT = 8081
AUDIO_WS_PORT = 8082

# ── Audio de-duplication so the same alert doesn't loop forever ──────
_AUDIO_REPEAT_COOLDOWN = 60     # same text won't replay for this many seconds
_AUDIO_MIN_GAP        = 15      # any two alerts must be at least this far apart
_audio_lock = threading.Lock()
_last_audio_text = ""
_last_audio_time = 0.0


# ═══════════════════════════════════════════════════════════════════
#  HTTP server (handlers live in web_dashboard_handlers)
# ═══════════════════════════════════════════════════════════════════

def run_http_server() -> None:
    server = HTTPServer(('0.0.0.0', WEB_PORT), DashboardHandler)
    server.timeout = 2
    log.info("HTTP dashboard on http://0.0.0.0:%d", WEB_PORT)
    while state.running:
        server.handle_request()
    server.server_close()


# ═══════════════════════════════════════════════════════════════════
#  WebSocket servers (telemetry + audio)
# ═══════════════════════════════════════════════════════════════════

async def ws_handler(websocket):
    """Stream MQTT data to a browser client."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=200)
    state.ws_clients.add(queue)
    log.info("Dashboard client connected (%d total)", len(state.ws_clients))
    try:
        # Prime the client with the last known value for every topic so
        # gauges render immediately instead of waiting for the next MQTT
        # update.
        for key, data in state.latest_state.items():
            if key.startswith('_'):
                continue
            topic = 'drifter/' + key.replace('_', '/')
            await websocket.send(json.dumps({
                'topic': topic, 'data': data, 'ts': time.time(),
            }))

        while state.running:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=1.0)
                await websocket.send(msg)
            except asyncio.TimeoutError:
                try:
                    await websocket.ping()
                except Exception:
                    break
    except websockets.ConnectionClosed:
        pass
    finally:
        state.ws_clients.discard(queue)
        log.info("Dashboard client disconnected (%d remaining)",
                 len(state.ws_clients))


async def audio_ws_handler(websocket):
    """Hold the socket open; audio frames are pushed by broadcast_audio."""
    state.audio_ws_clients.add(websocket)
    log.info("Audio client connected (%d total)", len(state.audio_ws_clients))
    try:
        while state.running:
            await asyncio.sleep(1)
    except websockets.ConnectionClosed:
        pass
    finally:
        state.audio_ws_clients.discard(websocket)


async def broadcast_audio(wav_bytes: bytes) -> None:
    """Send a WAV payload to every connected audio client."""
    if not wav_bytes or not state.audio_ws_clients:
        return
    dead = set()
    for ws in state.audio_ws_clients:
        try:
            await ws.send(wav_bytes)
        except Exception:
            dead.add(ws)
    state.audio_ws_clients.difference_update(dead)


def _schedule_audio_broadcast(wav: bytes) -> None:
    """Send ``wav`` to all audio clients from any thread."""
    loop = state._ws_loop
    if loop is None:
        return
    try:
        loop.call_soon_threadsafe(
            lambda w=wav: asyncio.ensure_future(broadcast_audio(w))
        )
    except RuntimeError:
        pass


# ═══════════════════════════════════════════════════════════════════
#  Alert → Audio Bridge
# ═══════════════════════════════════════════════════════════════════

def on_alert_message(client, userdata, msg) -> None:
    """Route alert audio to connected phones.

    There are two channels:
      1. drifter/audio/wav  — pre-encoded WAV from voice_alerts service
         (preferred, uses the better piper voice).
      2. drifter/alert/message — fall back to generating TTS locally here,
         rate-limited so the same alert doesn't spam the phone speaker.
    """
    global _last_audio_text, _last_audio_time
    try:
        data = json.loads(msg.payload)

        if msg.topic == 'drifter/audio/wav':
            wav_b64 = data.get('wav_b64') if isinstance(data, dict) else None
            if not wav_b64:
                return
            try:
                wav_bytes = base64.b64decode(wav_b64)
            except Exception:
                return
            with _audio_lock:
                _last_audio_text = data.get('text', '')
                _last_audio_time = time.time()
            _schedule_audio_broadcast(wav_bytes)
            return

        if not isinstance(data, dict):
            return
        level = data.get('level', 0)
        message = data.get('message', '')
        if not message or level < 2:
            return

        now = time.time()
        with _audio_lock:
            # Dedupe / rate limit.
            if message == _last_audio_text and now - _last_audio_time < _AUDIO_REPEAT_COOLDOWN:
                return
            if now - _last_audio_time < _AUDIO_MIN_GAP:
                return

        prefix = "Critical alert. " if level >= 3 else "Warning. "
        wav = generate_audio_wav(prefix + message)
        if not wav:
            return

        with _audio_lock:
            _last_audio_text = message
            _last_audio_time = now
        _schedule_audio_broadcast(wav)
    except Exception as e:
        log.warning("Audio alert error: %s", e)


# ═══════════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════════

def _connect_mqtt(client_id: str, on_message, topics: list[str]):
    """Connect a paho client to the broker and subscribe to ``topics``.

    Returns the client on success, None if the dashboard is shutting down
    before the connection succeeds.
    """
    client = mqtt.Client(client_id=client_id)
    client.on_message = on_message
    while state.running:
        try:
            client.connect(MQTT_HOST, MQTT_PORT, 60)
            break
        except Exception as e:
            log.warning("Waiting for MQTT broker... (%s)", e)
            time.sleep(3)
    else:
        return None
    for t in topics:
        client.subscribe(t)
    client.loop_start()
    return client


async def _run_ws_servers():
    """Run telemetry + audio WebSocket servers concurrently."""
    async with websockets.serve(ws_handler, '0.0.0.0', WS_PORT):
        log.info("WebSocket telemetry on ws://0.0.0.0:%d", WS_PORT)
        async with websockets.serve(audio_ws_handler, '0.0.0.0',
                                    AUDIO_WS_PORT):
            log.info("WebSocket audio on ws://0.0.0.0:%d", AUDIO_WS_PORT)
            log.info("")
            log.info("=== DRIFTER DASHBOARD LIVE ===")
            log.info("  Open on phone: http://10.42.0.1:%d", WEB_PORT)
            log.info("  Local:         http://localhost:%d", WEB_PORT)
            log.info("  RealDash TCP:  10.42.0.1:35000 (still available)")
            log.info("")
            while state.running:
                await asyncio.sleep(0.5)


def main() -> None:
    log.info("DRIFTER Web Dashboard starting...")

    if not HAS_WEBSOCKETS:
        log.error("websockets package not installed. Run: pip install websockets")
        return

    def _handle_signal(sig, frame):
        state.stop()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    # ── MQTT: telemetry ingest ──
    telem = _connect_mqtt("drifter-dashboard", state.on_message, ["drifter/#"])
    if telem is None:
        return
    state.set_mqtt_client(telem)

    # ── MQTT: alert audio (separate client so a slow audio callback can't
    #     block the telemetry ingest path) ──
    audio = _connect_mqtt(
        "drifter-dashboard-audio", on_alert_message,
        ["drifter/alert/message", "drifter/audio/wav"],
    )

    # ── HTTP Server Thread ──
    threading.Thread(target=run_http_server, daemon=True).start()

    # ── Async Event Loop (WebSocket servers) ──
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    state.set_ws_loop(loop)

    try:
        loop.run_until_complete(_run_ws_servers())
    except Exception as e:
        if state.running:
            log.error("WebSocket server error: %s", e)

    telem.loop_stop()
    telem.disconnect()
    if audio is not None:
        audio.loop_stop()
        audio.disconnect()
    log.info("Dashboard stopped")


if __name__ == '__main__':
    main()

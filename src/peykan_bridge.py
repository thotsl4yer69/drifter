#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Optional Peykan MCP → MQTT bridge.
Read-only snapshot and recorded-log analysis; no vehicle transmit tools.
UNCAGED TECHNOLOGY — EST 1991
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import re
import signal
import time
from urllib.parse import urlsplit

from config import MQTT_HOST, MQTT_PORT, TOPICS, make_mqtt_client
from peykan_context import normalize_snapshot

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [PEYKAN] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

MCP_URL = os.getenv("DRIFTER_PEYKAN_MCP_URL", "http://127.0.0.1:6278/mcp")
MODE = os.getenv("DRIFTER_PEYKAN_MODE", "")
POLL_SECONDS = max(2.0, min(30.0, float(os.getenv("DRIFTER_PEYKAN_POLL_S", "5"))))
REQUESTS: queue.Queue[dict] = queue.Queue(maxsize=4)
_LOG_NAME = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}$")
_ALLOWED_SUFFIXES = (".asc", ".blf", ".trc", ".log", ".csv")


def validate_config(url: str, mode: str) -> None:
    """Only connect to our local MCP process; never an untrusted remote agent."""
    parts = urlsplit(url)
    if (parts.scheme != "http" or parts.hostname not in ("127.0.0.1", "localhost")
            or parts.path != "/mcp" or parts.username or parts.password
            or parts.query or parts.fragment):
        raise ValueError("Peykan MCP must be a loopback HTTP /mcp endpoint")
    if mode not in ("demo", "socketcan"):
        raise ValueError("DRIFTER_PEYKAN_MODE must be demo or socketcan")


def validate_log_name(name: object) -> str | None:
    if not isinstance(name, str) or not _LOG_NAME.fullmatch(name):
        return None
    if name == "sample" or name.lower().endswith(_ALLOWED_SUFFIXES):
        return name
    return None


def _tool_data(result: object) -> dict:
    if getattr(result, "isError", False):
        raise RuntimeError("Peykan tool returned an error")
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured
    for block in getattr(result, "content", []) or []:
        raw = getattr(block, "text", None)
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if isinstance(parsed, dict):
                return parsed
    raise ValueError("Peykan returned no structured JSON object")


def _publish(client, key: str, content: dict, *, retain: bool = False) -> None:
    client.publish(TOPICS[key], json.dumps(content, allow_nan=False), qos=0, retain=retain)


def _on_message(_client, _userdata, message) -> None:
    if message.topic != TOPICS["peykan_analysis_request"] or message.retain:
        return
    try:
        req = json.loads(message.payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return
    if not isinstance(req, dict):
        return
    name = validate_log_name(req.get("path"))
    if not name:
        return
    task = {"path": name, "id": str(req.get("id", ""))[:64]}
    try:
        REQUESTS.put_nowait(task)
    except queue.Full:
        log.warning("Peykan analysis queue full; dropping request")


async def _run_client(client, running: asyncio.Event) -> None:
    # Optional dependency: install in isolated Peykan venv via scripts/install-peykan.sh.
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(MCP_URL) as (reader, writer, _):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            log.info("Peykan MCP session established (%s)", MODE)
            while not running.is_set():
                res = await asyncio.wait_for(
                    session.call_tool("get_vehicle_snapshot"), timeout=15)
                snapshot = normalize_snapshot(_tool_data(res), mode=MODE)
                _publish(client, "peykan_snapshot", snapshot)
                _publish(client, "peykan_status", {
                    "status": snapshot["status"],
                    "source": "peykan", "mode": MODE,
                    "signals": len(snapshot["signals"]), "ts": snapshot["ts"],
                }, retain=True)
                for _ in range(2):
                    try:
                        request = REQUESTS.get_nowait()
                    except queue.Empty:
                        break
                    try:
                        result = await asyncio.wait_for(
                            session.call_tool("analyze_can_log",
                                              {"path": request["path"]}), timeout=30)
                        data = _tool_data(result)
                        # Bound MQTT messages; never publish oversized logs.
                        if len(json.dumps(data)) > 24000:
                            raise ValueError("analysis result exceeds MQTT size limit")
                        reply = {"id": request["id"], "path": request["path"],
                                 "status": "ok", "data": data, "ts": time.time()}
                    except Exception as exc:
                        reply = {"id": request["id"], "path": request["path"],
                                 "status": "error", "message": str(exc)[:180],
                                 "ts": time.time()}
                    _publish(client, "peykan_analysis_result", reply)
                try:
                    await asyncio.wait_for(running.wait(), timeout=POLL_SECONDS)
                except TimeoutError:
                    pass


async def run() -> None:
    validate_config(MCP_URL, MODE)
    client = make_mqtt_client("drifter-peykan-bridge")
    client.on_message = _on_message
    client.will_set(TOPICS["peykan_status"],
                    json.dumps({"status": "offline", "source": "peykan", "ts": 0}),
                    retain=True)
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    client.connect(MQTT_HOST, MQTT_PORT, 60)
    client.subscribe(TOPICS["peykan_analysis_request"], qos=0)
    client.loop_start()
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stopping.set)
        except NotImplementedError:
            pass
    try:
        while not stopping.is_set():
            try:
                await _run_client(client, stopping)
            except (Exception, ExceptionGroup) as exc:
                log.warning("Peykan unavailable: %s", exc)
                _publish(client, "peykan_status", {
                    "status": "unavailable", "source": "peykan", "mode": MODE,
                    "ts": time.time(),
                }, retain=True)
                try:
                    await asyncio.wait_for(stopping.wait(), timeout=5)
                except TimeoutError:
                    pass
    finally:
        _publish(client, "peykan_status", {
            "status": "offline", "source": "peykan", "mode": MODE,
            "ts": time.time(),
        }, retain=True)
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    asyncio.run(run())

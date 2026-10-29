#!/usr/bin/env python3
"""
MZ1312 DRIFTER — On-device Peykan deployment acceptance.
Verify the actual MCP service + MQTT bridge without touching vehicle ECUs.
UNCAGED TECHNOLOGY — EST 1991
"""

from __future__ import annotations

import argparse
import asyncio
import json
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from config import MQTT_HOST, MQTT_PORT, TOPICS, make_mqtt_client
    from peykan_context import vivi_context
except ModuleNotFoundError:
    # Run directly out of a repository checkout during the CI simulator test.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from config import MQTT_HOST, MQTT_PORT, TOPICS, make_mqtt_client
    from peykan_context import vivi_context


def _decoded(result) -> dict:
    if getattr(result, "isError", False):
        raise RuntimeError("Peykan MCP tool returned an error")
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured
    for item in getattr(result, "content", []):
        try:
            value = json.loads(item.text)
        except (AttributeError, ValueError, TypeError):
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("MCP tool did not return a JSON object")


def _http_health() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:6278/healthz", timeout=2) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


async def _mcp_check(mode: str, until: float) -> None:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    last_error: Exception | None = None
    while time.monotonic() < until:
        try:
            if not _http_health():
                raise ConnectionError("MCP /healthz not responding")
            async with streamable_http_client("http://127.0.0.1:6278/mcp") as (
                reader, writer
            ):
                async with ClientSession(reader, writer) as session:
                    await asyncio.wait_for(session.initialize(), timeout=7)
                    result = _decoded(await asyncio.wait_for(
                        session.call_tool("get_vehicle_snapshot"), timeout=7
                    ))
                    if mode == "demo" and not result.get("signals"):
                        raise RuntimeError("virtual ECU simulator has no decoded signals")
                    # Ask for genuine shipped recorded CAN evidence, not a stub result.
                    analysis = _decoded(await asyncio.wait_for(
                        session.call_tool("analyze_can_log", {"path": "sample"}),
                        timeout=10,
                    ))
                    if not analysis:
                        raise RuntimeError("sample CAN log analysis returned nothing")
                    print("PASS Peykan HTTP health + MCP snapshot + sample log analysis")
                    return
        except (Exception, ExceptionGroup) as exc:
            last_error = exc
            await asyncio.sleep(1)
    raise RuntimeError(f"Peykan MCP not ready: {last_error}") from last_error


def _mqtt_check(mode: str, until: float) -> None:
    """Check live evidence and a fresh analysis round-trip, not just retained status."""
    status_seen = threading.Event()
    analysis_seen = threading.Event()
    error = []
    request_id = "peykan-check-" + uuid.uuid4().hex[:12]

    client = make_mqtt_client("drifter-peykan-verify-" + uuid.uuid4().hex[:8])

    def on_connect(cli, _userdata, _flags, reason_code, _properties=None):
        if reason_code == 0:
            cli.subscribe([
                (TOPICS["peykan_status"], 0),
                (TOPICS["peykan_analysis_result"], 0),
            ])
            # Allow subscriptions to be registered before issuing this request;
            # retained status is delivered on subscribe.
        else:
            error.append(f"MQTT connection rejected: {reason_code}")

    def on_message(_cli, _userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        if not isinstance(payload, dict):
            return
        if msg.topic == TOPICS["peykan_status"]:
            timestamp = payload.get("ts")
            state = payload.get("status")
            if (isinstance(timestamp, (int, float))
                    and 0 <= time.time() - timestamp <= 20
                    and state in (("simulated",) if mode == "demo"
                                  else ("live", "no-data"))):
                status_seen.set()
        elif msg.topic == TOPICS["peykan_analysis_result"]:
            if payload.get("id") == request_id:
                if payload.get("status") != "ok" or not isinstance(payload.get("data"), dict):
                    error.append("Peykan MQTT CAN analysis failed")
                else:
                    analysis_seen.set()

    client.on_connect = on_connect
    client.on_message = on_message
    try:
        client.connect(MQTT_HOST, MQTT_PORT, 60)
        client.loop_start()
        while time.monotonic() < until and not status_seen.is_set():
            status_seen.wait(timeout=0.5)
            if error:
                break
        if not status_seen.is_set():
            raise RuntimeError("No fresh Peykan status from MQTT bridge: " +
                               "; ".join(error))
        client.publish(TOPICS["peykan_analysis_request"],
                       json.dumps({"id": request_id, "path": "sample"}), qos=0)
        while time.monotonic() < until and not analysis_seen.is_set():
            analysis_seen.wait(timeout=0.5)
            if error:
                break
        if not analysis_seen.is_set():
            raise RuntimeError("No successful MQTT CAN log analysis: " +
                               "; ".join(error))
        print(f"PASS Peykan MQTT ({mode}) + recorded-log round trip")
    finally:
        client.loop_stop()
        client.disconnect()


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify deployed Peykan without CAN writes")
    parser.add_argument("--mode", required=True, choices=("demo", "socketcan"))
    parser.add_argument("--timeout", type=int, default=45)
    args = parser.parse_args()
    until = time.monotonic() + max(15, min(120, args.timeout))
    try:
        asyncio.run(_mcp_check(args.mode, until))
        _mqtt_check(args.mode, time.monotonic() + 25)
    except Exception as exc:
        print(f"FAIL Peykan deployment check: {exc}", file=sys.stderr)
        return 1
    print("PASS Peykan deployment verified; no ECU transmit tools called")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

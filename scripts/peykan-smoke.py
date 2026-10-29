#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Full Peykan simulator → MCP → MQTT bridge smoke test.
Runs against the actual pinned Peykan distribution, not a fake implementation.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from config import MQTT_HOST, MQTT_PORT, TOPICS, make_mqtt_client
from peykan_context import vivi_context

ENV = {
    **os.environ,
    "DRIFTER_PEYKAN_MODE": "demo",
    "DRIFTER_PEYKAN_MCP_URL": "http://127.0.0.1:6278/mcp",
    "DRIFTER_PEYKAN_POLL_S": "2",
    "PEYKAN_CAN_INTERFACE": "virtual",
    "PEYKAN_CAN_CHANNEL": "bus0",
    "PEYKAN_MCP_HOST": "127.0.0.1",
    "PEYKAN_MCP_TRANSPORT": "streamable-http",
    "PEYKAN_ALLOW_TRANSMIT": "false",
    "PEYKAN_ALLOW_WRITE_SERVICES": "false",
    "PEYKAN_SIMULATOR_ON_HARDWARE": "false",
    "PEYKAN_J1939_ENABLED": "false",
}


def wait_for(predicate, timeout=30.0):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        result = predicate()
        if result:
            return result
        time.sleep(0.2)
    raise TimeoutError("Peykan smoke test condition not reached")


def healthy():
    try:
        with urllib.request.urlopen(
            "http://127.0.0.1:6278/healthz", timeout=1
        ) as result:
            return result.status == 200
    except OSError:
        return False


def main():
    messages = []
    client = make_mqtt_client("drifter-peykan-smoke")
    def on_message(_client, _userdata, msg):
        try:
            packet = json.loads(msg.payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        messages.append((msg.topic, packet))

    client.on_message = on_message
    client.connect(MQTT_HOST, MQTT_PORT, 60)
    client.subscribe([
        (TOPICS["peykan_snapshot"], 0),
        (TOPICS["peykan_status"], 0),
        (TOPICS["peykan_analysis_result"], 0),
    ])
    client.loop_start()
    processes = []
    try:
        server = subprocess.Popen(
            [sys.executable, str(SRC / "peykan_server.py")],
            env=ENV, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, text=True,
        )
        processes.append(server)
        wait_for(healthy)
        bridge = subprocess.Popen(
            [sys.executable, str(SRC / "peykan_bridge.py")],
            env=ENV, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, text=True,
        )
        processes.append(bridge)
        try:
            snap = wait_for(lambda: next(
                (p for t, p in messages
                 if t == TOPICS["peykan_snapshot"] and p.get("signals")), None
            ), timeout=35)
        except TimeoutError:
            print("MQTT messages:", messages[-12:], file=sys.stderr)
            for process in processes:
                process.terminate()
            for process in processes:
                _, stderr = process.communicate(timeout=5)
                print("Process stderr:", stderr[-10000:], file=sys.stderr)
            raise
        assert snap["source"] == "peykan"
        assert snap["mode"] == "demo"
        assert snap["status"] == "simulated"
        ctx = vivi_context(snap)
        assert "DEMO" in ctx and "NOT vehicle readings" in ctx
        # Never allow simulated RPM in the real-vehicle evidence context.
        assert "Extra CAN evidence" not in ctx
        client.publish(TOPICS["peykan_analysis_request"],
                       json.dumps({"id": "smoke", "path": "sample"}))
        result = wait_for(lambda: next(
            (p for t, p in messages
             if t == TOPICS["peykan_analysis_result"]
             and p.get("id") == "smoke"), None
        ), timeout=40)
        assert result["status"] == "ok", result
        assert isinstance(result.get("data"), dict)
        # Same acceptance probe run by the Raspberry Pi installer.
        subprocess.run(
            [sys.executable, str(SRC.parents[0] / "scripts" / "peykan-verify.py"),
             "--mode", "demo", "--timeout", "30"],
            check=True, env=ENV, timeout=60,
        )
        print("PASS: Peykan virtual ECU → MCP server → MQTT → Vivi guard + CAN log analysis")
    finally:
        for p in reversed(processes):
            p.terminate()
        for p in reversed(processes):
            try:
                p.communicate(timeout=4)
            except subprocess.TimeoutExpired:
                p.kill()
                p.communicate()
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()

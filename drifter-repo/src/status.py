#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Status CLI
Quick one-shot view of current telemetry and system health.
Usage: python3 src/status.py [--json]
UNCAGED TECHNOLOGY — EST 1991
"""

import json
import time
import argparse
import subprocess
import paho.mqtt.client as mqtt

MQTT_HOST = "localhost"
MQTT_PORT = 1883
COLLECT_SECONDS = 2

TOPICS = [
    "drifter/engine/rpm",
    "drifter/engine/coolant",
    "drifter/engine/stft1",
    "drifter/engine/stft2",
    "drifter/engine/load",
    "drifter/engine/throttle",
    "drifter/vehicle/speed",
    "drifter/power/voltage",
    "drifter/alert/level",
    "drifter/alert/message",
    "drifter/system/status",
]

SERVICES = [
    "drifter-canbridge",
    "drifter-alerts",
    "drifter-logger",
    "drifter-voice",
    "drifter-hotspot",
    "drifter-homesync",
]

CYAN  = "\033[0;36m"
GREEN = "\033[0;32m"
AMBER = "\033[0;33m"
RED   = "\033[0;31m"
BOLD  = "\033[1m"
NC    = "\033[0m"

ALERT_COLOURS = {0: GREEN, 1: CYAN, 2: AMBER, 3: RED}
ALERT_NAMES   = {0: "OK", 1: "INFO", 2: "AMBER", 3: "RED"}


collected = {}


def on_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload)
        collected[msg.topic] = data
    except (json.JSONDecodeError, ValueError):
        pass


def get_service_status(name):
    """Return 'active', 'inactive', or 'unknown'."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", name],
            capture_output=True, text=True, timeout=3
        )
        return result.stdout.strip()
    except Exception:
        return "unknown"


def collect_mqtt():
    """Connect to local MQTT and collect one round of retained values."""
    client = mqtt.Client(client_id="drifter-status")
    client.on_message = on_message
    try:
        client.connect(MQTT_HOST, MQTT_PORT, 10)
    except Exception as e:
        return False, str(e)

    for topic in TOPICS:
        client.subscribe(topic)

    client.loop_start()
    time.sleep(COLLECT_SECONDS)
    client.loop_stop()
    client.disconnect()
    return True, None


def fmt_value(topic, data):
    """Return a formatted (label, value_str, colour) tuple."""
    key = topic.split("/")[-1]
    value = data.get("value")
    unit  = data.get("unit", "")

    if value is None:
        return key, "—", NC

    colour = GREEN

    if key == "rpm":
        colour = RED if value > 6500 else AMBER if value > 5500 else GREEN
        return "RPM", f"{value:.0f} {unit}", colour

    if key == "coolant":
        colour = RED if value >= 108 else AMBER if value > 100 else GREEN
        return "Coolant", f"{value:.0f} °C", colour

    if key in ("stft1", "stft2"):
        colour = AMBER if abs(value) > 10 else GREEN
        label = "STFT Bank 1" if key == "stft1" else "STFT Bank 2"
        return label, f"{value:+.1f} %", colour

    if key == "load":
        return "Engine Load", f"{value:.1f} %", GREEN

    if key == "throttle":
        return "Throttle", f"{value:.1f} %", GREEN

    if key == "speed":
        return "Speed", f"{value:.0f} km/h", GREEN

    if key == "voltage":
        colour = RED if value < 12.0 else AMBER if value < 13.2 else GREEN
        return "Battery", f"{value:.2f} V", colour

    return key, f"{value} {unit}".strip(), GREEN


def print_status():
    ok, err = collect_mqtt()

    print(f"\n{BOLD}{CYAN}  DRIFTER STATUS{NC}")
    print(f"  {'─' * 38}")

    # ── Telemetry ──
    TELEMETRY_TOPICS = [t for t in TOPICS if "/alert/" not in t and "/system/" not in t]
    print(f"\n{BOLD}  Telemetry{NC}")

    for topic in TELEMETRY_TOPICS:
        if topic in collected:
            label, val, colour = fmt_value(topic, collected[topic])
            print(f"    {label:<18} {colour}{val}{NC}")
        else:
            label = topic.split("/")[-1]
            print(f"    {label:<18} {AMBER}—{NC}")

    # ── Alert ──
    print(f"\n{BOLD}  Diagnostics{NC}")
    alert_level = 0
    if "drifter/alert/level" in collected:
        alert_level = collected["drifter/alert/level"].get("level", 0)
    alert_msg = "—"
    if "drifter/alert/message" in collected:
        alert_msg = collected["drifter/alert/message"].get("message", "—")

    alert_colour = ALERT_COLOURS.get(alert_level, NC)
    alert_name   = ALERT_NAMES.get(alert_level, "?")
    print(f"    {'Alert Level':<18} {alert_colour}{alert_name}{NC}")
    print(f"    {'Message':<18} {alert_colour}{alert_msg}{NC}")

    # ── Services ──
    print(f"\n{BOLD}  Services{NC}")
    for svc in SERVICES:
        state = get_service_status(svc)
        colour = GREEN if state == "active" else AMBER if state == "activating" else RED
        print(f"    {svc:<26} {colour}{state}{NC}")

    # ── Connection ──
    if not ok:
        print(f"\n  {RED}MQTT broker unreachable: {err}{NC}")
        print(f"  {AMBER}Start broker: sudo systemctl start nanomq{NC}")
    elif not collected:
        print(f"\n  {AMBER}MQTT connected but no data — is drifter-canbridge running?{NC}")

    print()


def print_json():
    ok, err = collect_mqtt()
    output = {
        "timestamp": time.time(),
        "mqtt_connected": ok,
        "mqtt_error": err,
        "telemetry": {},
        "alert": {},
        "services": {},
    }

    for topic, data in collected.items():
        key = topic.replace("drifter/", "").replace("/", "_")
        output["telemetry"][key] = data

    if "drifter/alert/level" in collected:
        output["alert"]["level"] = collected["drifter/alert/level"].get("level")
    if "drifter/alert/message" in collected:
        output["alert"]["message"] = collected["drifter/alert/message"].get("message")

    for svc in SERVICES:
        output["services"][svc] = get_service_status(svc)

    print(json.dumps(output, indent=2))


def main():
    parser = argparse.ArgumentParser(
        description="DRIFTER — one-shot system status"
    )
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON instead of human-readable")
    args = parser.parse_args()

    if args.json:
        print_json()
    else:
        print_status()


if __name__ == "__main__":
    main()

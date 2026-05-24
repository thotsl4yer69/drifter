"""Boot reason reporter (drifter-boot-reason.service).

Runs once on boot to classify why the Pi rebooted, writes the result
to /opt/drifter/state/last_boot_reason as a single-line JSON, and
publishes the same payload to MQTT (topic sentient/drifter/boot/reason
on nanob:1883, user 'sentient', retain=true).

Reasons:
    hardware_watchdog  /sys/class/watchdog/watchdog0/bootstatus != 0
    kernel_panic       previous-boot journal contains "Kernel panic" or "sysrq"
    flap_reboot        previous-boot journal contains "reboot-force" or "StartLimit"
    clean              none of the above

MQTT failure is tolerated: the file write is the source of truth.
Script always exits 0 so systemd doesn't restart it.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

NANOB_HOST = "192.168.1.159"
NANOB_PORT = 1883
NANOB_USER = "sentient"
MQTT_TOPIC = "sentient/drifter/boot/reason"
MQTT_CONNECT_TIMEOUT = 5

BOOTSTATUS_PATH = Path("/sys/class/watchdog/watchdog0/bootstatus")
STATE_PATH = Path("/opt/drifter/state/last_boot_reason")

PANIC_MARKERS = ("Kernel panic", "sysrq")
FLAP_MARKERS = ("reboot-force", "StartLimit")


def read_bootstatus() -> int | None:
    try:
        return int(BOOTSTATUS_PATH.read_text().strip())
    except (OSError, ValueError):
        return None


def journal_grep_prev_boot(markers: tuple[str, ...]) -> list[str]:
    try:
        result = subprocess.run(
            ["journalctl", "-b", "-1", "--no-pager"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    return [
        line
        for line in result.stdout.splitlines()
        if any(m in line for m in markers)
    ]


def classify() -> tuple[str, dict]:
    bootstatus = read_bootstatus()
    panic_lines = journal_grep_prev_boot(PANIC_MARKERS)
    flap_lines = journal_grep_prev_boot(FLAP_MARKERS)

    if bootstatus and bootstatus != 0:
        return "hardware_watchdog", {
            "bootstatus": hex(bootstatus),
            "panic_evidence": panic_lines[-3:],
        }
    if panic_lines:
        return "kernel_panic", {"panic_evidence": panic_lines[-3:]}
    if flap_lines:
        return "flap_reboot", {"flap_evidence": flap_lines[-3:]}
    return "clean", {"bootstatus": bootstatus}


def write_state(payload: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(payload) + "\n")


def _make_client(client_id: str):
    """Construct a paho client on the v2 callback API where available.

    Matches config.make_mqtt_client's version-detection so the
    DeprecationWarning paho 2.0 emits for the legacy v1 API stays out
    of the boot journal. Inlined (not imported from config) so this
    one-shot reporter has no project-internal imports.
    """
    import paho.mqtt.client as mqtt

    if hasattr(mqtt, "CallbackAPIVersion"):
        return mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
        )
    return mqtt.Client(client_id=client_id)


def publish_mqtt(payload: dict) -> tuple[str, str]:
    try:
        client = _make_client("drifter-boot-reason")
    except ImportError:
        return "skipped", "paho-mqtt not installed"
    try:
        client.username_pw_set(NANOB_USER)
        client.connect(NANOB_HOST, NANOB_PORT, MQTT_CONNECT_TIMEOUT)
        client.publish(MQTT_TOPIC, json.dumps(payload), qos=1, retain=True)
        client.loop(timeout=5.0)
        client.disconnect()
        return "ok", f"published to {NANOB_HOST}:{NANOB_PORT}{MQTT_TOPIC}"
    except OSError as exc:
        return "skipped", f"broker unreachable: {exc}"
    except Exception as exc:
        return "error", f"{type(exc).__name__}: {exc}"


def main() -> int:
    reason, evidence = classify()
    payload = {
        "timestamp": time.time(),
        "reason": reason,
        "evidence": evidence,
    }
    write_state(payload)
    print(f"boot_reason: state file written reason={reason}", flush=True)
    status, message = publish_mqtt(payload)
    print(f"boot_reason: mqtt {status} — {message}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

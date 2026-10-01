#!/usr/bin/env python3
"""Evidence-backed DRIFTER physical field acceptance gate.

This command does not turn hardware assumptions into software claims. It records
repeatable evidence for the DRIFTER VIM release gates: ten unique vehicle-power
cold boots, strict adapter+ECU proof, a 30-minute live telemetry soak, and
operator-confirmed ELM/display recovery tests. The broader R&D platform may
also record an RF sequence, but RF is not required for VIM vehicle sign-off.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config import MQTT_HOST, MQTT_PORT, TOPICS, make_mqtt_client

STATE_PATH = Path(os.getenv("DRIFTER_ACCEPTANCE_STATE", "/opt/drifter/data/field_acceptance.json"))
EVIDENCE_DIR = Path(os.getenv("DRIFTER_ACCEPTANCE_DIR", "/opt/drifter/logs/acceptance"))
DRIFTER_CLI = os.getenv("DRIFTER_CLI", "/usr/local/bin/drifter")
MIN_COLD_BOOTS = 10
MIN_SOAK_SECONDS = 30 * 60
DEFAULT_MAX_GAP_SECONDS = 30.0
CRITICAL_SERVICES = (
    "drifter-dashboard",
    "drifter-logger",
    "drifter-obdbridge",
    "drifter-lcd",
)
PHYSICAL_GATES = ("elm_recovery", "display_recovery", "hailo_vision", "recon_evidence", "rf_sequence")
VIM_REQUIRED_PHYSICAL_GATES = ("elm_recovery", "display_recovery")
VIM_OPTIONAL_PHYSICAL_GATES = ("hailo_vision", "recon_evidence", "rf_sequence")
TELEMETRY_TOPICS = {
    "rpm": TOPICS["rpm"],
    "coolant": TOPICS["coolant"],
    "speed": TOPICS["speed"],
    "voltage": TOPICS["voltage"],
}
OBD_STATUS_TOPIC = TOPICS["obd_status"]


def _utc() -> str:
    return datetime.now(UTC).isoformat()


def _run(argv: list[str], timeout: float = 20.0) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(argv, 127, "", str(exc))


def _load_state(path: Path | None = None) -> dict[str, Any]:
    path = STATE_PATH if path is None else path
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}


def _save_state(state: dict[str, Any], path: Path | None = None) -> None:
    path = STATE_PATH if path is None else path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(state, indent=2, sort_keys=True, allow_nan=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _boot_id() -> str:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    except OSError:
        return "unknown"


def _field_dump() -> str:
    result = _run([DRIFTER_CLI, "field-dump", "--boots", "2"], timeout=90)
    if result.returncode == 0:
        return result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    return f"field-dump failed rc={result.returncode}: {(result.stderr or result.stdout).strip()[:300]}"


def _power_state() -> dict[str, Any]:
    if not shutil.which("vcgencmd"):
        return {"available": False, "ok": False, "raw": "vcgencmd unavailable"}
    result = _run(["vcgencmd", "get_throttled"], timeout=5)
    raw = (result.stdout or result.stderr).strip()
    match = re.search(r"0x([0-9a-fA-F]+)", raw)
    if result.returncode != 0 or not match:
        return {"available": True, "ok": False, "raw": raw, "rc": result.returncode}
    flags = int(match.group(1), 16)
    return {"available": True, "ok": flags == 0, "raw": raw, "flags": flags}


def _service_states() -> dict[str, bool]:
    states: dict[str, bool] = {}
    for service in CRITICAL_SERVICES:
        result = _run(["systemctl", "is-active", service], timeout=5)
        states[service] = result.returncode == 0 and result.stdout.strip() == "active"
    return states


def _json_output(result: subprocess.CompletedProcess) -> dict[str, Any]:
    try:
        parsed = json.loads(result.stdout)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    return {}


def _live_checks() -> dict[str, Any]:
    obd_run = _run([DRIFTER_CLI, "obd", "--json", "test"], timeout=45)
    obd = _json_output(obd_run)
    display_run = _run([DRIFTER_CLI, "display", "status"], timeout=15)
    services = _service_states()
    power = _power_state()
    return {
        "obd": {
            "ok": obd_run.returncode == 0 and bool(obd.get("ok")),
            "rc": obd_run.returncode,
            "result": obd,
            "stderr": obd_run.stderr.strip()[:500],
        },
        "display": {
            "ok": display_run.returncode == 0,
            "rc": display_run.returncode,
            "output": display_run.stdout.strip()[-1500:],
        },
        "services": services,
        "services_ok": bool(services) and all(services.values()),
        "power": power,
    }


def _passed_boot_ids(boots: list[dict[str, Any]]) -> set[str]:
    """Count the current consecutive passing sequence, not lifetime successes."""
    passed: set[str] = set()
    for item in boots:
        boot_id = item.get("boot_id")
        if item.get("ok") is not True or not boot_id or boot_id == "unknown":
            passed.clear()
        else:
            passed.add(boot_id)
    return passed


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _live_ok(checks: dict[str, Any]) -> bool:
    return bool(
        checks.get("obd", {}).get("ok")
        and checks.get("display", {}).get("ok")
        and checks.get("services_ok")
        and checks.get("power", {}).get("ok")
    )


def _record_cold_boot(args) -> int:
    state = _load_state()
    boot_id = _boot_id()
    checks = _live_checks()
    ok = bool(
        args.no_replug
        and boot_id != "unknown"
        and checks["obd"]["ok"]
        and checks["display"]["ok"]
        and checks["services_ok"]
        and checks["power"]["ok"]
    )
    entry = {
        "boot_id": boot_id,
        "recorded": _utc(),
        "no_replug": bool(args.no_replug),
        "ok": ok,
        "note": args.note or "",
        "checks": checks,
        "field_dump": _field_dump(),
    }
    previous = [item for item in state.get("cold_boots", []) if item.get("boot_id") == boot_id]
    failed = next((item for item in previous if item.get("ok") is not True), None)
    if failed is not None:
        ok = False
        entry["ok"] = False
        entry["first_failure"] = failed.get("first_failure") or {
            "recorded": failed.get("recorded"),
            "checks": failed.get("checks"),
            "no_replug": failed.get("no_replug"),
        }
    boots = [item for item in state.get("cold_boots", []) if item.get("boot_id") != boot_id]
    boots.append(entry)
    state["cold_boots"] = boots
    state["updated"] = _utc()
    _save_state(state)

    passed = len(_passed_boot_ids(boots))
    print(json.dumps({"record": entry, "passed_unique_boots": passed, "required": MIN_COLD_BOOTS}, indent=2))
    return 0 if ok else 2


def _summarize_telemetry(
    samples: dict[str, list[tuple[float, float]]],
    start: float,
    end: float,
    max_gap: float,
    obd_failures: list[dict[str, Any]],
) -> dict[str, Any]:
    duration = max(0.0, end - start)
    sensors: dict[str, Any] = {}
    all_ok = math.isfinite(end - start) and end >= start and math.isfinite(max_gap) and 2 <= max_gap <= DEFAULT_MAX_GAP_SECONDS
    for name in TELEMETRY_TOPICS:
        points = samples.get(name, [])
        times = [point[0] for point in points]
        values = [point[1] for point in points]
        gaps = [b - a for a, b in zip(times, times[1:])]
        first_delay = times[0] - start if times else math.inf
        tail_delay = end - times[-1] if times else math.inf
        worst_gap = max(gaps, default=0.0) if times else math.inf
        ok = bool(
            len(points) >= 3
            and all(math.isfinite(t) and start <= t <= end for t in times)
            and all(math.isfinite(v) for v in values)
            and all(gap >= 0 for gap in gaps)
            and first_delay <= max_gap
            and tail_delay <= max_gap
            and worst_gap <= max_gap
        )
        all_ok = all_ok and ok
        sensors[name] = {
            "ok": ok,
            "count": len(points),
            "first_delay_s": round(first_delay, 3) if math.isfinite(first_delay) else None,
            "tail_delay_s": round(tail_delay, 3) if math.isfinite(tail_delay) else None,
            "max_gap_s": round(worst_gap, 3) if math.isfinite(worst_gap) else None,
            "min": round(min(values), 3) if values else None,
            "max": round(max(values), 3) if values else None,
            "last": round(values[-1], 3) if values else None,
        }
    return {
        "ok": all_ok and not obd_failures,
        "duration_s": round(duration, 3),
        "max_allowed_gap_s": max_gap,
        "sensors": sensors,
        "obd_failures": obd_failures,
    }


def _telemetry_soak(args) -> int:
    duration = _finite_number(args.seconds)
    max_gap = _finite_number(args.max_gap)
    if duration is None or not 10 <= duration <= 86400:
        print("--seconds must be finite and between 10 and 86400", file=sys.stderr)
        return 2
    if max_gap is None or not 2 <= max_gap <= DEFAULT_MAX_GAP_SECONDS:
        print("--max-gap must be finite and between 2 and 30", file=sys.stderr)
        return 2

    samples: dict[str, list[tuple[float, float]]] = {name: [] for name in TELEMETRY_TOPICS}
    topic_to_name = {topic: name for name, topic in TELEMETRY_TOPICS.items()}
    obd_failures: list[dict[str, Any]] = []
    before = _live_checks()
    start_wall = time.time()
    start = time.monotonic()
    stopping = False

    def failure(state: str, **details) -> None:
        obd_failures.append({"offset_s": round(time.monotonic() - start, 3), "state": state, **details})

    if not _live_ok(before):
        failure("preflight_failed")

    client = make_mqtt_client(f"drifter-acceptance-{os.getpid()}")

    def on_connect(cli, _userdata, _flags, reason_code, _properties):
        if reason_code != 0:
            failure("mqtt_connection_refused", reason=str(reason_code))
            return
        # Subscribe on EVERY CONNACK, including after a broker restart.
        for topic in [*TELEMETRY_TOPICS.values(), OBD_STATUS_TOPIC]:
            result, _mid = cli.subscribe(topic)
            if result != 0:
                failure("mqtt_subscribe_failed", topic=topic, rc=result)

    def on_disconnect(_client, _userdata, _flags, reason_code, _properties):
        if not stopping:
            failure("mqtt_disconnected", reason=str(reason_code))

    def on_message(_client, _userdata, msg):
        if stopping:
            return
        now = time.monotonic()
        try:
            data = json.loads(msg.payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        if msg.topic == OBD_STATUS_TOPIC:
            if isinstance(data, dict):
                state = str(data.get("state") or "unknown").lower()
                if state != "online":
                    failure("obd_not_online", bridge_state=state)
            return
        name = topic_to_name.get(msg.topic)
        if not name or getattr(msg, "retain", False):
            return
        # Retained snapshots, replay/demo-labelled data and stale timestamps
        # are not evidence of continuing live vehicle telemetry.
        if isinstance(data, dict):
            if data.get("simulated") or str(data.get("source")) in {"replay", "demo", "simulation", "fuzz"}:
                failure("synthetic_telemetry", sensor=name)
                return
            if "ts" in data:
                ts = _finite_number(data["ts"])
                if ts is None or not 0 <= time.time() - ts <= DEFAULT_MAX_GAP_SECONDS:
                    return
        value = _finite_number(data.get("value") if isinstance(data, dict) else data)
        if value is not None:
            samples[name].append((now, value))

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    try:
        if not _live_ok(before):
            raise RuntimeError("Preflight failed; fix vehicle link/display/services/power before soaking")
        client.connect(MQTT_HOST, MQTT_PORT, 30)
        client.loop_start()
        deadline = start + duration
        while time.monotonic() < deadline:
            time.sleep(min(0.5, max(0.05, deadline - time.monotonic())))
    except KeyboardInterrupt:
        failure("interrupted")
    except Exception as exc:
        failure("mqtt_error", error=str(exc))
    finally:
        stopping = True
        end = time.monotonic()
        try:
            client.loop_stop()
            client.disconnect()
        except Exception as exc:
            failure("mqtt_cleanup_error", error=str(exc))

    after = _live_checks()
    if not _live_ok(after):
        failure("postflight_failed")
    if end - start < duration:
        failure("incomplete_duration")
    report = _summarize_telemetry(samples, start, end, max_gap, obd_failures)
    report.update({
        "started_epoch": start_wall,
        "started": datetime.fromtimestamp(start_wall, UTC).isoformat(),
        "completed": _utc(),
        "required_signoff_duration_s": MIN_SOAK_SECONDS,
        "requested_duration_s": duration,
        "live_before": before,
        "live_after": after,
    })
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    evidence = EVIDENCE_DIR / f"telemetry-soak-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S.%fZ')}.json"
    _save_state(report, evidence)
    report["evidence"] = str(evidence)

    state = _load_state()
    state["telemetry_soak"] = report
    state["updated"] = _utc()
    _save_state(state)
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 2


def _mark(args) -> int:
    gate = args.gate.replace("-", "_")
    if gate not in PHYSICAL_GATES:
        raise SystemExit(f"unknown physical gate: {args.gate}")
    passed = bool(args.pass_gate)
    record = {
        "ok": passed,
        "recorded": _utc(),
        "boot_id": _boot_id(),
        "note": args.note or "",
        "field_dump": _field_dump(),
    }
    state = _load_state()
    state.setdefault("physical", {})[gate] = record
    state["updated"] = _utc()
    _save_state(state)
    print(json.dumps({gate: record}, indent=2))
    return 0 if passed else 2


def _status(args) -> int:
    state = _load_state()
    boots = state.get("cold_boots", [])
    passed_boots = _passed_boot_ids(boots)
    soak = state.get("telemetry_soak") or {}
    physical = state.get("physical") or {}
    live = _live_checks() if not args.no_live else {}

    boot_gate = len(passed_boots) >= MIN_COLD_BOOTS
    soak_duration = _finite_number(soak.get("duration_s"))
    soak_gap = _finite_number(soak.get("max_allowed_gap_s", DEFAULT_MAX_GAP_SECONDS))
    soak_gate = bool(
        soak.get("ok") is True
        and soak_duration is not None and soak_duration >= MIN_SOAK_SECONDS
        and soak_gap is not None and 2 <= soak_gap <= DEFAULT_MAX_GAP_SECONDS
    )
    physical_gate = all(
        bool((physical.get(name) or {}).get("ok"))
        for name in VIM_REQUIRED_PHYSICAL_GATES
    )
    live_gate = not args.no_live and _live_ok(live)
    stored_gates_ready = boot_gate and soak_gate and physical_gate
    ready = stored_gates_ready and live_gate
    payload = {
        "signoff_ready": ready,
        "stored_gates_ready": stored_gates_ready,
        "live_checked": not args.no_live,
        "cold_boots": {"ok": boot_gate, "passed": len(passed_boots), "required": MIN_COLD_BOOTS},
        "telemetry_soak": {
            "ok": soak_gate,
            "duration_s": soak.get("duration_s"),
            "required_s": MIN_SOAK_SECONDS,
            "evidence": soak.get("evidence"),
        },
        "physical": {name: physical.get(name) or {"ok": False} for name in PHYSICAL_GATES},
        "required_physical_gates": list(VIM_REQUIRED_PHYSICAL_GATES),
        "optional_physical_gates": list(VIM_OPTIONAL_PHYSICAL_GATES),
        "live": live,
        "state_file": str(STATE_PATH),
    }
    print(json.dumps(payload, indent=2))
    return 0 if ready else 2


def _reset(args) -> int:
    if not args.yes:
        print("Refusing to erase acceptance evidence without --yes", file=sys.stderr)
        return 2
    STATE_PATH.unlink(missing_ok=True)
    print(f"reset {STATE_PATH}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="drifter acceptance", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    command = sub.add_parser("cold-boot", help="record one unique cold boot after physical power-up")
    command.add_argument("--no-replug", action="store_true", help="confirm this boot did not require a manual power replug")
    command.add_argument("--note", default="")
    command.set_defaults(func=_record_cold_boot)

    command = sub.add_parser("soak", help="record live RPM/coolant/speed/voltage continuity")
    command.add_argument("--seconds", type=float, default=MIN_SOAK_SECONDS)
    command.add_argument("--max-gap", type=float, default=DEFAULT_MAX_GAP_SECONDS)
    command.set_defaults(func=_telemetry_soak)

    command = sub.add_parser("mark", help="record a physical recovery/operation gate")
    command.add_argument("gate", choices=[name.replace("_", "-") for name in PHYSICAL_GATES])
    outcome = command.add_mutually_exclusive_group(required=True)
    outcome.add_argument("--pass", dest="pass_gate", action="store_true")
    outcome.add_argument("--fail", dest="pass_gate", action="store_false")
    command.add_argument("--note", default="")
    command.set_defaults(func=_mark)

    command = sub.add_parser("status", help="show release-gate state and current live health")
    command.add_argument("--no-live", action="store_true", help="show stored evidence without probing hardware")
    command.set_defaults(func=_status)

    command = sub.add_parser("reset", help="clear stored acceptance evidence")
    command.add_argument("--yes", action="store_true")
    command.set_defaults(func=_reset)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

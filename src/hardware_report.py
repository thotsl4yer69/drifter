#!/usr/bin/env python3
"""Capture a redacted DRIFTER VIM reference-hardware report.

The report is intended for BOM/reproducibility evidence. It deliberately omits
Wi-Fi credentials, VINs, API keys and USB serial numbers. Bluetooth MAC
addresses are redacted while device names are retained so an ELM adapter can be
identified without publishing a unique radio identifier.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPORT_DIR = Path(os.getenv("DRIFTER_HARDWARE_REPORT_DIR", "/opt/drifter/logs/hardware"))
REPO = Path(os.getenv("DRIFTER_REPO", "/home/kali/drifter"))

_MAC_RE = re.compile(r"(?i)\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b")
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

# Only hardware-identification properties needed for a reproducible BOM.
# Unique USB serial values are intentionally excluded.
_UDEV_KEYS = (
    "ID_BUS",
    "ID_VENDOR",
    "ID_VENDOR_FROM_DATABASE",
    "ID_VENDOR_ID",
    "ID_MODEL",
    "ID_MODEL_FROM_DATABASE",
    "ID_MODEL_ID",
    "ID_USB_DRIVER",
)


def _utc() -> str:
    return datetime.now(UTC).isoformat()


def _run(argv: list[str], timeout: float = 10.0) -> dict[str, Any]:
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return {
            "available": True,
            "rc": result.returncode,
            "stdout": _redact(result.stdout.strip()),
            "stderr": _redact(result.stderr.strip()),
        }
    except FileNotFoundError:
        return {"available": False, "rc": 127, "stdout": "", "stderr": f"{argv[0]} unavailable"}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"available": True, "rc": 127, "stdout": "", "stderr": _redact(str(exc))}


def _redact(text: str) -> str:
    text = _MAC_RE.sub("<REDACTED_MAC>", text or "")
    # Hardware reports do not need IP addresses. Removing them makes the JSON
    # safer to attach to a public beta issue.
    return _IPV4_RE.sub("<REDACTED_IP>", text)


def _text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace").replace("\x00", "").strip()
    except OSError:
        return None


def _os_release() -> dict[str, str]:
    values: dict[str, str] = {}
    raw = _text(Path("/etc/os-release")) or ""
    for line in raw.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in {"NAME", "VERSION", "VERSION_ID", "PRETTY_NAME"}:
            values[key.lower()] = value.strip().strip('"')
    return values


def _serial_devices() -> list[dict[str, Any]]:
    paths: set[Path] = set()
    for pattern in ("/dev/ttyUSB*", "/dev/ttyACM*", "/dev/drifter-*"):
        paths.update(Path("/dev").glob(Path(pattern).name))

    devices: list[dict[str, Any]] = []
    for path in sorted(paths, key=str):
        info: dict[str, Any] = {"device": str(path)}
        query = _run(["udevadm", "info", "--query=property", f"--name={path}"])
        props: dict[str, str] = {}
        if query.get("rc") == 0:
            for line in query.get("stdout", "").splitlines():
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                if key in _UDEV_KEYS:
                    props[key.lower()] = value
        info["udev"] = props
        devices.append(info)
    return devices


def _framebuffers() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for fb in sorted(Path("/sys/class/graphics").glob("fb*"), key=str):
        entry: dict[str, Any] = {
            "device": f"/dev/{fb.name}",
            "name": _text(fb / "name"),
        }
        try:
            entry["sysfs_device"] = str((fb / "device").resolve())
        except OSError:
            entry["sysfs_device"] = None
        out.append(entry)
    return out


def _git_state() -> dict[str, Any]:
    if not (REPO / ".git").exists():
        return {"repo": str(REPO), "present": False}
    head = _run(["git", "-C", str(REPO), "rev-parse", "HEAD"])
    branch = _run(["git", "-C", str(REPO), "branch", "--show-current"])
    dirty = _run(["git", "-C", str(REPO), "status", "--porcelain"])
    return {
        "repo": str(REPO),
        "present": True,
        "head": head.get("stdout") if head.get("rc") == 0 else None,
        "branch": branch.get("stdout") if branch.get("rc") == 0 else None,
        "dirty": bool(dirty.get("stdout")) if dirty.get("rc") == 0 else None,
    }


def build_report() -> dict[str, Any]:
    model = _text(Path("/proc/device-tree/model"))
    meminfo = _text(Path("/proc/meminfo")) or ""
    mem_total_kb = None
    match = re.search(r"^MemTotal:\s+(\d+)\s+kB$", meminfo, re.MULTILINE)
    if match:
        mem_total_kb = int(match.group(1))

    report: dict[str, Any] = {
        "schema": 1,
        "generated": _utc(),
        "purpose": "DRIFTER VIM reference hardware / founding-beta BOM evidence",
        "privacy": {
            "vin_collected": False,
            "wifi_credentials_collected": False,
            "api_keys_collected": False,
            "usb_serial_numbers_collected": False,
            "bluetooth_mac_redacted": True,
            "ip_addresses_redacted": True,
        },
        "system": {
            "model": model,
            "machine": platform.machine(),
            "kernel": platform.release(),
            "python": platform.python_version(),
            "memory_total_kb": mem_total_kb,
            "os": _os_release(),
        },
        "git": _git_state(),
        "storage": _run(["lsblk", "-J", "-o", "NAME,SIZE,TYPE,MODEL,TRAN,MOUNTPOINTS"]),
        "usb": _run(["lsusb"]),
        "serial_devices": _serial_devices(),
        "bluetooth_devices": _run(["bluetoothctl", "devices"]),
        "framebuffers": _framebuffers(),
        "audio_playback": _run(["aplay", "-l"]),
        "audio_capture": _run(["arecord", "-l"]),
        "power_throttle": _run(["vcgencmd", "get_throttled"]),
    }
    return report


def _write_report(report: dict[str, Any], output: Path | None = None) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    target = output or REPORT_DIR / f"hardware-report-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, target)
    return target


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="drifter hardware-report",
        description="Capture redacted reference-hardware evidence for the DRIFTER VIM beta/BOM.",
    )
    parser.add_argument("--json", action="store_true", help="print the complete JSON report")
    parser.add_argument("--output", type=Path, help="optional report path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = build_report()
    path = _write_report(report, args.output)
    if args.json:
        print(json.dumps({**report, "evidence": str(path)}, indent=2, sort_keys=True))
    else:
        print(path)
        print("DRIFTER hardware report captured (VIN/credentials/USB serials omitted; MAC/IP redacted).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

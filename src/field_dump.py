#!/usr/bin/env python3
"""Create a DRIFTER field-failure diagnostic bundle.

Designed for the exact situation where the car test went wrong and the operator
needs evidence from the last boot(s), not a scavenger hunt through journalctl.
The report intentionally avoids dumping the complete .env or credentials.
"""
from __future__ import annotations

import argparse
import os
import shlex
import shutil
import socket
import subprocess
import time
from pathlib import Path

DEFAULT_DIRS = (
    Path("/opt/drifter/logs/field-dumps"),
    Path.home() / "drifter-field-dumps",
    Path("/tmp/drifter-field-dumps"),
)

SERVICES = (
    "drifter-boot-manager",
    "drifter-lcd",
    "drifter-obdbridge",
    "drifter-canbridge",
    "drifter-autoconnect",
    "drifter-hotspot",
    "drifter-dashboard",
    "drifter-watchdog",
    "NetworkManager",
    "bluetooth",
)

SAFE_ENV_KEYS = (
    "DRIFTER_TRANSPORT",
    "DRIFTER_ELM_LINK",
    "OBD_SERIAL_DEV",
    "OBD_SERIAL_BAUD",
    "ELM_BT_MAC",
    "ELM_BT_CHANNEL",
    "ELM_WIFI_HOST",
    "ELM_WIFI_PORT",
    "ELM_TIMEOUT",
    "LCD_FB_DEVICE",
    "LCD_ROTATE",
)


def _pick_dir(requested: str | None) -> Path:
    candidates = (Path(requested),) if requested else DEFAULT_DIRS
    for root in candidates:
        try:
            root.mkdir(parents=True, exist_ok=True)
            test = root / ".write-test"
            test.write_text("ok", encoding="utf-8")
            test.unlink(missing_ok=True)
            return root
        except OSError:
            continue
    raise SystemExit("No writable field-dump directory")


def _run(cmd: list[str], timeout: float = 15.0) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        text = p.stdout
        if p.stderr.strip():
            text += ("\n[stderr]\n" + p.stderr)
        return p.returncode, text.rstrip()
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, f"{type(exc).__name__}: {exc}"


def _section(fp, title: str, body: str) -> None:
    fp.write("\n" + "=" * 78 + "\n")
    fp.write(title + "\n")
    fp.write("=" * 78 + "\n")
    fp.write((body or "<no output>").rstrip() + "\n")


def _command_section(fp, title: str, cmd: list[str], timeout: float = 15.0) -> None:
    rc, out = _run(cmd, timeout=timeout)
    _section(fp, f"{title}  [rc={rc}]\n$ {' '.join(shlex.quote(x) for x in cmd)}", out)


def _read_safe_env() -> str:
    path = Path(os.getenv("DRIFTER_ENV_FILE", "/opt/drifter/.env"))
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return f"{path}: unavailable ({exc})"
    values = {}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key in SAFE_ENV_KEYS:
            values[key] = value.strip()
    if not values:
        return f"{path}: no field-relevant keys present"
    return "\n".join(f"{k}={values[k]}" for k in SAFE_ENV_KEYS if k in values)


def _framebuffers() -> str:
    lines = []
    root = Path("/sys/class/graphics")
    for fb in sorted(root.glob("fb*")) if root.exists() else []:
        vals = {}
        for leaf in ("name", "virtual_size", "bits_per_pixel", "stride"):
            try:
                vals[leaf] = (fb / leaf).read_text().strip()
            except OSError:
                vals[leaf] = "?"
        lines.append(f"{fb.name}: " + " ".join(f"{k}={v}" for k, v in vals.items()))
    return "\n".join(lines) or "no /sys/class/graphics/fb* entries"


def _spi_bindings() -> str:
    root = Path("/sys/bus/spi/drivers")
    lines = []
    if root.exists():
        for drv in sorted(root.glob("fb_*")):
            bound = sorted(p.name for p in drv.glob("spi*"))
            lines.append(f"{drv.name}: {', '.join(bound) if bound else 'no bound spi device'}")
    return "\n".join(lines) or "no fbtft SPI driver binding found"


def _boot_id() -> str:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="drifter field-dump", description=__doc__)
    p.add_argument("--output-dir", default=None)
    p.add_argument("--boots", type=int, default=2,
                   help="number of previous boots to include (default: 2)")
    args = p.parse_args(argv)

    out_dir = _pick_dir(args.output_dir)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"drifter-field-{stamp}.txt"

    with path.open("w", encoding="utf-8", errors="replace") as fp:
        fp.write("DRIFTER FIELD FAILURE BUNDLE\n")
        fp.write(f"generated={time.strftime('%Y-%m-%dT%H:%M:%S%z')}\n")
        fp.write(f"host={socket.gethostname()}\n")
        fp.write(f"boot_id={_boot_id()}\n")

        _command_section(fp, "KERNEL / HOST", ["uname", "-a"])
        _command_section(fp, "UPTIME", ["uptime"])
        _command_section(fp, "FILESYSTEM", ["df", "-h"])
        if shutil.which("free"):
            _command_section(fp, "MEMORY", ["free", "-h"])

        _section(fp, "FIELD-RELEVANT CONFIG (credentials deliberately omitted)", _read_safe_env())

        if shutil.which("systemctl"):
            _command_section(fp, "FAILED UNITS", ["systemctl", "--failed", "--no-pager"])
            for svc in SERVICES:
                _command_section(
                    fp, f"SERVICE STATE — {svc}",
                    ["systemctl", "show", svc, "--no-pager",
                     "-p", "LoadState", "-p", "ActiveState", "-p", "SubState",
                     "-p", "NRestarts", "-p", "ExecMainCode", "-p", "ExecMainStatus",
                     "-p", "Result", "-p", "StateChangeTimestamp"], timeout=6,
                )

        if shutil.which("journalctl"):
            _command_section(fp, "AVAILABLE BOOTS", ["journalctl", "--list-boots", "--no-pager"])
            for boot in range(0, -(max(0, args.boots) + 1), -1):
                b = str(boot)
                _command_section(fp, f"BOOT {b} — KERNEL (last 350 lines)",
                                 ["journalctl", "-b", b, "-k", "-n", "350", "--no-pager"], timeout=20)
                for svc in SERVICES:
                    _command_section(fp, f"BOOT {b} — {svc} (last 180 lines)",
                                     ["journalctl", "-b", b, "-u", svc, "-n", "180", "--no-pager"], timeout=15)

        if shutil.which("vcgencmd"):
            _command_section(fp, "PI POWER / THROTTLE FLAGS", ["vcgencmd", "get_throttled"])
            _command_section(fp, "PI CORE VOLTAGE", ["vcgencmd", "measure_volts", "core"])
        else:
            _section(fp, "PI POWER / THROTTLE FLAGS", "vcgencmd not installed")

        _section(fp, "FRAMEBUFFERS", _framebuffers())
        _section(fp, "SPI DISPLAY DRIVER BINDINGS", _spi_bindings())

        if shutil.which("lsusb"):
            _command_section(fp, "USB DEVICES", ["lsusb"])
            _command_section(fp, "USB TREE", ["lsusb", "-t"])
        if shutil.which("rfkill"):
            _command_section(fp, "RFKILL", ["rfkill", "list"])
        if shutil.which("bluetoothctl"):
            _command_section(fp, "BLUETOOTH CONTROLLER", ["bluetoothctl", "show"])
            _command_section(fp, "BLUETOOTH DEVICES", ["bluetoothctl", "devices"])
        if shutil.which("nmcli"):
            _command_section(fp, "NETWORK DEVICES", ["nmcli", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"])
            _command_section(fp, "NETWORK CONNECTIONS", ["nmcli", "-f", "NAME,TYPE,DEVICE", "connection", "show", "--active"])
        if shutil.which("ip"):
            _command_section(fp, "IP ADDRESSES", ["ip", "-brief", "addr"])
            _command_section(fp, "IP ROUTES", ["ip", "route"])

        if shutil.which("mosquitto_sub"):
            _command_section(fp, "RETAINED OBD STATUS",
                             ["mosquitto_sub", "-h", "127.0.0.1", "-t", "drifter/obd/status",
                              "-C", "1", "-W", "2"], timeout=5)
            _command_section(fp, "RECENT VEHICLE TELEMETRY (up to 12 messages / 3s)",
                             ["mosquitto_sub", "-h", "127.0.0.1", "-t", "drifter/engine/#",
                              "-v", "-C", "12", "-W", "3"], timeout=6)

        # Fast storage/power clues that often explain "needed replug" behaviour.
        if shutil.which("dmesg"):
            rc, text = _run(["dmesg", "-T"], timeout=10)
            needles = ("under-voltage", "undervoltage", "voltage", "mmc", "nvme",
                       "ext4", "i/o error", "usb", "spi", "fb", "oom", "out of memory")
            filtered = "\n".join(line for line in text.splitlines()
                                   if any(n in line.lower() for n in needles))
            _section(fp, f"DMESG POWER / STORAGE / USB / DISPLAY CLUES [rc={rc}]", filtered)

    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

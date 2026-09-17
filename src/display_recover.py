#!/usr/bin/env python3
"""Inspect or recover the DRIFTER SPI LCD without power-cycling the Pi."""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

SYS_GRAPHICS_ROOT = Path(os.getenv("DRIFTER_SYS_GRAPHICS_ROOT", "/sys/class/graphics"))
SPI_DRIVER_ROOT = Path(os.getenv("DRIFTER_SPI_DRIVER_ROOT", "/sys/bus/spi/drivers"))
SPI_DEVICE_ROOT = Path(os.getenv("DRIFTER_SPI_DEVICE_ROOT", "/sys/bus/spi/devices"))
LCD_FB_DEVICE = Path(os.getenv("LCD_FB_DEVICE", "/dev/fb1"))
LCD_SPI_DEVICE = os.getenv("LCD_SPI_DEVICE", "spi0.0").strip() or "spi0.0"
LCD_SERVICE = "drifter-lcd"


def _driver() -> Path | None:
    preferred = SPI_DRIVER_ROOT / "fb_ili9486"
    if preferred.exists():
        return preferred
    for path in sorted(SPI_DRIVER_ROOT.glob("fb_*")):
        if path.is_dir():
            return path
    return None


def _bound_devices(driver: Path) -> list[str]:
    return sorted(path.name for path in driver.glob("spi*") if path.name not in {"bind", "unbind"})


def _framebuffers() -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    for name_path in sorted(SYS_GRAPHICS_ROOT.glob("fb*/name")):
        fb = name_path.parent.name
        try:
            name = name_path.read_text().strip()
        except OSError:
            name = "?"

        def read(leaf: str) -> str:
            try:
                return (name_path.parent / leaf).read_text().strip()
            except OSError:
                return "?"

        found.append({
            "fb": fb,
            "name": name,
            "size": read("virtual_size"),
            "bpp": read("bits_per_pixel"),
        })
    return found


def _service_state() -> dict[str, object]:
    if not shutil.which("systemctl"):
        return {"available": False, "active": False, "detail": "systemctl unavailable"}
    result = subprocess.run(
        [
            "systemctl", "show", LCD_SERVICE, "--no-pager",
            "-p", "ActiveState", "-p", "SubState", "-p", "NRestarts",
            "-p", "ExecMainStatus", "-p", "Result",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    values: dict[str, object] = {"available": True, "returncode": result.returncode}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    values["active"] = values.get("ActiveState") == "active" and values.get("SubState") == "running"
    return values


def _health() -> tuple[bool, dict[str, object]]:
    framebuffers = _framebuffers()
    expected_fb_name = LCD_FB_DEVICE.name
    expected_sysfs = any(item["fb"] == expected_fb_name for item in framebuffers)
    expected_device = LCD_FB_DEVICE.exists()
    driver = _driver()
    bound = _bound_devices(driver) if driver else []
    service = _service_state()
    healthy = bool(
        expected_device
        and expected_sysfs
        and driver is not None
        and bound
        and service.get("active")
    )
    return healthy, {
        "framebuffers": framebuffers,
        "expected_fb": str(LCD_FB_DEVICE),
        "expected_device": expected_device,
        "expected_sysfs": expected_sysfs,
        "driver": driver.name if driver else None,
        "bound": bound,
        "service": service,
    }


def status() -> int:
    healthy, detail = _health()
    framebuffers = detail["framebuffers"]
    if framebuffers:
        for item in framebuffers:
            print(f"{item['fb']}: name={item['name']} size={item['size']} bpp={item['bpp']}")
    else:
        print("no framebuffer registered")

    driver = detail["driver"] or "not found"
    bound = ",".join(detail["bound"]) or "none"
    print(f"driver={driver} bound={bound}")
    service = detail["service"]
    if service.get("available"):
        print(
            f"{LCD_SERVICE}: {service.get('ActiveState', 'unknown')}/"
            f"{service.get('SubState', 'unknown')} restarts={service.get('NRestarts', '?')} "
            f"main_status={service.get('ExecMainStatus', '?')}"
        )
    else:
        print(f"{LCD_SERVICE}: systemctl unavailable")

    print(
        "display-control-path=" + ("HEALTHY" if healthy else "DEGRADED")
        + f" expected={LCD_FB_DEVICE} device={detail['expected_device']}"
        + f" sysfs={detail['expected_sysfs']}"
    )
    return 0 if healthy else 2


def recover() -> int:
    if os.geteuid() != 0:
        print("Run with sudo: sudo drifter display recover", file=sys.stderr)
        return 2

    driver = _driver()
    if driver is None:
        print(f"No fbtft SPI driver found under {SPI_DRIVER_ROOT}", file=sys.stderr)
        return 2

    bound = _bound_devices(driver)
    device = bound[0] if bound else LCD_SPI_DEVICE
    if not bound and not (SPI_DEVICE_ROOT / device).exists():
        print(
            f"SPI display device {device} is not present under {SPI_DEVICE_ROOT}; refusing to guess a bind target",
            file=sys.stderr,
        )
        return 2

    print(f"reinitialising {driver.name}/{device}...")
    try:
        if bound:
            (driver / "unbind").write_text(device, encoding="ascii")
            time.sleep(0.4)
        (driver / "bind").write_text(device, encoding="ascii")
        time.sleep(1.0)
    except OSError as exc:
        print(f"display rebind failed: {exc}", file=sys.stderr)
        return 2

    if not shutil.which("systemctl"):
        print("systemctl unavailable; cannot restart drifter-lcd", file=sys.stderr)
        return 2
    restart = subprocess.run(["systemctl", "restart", LCD_SERVICE], check=False)
    if restart.returncode != 0:
        print(f"failed to restart {LCD_SERVICE} (rc={restart.returncode})", file=sys.stderr)
        return 2
    time.sleep(1.0)

    print("display controller reinitialised; verifying framebuffer + service health...")
    rc = status()
    if rc != 0:
        print(
            "display control path is still degraded; capture `drifter field-dump` before power cycling",
            file=sys.stderr,
        )
    return rc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="drifter display", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("recover")
    args = parser.parse_args(argv)
    return status() if args.cmd == "status" else recover()


if __name__ == "__main__":
    raise SystemExit(main())

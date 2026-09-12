#!/usr/bin/env python3
"""Inspect or recover the DRIFTER SPI LCD without power-cycling the Pi."""
from __future__ import annotations

import argparse
import glob
import os
import subprocess
import sys
import time
from pathlib import Path


def _driver() -> Path | None:
    preferred = Path("/sys/bus/spi/drivers/fb_ili9486")
    if preferred.exists():
        return preferred
    for p in sorted(Path("/sys/bus/spi/drivers").glob("fb_*")):
        if p.is_dir():
            return p
    return None


def _bound_devices(driver: Path) -> list[str]:
    return sorted(p.name for p in driver.glob("spi*"))


def status() -> int:
    found = False
    for name_path in sorted(glob.glob("/sys/class/graphics/fb*/name")):
        found = True
        fb = Path(name_path).parent.name
        try:
            name = Path(name_path).read_text().strip()
        except OSError:
            name = "?"
        def read(leaf: str) -> str:
            try:
                return (Path(name_path).parent / leaf).read_text().strip()
            except OSError:
                return "?"
        print(f"{fb}: name={name} size={read('virtual_size')} bpp={read('bits_per_pixel')}")
    if not found:
        print("no framebuffer registered")
    drv = _driver()
    if drv:
        print(f"driver={drv.name} bound={','.join(_bound_devices(drv)) or 'none'}")
    else:
        print("driver=not found")
    if shutil_which("systemctl"):
        p = subprocess.run(["systemctl", "show", "drifter-lcd", "--no-pager",
                            "-p", "ActiveState", "-p", "SubState", "-p", "NRestarts",
                            "-p", "ExecMainStatus"], capture_output=True, text=True)
        print(p.stdout.strip())
    return 0


def shutil_which(name: str) -> str | None:
    import shutil
    return shutil.which(name)


def recover() -> int:
    if os.geteuid() != 0:
        print("Run with sudo: sudo drifter display recover", file=sys.stderr)
        return 2
    drv = _driver()
    if drv is None:
        print("No fbtft SPI driver found under /sys/bus/spi/drivers", file=sys.stderr)
        return 2
    bound = _bound_devices(drv)
    device = bound[0] if bound else "spi0.0"
    print(f"reinitialising {drv.name}/{device}...")
    try:
        if bound:
            (drv / "unbind").write_text(device, encoding="ascii")
            time.sleep(0.4)
        (drv / "bind").write_text(device, encoding="ascii")
        time.sleep(1.0)
    except OSError as exc:
        print(f"display rebind failed: {exc}", file=sys.stderr)
        return 2
    if shutil_which("systemctl"):
        subprocess.run(["systemctl", "restart", "drifter-lcd"], check=False)
        time.sleep(1.0)
    print("display controller reinitialised; drifter-lcd restarted")
    return status()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="drifter display", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("recover")
    args = p.parse_args(argv)
    return status() if args.cmd == "status" else recover()


if __name__ == "__main__":
    raise SystemExit(main())

"""MZ1312 DRIFTER — Marauder bridge module: transport autodetect + serial I/O.

See docs/superpowers/specs/2026-05-24-marauder-bridge-design.md §2.
"""

import logging
import re
from pathlib import Path

log = logging.getLogger("marauder.transport")

# Known VID:PID pairs for Marauder-flashable boards.
KNOWN_MARAUDER_VIDPIDS: list[tuple[str, str]] = [
    ("303a", "1001"),  # Espressif ESP32-S2
    ("303a", "1014"),  # Espressif ESP32-S3
    ("10c4", "ea60"),  # Silicon Labs CP210x (common dev-board USB-UART)
]

_BY_ID = Path("/dev/serial/by-id")

# Symlink names look like: usb-Vendor_Product_VVVV_PPPP_SERIAL-...
_RE_VIDPID = re.compile(r"_([0-9a-f]{4})_([0-9a-f]{4})_", re.IGNORECASE)


def enumerate_serial_candidates(by_id_dir: Path | None = None) -> list[str]:
    """Return absolute paths of serial devices whose VID:PID matches a
    known Marauder board. Sorted for deterministic probe order.
    """
    d = by_id_dir if by_id_dir is not None else _BY_ID
    if not d.exists():
        return []
    out: list[str] = []
    for entry in sorted(d.iterdir()):
        m = _RE_VIDPID.search(entry.name)
        if not m:
            continue
        vid, pid = m.group(1).lower(), m.group(2).lower()
        if (vid, pid) in KNOWN_MARAUDER_VIDPIDS:
            out.append(str(entry))
    return out


import time

try:
    import serial  # pyserial
except ImportError:
    serial = None


def probe_direct(port_path: str, timeout: float = 0.5) -> tuple[bool, str]:
    """Open a serial port, send stopscan, look for Marauder/ESP32 banner.

    Returns (matched, detail).
    """
    if serial is None:
        return False, "pyserial not installed"

    try:
        ser = serial.Serial(port_path, baudrate=115200, timeout=timeout)
    except (OSError, serial.SerialException) as e:
        return False, f"open failed: {e}"

    try:
        ser.write(b"stopscan\r\n")
        ser.flush()
        # Read up to timeout, accumulating bytes; accept on first banner hit.
        deadline = time.monotonic() + timeout
        buf = b""
        while time.monotonic() < deadline:
            chunk = ser.read(128)
            if chunk:
                buf += chunk
                if b"Marauder" in buf or b"ESP32" in buf or b">" in buf:
                    # Strip control bytes for the detail field
                    detail = buf.decode("utf-8", errors="replace").strip()[:160]
                    return True, detail
        return False, "no response (timeout)"
    finally:
        try:
            ser.close()
        except Exception:
            pass


try:
    import requests
except ImportError:
    requests = None


def probe_flipper_proxy(
    dashboard_base_url: str = "http://127.0.0.1:8080",
    timeout: float = 1.5,
) -> tuple[bool, str]:
    """Query drifter-flipper's /api/flipper/hardware. Returns (found, detail)."""
    if requests is None:
        return False, "requests not installed"
    try:
        r = requests.get(f"{dashboard_base_url}/api/flipper/hardware",
                         timeout=timeout)
    except Exception as e:
        return False, f"dashboard unreachable: {e}"
    if r.status_code != 200:
        return False, f"dashboard returned HTTP {r.status_code}"
    try:
        payload = r.json()
    except Exception as e:
        return False, f"dashboard returned non-JSON: {e}"
    if payload.get("marauder_module_present"):
        return True, f"marauder module via flipper: caps={payload.get('capabilities', [])}"
    return False, "marauder module not present on flipper"


if __name__ == "__main__":
    raise NotImplementedError("marauder_transport is a library; import don't run")

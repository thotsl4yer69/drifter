#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Hardware Platform Abstraction
Detects the host board (Raspberry Pi 5 vs D-Robotics RDK X5) and selects
the matching CAN backend so the rest of the fleet code is platform-agnostic.
UNCAGED TECHNOLOGY — EST 1991
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from config import (
    CAN_BITRATE,
    CAN_FD_DATA_BITRATE,
    CAN_FD_ENABLED,
    CAN_NATIVE_CHANNEL,
    PLATFORM,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [HARDWARE] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger(__name__)

# ── Platform identifiers ──
PI5 = "pi5"
RDKX5 = "rdkx5"

_MODEL_PATH = Path("/proc/device-tree/model")


@dataclass
class CanBackend:
    """Description of how to bring up + talk to the CAN interface on this board.

    `interface` is the python-can interface string ('socketcan'), `channel`
    the netdev name, `fd` whether CAN FD framing should be requested. The
    `setup_cmd` list (may be empty) is the privileged ip-link sequence that
    brings the interface up; can_native.py runs it when the link is down.
    """
    interface: str = "socketcan"
    channel: str = CAN_NATIVE_CHANNEL
    fd: bool = False
    bitrate: int = CAN_BITRATE
    data_bitrate: int = CAN_FD_DATA_BITRATE
    native: bool = False           # True = on-board controller (no slcan shim)
    setup_cmd: list[str] = field(default_factory=list)


@dataclass
class Platform:
    """Resolved host platform + its CAN backend."""
    name: str
    model: str
    can: CanBackend

    @property
    def is_rdkx5(self) -> bool:
        return self.name == RDKX5

    @property
    def is_pi5(self) -> bool:
        return self.name == PI5

    def as_dict(self) -> dict:
        return {
            'platform': self.name,
            'model': self.model,
            'can': {
                'interface': self.can.interface,
                'channel': self.can.channel,
                'fd': self.can.fd,
                'bitrate': self.can.bitrate,
                'data_bitrate': self.can.data_bitrate,
                'native': self.can.native,
            },
        }


def read_model() -> str:
    """Return the device-tree model string (lowercased) or '' if unavailable."""
    try:
        return _MODEL_PATH.read_text(errors="ignore").strip("\x00").strip().lower()
    except Exception:
        return ""


def detect_platform() -> str:
    """Return 'pi5' or 'rdkx5'.

    Honours the DRIFTER_PLATFORM override first (bench/CI), then the
    config.PLATFORM hint (already device-tree-derived), then a fresh
    device-tree read as a last resort. Defaults to pi5 — the original
    bring-up board — when nothing matches.
    """
    forced = os.getenv("DRIFTER_PLATFORM", "").strip().lower()
    if forced in (PI5, RDKX5):
        return forced
    if PLATFORM in (PI5, RDKX5):
        return PLATFORM
    model = read_model()
    if "rdk x5" in model or "sunrise" in model:
        return RDKX5
    return PI5


def _rdkx5_can_backend() -> CanBackend:
    """Native socketcan on the RDK X5 on-board CAN controller.

    The X5 exposes a real CAN controller (no USB-serial slcan shim), so we
    request CAN FD when CAN_FD_ENABLED. The ip-link sequence sets the
    nominal bitrate plus the FD data bitrate and enables fd-on.
    """
    fd = CAN_FD_ENABLED
    if fd:
        setup = [
            "ip", "link", "set", CAN_NATIVE_CHANNEL, "up", "type", "can",
            "bitrate", str(CAN_BITRATE),
            "dbitrate", str(CAN_FD_DATA_BITRATE), "fd", "on",
        ]
    else:
        setup = [
            "ip", "link", "set", CAN_NATIVE_CHANNEL, "up", "type", "can",
            "bitrate", str(CAN_BITRATE),
        ]
    return CanBackend(
        channel=CAN_NATIVE_CHANNEL, fd=fd, native=True, setup_cmd=setup,
    )


def _pi5_can_backend() -> CanBackend:
    """Classic CAN on the Pi 5.

    On the Pi 5 the CAN link is most often a USB2CANFD/slcan adapter that
    can_bridge.py brings up via slcand, so we leave setup_cmd empty (the
    existing bridge owns slcan bring-up) and run classic 500 kbps. If a
    native MCP2515-style HAT is present on can0, can_native.py can still
    drive it through the same socketcan backend.
    """
    return CanBackend(channel=CAN_NATIVE_CHANNEL, fd=False, native=False, setup_cmd=[])


def get_can_backend(platform_name: str | None = None) -> CanBackend:
    """Return the CAN backend description for the given (or detected) platform."""
    name = platform_name or detect_platform()
    if name == RDKX5:
        return _rdkx5_can_backend()
    return _pi5_can_backend()


def get_platform() -> Platform:
    """Resolve the full platform descriptor (cached-friendly; cheap to call)."""
    name = detect_platform()
    model = read_model() or name
    return Platform(name=name, model=model, can=get_can_backend(name))


def can_interface_is_up(channel: str) -> bool:
    """True if the given CAN netdev exists and is UP/UNKNOWN."""
    try:
        r = subprocess.run(
            ["ip", "-details", "link", "show", channel],
            capture_output=True, text=True, timeout=3,
        )
    except Exception:
        return False
    if r.returncode != 0:
        return False
    out = r.stdout.lower()
    return "state up" in out or "state unknown" in out


def ensure_can_up(backend: CanBackend) -> bool:
    """Bring the CAN interface up if it isn't already.

    Returns True if the interface is up afterwards. A no-op (returns the
    current link state) when setup_cmd is empty — i.e. on the Pi 5 where
    can_bridge.py owns slcan bring-up. Requires root to actually run the
    ip-link command; logs and returns False otherwise.
    """
    if can_interface_is_up(backend.channel):
        return True
    if not backend.setup_cmd:
        return can_interface_is_up(backend.channel)
    if shutil.which("ip") is None:
        log.warning("ip(8) not found — cannot bring up %s", backend.channel)
        return False
    try:
        subprocess.run(backend.setup_cmd, check=True, capture_output=True, text=True, timeout=10)
        log.info("brought up %s (fd=%s, bitrate=%d)", backend.channel, backend.fd, backend.bitrate)
    except subprocess.CalledProcessError as e:
        log.warning("failed to bring up %s: %s", backend.channel, e.stderr.strip() if e.stderr else e)
        return False
    except Exception as e:
        log.warning("failed to bring up %s: %s", backend.channel, e)
        return False
    return can_interface_is_up(backend.channel)


def main() -> None:
    """CLI: print the detected platform + CAN backend as JSON."""
    import json
    print(json.dumps(get_platform().as_dict(), indent=2))


if __name__ == '__main__':
    main()

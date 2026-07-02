#!/usr/bin/env python3
"""
MZ1312 DRIFTER — OBD-II transport auto-selection (raw CAN vs ELM327).

DRIFTER has two telemetry transports that publish the SAME metric topics and are
therefore mutually exclusive:

  * ``can_bridge.py``  — raw SocketCAN (a CANable / gs_usb adapter → slcan0/can0).
    High poll rate, but only works when the car's OBD pins are CAN.
  * ``obd_bridge.py``  — an ELM327 serial adapter. Slower, but abstracts the
    physical layer so it works on K-line (ISO 9141 / KWP2000), J1850 and 29-bit
    CAN that raw SocketCAN can't reach.

Both are now first-class monitored services (config.SERVICES). To keep them from
double-publishing, each self-selects at boot (and re-checks on a slow timer) via
:func:`select_transport`; the transport that isn't chosen idles and publishes a
hardware-pending status instead of telemetry.

Selection precedence:
  1. ``DRIFTER_TRANSPORT`` env override (``can`` / ``elm327``) — operator wins.
  2. A live SocketCAN interface (can0/can1/slcan0) → ``can``.
  3. A plugged raw-CAN serial adapter (VID:PID on ``config.CAN_USB_IDS``), before
     slcand has brought its interface up → ``can``.
  4. An ELM327 serial device present at ``config.OBD_SERIAL_DEV`` → ``elm327``.
  5. Default → ``can`` (canbridge then idles hw-pending until an adapter appears;
     nothing double-publishes).

Deliberately imports only stdlib + config — NOT python-can — because obd_bridge
must import and run on a K-line-only node where python-can may be absent.
UNCAGED TECHNOLOGY — EST 1991
"""
from __future__ import annotations

import glob
import os
import subprocess

from config import CAN_USB_IDS, OBD_SERIAL_DEV

CAN = "can"
ELM327 = "elm327"

_ENV_CAN = ("can", "socketcan", "raw", "canbridge")
_ENV_ELM = ("elm327", "elm", "obd", "obdbridge", "serial", "kline", "k-line")


def _env_override() -> str | None:
    v = (os.getenv("DRIFTER_TRANSPORT") or "").strip().lower()
    if v in _ENV_CAN:
        return CAN
    if v in _ENV_ELM:
        return ELM327
    return None


def _socketcan_iface_present() -> bool:
    """True if a CAN network interface already exists (no python-can needed)."""
    return any(os.path.exists(f"/sys/class/net/{iface}")
               for iface in ("can0", "can1", "slcan0"))


def _usb_vid_pid(dev: str):
    try:
        r = subprocess.run(
            ['udevadm', 'info', '--name', dev, '--query=property'],
            capture_output=True, text=True, timeout=2,
        )
        if r.returncode != 0:
            return None
        vid = pid = None
        for line in r.stdout.splitlines():
            if line.startswith('ID_VENDOR_ID='):
                vid = line.split('=', 1)[1].strip().lower()
            elif line.startswith('ID_MODEL_ID='):
                pid = line.split('=', 1)[1].strip().lower()
        return (vid, pid)
    except Exception:
        return None


def _can_serial_adapter_present() -> bool:
    """True if a raw-CAN serial adapter (allowlisted VID:PID) is plugged in,
    even before slcand has created its interface."""
    for dev in glob.glob('/dev/ttyACM*') + glob.glob('/dev/ttyUSB*'):
        if _usb_vid_pid(dev) in CAN_USB_IDS:
            return True
    return False


def _elm327_present() -> bool:
    try:
        return bool(OBD_SERIAL_DEV) and os.path.exists(OBD_SERIAL_DEV)
    except OSError:  # pragma: no cover - defensive
        return False


def select_transport() -> str:
    """Return :data:`CAN` or :data:`ELM327` for this node (see module docstring
    for precedence). Cheap and side-effect-free — safe to poll on a slow timer.
    """
    forced = _env_override()
    if forced:
        return forced
    if _socketcan_iface_present():
        return CAN
    if _can_serial_adapter_present():
        return CAN
    if _elm327_present():
        return ELM327
    return CAN

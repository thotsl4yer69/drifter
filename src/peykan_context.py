#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Peykan CAN evidence normalization and Vivi grounding.
Only fresh decoded CAN signals may reach the assistant. Simulator values
are always labelled and are never represented as live vehicle data.
UNCAGED TECHNOLOGY — EST 1991
"""

from __future__ import annotations

import math
import re
import time

_SAFE_SIGNAL = re.compile(r"^[a-zA-Z][a-zA-Z0-9_ .:/()-]{0,63}$")
_SAFE_TEXT = re.compile(r"^[a-zA-Z0-9_ ./:%°-]{1,24}$")


def normalize_snapshot(raw: object, *, mode: str, now: float | None = None,
                       max_signal_age: float = 5.0) -> dict:
    """Whitelist bounded, fresh numeric/categorical DBC observations."""
    ts = time.time() if now is None else now
    result = {"source": "peykan", "mode": mode, "ts": ts, "signals": {},
              "frame_count": 0, "status": "no-data"}
    if not isinstance(raw, dict):
        return result
    frame_count = raw.get("frame_count", 0)
    if type(frame_count) is int and frame_count >= 0:
        result["frame_count"] = frame_count
    received = raw.get("signals")
    if not isinstance(received, dict):
        return result
    for name, entry in list(received.items())[:128]:
        if not isinstance(name, str) or not _SAFE_SIGNAL.fullmatch(name):
            continue
        if not isinstance(entry, dict):
            continue
        age = entry.get("age_s")
        if (not isinstance(age, (int, float)) or isinstance(age, bool)
                or not math.isfinite(age) or not 0 <= age <= max_signal_age):
            continue
        value = entry.get("value")
        if isinstance(value, bool):
            pass
        elif isinstance(value, (int, float)):
            if not math.isfinite(value):
                continue
        elif isinstance(value, str):
            if not _SAFE_TEXT.fullmatch(value):
                continue
        else:
            continue
        unit = str(entry.get("unit") or "")
        if len(unit) > 16 or (unit and not _SAFE_TEXT.fullmatch(unit)):
            unit = ""
        result["signals"][name] = {"value": value, "unit": unit,
                                    "age_s": round(float(age), 2)}
        if len(result["signals"]) >= 32:
            break
    if result["signals"]:
        result["status"] = "simulated" if mode == "demo" else "live"
    return result


def vivi_context(snapshot: object, *, now: float | None = None,
                 max_snapshot_age: float = 10.0) -> str:
    """Return grounded context; ignore stale, unrecognised and simulated values."""
    if not isinstance(snapshot, dict) or snapshot.get("source") != "peykan":
        return ""
    ts = snapshot.get("ts")
    moment = time.time() if now is None else now
    if (not isinstance(ts, (int, float)) or isinstance(ts, bool)
            or not math.isfinite(ts) or not 0 <= moment - ts <= max_snapshot_age):
        return ""
    if snapshot.get("mode") == "demo":
        return "Peykan is in DEMO mode; its simulated CAN values are NOT vehicle readings."
    if snapshot.get("mode") != "socketcan" or snapshot.get("status") != "live":
        return ""
    signals = snapshot.get("signals")
    if not isinstance(signals, dict) or not signals:
        return ""
    lines = []
    for name, value in list(signals.items())[:12]:
        if not isinstance(name, str) or not _SAFE_SIGNAL.fullmatch(name):
            continue
        if not isinstance(value, dict):
            continue
        val = value.get("value")
        if not isinstance(val, (str, bool, int, float)):
            continue
        unit = str(value.get("unit") or "")[:16]
        age = value.get("age_s")
        if not isinstance(age, (int, float)) or not 0 <= age <= 5:
            continue
        lines.append(f"{name}: {val} {unit} (age {age}s)")
    if not lines:
        return ""
    return ("Extra CAN evidence from Peykan (decoded with the operator-supplied "
            "DBC, independent of DRIFTER's OBD feed; only cite it as CAN evidence):\n"
            + "\n".join(lines))

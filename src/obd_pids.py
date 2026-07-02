#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Canonical OBD-II Mode 01 PID registry (single source of truth).

Both telemetry transports build their tables from THIS module so the PID set and
the decode math live in exactly one place:

  * ``can_bridge.py`` (raw SocketCAN) builds its int-keyed ``PIDS`` +
    ``TWO_BYTE_PIDS`` from :data:`PID_TABLE`.
  * ``obd_bridge.py`` (ELM327 serial, incl. K-line) builds its ASCII-keyed
    ``PID_DEFS`` (``"010C"`` …) from :data:`PID_TABLE`.

Before this module the two bridges carried divergent, hand-copied tables — the
ELM327 path was missing timing / O2 / run-time / fuel-level / baro that the CAN
path had, so a K-line car silently lost half its telemetry. Unifying them means
adding a PID once benefits both transports.

A decoder takes the **data bytes** (the ``A, B, …`` that follow the mode+PID
echo) as a sequence and returns the scaled value, so a raw CAN frame slice and
an ELM327 hex line decode identically. Every decoder is a pure function of its
bytes — no hidden state — which is what lets both bridges share them.

Each entry also carries a powertrain applicability tag (:data:`ALL` /
:data:`ICE` / :data:`HYBRID`) and its Mode-01 support-bitmap group, so the
poller can (a) skip combustion-only PIDs on a pure EV, skip electrified-only
PIDs on a pure ICE car, and (b) request only PIDs the ECU actually reports (see
``obd_pids.supported_from_bitmaps`` + the bridges' discovery step).

Coverage is the full common standard set, not just the handful the X-Type
happens to expose — notably **MAP (0x0B)** so a MAF-less car (many post-2010
speed-density engines report MAP but not MAF) can still have air-mass/fuel
estimated, plus oil temp, ambient temp, fuel pressure, engine fuel rate, and
the standard hybrid/EV **battery life (0x5B)**. Unsupported PIDs are simply
never polled once discovery has run.

Dependency-light: imports only ``config`` (for ``TOPICS``) + stdlib. Safe to
import from either bridge.
UNCAGED TECHNOLOGY — EST 1991
"""
from __future__ import annotations

from config import TOPICS

# ── Powertrain applicability ─────────────────────────────────────────────────
# A PID applies to any powertrain (ALL), only to a combustion engine (ICE —
# petrol/diesel/hybrid), or only to an electrified powertrain (HYBRID — hybrid or
# pure EV, which have a traction battery). Pure EVs have no coolant/MAF/fuel-
# trim/O2, so requesting those wastes bus time and can confuse a naive ECU
# gateway; a pure ICE car has no traction battery. The live Mode-01 support
# bitmap is still the authoritative gate; this tag is the fallback used before
# discovery has run (or when the ECU under-reports).
ALL = "all"
ICE = "ice"
HYBRID = "hybrid"

_EV_FUEL = ("ev", "electric", "bev")
_HYBRID_FUEL = ("hybrid", "phev", "hev", "mhev")


def _group(pid: int) -> int:
    """Mode-01 support-bitmap group a PID belongs to.

    PID 0x00 reports support for 0x01-0x20, 0x20 for 0x21-0x40, 0x40 for
    0x41-0x60, and so on. The group's *probe* PID is the low boundary
    (0x00, 0x20, 0x40, 0x60 …).
    """
    return ((pid - 1) // 0x20) * 0x20


class Pid:
    """One OBD-II Mode 01 PID definition. Plain attributes, no behaviour."""

    __slots__ = ("applies", "decode", "hz", "name", "nbytes", "pid", "topic", "unit")

    def __init__(self, pid, name, topic_key, decode, unit, hz, nbytes, applies=ALL):
        self.pid = pid
        self.name = name
        self.topic = TOPICS[topic_key]
        self.decode = decode
        self.unit = unit
        self.hz = hz
        self.nbytes = nbytes
        self.applies = applies

    @property
    def group(self) -> int:
        return _group(self.pid)

    @property
    def cmd(self) -> str:
        """ELM327 request string for this PID, e.g. ``"010C"`` (Mode 01)."""
        return f"01{self.pid:02X}"


# ── The registry ─────────────────────────────────────────────────────────────
# Ordered low→high PID. Decode math is byte-for-byte identical to the historical
# tables in can_bridge.py / obd_bridge.py (the X-Type regression suite proves
# it) — do NOT change existing scaling without a test update.
_DEFS: list[Pid] = [
    Pid(0x04, 'load',       'load',       lambda d: round(d[0] / 2.55, 1),                    '%',    5,   1, ALL),
    Pid(0x05, 'coolant',    'coolant',    lambda d: d[0] - 40,                                'C',    1,   1, ICE),
    Pid(0x06, 'stft1',      'stft1',      lambda d: round((d[0] / 1.28) - 100, 2),            '%',    5,   1, ICE),
    Pid(0x07, 'ltft1',      'ltft1',      lambda d: round((d[0] / 1.28) - 100, 2),            '%',    1,   1, ICE),
    Pid(0x08, 'stft2',      'stft2',      lambda d: round((d[0] / 1.28) - 100, 2),            '%',    5,   1, ICE),
    Pid(0x09, 'ltft2',      'ltft2',      lambda d: round((d[0] / 1.28) - 100, 2),            '%',    1,   1, ICE),
    Pid(0x0A, 'fuel_pressure', 'fuel_pressure', lambda d: d[0] * 3,                           'kPa',  1,   1, ICE),
    Pid(0x0B, 'map',        'map',        lambda d: d[0],                                     'kPa',  5,   1, ICE),
    Pid(0x0C, 'rpm',        'rpm',        lambda d: ((d[0] * 256) + d[1]) / 4.0,              'rpm',  10,  2, ALL),
    Pid(0x0D, 'speed',      'speed',      lambda d: d[0],                                     'km/h', 5,   1, ALL),
    Pid(0x0E, 'timing',     'timing',     lambda d: (d[0] / 2) - 64,                          'deg',  5,   1, ICE),
    Pid(0x0F, 'iat',        'iat',        lambda d: d[0] - 40,                                'C',    1,   1, ICE),
    Pid(0x10, 'maf',        'maf',        lambda d: round(((d[0] * 256) + d[1]) / 100.0, 2),  'g/s',  5,   2, ICE),
    Pid(0x11, 'throttle',   'throttle',   lambda d: round(d[0] / 2.55, 1),                    '%',    10,  1, ALL),
    Pid(0x14, 'o2_b1s1',    'o2_b1s1',    lambda d: round(d[0] / 200.0, 2),                   'V',    5,   1, ICE),
    Pid(0x15, 'o2_b2s1',    'o2_b2s1',    lambda d: round(d[0] / 200.0, 2),                   'V',    5,   1, ICE),
    Pid(0x1F, 'run_time',   'run_time',   lambda d: (d[0] * 256) + d[1],                      's',    1,   2, ALL),
    Pid(0x2F, 'fuel_lvl',   'fuel_lvl',   lambda d: round((d[0] * 100) / 255.0, 1),           '%',    0.5, 1, ICE),
    Pid(0x33, 'baro',       'baro',       lambda d: d[0],                                     'kPa',  0.1, 1, ALL),
    Pid(0x42, 'voltage',    'voltage',    lambda d: round(((d[0] * 256) + d[1]) / 1000.0, 2), 'V',    1,   2, ALL),
    Pid(0x46, 'ambient_air_temp', 'ambient_air_temp', lambda d: d[0] - 40,                    'C',    0.1, 1, ALL),
    Pid(0x5C, 'oil_temp',   'oil_temp',   lambda d: d[0] - 40,                                'C',    0.5, 1, ICE),
    Pid(0x5E, 'fuel_rate',  'fuel_rate',  lambda d: round(((d[0] * 256) + d[1]) / 20.0, 2),   'L/h',  1,   2, ICE),
    # Electrified powertrain — standard hybrid/EV PIDs (deep manufacturer EV
    # metrics are out of scope; these are the SAE-standard ones).
    Pid(0x5B, 'hybrid_batt_life', 'hybrid_batt_life', lambda d: round(d[0] * 100 / 255.0, 1), '%',    0.2, 1, HYBRID),
]

# Fast lookups (kept in module scope; both bridges read them at import).
PID_TABLE: dict[int, Pid] = {p.pid: p for p in _DEFS}


def can_pids() -> dict[int, dict]:
    """Build ``can_bridge.PIDS`` — int-keyed dicts with a list-decoder.

    Shape matches what ``decode_obd_response`` and ``can_native`` expect:
    ``{pid: {'name','topic','decode','unit','hz'}}``. ``decode`` takes the
    data-byte sequence (A, B, …).
    """
    return {
        p.pid: {'name': p.name, 'topic': p.topic, 'decode': p.decode,
                'unit': p.unit, 'hz': p.hz}
        for p in _DEFS
    }


def two_byte_pids() -> set[int]:
    """PIDs whose decoder needs two data bytes (A and B)."""
    return {p.pid for p in _DEFS if p.nbytes >= 2}


def obd_pid_defs() -> dict[str, dict]:
    """Build ``obd_bridge.PID_DEFS`` — ELM327 command-keyed (``"010C"``)."""
    return {
        p.cmd: {'name': p.name, 'topic': p.topic, 'decode': p.decode,
                'unit': p.unit, 'pid': p.pid}
        for p in _DEFS
    }


def applies_to(fuel_type: str | None) -> set[int]:
    """The subset of PIDs meaningful for a powertrain.

    * pure EV  → universal PIDs + electrified (battery) PIDs; drop the
      combustion-only ones (coolant/MAF/O2/fuel-trim/…).
    * hybrid   → everything (it has both an engine and a traction battery).
    * ICE (petrol/diesel/unknown) → universal + combustion; drop battery PIDs.

    This is the *powertrain* gate; the live Mode-01 support bitmap is the
    authoritative *per-ECU* gate (see the bridges).
    """
    ft = (fuel_type or "").strip().lower()
    ev = ft in _EV_FUEL
    hybrid = ft in _HYBRID_FUEL
    out: set[int] = set()
    for p in _DEFS:
        if p.applies == ALL or (p.applies == ICE and not ev) or (p.applies == HYBRID and (ev or hybrid)):
            out.add(p.pid)
    return out


def supported_from_bitmaps(bitmaps: dict[int, int]) -> set[int]:
    """Decode Mode-01 support bitmaps into the set of supported PIDs.

    ``bitmaps`` maps a probe PID (0x00, 0x20, 0x40 …) to its 32-bit response
    value, where bit 31 = the first PID after the probe and bit 0 = the last.
    Only PIDs present in :data:`PID_TABLE` are returned (we never poll a PID we
    can't decode). The probe PIDs themselves are always considered supported.
    """
    supported: set[int] = set()
    for probe, value in bitmaps.items():
        for i in range(32):
            if value & (1 << (31 - i)):
                pid = probe + i + 1
                if pid in PID_TABLE:
                    supported.add(pid)
    return supported


# Probe PIDs used for Mode-01 support discovery. Each reports the next 32 PIDs;
# a probe is only worth sending if the *previous* group's bit for it is set, but
# sending all four unconditionally is cheap and robust against ECUs that don't
# chain the 0x20/0x40 "more supported" bit correctly.
SUPPORT_PROBE_PIDS = (0x00, 0x20, 0x40, 0x60)

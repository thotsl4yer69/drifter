#!/usr/bin/env python3
"""
MZ1312 DRIFTER — DTC catalog: generic OBD-II base + per-vehicle overlay.

DTC diagnosis used to be a single X-Type-specific table (`XTYPE_DTC_LOOKUP`).
For a multi-vehicle node that is split into two layers:

  * a **generic OBD-II base** (:func:`decode_generic`) — the standard SAE J2012
    meaning of a code, derived from its structure, valid on any car; and
  * a **per-vehicle overlay** (:data:`_OVERLAYS`) selected off the ACTIVE vehicle
    profile (make/model). The X-Type overlay is the historical
    ``XTYPE_DTC_LOOKUP`` with its Jaguar-specific causes/actions; author more
    overlays here as they are written.

:func:`active_overlay` returns just the vehicle-specific table (what the alert
engine + LCD read, so the X-Type's behaviour is byte-for-byte unchanged when its
profile is active). :func:`lookup` returns the overlay entry if present else the
generic base (what the LLM-facing consumers read, so *any* car gets a usable
description). Only manufacturer-generic codes (2nd digit 0/2) decode generically
— manufacturer-specific codes (2nd digit 1/3, e.g. P1131) have no portable
meaning and return None unless an overlay defines them.

Dependency-light: `_config_dtc` (data) + `vehicle_profile` (active identity).
UNCAGED TECHNOLOGY — EST 1991
"""
from __future__ import annotations

import _config_dtc
import vehicle_profile

# Per-vehicle DTC overlays, keyed by (make, model) lowercased. Add more as they
# are authored; an unlisted car simply gets the generic base.
_OVERLAYS: dict[tuple[str, str], dict] = {
    ("jaguar", "x-type"): _config_dtc.XTYPE_DTC_LOOKUP,
}

# Powertrain "system" the third character of a generic P-code names (SAE J2012).
_P_SYSTEMS = {
    "0": "Fuel and air metering",
    "1": "Fuel and air metering",
    "2": "Fuel and air metering (injector circuit)",
    "3": "Ignition system or misfire",
    "4": "Auxiliary emission controls",
    "5": "Vehicle speed, idle control, auxiliary inputs",
    "6": "Computer output circuit / control module",
    "7": "Transmission",
    "8": "Transmission",
    "9": "Transmission / drive",
    "A": "Hybrid propulsion",
    "B": "Hybrid propulsion",
    "C": "Hybrid propulsion",
}

_FIRST_CHAR_SYSTEM = {
    "B": "Body",
    "C": "Chassis",
    "U": "Network / communication",
}

# Standard descriptions for the common generic codes (manufacturer-neutral —
# the SAE wording, no per-car specifics). Everything else falls back to a
# structural category description.
_GENERIC_DESC = {
    "P0100": "Mass or Volume Air Flow Circuit Malfunction",
    "P0101": "Mass or Volume Air Flow Circuit Range/Performance",
    "P0106": "Manifold Absolute Pressure Circuit Range/Performance",
    "P0110": "Intake Air Temperature Circuit Malfunction",
    "P0115": "Engine Coolant Temperature Circuit Malfunction",
    "P0128": "Coolant Thermostat Below Regulating Temperature",
    "P0131": "O2 Sensor Circuit Low Voltage — Bank 1 Sensor 1",
    "P0135": "O2 Sensor Heater Circuit — Bank 1 Sensor 1",
    "P0171": "System Too Lean — Bank 1",
    "P0172": "System Too Rich — Bank 1",
    "P0174": "System Too Lean — Bank 2",
    "P0175": "System Too Rich — Bank 2",
    "P0300": "Random/Multiple Cylinder Misfire Detected",
    "P0325": "Knock Sensor Circuit Malfunction",
    "P0340": "Camshaft Position Sensor Circuit — Bank 1",
    "P0345": "Camshaft Position Sensor Circuit — Bank 2",
    "P0401": "Exhaust Gas Recirculation Flow Insufficient",
    "P0420": "Catalyst System Efficiency Below Threshold — Bank 1",
    "P0430": "Catalyst System Efficiency Below Threshold — Bank 2",
    "P0440": "EVAP Emission Control System Malfunction",
    "P0443": "EVAP Purge Control Valve Circuit Malfunction",
    "P0455": "EVAP System Large Leak Detected",
    "P0500": "Vehicle Speed Sensor Malfunction",
    "P0505": "Idle Air Control System Malfunction",
    "P0507": "Idle Air Control RPM Higher Than Expected",
    "P0562": "System Voltage Low",
    "P0563": "System Voltage High",
    "P0700": "Transmission Control System Malfunction",
    "P2106": "Throttle Actuator Control System — Forced Limited Power",
    "P2111": "Throttle Actuator Control System — Stuck Open",
    "P2112": "Throttle Actuator Control System — Stuck Closed",
    "P2135": "Throttle/Pedal Position Sensor Correlation",
    "U0100": "Lost Communication with ECM/PCM",
    "U0101": "Lost Communication with TCM",
    "U0121": "Lost Communication with ABS Control Module",
    "U0155": "Lost Communication with Instrument Panel Cluster",
    # EV / hybrid propulsion (generic ISO/SAE hybrid range)
    "P0A80": "Replace Hybrid Battery Pack",
    "P0AA6": "Hybrid Battery Voltage System Isolation Fault",
    "P0A0F": "Engine Failed to Start (hybrid)",
    "P0A7F": "Hybrid Battery Pack Deterioration",
}

# Codes whose generic severity should escalate above the AMBER default.
_RED_CODES = {
    "P2106", "P2111", "P2112", "P2135",   # limp-mode / throttle authority
    "U0100", "U0101", "U0121", "U0155",   # lost module communication
    "P0A80", "P0AA6", "P0A0F", "P0A7F",   # hybrid HV battery / isolation
}
_INFO_CODES = {"P1000"}  # readiness-not-complete style


def _is_generic(code: str) -> bool:
    """True if the code is SAE-generic (portable across manufacturers). The 2nd
    char is 0/2 for generic, 1/3 for manufacturer-specific."""
    return len(code) == 5 and code[0] in "PBCU" and code[1] in "02"


def _category_desc(code: str) -> str:
    if code[0] == "P":
        # Cylinder-specific misfire: P0301..P0312 -> "Cylinder N misfire".
        if code[1:3] == "03" and code[3:].isdigit():
            n = int(code[3:])
            if 1 <= n <= 16:
                return f"Cylinder {n} Misfire Detected"
        system = _P_SYSTEMS.get(code[2].upper(), "Powertrain")
        return f"{system} fault"
    return f"{_FIRST_CHAR_SYSTEM.get(code[0], 'Powertrain')} fault"


def _severity(code: str) -> str:
    if code in _INFO_CODES:
        return "INFO"
    if code in _RED_CODES:
        return "RED"
    return "AMBER"


def decode_generic(code: str) -> dict | None:
    """Manufacturer-neutral meaning of a standard OBD-II code, from its
    structure. Returns ``{desc, cause, action, severity}`` for an SAE-generic
    code, or None for a manufacturer-specific / malformed code (no portable
    meaning)."""
    if not code:
        return None
    code = str(code).strip().upper()
    if not _is_generic(code):
        return None
    desc = _GENERIC_DESC.get(code) or _category_desc(code)
    return {
        "desc": desc,
        "cause": "Standard OBD-II fault. Scan with a full-system tool for the "
                 "manufacturer-specific root cause; correlate with live fuel "
                 "trims, misfire counts, and freeze-frame data.",
        "action": "Confirm the code is current (not historical), fix any "
                  "upstream/root-cause codes first, then clear and retest.",
        "severity": _severity(code),
    }


def active_overlay(profile: dict | None = None) -> dict:
    """The per-vehicle DTC overlay for the ACTIVE profile (empty if none is
    authored for this car). This is what the alert engine + LCD read, so the
    X-Type's DTC behaviour is unchanged when its profile is active."""
    ident = profile or vehicle_profile.identity()
    key = ((ident.get("make") or "").strip().lower(),
           (ident.get("model") or "").strip().lower())
    return _OVERLAYS.get(key, {})


def lookup(code: str, profile: dict | None = None,
           generic_fallback: bool = True) -> dict | None:
    """DTC entry for a code: the active vehicle's overlay if it defines the
    code, else the generic OBD-II base. Returns None if unknown (and no generic
    decode). With ``generic_fallback=False`` only the overlay is consulted —
    used where behaviour must stay overlay-exact (alerts / LCD)."""
    if not code:
        return None
    norm = str(code).strip().upper()
    entry = active_overlay(profile).get(norm)
    if entry:
        return entry
    if generic_fallback:
        return decode_generic(norm)
    return None

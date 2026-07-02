#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Active vehicle profile (single source of truth).

DRIFTER supports any OBD-II vehicle. `vehicle_id.py` detects the VIN, resolves a
profile (a `vehicles/<VIN>.yaml`, an AI-generated one, or defaults), writes it to
`config.VEHICLE_PROFILE_FILE`, and publishes it on `drifter/vehicle/profile`.

THIS module is what the diagnostic / safety / trip pipeline reads so that
thresholds, engine topology, fuel math, and powertrain-specific rules adapt to
whatever car is plugged in — instead of every module importing the fixed
`config.py` constants (which describe one specific Jaguar).

Design:
  * A config-derived BASE captures the historical constants (thresholds,
    calibration, identity, topology, tyre, fuel). When no profile is active
    (bench, tests, a fresh boot before VIN detection), every accessor returns
    exactly the old config value — so behaviour is unchanged and the existing
    test suite is a regression check.
  * The active profile OVERLAYS the base. A profile only needs to specify the
    fields that differ; anything it omits falls through to the base.
  * Resolution is cached; call `reload()` after the active profile file changes
    (the vehicleid service rewrites it on a VIN change) or in tests.

Dependency-light: imports only `config` + stdlib, plus `yaml` lazily (only if a
YAML profile is read). Safe to import from any service.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import config

log = logging.getLogger(__name__)

# Powertrain families. Anything not recognised is treated as ICE (fuel engine)
# so ICE diagnostics stay on by default — the conservative choice.
_EV_TYPES = {"ev", "electric", "bev"}
_HYBRID_TYPES = {"hybrid", "phev", "hev", "mhev"}
_DIESEL_TYPES = {"diesel"}

_cache: dict | None = None


def _config_base() -> dict:
    """The historical single-vehicle constants, shaped as a profile dict.

    This is the fallback for every field a profile doesn't override, and the
    whole profile when none is active — so an un-identified car behaves exactly
    as the codebase did before multi-vehicle support.
    """
    return {
        # identity
        "vin": None,
        "make": config.VEHICLE_MAKE,
        "model": config.VEHICLE_MODEL,
        "year": config.VEHICLE_YEAR,
        "engine": config.VEHICLE_ENGINE,
        "source": "config-default",
        # powertrain + topology
        "fuel_type": config.FUEL_TYPE,
        "cylinder_count": getattr(config, "CYLINDER_COUNT", 4),
        # X-Type is a V6 (two banks); default derivation lives in bank_count().
        "bank_count": _derive_bank_count(
            config.FUEL_TYPE, getattr(config, "CYLINDER_COUNT", 4),
            config.VEHICLE_ENGINE,
        ),
        "redline_rpm": getattr(config, "REDLINE_RPM", 6500),
        "engine_code": getattr(config, "ENGINE_CODE", None),
        "drivetrain": getattr(config, "DRIVETRAIN", "unknown"),
        "transmission": getattr(config, "TRANSMISSION", "unknown"),
        # fuel / trip
        "tank_litres": config.TRIP_FUEL_TANK_LITRES,
        "avg_consumption_l_per_100km": config.TRIP_AVG_CONSUMPTION_L_PER_100KM,
        # tyres
        "tire_size": config.TIRE_SIZE,
        "tire_pressure_front": config.TIRE_PRESSURE_FRONT,
        "tire_pressure_rear": config.TIRE_PRESSURE_REAR,
        # tuning blocks (nested)
        "thresholds": dict(config.THRESHOLDS),
        "calibration": dict(config.CALIBRATION_DEFAULTS),
        # engine operating points (the coolant/idle/MAF/warmup windows that the
        # alert engine reads directly, distinct from alert THRESHOLDS).
        "engine_params": {
            "coolant_normal_low": config.COOLANT_NORMAL_LOW,
            "coolant_normal_high": config.COOLANT_NORMAL_HIGH,
            "thermostat_open_c": config.THERMOSTAT_OPEN_C,
            "idle_rpm_warm_low": config.IDLE_RPM_WARM_LOW,
            "idle_rpm_warm_high": config.IDLE_RPM_WARM_HIGH,
            "maf_idle_min": config.MAF_IDLE_MIN,
            "maf_idle_max": config.MAF_IDLE_MAX,
            "warmup_coolant_threshold": config.WARMUP_COOLANT_THRESHOLD,
            "warmup_coolant_target": config.WARMUP_COOLANT_TARGET,
            "warmup_time_max": config.WARMUP_TIME_MAX,
        },
        # per-vehicle diagnostic prose used to ground LLM prompts / DTC hints.
        # Seeded from config for the default (X-Type) vehicle; an active profile
        # for a different car replaces the list.
        "known_issues": list(getattr(config, "VEHICLE_KNOWN_ISSUES", []) or []),
    }


def _derive_bank_count(fuel_type: str, cylinders: int, engine: str) -> int:
    """Best-effort bank count when a profile doesn't state it explicitly.

    Fuel-trim / O2 diagnostics run per bank; an EV has none, an inline engine
    has one, a V/flat/boxer has two. We only *derive* when unstated — a profile
    can always set `bank_count` directly (authoritative).
    """
    if (fuel_type or "").lower() in _EV_TYPES:
        return 0
    e = (engine or "").lower()
    if any(tok in e for tok in ("v6", "v8", "v10", "v12", "v-6", "v-8")):
        return 2
    if "flat" in e or "boxer" in e or "h4" in e or "h6" in e:
        return 2
    # Inline engines (I3/I4/I5/I6) and unknown small engines: one bank.
    return 1


def _read_active_profile() -> dict | None:
    """Load the active profile written by vehicle_id.py, if present."""
    path = Path(config.VEHICLE_PROFILE_FILE)
    if not path.exists():
        return None
    try:
        text = path.read_text()
    except OSError as e:
        log.warning("Could not read active profile %s: %s", path, e)
        return None
    # vehicle_id writes JSON (despite the .yaml name); accept both.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    try:
        import yaml
        return yaml.safe_load(text)
    except Exception as e:  # pragma: no cover - defensive
        log.warning("Active profile %s did not parse: %s", path, e)
        return None


def _merge(base: dict, overlay: dict) -> dict:
    """Overlay a profile onto the base. Nested `thresholds`/`calibration` dicts
    are merged key-wise (a profile can override one threshold without dropping
    the rest); other keys replace. None values in the overlay are ignored so a
    sparse/AI profile can't blank out a good default."""
    out = dict(base)
    for k, v in (overlay or {}).items():
        if v is None:
            continue
        if k in ("thresholds", "calibration", "engine_params") and isinstance(v, dict):
            merged = dict(base.get(k) or {})
            merged.update({kk: vv for kk, vv in v.items() if vv is not None})
            out[k] = merged
        else:
            out[k] = v
    # If the overlay set topology-relevant fields but not bank_count, re-derive.
    if "bank_count" not in (overlay or {}) and any(
        key in (overlay or {}) for key in ("fuel_type", "engine", "cylinder_count")
    ):
        out["bank_count"] = _derive_bank_count(
            out.get("fuel_type"), out.get("cylinder_count", 4), out.get("engine"),
        )
    return out


def get() -> dict:
    """Return the resolved active profile (config base ← active overlay), cached."""
    global _cache
    if _cache is None:
        base = _config_base()
        overlay = _read_active_profile()
        _cache = _merge(base, overlay) if overlay else base
    return _cache


def reload() -> dict:
    """Drop the cache and re-resolve. Call after the active profile file changes
    (vehicleid rewrites it on a VIN change) or in tests."""
    global _cache
    _cache = None
    return get()


def set_active(profile: dict | None) -> dict:
    """Install a profile directly (e.g. from an MQTT drifter/vehicle/profile
    message) without a disk round-trip. Overlays onto the config base."""
    global _cache
    _cache = _merge(_config_base(), profile or {})
    return _cache


# ── Convenience accessors ────────────────────────────────────────────────────

def thresholds() -> dict:
    """Merged alert thresholds (config defaults ← profile overrides)."""
    return dict(get().get("thresholds") or {})


def calibration() -> dict:
    """Merged calibration baselines (config defaults ← profile overrides)."""
    return dict(get().get("calibration") or {})


def engine_params() -> dict:
    """Merged engine operating points (coolant/idle/MAF/warmup windows)."""
    return dict(get().get("engine_params") or {})


def spec(key: str, default=None):
    """Generic top-level profile field getter."""
    return get().get(key, default)


def identity() -> dict:
    p = get()
    return {k: p.get(k) for k in ("vin", "make", "model", "year", "engine")}


def display_name() -> str:
    """Human label like '2004 Jaguar X-Type 2.5 V6' for prompts / UI."""
    p = get()
    parts = [str(p.get("year") or "").strip(), p.get("make") or "", p.get("model") or ""]
    name = " ".join(x for x in parts if x).strip()
    eng = (p.get("engine") or "").strip()
    if eng:
        name = f"{name} {eng}".strip()
    return name or "vehicle"


def fuel_type() -> str:
    return (get().get("fuel_type") or "petrol").lower()


def is_ev() -> bool:
    return fuel_type() in _EV_TYPES


def is_hybrid() -> bool:
    return fuel_type() in _HYBRID_TYPES


def is_diesel() -> bool:
    return fuel_type() in _DIESEL_TYPES


def is_combustion() -> bool:
    """True if the powertrain burns fuel (ICE, diesel, or hybrid) — i.e. the
    fuel-trim / coolant / MAF diagnostics are meaningful. False for pure EVs."""
    return not is_ev()


def bank_count() -> int:
    return int(get().get("bank_count", 1))


def cylinder_count() -> int:
    return int(get().get("cylinder_count", 4))


def redline_rpm() -> int:
    return int(get().get("redline_rpm", 6500))


def known_issues() -> list:
    ki = get().get("known_issues") or []
    return list(ki) if isinstance(ki, (list, tuple)) else []


def engine_code() -> str | None:
    """Manufacturer engine code (e.g. 'AJ-V6'), if the profile carries one."""
    return get().get("engine_code")


def prompt_identity() -> str:
    """Vehicle identity line for LLM prompts, e.g.
    '2004 Jaguar X-Type 2.5 V6 (AJ-V6)'. Built from the active profile so the
    prompts adapt to whatever car is plugged in instead of hardcoding one."""
    name = display_name()
    ec = engine_code()
    return f"{name} ({ec})" if ec else name


def known_issues_text(sep: str = "; ") -> str:
    """The active vehicle's known failure modes as one prompt-ready string
    (empty string if the profile lists none)."""
    return sep.join(known_issues())


def known_issues_block(header: str = "KNOWN FAILURE MODES for this vehicle:") -> str:
    """A bulleted known-failure-modes block for a system prompt, or '' if the
    active profile lists none (so a generic car simply omits the section)."""
    issues = known_issues()
    if not issues:
        return ""
    body = "\n".join(f"- {i}" for i in issues)
    return f"{header}\n{body}\n"

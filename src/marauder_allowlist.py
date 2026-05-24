"""MZ1312 DRIFTER — Marauder bridge module: allowlist load + per-category scope gating.

See docs/superpowers/specs/2026-05-24-marauder-bridge-design.md §5.3.
"""

import logging
import os
from pathlib import Path

log = logging.getLogger("marauder.allowlist")

ALLOWLIST_PATH = Path(os.environ.get(
    "MARAUDER_ALLOWLIST", "/opt/drifter/etc/audit_targets.yaml"
))

_EMPTY = {"wifi": [], "ble": [], "evilportal": []}


def load_marauder_allowlist(path: Path | str | None = None) -> dict:
    """Load marauder allowlist from audit_targets.yaml.

    Returns dict with keys 'wifi', 'ble', 'evilportal', each a list of
    entry dicts. Missing file / missing 'marauder:' block / malformed YAML
    all return {wifi:[], ble:[], evilportal:[]} — empty scope is safe.
    """
    p = Path(path) if path else ALLOWLIST_PATH
    if not p.exists():
        log.warning("allowlist not found at %s — treating as empty", p)
        return dict(_EMPTY)

    try:
        import yaml
    except ImportError:
        log.error("PyYAML missing — cannot parse allowlist; treating as empty")
        return dict(_EMPTY)

    try:
        with p.open() as fh:
            data = yaml.safe_load(fh) or {}
    except yaml.YAMLError as e:
        log.error("allowlist YAML parse error: %s — treating as empty", e)
        return dict(_EMPTY)

    block = (data or {}).get("marauder") or {}
    return {
        "wifi": list(block.get("wifi") or []),
        "ble": list(block.get("ble") or []),
        "evilportal": list(block.get("evilportal") or []),
    }


def is_target_allowed(
    scope: dict, category: str, **fields
) -> tuple[bool, str]:
    """Check whether a target falls inside the allowlist scope.

    Args:
        scope: result of load_marauder_allowlist()
        category: 'wifi' | 'ble' | 'evilportal'
        **fields: category-specific (ssid, bssid, mac, template, ...)

    Returns:
        (allowed: bool, reason: str)
    """
    entries = scope.get(category, [])
    if not entries:
        return False, f"allowlist empty for category={category}"

    if category == "wifi":
        return _check_wifi(entries, fields)
    if category == "ble":
        return _check_ble(entries, fields)
    if category == "evilportal":
        return _check_evilportal(entries, fields)
    return False, f"unknown allowlist category={category}"


def _check_wifi(entries: list[dict], fields: dict) -> tuple[bool, str]:
    ssid = fields.get("ssid", "")
    bssid = (fields.get("bssid") or "").lower()
    for entry in entries:
        if "ssid" in entry and entry["ssid"] == ssid:
            return True, f"matched ssid={ssid}"
        if "bssid" in entry and entry["bssid"].lower() == bssid:
            return True, f"matched bssid={bssid}"
    return False, "no match in wifi allowlist"


def _check_ble(entries: list[dict], fields: dict) -> tuple[bool, str]:
    # Implemented in Phase 3 — stub returns refuse for now.
    return False, "ble allowlist check not yet implemented"


def _check_evilportal(entries: list[dict], fields: dict) -> tuple[bool, str]:
    # Implemented in Phase 4 — stub returns refuse for now.
    return False, "evilportal allowlist check not yet implemented"


if __name__ == "__main__":
    raise NotImplementedError("marauder_allowlist is a library; import don't run")

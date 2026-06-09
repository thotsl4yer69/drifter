"""MZ1312 DRIFTER — Marauder bridge module: allowlist load + per-category scope gating.

See docs/MARAUDER.md for the design overview and authorization model.
"""

import logging
import os
from pathlib import Path

log = logging.getLogger("marauder.allowlist")

ALLOWLIST_PATH = Path(os.environ.get(
    "MARAUDER_ALLOWLIST", "/opt/drifter/etc/audit_targets.yaml"
))

def _empty_scope() -> dict:
    """Fresh empty scope. Returns NEW lists each call — never share mutable
    list objects across callers (a shared list would let one consumer's
    append() permanently poison the 'safe empty scope' for everyone)."""
    return {"wifi": [], "ble": [], "evilportal": []}


def load_marauder_allowlist(path: Path | str | None = None) -> dict:
    """Load marauder allowlist from audit_targets.yaml.

    Returns dict with keys 'wifi', 'ble', 'evilportal', each a list of
    entry dicts. Missing file / missing 'marauder:' block / malformed YAML
    all return {wifi:[], ble:[], evilportal:[]} — empty scope is safe.
    """
    p = Path(path) if path else ALLOWLIST_PATH
    if not p.exists():
        log.warning("allowlist not found at %s — treating as empty", p)
        return _empty_scope()

    try:
        import yaml
    except ImportError:
        log.error("PyYAML missing — cannot parse allowlist; treating as empty")
        return _empty_scope()

    try:
        with p.open() as fh:
            data = yaml.safe_load(fh) or {}
    except yaml.YAMLError as e:
        log.error("allowlist YAML parse error: %s — treating as empty", e)
        return _empty_scope()

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
    action = fields.get("action", "targeted")  # 'targeted' | 'spam' | 'scan'
    mac = (fields.get("mac") or "").lower()

    if action in ("targeted", "scan"):
        for entry in entries:
            if "mac" in entry and entry["mac"].lower() == mac:
                return True, f"matched ble mac={mac}"
        return False, "no per-mac match in ble allowlist"

    if action == "spam":
        for entry in entries:
            if entry.get("area_authorized") is True:
                label = entry.get("area_label")
                if not label:
                    return False, ("ble area_authorized entry missing area_label "
                                   "— operator must record where authorization applies")
                return True, f"matched area_authorized: {label}"
        return False, "no area_authorized:true entry in ble allowlist for spam"

    return False, f"unknown ble action={action}"


def _check_evilportal(entries: list[dict], fields: dict) -> tuple[bool, str]:
    ssid = fields.get("ssid", "")
    # Two call sites reach here: the bridge gate forwards the raw command args
    # (key `template_name`), while the feature-level call passes `template`.
    # Accept either so an authorized (ssid, template) pair matches from both —
    # otherwise the bridge gate always reads "" and refuses every authorized
    # portal before the feature gate is even reached.
    template = fields.get("template")
    if template is None:
        template = fields.get("template_name", "")
    for entry in entries:
        if entry.get("ssid") == ssid and entry.get("template") == template:
            return True, f"matched evilportal (ssid={ssid}, template={template})"
    return False, ("no (ssid, template) pair match in evilportal allowlist — "
                   "both must match a single entry")


if __name__ == "__main__":
    raise NotImplementedError("marauder_allowlist is a library; import don't run")

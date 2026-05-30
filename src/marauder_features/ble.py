"""MZ1312 DRIFTER — Marauder bridge module: BLE recon + spam."""

import marauder_allowlist as ma
import marauder_protocol as mp

MAX_SCAN_DURATION_S = 600
MAX_ATTACK_DURATION_S = 300

# Per-process state: track whether apple-proximity collateral warning
# has been emitted in this service-start lifetime.
_apple_warned: bool = False


def reset_collateral_warning_state() -> None:
    """Test helper — also called on service start in production."""
    global _apple_warned
    _apple_warned = False


def start_scan(transport, *, mode: str, duration_s: int) -> dict:
    if transport.mode == "none":
        return {"ok": False, "response": "no transport available"}
    try:
        cmd = mp.cmd_ble_scan(mode)
    except ValueError as e:
        return {"ok": False, "response": str(e)}
    capped = min(int(duration_s), MAX_SCAN_DURATION_S)
    transport.send(cmd)
    return {"ok": True, "response": f"ble scan started mode={mode}",
            "duration_s": capped}


def start_spam(transport, allowlist_scope: dict, *,
              mode: str, duration_s: int,
              acked_warning: bool = False) -> dict:
    """BLE indiscriminate spam. Requires area_authorized scope. For
    'apple' and 'all', first invocation per service-start ALSO requires
    acked_warning=True (collateral warning has been shown to operator).
    """
    global _apple_warned
    if transport.mode == "none":
        return {"ok": False, "response": "no transport available"}

    ok, reason = ma.is_target_allowed(allowlist_scope, "ble",
                                       mac=None, action="spam")
    if not ok:
        return {"ok": False, "response": reason}

    area_label = next(
        (e.get("area_label") for e in allowlist_scope.get("ble", [])
         if e.get("area_authorized")),
        None,
    )

    try:
        cmd = mp.cmd_ble_spam(mode)
    except ValueError as e:
        return {"ok": False, "response": str(e)}

    warning_emitted = False
    if mode in ("apple", "all") and not _apple_warned:
        if not acked_warning:
            return {"ok": False,
                    "response": "ble apple proximity spam: collateral warning not yet acked. "
                                "This affects ALL nearby iOS devices, can crash iOS<17. "
                                "Re-send with acked_warning=true to proceed."}
        _apple_warned = True
        warning_emitted = True

    capped = min(int(duration_s), MAX_ATTACK_DURATION_S)
    transport.send(cmd)
    return {"ok": True,
            "response": f"ble spam started mode={mode}",
            "duration_s": capped,
            "area_label_at_runtime": area_label,
            "collateral_warning_emitted": warning_emitted}

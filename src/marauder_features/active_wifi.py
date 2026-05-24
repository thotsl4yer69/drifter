"""MZ1312 DRIFTER — Marauder bridge module: active Wi-Fi (deauth/beacon/probe-flood)."""

import marauder_protocol as mp

MAX_ATTACK_DURATION_S = 300


def start_deauth_detect(transport, *, duration_s: int) -> dict:
    """Passive deauth frame listener. LOW risk — no RF emission."""
    if transport.mode == "none":
        return {"ok": False, "response": "no transport available"}
    capped = min(int(duration_s), MAX_ATTACK_DURATION_S)
    transport.send(mp.cmd_attack_deauth_detect())
    return {"ok": True, "response": "deauth_detect started",
            "duration_s": capped}

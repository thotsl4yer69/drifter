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


import marauder_allowlist as ma


def start_deauth_attack(transport, allowlist_scope: dict, *,
                        bssid: str, ssid: str, duration_s: int) -> dict:
    if transport.mode == "none":
        return {"ok": False, "response": "no transport available"}

    # Allowlist re-check (defense in depth — Bridge also gates).
    ok, reason = ma.is_target_allowed(allowlist_scope, "wifi",
                                       bssid=bssid, ssid=ssid)
    if not ok:
        return {"ok": False, "response": reason}

    capped = min(int(duration_s), MAX_ATTACK_DURATION_S)
    transport.send(mp.cmd_attack_deauth())
    return {"ok": True, "response": f"deauth_attack started target={bssid}",
            "duration_s": capped, "target_bssid": bssid, "target_ssid": ssid}

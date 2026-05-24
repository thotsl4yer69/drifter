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


from pathlib import Path

try:
    import config
except ImportError:
    config = None  # tests may run without the full drifter env


def _refuse_random_flag() -> bool:
    return getattr(config, "BEACON_SPAM_RANDOM_REFUSE", True) if config else True


def _refuse_rickroll_flag() -> bool:
    return getattr(config, "BEACON_SPAM_RICKROLL_REFUSE", True) if config else True


def _has_wildcard_wifi_scope(scope: dict) -> bool:
    return any(
        (entry.get("ssid") == "*") for entry in scope.get("wifi", [])
    )


def start_beacon_spam(transport, allowlist_scope: dict, *,
                     mode: str, duration_s: int,
                     beacon_list_path: str | None = None,
                     list_idx: int | None = None) -> dict:
    if transport.mode == "none":
        return {"ok": False, "response": "no transport available"}

    if mode == "random":
        if _refuse_random_flag():
            return {"ok": False,
                    "response": "beacon_spam random refused unconditionally "
                                "(see BEACON_SPAM_RANDOM_REFUSE in config.py)"}
        capped = min(int(duration_s), MAX_ATTACK_DURATION_S)
        transport.send(mp.cmd_attack_beacon(mode="random"))
        return {"ok": True, "response": "beacon_spam random started",
                "duration_s": capped}

    if mode == "rickroll":
        if _refuse_rickroll_flag() and not _has_wildcard_wifi_scope(allowlist_scope):
            return {"ok": False,
                    "response": "beacon_spam rickroll refused (set wifi[].ssid='*' "
                                "in allowlist AND flip BEACON_SPAM_RICKROLL_REFUSE)"}
        capped = min(int(duration_s), MAX_ATTACK_DURATION_S)
        transport.send(mp.cmd_attack_beacon(mode="rickroll"))
        return {"ok": True, "response": "beacon_spam rickroll started",
                "duration_s": capped}

    if mode == "list":
        if not beacon_list_path or list_idx is None:
            return {"ok": False,
                    "response": "beacon_spam list requires beacon_list_path + list_idx"}
        try:
            entries = [
                line.strip() for line in Path(beacon_list_path).read_text().splitlines()
                if line.strip()
            ]
        except OSError as e:
            return {"ok": False, "response": f"cannot read beacon list: {e}"}
        if not entries:
            return {"ok": False, "response": "beacon list is empty"}
        out_of_scope = [
            ssid for ssid in entries
            if not ma.is_target_allowed(allowlist_scope, "wifi", ssid=ssid, bssid="")[0]
        ]
        if out_of_scope:
            return {"ok": False,
                    "response": f"beacon list contains out-of-scope SSIDs: {out_of_scope[:5]}"}
        capped = min(int(duration_s), MAX_ATTACK_DURATION_S)
        transport.send(mp.cmd_attack_beacon(mode="list", list_idx=int(list_idx)))
        return {"ok": True, "response": f"beacon_spam list ({len(entries)} SSIDs) started",
                "duration_s": capped, "list_size": len(entries)}

    return {"ok": False, "response": f"unknown beacon mode={mode}"}


def start_probe_flood(transport, allowlist_scope: dict, *,
                     beacon_list_path: str, list_idx: int,
                     duration_s: int) -> dict:
    if transport.mode == "none":
        return {"ok": False, "response": "no transport available"}
    try:
        entries = [
            line.strip() for line in Path(beacon_list_path).read_text().splitlines()
            if line.strip()
        ]
    except OSError as e:
        return {"ok": False, "response": f"cannot read probe list: {e}"}
    if not entries:
        return {"ok": False, "response": "probe list is empty"}
    out_of_scope = [
        ssid for ssid in entries
        if not ma.is_target_allowed(allowlist_scope, "wifi", ssid=ssid, bssid="")[0]
    ]
    if out_of_scope:
        return {"ok": False,
                "response": f"probe list contains out-of-scope SSIDs: {out_of_scope[:5]}"}
    capped = min(int(duration_s), MAX_ATTACK_DURATION_S)
    transport.send(mp.cmd_attack_probe_flood(list_idx=int(list_idx)))
    return {"ok": True, "response": f"probe_flood ({len(entries)} SSIDs) started",
            "duration_s": capped, "list_size": len(entries)}

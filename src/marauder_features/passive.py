"""MZ1312 DRIFTER — Marauder bridge module: passive recon (scanap/scansta/sniffprobe)."""

import marauder_protocol as mp

MAX_DURATION_S = 600

_MODE_TO_BUILDER = {
    "ap": mp.cmd_scan_ap,
    "sta": mp.cmd_scan_sta,
    "probe": mp.cmd_scan_probes,
}


def start_scan(transport, *, mode: str, duration_s: int) -> dict:
    """Issue a passive scan via the transport.

    Returns {ok, response, mode, duration_s}. Does NOT block — the caller
    is responsible for setting a timer to call stop_scan() after duration_s.
    """
    if transport.mode == "none":
        return {"ok": False, "response": "no transport available",
                "mode": mode, "duration_s": duration_s}

    builder = _MODE_TO_BUILDER.get(mode)
    if builder is None:
        return {"ok": False,
                "response": f"unknown mode={mode} (want ap/sta/probe)",
                "mode": mode, "duration_s": duration_s}

    capped = min(int(duration_s), MAX_DURATION_S)
    transport.send(builder())
    return {"ok": True, "response": f"scan started mode={mode}",
            "mode": mode, "duration_s": capped}


def stop_scan(transport) -> dict:
    if transport.mode == "none":
        return {"ok": False, "response": "no transport"}
    transport.send(mp.cmd_stop())
    return {"ok": True, "response": "stop sent"}

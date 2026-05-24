"""MZ1312 DRIFTER — Marauder bridge module: Marauder CLI command builders + line-event parser.

See docs/superpowers/specs/2026-05-24-marauder-bridge-design.md §3.
"""

# ── Command builders ──────────────────────────────────────────────────
# Pure functions. No I/O. The transport layer is responsible for
# actually writing these strings to the serial port.

def cmd_scan_ap() -> str:
    return "scanap\r\n"


def cmd_scan_sta() -> str:
    return "scansta\r\n"


def cmd_scan_probes() -> str:
    return "sniffprobe\r\n"


def cmd_stop() -> str:
    return "stopscan\r\n"


def cmd_attack_deauth(target_idx: int | None = None,
                      mode: str = "single") -> str:
    if mode == "all":
        return "attack -t deauth -c\r\n"
    if mode == "single":
        if target_idx is None:
            return "attack -t deauth\r\n"
        return f"attack -t deauth -a {int(target_idx)}\r\n"
    raise ValueError(f"unknown deauth mode={mode}")


def cmd_attack_deauth_detect() -> str:
    return "attack -t deauth -d\r\n"


def cmd_attack_beacon(mode: str, list_idx: int | None = None) -> str:
    if mode == "random":
        return "attack -t beacon -r\r\n"
    if mode == "rickroll":
        return "attack -t rickroll\r\n"
    if mode == "list":
        if list_idx is None:
            raise ValueError("list_idx required for beacon mode=list")
        return f"attack -t beacon -l {int(list_idx)}\r\n"
    raise ValueError(f"unknown beacon mode={mode}")


def cmd_attack_probe_flood(list_idx: int) -> str:
    return f"attack -t probe -l {int(list_idx)}\r\n"


# ── Event parser ──────────────────────────────────────────────────────
# Single regex table for all known Marauder line shapes. A firmware
# bump that changes line format is a one-place edit here.
#
# Patterns are (compiled_regex, type_label, group_to_event_func).
# Order matters — first match wins. Put more specific patterns first.

import re
import time

_RE_STA = re.compile(
    r"^RSSI:\s*(?P<rssi>-?\d+)\s+"
    r"BSSID:\s*(?P<ap_bssid>[0-9a-fA-F:]{17})\s+"
    r"STA:\s*(?P<sta_mac>[0-9a-fA-F:]{17})\s+"
    r"ESSID:\s?(?P<ssid>.*?)$"
)


def _build_sta(m: re.Match) -> dict:
    return {
        "rssi": int(m.group("rssi")),
        "ap_bssid": m.group("ap_bssid").lower(),
        "sta_mac": m.group("sta_mac").lower(),
        "ssid": m.group("ssid"),
    }


_RE_AP = re.compile(
    r"^RSSI:\s*(?P<rssi>-?\d+)\s+"
    r"Ch:\s*(?P<ch>\d+)\s+"
    r"BSSID:\s*(?P<bssid>[0-9a-fA-F:]{17})\s+"
    r"ESSID:\s?(?P<ssid>.*?)$"
)


def _build_ap(m: re.Match) -> dict:
    return {
        "rssi": int(m.group("rssi")),
        "ch": int(m.group("ch")),
        "bssid": m.group("bssid").lower(),
        "ssid": m.group("ssid"),
    }


_RE_PROBE = re.compile(
    r"^Probe req:\s*(?P<sta_mac>[0-9a-fA-F:]{17})\s*"
    r"(?:->|→)\s*"
    r'"(?P<ssid>.*)"\s*$'
)


def _build_probe(m: re.Match) -> dict:
    return {
        "sta_mac": m.group("sta_mac").lower(),
        "looking_for_ssid": m.group("ssid"),
    }


_PARSERS: list[tuple[re.Pattern, str, "callable"]] = [
    (_RE_STA, "station", _build_sta),
    (_RE_AP, "ap", _build_ap),
    (_RE_PROBE, "probe", _build_probe),
]


def parse_event(line: str) -> dict | None:
    """Parse one line of Marauder serial output.

    Returns:
        - dict with at least {'type': ..., 'ts': float} for known lines
        - {'type': 'unknown', 'raw': line} for lines that match no pattern
        - None for empty / whitespace-only lines
    """
    if line is None:
        return None
    stripped = line.strip()
    if not stripped:
        return None

    for pattern, type_label, builder in _PARSERS:
        m = pattern.match(stripped)
        if m:
            ev = builder(m)
            ev.setdefault("type", type_label)
            ev.setdefault("ts", time.time())
            return ev

    return {"type": "unknown", "raw": stripped}


if __name__ == "__main__":
    raise NotImplementedError("marauder_protocol is a library; import don't run")

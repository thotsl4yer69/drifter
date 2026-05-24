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


# ── Event parser ──────────────────────────────────────────────────────
# Single regex table for all known Marauder line shapes. A firmware
# bump that changes line format is a one-place edit here.
#
# Patterns are (compiled_regex, type_label, group_to_event_func).
# Order matters — first match wins. Put more specific patterns first.

import re
import time

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


_PARSERS: list[tuple[re.Pattern, str, "callable"]] = [
    (_RE_AP, "ap", _build_ap),
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

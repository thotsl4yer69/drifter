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


if __name__ == "__main__":
    raise NotImplementedError("marauder_protocol is a library; import don't run")

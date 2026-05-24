"""MZ1312 DRIFTER — Marauder bridge module: service entry point, main loop, command dispatch.

See docs/superpowers/specs/2026-05-24-marauder-bridge-design.md §1, §5.2, §6.
"""

import logging
import threading
import time
import uuid

log = logging.getLogger("marauder.bridge")

# Risk tiers per spec §5.2. Unknown commands fail closed (HIGH).
_LOW_RISK = {
    "scan_ap", "scan_sta", "scan_probes", "stop",
    "deauth_detect",
    "ble_scan_all", "ble_scan_airtag", "ble_scan_skim",
    "probe", "status",
}
_MED_RISK = {
    "select_ap", "channel_hop", "scan_param",
}
_HIGH_RISK = {
    "deauth_attack", "beacon_spam_list",
    "beacon_spam_random", "beacon_spam_rickroll",
    "probe_flood",
    "ble_spam_swift_pair", "ble_spam_easy_setup",
    "ble_spam_apple_proximity", "ble_spam_all",
    "evilportal_start", "evilportal_stop",
}


def classify_risk(command: str) -> str:
    """Return 'LOW' | 'MED' | 'HIGH'. Unknown → HIGH (fail closed)."""
    if command in _LOW_RISK:
        return "LOW"
    if command in _MED_RISK:
        return "MED"
    return "HIGH"


class CommandLock:
    """Single command at a time. Marauder firmware can't run two
    scans/attacks concurrently."""

    def __init__(self):
        self._lock = threading.Lock()
        self._holder: tuple[str, str] | None = None  # (command, op_uuid)

    def try_acquire(self, command: str, op_uuid: str) -> bool:
        with self._lock:
            if self._holder is not None:
                return False
            self._holder = (command, op_uuid)
            return True

    def release(self) -> None:
        with self._lock:
            self._holder = None

    def held_by(self) -> tuple[str, str] | None:
        with self._lock:
            return self._holder


class PendingConfirms:
    """HIGH-risk command confirmation tokens. Single-use, expire after TTL."""

    def __init__(self, ttl_s: float = 120):
        self._ttl = ttl_s
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[float, str, dict]] = {}  # token → (ts, cmd, args)

    def register(self, command: str, args: dict) -> str:
        token = uuid.uuid4().hex
        with self._lock:
            self._entries[token] = (time.time(), command, args)
        return token

    def pop(self, token: str) -> tuple[str, dict] | None:
        with self._lock:
            entry = self._entries.pop(token, None)
        if entry is None:
            return None
        ts, cmd, args = entry
        if time.time() - ts > self._ttl:
            return None  # expired between register and pop
        return cmd, args

    def sweep(self) -> int:
        """Remove expired entries. Returns count removed."""
        cutoff = time.time() - self._ttl
        removed = 0
        with self._lock:
            stale = [t for t, (ts, _, _) in self._entries.items() if ts < cutoff]
            for t in stale:
                del self._entries[t]
                removed += 1
        return removed


if __name__ == "__main__":
    raise NotImplementedError("marauder_bridge main() lands in Task 1.18")

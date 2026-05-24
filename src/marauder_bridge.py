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


import json

import marauder_allowlist as ma
from marauder_features import passive as passive_feat


class Bridge:
    """Service-level orchestrator: holds transport + MQTT + allowlist +
    storage; dispatches commands; manages locks and confirmations.
    """

    def __init__(self, *, transport, mqtt_client, allowlist_scope,
                 session_writer):
        self.transport = transport
        self.mqtt = mqtt_client
        self.allowlist = allowlist_scope  # dict from load_marauder_allowlist
        self.storage = session_writer
        self.lock = CommandLock()
        self.confirms = PendingConfirms(ttl_s=120)

    # ── MQTT helpers ─────────────────────────────────────────────────
    def _publish(self, topic: str, payload: dict, retain: bool = False) -> None:
        body = json.dumps(payload, separators=(",", ":"))
        self.mqtt.publish(topic, body, qos=0, retain=retain)

    def _publish_event(self, op_id, ok: bool, response: str,
                       **extra) -> None:
        ev = {"id": op_id, "ok": ok, "response": response, "ts": time.time()}
        ev.update(extra)
        self._publish("drifter/marauder/event", ev)

    # ── Dispatch ─────────────────────────────────────────────────────
    def dispatch(self, payload: dict) -> None:
        op_id = payload.get("id")
        command = payload.get("command", "")
        args = payload.get("args") or {}
        confirm_token = payload.get("confirm_token")

        # 1) Risk classification
        risk = classify_risk(command)

        # 2) HIGH risk → confirm flow
        if risk == "HIGH":
            if not confirm_token:
                # First leg — issue token
                token = self.confirms.register(command, args)
                self._publish_event(op_id, False,
                                    "Confirmation required",
                                    confirm_token=token,
                                    expires_in_s=120)
                return
            # Second leg — validate token
            popped = self.confirms.pop(confirm_token)
            if popped is None:
                self._publish_event(op_id, False,
                                    "Invalid or expired confirm_token")
                return
            command, args = popped

            # 3) Allowlist gate
            category = self._command_to_allowlist_category(command)
            if category is not None:
                ok, reason = ma.is_target_allowed(self.allowlist, category, **args)
                if not ok:
                    self._publish_event(op_id, False,
                                        reason,
                                        scope=f"marauder.{category}")
                    return

        # 4) Acquire command lock
        if not self.lock.try_acquire(command, op_id or ""):
            held = self.lock.held_by()
            self._publish_event(op_id, False,
                                f"command locked (in use by {held})")
            return

        # 5) Execute via feature dispatcher
        try:
            result = self._execute(command, args)
            self._publish_event(op_id, result["ok"], result["response"])
        finally:
            # LOW-risk scans hold the lock for their duration; the timer that
            # ends the scan also releases. For now, always release here;
            # duration-based release lands when we add the timer in Task 1.18.
            self.lock.release()

    def _command_to_allowlist_category(self, command: str):
        if command in {"deauth_attack", "beacon_spam_list",
                       "beacon_spam_random", "beacon_spam_rickroll",
                       "probe_flood"}:
            return "wifi"
        if command in {"ble_spam_swift_pair", "ble_spam_easy_setup",
                       "ble_spam_apple_proximity", "ble_spam_all"}:
            return "ble"
        if command in {"evilportal_start"}:
            return "evilportal"
        return None

    def _execute(self, command: str, args: dict) -> dict:
        # Phase 1 dispatch table — extended in Phases 2/3/4
        if command == "scan_ap":
            return passive_feat.start_scan(self.transport, mode="ap",
                                            duration_s=args.get("duration_s", 60))
        if command == "scan_sta":
            return passive_feat.start_scan(self.transport, mode="sta",
                                            duration_s=args.get("duration_s", 60))
        if command == "scan_probes":
            return passive_feat.start_scan(self.transport, mode="probe",
                                            duration_s=args.get("duration_s", 60))
        if command == "stop":
            return passive_feat.stop_scan(self.transport)
        return {"ok": False, "response": f"command not implemented in this phase: {command}"}


import signal
import sys
from pathlib import Path


def _make_mqtt_client(client_id: str):
    """Defer paho import so tests that monkey-patch can run without it."""
    from config import MQTT_HOST, MQTT_PORT, make_mqtt_client
    return make_mqtt_client(client_id), MQTT_HOST, MQTT_PORT


def _publish_status(bridge, state: str) -> None:
    bridge._publish("drifter/marauder/status", {
        "state": state,
        "mode": bridge.transport.mode,
        "transport": bridge.transport.mode,
        "hw_detail": bridge.transport.hw_detail,
        "ts": time.time(),
    }, retain=True)


def main(argv=None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    # 1) Transport autodetect
    from marauder_transport import MarauderTransport
    transport = MarauderTransport()
    transport.autodetect()

    # 2) Allowlist
    scope = ma.load_marauder_allowlist()

    # 3) Session storage
    from marauder_storage import SessionWriter
    state_root = Path("/opt/drifter/state/marauder")
    state_root.mkdir(parents=True, exist_ok=True)
    storage = SessionWriter(state_root=state_root)

    # 4) MQTT
    mqtt_client, host, port = _make_mqtt_client("drifter-marauder")
    mqtt_client.connect(host, port, keepalive=60)

    bridge = Bridge(transport=transport, mqtt_client=mqtt_client,
                    allowlist_scope=scope, session_writer=storage)

    # 5) Reader thread → MQTT scan events (only if direct transport)
    if transport.mode == "direct":
        from marauder_protocol import parse_event

        def line_handler(line: str) -> None:
            ev = parse_event(line)
            if ev is None:
                return
            topic_for_type = {
                "ap": "drifter/marauder/scan/ap",
                "station": "drifter/marauder/scan/sta",
                "probe": "drifter/marauder/scan/probe",
            }
            topic = topic_for_type.get(ev["type"])
            if topic:
                bridge._publish(topic, ev)
            elif ev["type"] == "unknown":
                log.debug("unknown line: %s", ev.get("raw", "")[:120])

        transport.start(line_callback=line_handler)

    # 6) MQTT command subscribe
    def on_message(client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
        except Exception as e:
            log.warning("invalid cmd payload: %s", e)
            return
        bridge.dispatch(payload)

    mqtt_client.on_message = on_message
    mqtt_client.subscribe("drifter/marauder/cmd", qos=0)

    # 7) Initial status publish
    initial_state = "no_hardware" if transport.mode == "none" else "idle"
    _publish_status(bridge, initial_state)

    # 8) Signal handling
    stop_event = threading.Event()

    def handle_signal(signum, frame):
        log.info("signal %s received — shutting down", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    # 9) Background loop — status heartbeat every 30s, sweep stale confirms
    last_status = 0.0
    mqtt_client.loop_start()
    try:
        while not stop_event.is_set():
            time.sleep(0.5)
            now = time.time()
            if now - last_status > 30:
                state = "no_hardware" if transport.mode == "none" else "idle"
                _publish_status(bridge, state)
                last_status = now
            bridge.confirms.sweep()
    finally:
        mqtt_client.loop_stop()
        if transport.mode == "direct":
            transport.stop()
        try:
            mqtt_client.disconnect()
        except Exception:
            pass
        log.info("marauder bridge exiting cleanly")
    return 0


if __name__ == "__main__":
    sys.exit(main())

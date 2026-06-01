#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Wi-Fi handshake / PMKID audit (allowlist-scoped)

Operator-private self-audit. Spawns bettercap with a caplet that enables
wifi.recon + wifi.handshake.collect_pmkid, but DOES NOT load
wifi.deauth / wifi.assoc — capture is passive only. Captures are
filtered against /opt/drifter/etc/audit_targets.yaml; anything not on
the allowlist is dropped before being persisted.

Publishes counters to drifter/wifi/audit:
    {ts, handshakes_total, pmkids_total,
     this_drive_handshakes, this_drive_pmkids,
     recent_targets:[{ssid, bssid, type, ts}],
     state}

If the allowlist file is missing OR empty, the service logs the
condition and parks in 'idle' — bettercap is NEVER spawned. The
allowlist is the strict scope gate.

UNCAGED TECHNOLOGY — EST 1991
"""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - PyYAML is a deploy dep
    yaml = None

from config import MQTT_HOST, MQTT_PORT, make_mqtt_client

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [WIFI-AUDIT] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger(__name__)

ALLOWLIST_PATH = Path(os.environ.get(
    'AUDIT_ALLOWLIST', '/opt/drifter/etc/audit_targets.yaml'))
HANDSHAKE_DIR = Path(os.environ.get(
    'AUDIT_HANDSHAKE_DIR', '/opt/drifter/state/handshakes'))
AUDIT_IFACE = os.environ.get('AUDIT_IFACE', 'wlan1')

TOPIC_AUDIT = 'drifter/wifi/audit'

MAC_RE = re.compile(r'([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})')


def load_allowlist(path: Path) -> list[dict]:
    """Return list of {ssid, bssid?} entries from the YAML.

    Returns [] if the file is missing, empty, malformed, or contains
    no allowed entries. Empty list → bettercap MUST NOT spawn.
    """
    if not path.exists():
        log.warning("allowlist not found at %s", path)
        return []
    if yaml is None:
        log.error("PyYAML missing — cannot parse allowlist; treating as empty")
        return []
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        log.error("allowlist YAML parse error: %s", e)
        return []
    items = raw.get('allowed') or []
    if not isinstance(items, list):
        return []
    cleaned: list[dict] = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        ssid = entry.get('ssid')
        bssid = entry.get('bssid')
        if not ssid and not bssid:
            continue
        item: dict = {}
        if ssid:
            item['ssid'] = str(ssid)
        if bssid:
            item['bssid'] = str(bssid).upper()
        cleaned.append(item)
    return cleaned


def matches_allowlist(ssid: str, bssid: str, allowlist: list[dict]) -> bool:
    """Strict allowlist check — used before persisting any capture.

    SSID match is case-sensitive (Wi-Fi SSIDs are bytes, not text). If
    an entry includes a BSSID, it must match too (case-insensitive).
    """
    bssid_u = (bssid or '').upper()
    for entry in allowlist:
        want_ssid = entry.get('ssid')
        want_bssid = (entry.get('bssid') or '').upper()
        if want_ssid is not None and ssid != want_ssid:
            continue
        if want_bssid and bssid_u != want_bssid:
            continue
        if want_ssid is None and not want_bssid:
            continue
        return True
    return False


def build_caplet(iface: str, output_dir: Path) -> str:
    """Build a passive bettercap caplet — NO wifi.deauth / wifi.assoc."""
    return (
        f"set wifi.interface {iface}\n"
        f"set wifi.handshakes.file {output_dir}/handshake.pcap\n"
        f"set wifi.handshakes.aggregate true\n"
        f"set wifi.show.sort clients desc\n"
        # Modules: recon + PMKID collection only. Explicitly NOT loading
        # wifi.deauth, wifi.assoc, wifi.client.probe, or any active mod.
        "wifi.recon on\n"
        "wifi.handshake.collect_pmkid on\n"
    )


class AuditState:
    """Counters + recent-capture ring buffer published to MQTT."""

    def __init__(self) -> None:
        self.handshakes_total = 0
        self.pmkids_total = 0
        self.this_drive_handshakes = 0
        self.this_drive_pmkids = 0
        # Bounded — the cockpit only renders the last few anyway.
        self.recent: deque = deque(maxlen=20)
        self.state = 'idle'
        self.lock = threading.Lock()

    def record(self, kind: str, ssid: str, bssid: str) -> None:
        with self.lock:
            row = {'ssid': ssid, 'bssid': bssid, 'type': kind, 'ts': time.time()}
            self.recent.appendleft(row)
            if kind == 'handshake':
                self.handshakes_total += 1
                self.this_drive_handshakes += 1
            elif kind == 'pmkid':
                self.pmkids_total += 1
                self.this_drive_pmkids += 1

    def snapshot(self) -> dict:
        with self.lock:
            return {
                'ts': time.time(),
                'state': self.state,
                'handshakes_total': self.handshakes_total,
                'pmkids_total': self.pmkids_total,
                'this_drive_handshakes': self.this_drive_handshakes,
                'this_drive_pmkids': self.this_drive_pmkids,
                'recent_targets': list(self.recent),
            }


def parse_capture_line(line: str) -> dict | None:
    """Parse a bettercap stdout line announcing a capture.

    bettercap log lines look like:
        [wifi.handshake] captured XX:XX:XX:XX:XX:XX -> YY:... (ssid)
        new pmkid for AA:BB:... (ssid)

    We extract (kind, bssid, ssid). Returns None if not a capture line.
    """
    if 'pmkid' in line.lower():
        kind = 'pmkid'
    elif 'handshake' in line.lower() and 'captured' in line.lower():
        kind = 'handshake'
    else:
        return None
    mac_m = MAC_RE.search(line)
    if not mac_m:
        return None
    bssid = mac_m.group(1).upper()
    # SSID — bettercap prints it in parens or after "for "; fall back to ''.
    ssid = ''
    paren = re.search(r'\(([^)]+)\)', line)
    if paren:
        ssid = paren.group(1).strip()
    return {'kind': kind, 'bssid': bssid, 'ssid': ssid}


def process_bettercap_line(line: str, allowlist: list[dict],
                           audit: AuditState) -> dict | None:
    """Apply allowlist gate to a parsed bettercap line.

    Returns the recorded capture dict, or None if the line was either
    not a capture or was dropped because the source AP isn't on the
    allowlist.
    """
    parsed = parse_capture_line(line)
    if not parsed:
        return None
    if not matches_allowlist(parsed['ssid'], parsed['bssid'], allowlist):
        log.warning("non-allowed AP, dropping (ssid=%r bssid=%s type=%s)",
                    parsed['ssid'], parsed['bssid'], parsed['kind'])
        return None
    audit.record(parsed['kind'], parsed['ssid'], parsed['bssid'])
    return parsed


def spawn_bettercap(caplet_path: Path) -> subprocess.Popen:
    """Start bettercap pointed at the caplet. Caller owns lifecycle."""
    return subprocess.Popen(
        ['bettercap', '-no-colors', '-caplet', str(caplet_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )


def _publish_loop(mqtt_client, audit: AuditState, running_flag) -> None:
    """Background thread — push snapshot every 5s."""
    while running_flag():
        mqtt_client.publish(TOPIC_AUDIT, json.dumps(audit.snapshot()))
        time.sleep(5)


def main():
    running = True

    def _handle_signal(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    audit = AuditState()

    mqtt_client = make_mqtt_client("drifter-wifi-audit")
    while running:
        try:
            mqtt_client.connect(MQTT_HOST, MQTT_PORT, 60)
            break
        except Exception as e:
            log.warning("Waiting for MQTT broker... (%s)", e)
            time.sleep(3)
    mqtt_client.loop_start()

    HANDSHAKE_DIR.mkdir(parents=True, exist_ok=True)

    # ── ALLOWLIST GATE ───────────────────────────────────────────────
    allowlist = load_allowlist(ALLOWLIST_PATH)
    if not allowlist:
        audit.state = 'idle-no-allowlist'
        log.warning("no allowlist entries — audit service idle "
                    "(populate %s to enable)", ALLOWLIST_PATH)
        # Publish the idle state forever so the cockpit can render it.
        while running:
            mqtt_client.publish(TOPIC_AUDIT, json.dumps(audit.snapshot()),
                                retain=True)
            time.sleep(15)
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
        return

    log.info("allowlist has %d entries; arming bettercap on %s",
             len(allowlist), AUDIT_IFACE)

    caplet_path = HANDSHAKE_DIR / 'drifter_audit.cap'
    caplet_path.write_text(build_caplet(AUDIT_IFACE, HANDSHAKE_DIR))

    audit.state = 'scanning'

    publisher = threading.Thread(
        target=_publish_loop,
        args=(mqtt_client, audit, lambda: running),
        daemon=True,
    )
    publisher.start()

    proc: subprocess.Popen | None = None
    try:
        proc = spawn_bettercap(caplet_path)
        assert proc.stdout is not None
        for line in proc.stdout:
            if not running:
                break
            line = line.rstrip()
            result = process_bettercap_line(line, allowlist, audit)
            if result:
                audit.state = 'captured'
                log.info("captured %s for %r (%s)",
                         result['kind'], result['ssid'], result['bssid'])
    except FileNotFoundError:
        log.error("bettercap binary missing — install via apt")
        audit.state = 'error-no-bettercap'
    finally:
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    mqtt_client.publish(TOPIC_AUDIT, json.dumps(audit.snapshot()), retain=True)
    mqtt_client.loop_stop()
    mqtt_client.disconnect()
    log.info("wifi-audit stopped")


if __name__ == '__main__':
    main()

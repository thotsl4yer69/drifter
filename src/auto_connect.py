#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Wi-Fi Hotspot Auto-Connector

Boots looking for the operator's phone hotspot (or any known SSID) and joins
it as a CLIENT so the node has internet. If no known SSID appears within
AUTOCONNECT_AP_FALLBACK_SEC, it falls back to bringing up the node's OWN
AP (MZ1312_DRIFTER) so the operator can always SSH in and fix things.

Publishes its state to drifter/network/status so the in-car LCD dashboard
(and the cockpit) can show connection progress without a monitor.

  {"state": "...", "ssid": ..., "ip": ..., "internet": bool,
   "ap_fallback": bool, "ts": ...}

States: searching · connecting · connected · ap_fallback · offline

NOTE: the Pi has a single wlan0 radio — it cannot be a client AND run the
MZ1312_DRIFTER AP at the same time. This service brings the AP DOWN to join
a client network and back UP for the fallback. If you want the phone to
tether to the Pi instead (Pi-as-AP), disable this service and keep
drifter-hotspot enabled.

UNCAGED TECHNOLOGY — EST 1991
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import signal
import subprocess
import time

from config import (
    AP_FALLBACK_CONNECTION,
    AUTOCONNECT_AP_FALLBACK_SEC,
    AUTOCONNECT_KNOWN_SSIDS,
    AUTOCONNECT_RETRY_SEC,
    AUTOCONNECT_SCAN_TIMEOUT,
    AUTOCONNECT_WIFI_IFACE,
    MQTT_HOST,
    MQTT_PORT,
    PHONE_HOTSPOT_PSK,
    PHONE_HOTSPOT_SSID,
    PING_HOST,
    PING_TIMEOUT_SEC,
    TOPICS,
    make_mqtt_client,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [AUTOCONNECT] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger(__name__)


# ── Pure helpers (unit-tested) ────────────────────────────────────────

def _unescape_terse(field: str) -> str:
    """nmcli -t escapes ':' as '\\:' and '\\' as '\\\\' inside fields."""
    return re.sub(r'\\(.)', r'\1', field)


def _split_terse(line: str) -> list[str]:
    """Split an nmcli -t line on unescaped ':' and unescape each field."""
    return [_unescape_terse(f) for f in re.split(r'(?<!\\):', line)]


def parse_wifi_scan(nmcli_out: str) -> set[str]:
    """SSIDs visible in `nmcli -t -f SSID dev wifi`. Drops blanks/hidden.

    A single field per line, but an SSID containing ':' arrives escaped as
    '\\:' — unescape so it matches the configured name exactly."""
    return {_unescape_terse(ln).strip()
            for ln in nmcli_out.splitlines() if ln.strip()}


def parse_active_ssid(nmcli_out: str) -> str | None:
    """From `nmcli -t -f ACTIVE,SSID dev wifi`, the joined SSID (client).

    Split on the *unescaped* field separator so an SSID with a ':' (emitted
    as '\\:') isn't truncated."""
    for line in nmcli_out.splitlines():
        parts = _split_terse(line)
        if parts and parts[0] == 'yes' and len(parts) > 1 and parts[1]:
            return parts[1]
    return None


def pick_target_ssid(visible: set[str], known: list[str]) -> str | None:
    """First known SSID (priority order) that is currently visible."""
    for ssid in known:
        if ssid and ssid in visible:
            return ssid
    return None


# ── nmcli wrappers ────────────────────────────────────────────────────

def _run(cmd: list[str], timeout: float = 20.0) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def scan_wifi() -> set[str]:
    if not shutil.which('nmcli'):
        return set()
    try:
        _run(['nmcli', 'dev', 'wifi', 'rescan'], timeout=AUTOCONNECT_SCAN_TIMEOUT)
    except Exception:
        pass  # rescan can fail if a scan is already in flight — list anyway
    try:
        r = _run(['nmcli', '-t', '-f', 'SSID', 'dev', 'wifi', 'list'],
                 timeout=AUTOCONNECT_SCAN_TIMEOUT)
        return parse_wifi_scan(r.stdout)
    except Exception as e:
        log.warning(f"wifi scan failed: {e}")
        return set()


def active_client_ssid() -> str | None:
    if not shutil.which('nmcli'):
        return None
    try:
        r = _run(['nmcli', '-t', '-f', 'ACTIVE,SSID', 'dev', 'wifi'], timeout=8)
        return parse_active_ssid(r.stdout)
    except Exception:
        return None


def connection_profile_exists(name: str) -> bool:
    try:
        r = _run(['nmcli', '-t', '-f', 'NAME', 'connection', 'show'], timeout=8)
        return name in {ln.strip() for ln in r.stdout.splitlines()}
    except Exception:
        return False


def connect_ssid(ssid: str) -> bool:
    """Join an SSID as a client. Prefers an existing NM profile; else uses
    the configured PSK for the phone hotspot; else tries an open/saved join."""
    iface = AUTOCONNECT_WIFI_IFACE
    try:
        if connection_profile_exists(ssid):
            r = _run(['nmcli', 'connection', 'up', ssid], timeout=30)
        elif ssid == PHONE_HOTSPOT_SSID and PHONE_HOTSPOT_PSK:
            r = _run(['nmcli', 'dev', 'wifi', 'connect', ssid,
                      'password', PHONE_HOTSPOT_PSK, 'ifname', iface], timeout=30)
        else:
            r = _run(['nmcli', 'dev', 'wifi', 'connect', ssid,
                      'ifname', iface], timeout=30)
        if r.returncode == 0:
            log.info(f"joined '{ssid}'")
            return True
        log.warning(f"join '{ssid}' failed: {r.stderr.strip() or r.stdout.strip()}")
        return False
    except Exception as e:
        log.warning(f"join '{ssid}' error: {e}")
        return False


def bring_up_ap() -> bool:
    """Fallback: bring up our own AP so the operator can SSH in."""
    if not AP_FALLBACK_CONNECTION or not shutil.which('nmcli'):
        return False
    try:
        r = _run(['nmcli', 'connection', 'up', AP_FALLBACK_CONNECTION], timeout=30)
        if r.returncode == 0:
            log.info(f"AP fallback up: {AP_FALLBACK_CONNECTION}")
            return True
        log.warning(f"AP fallback failed: {r.stderr.strip()}")
        return False
    except Exception as e:
        log.warning(f"AP fallback error: {e}")
        return False


def current_ip(iface: str = AUTOCONNECT_WIFI_IFACE) -> str | None:
    try:
        r = _run(['ip', '-4', '-brief', 'addr', 'show', iface], timeout=5)
        for line in r.stdout.splitlines():
            f = line.split()
            if len(f) >= 3 and '/' in f[-1]:
                return f[-1].split('/')[0]
    except Exception:
        pass
    return None


def disable_power_save(iface: str = AUTOCONNECT_WIFI_IFACE) -> bool:
    """Turn off Wi-Fi power save before connecting. The brcmfmac power-save
    cycle is what wedges boot on the Pi 5 (see scripts/fix-wifi-boot.sh); a
    flapping power state also makes client joins drop. Best-effort: tries
    `iw` first, then nmcli's per-connection setting. Never raises."""
    ok = False
    if shutil.which('iw'):
        try:
            r = _run(['iw', 'dev', iface, 'set', 'power_save', 'off'], timeout=8)
            ok = r.returncode == 0
        except Exception:
            pass
    if shutil.which('iwconfig'):
        try:
            _run(['iwconfig', iface, 'power', 'off'], timeout=8)
        except Exception:
            pass
    if ok:
        log.info(f"{iface} power save disabled")
    else:
        log.warning(f"could not disable power save on {iface} (iw missing?)")
    return ok


def internet_ok() -> bool:
    if not shutil.which('ping'):
        return False
    try:
        r = _run(['ping', '-c', '1', '-W', str(PING_TIMEOUT_SEC), PING_HOST],
                 timeout=PING_TIMEOUT_SEC + 2)
        return r.returncode == 0
    except Exception:
        return False


# ── Service ───────────────────────────────────────────────────────────

def publish_status(client, state: str, ssid, ip, internet: bool,
                   ap_fallback: bool) -> None:
    if client is None:
        return
    payload = {
        'state': state,
        'ssid': ssid,
        'ip': ip,
        'internet': internet,
        'ap_fallback': ap_fallback,
        'ts': time.time(),
    }
    try:
        client.publish(TOPICS['network_status'], json.dumps(payload), retain=True)
    except Exception:
        pass


def main() -> None:
    log.info("DRIFTER Wi-Fi auto-connector starting...")
    known = list(AUTOCONNECT_KNOWN_SSIDS)
    if not known:
        log.warning("No known SSIDs configured (set PHONE_HOTSPOT_SSID or "
                    "AUTOCONNECT_KNOWN_SSIDS). Will still report status + AP fallback.")
    else:
        log.info(f"Known SSIDs (priority): {', '.join(known)}")
    if not shutil.which('nmcli'):
        log.error("nmcli not found — NetworkManager required. Exiting.")
        return

    # Kill Wi-Fi power save before doing anything — the brcmfmac power-save
    # cycle both wedges boot and destabilises client joins.
    disable_power_save()

    running = True

    def _stop(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    client = make_mqtt_client("drifter-autoconnect")
    try:
        client.connect(MQTT_HOST, MQTT_PORT, 30)
        client.loop_start()
    except OSError as e:
        log.warning(f"MQTT unavailable ({e}) — status not published, continuing")
        client = None

    start = time.time()
    ap_active = False

    while running:
        joined = active_client_ssid()
        if joined and (not known or joined in known or not ap_active):
            # Connected as a client to something (phone hotspot or saved net).
            # NM re-enables power save on association — keep it off each pass.
            disable_power_save()
            ip = current_ip()
            net = internet_ok()
            publish_status(client, 'connected', joined, ip, net, False)
            log.info(f"connected: {joined} ip={ip} internet={net}")
            start = time.time()  # reset the fallback clock while connected
            ap_active = False
            _sleep(AUTOCONNECT_RETRY_SEC, lambda: running)
            continue

        # Not connected to a known client network — try to join one.
        visible = scan_wifi()
        target = pick_target_ssid(visible, known)
        if target:
            publish_status(client, 'connecting', target, None, False, ap_active)
            log.info(f"target '{target}' visible — joining")
            if connect_ssid(target):
                ip = current_ip()
                net = internet_ok()
                publish_status(client, 'connected', target, ip, net, False)
                start = time.time()
                ap_active = False
                _sleep(AUTOCONNECT_RETRY_SEC, lambda: running)
                continue
        else:
            publish_status(client, 'searching', None, current_ip(), False, ap_active)
            log.info(f"no known SSID visible ({len(visible)} networks seen)")

        # AP fallback after the grace period with no client connection.
        elapsed = time.time() - start
        if (not ap_active and AUTOCONNECT_AP_FALLBACK_SEC > 0
                and elapsed >= AUTOCONNECT_AP_FALLBACK_SEC):
            log.warning(f"no hotspot for {int(elapsed)}s — bringing up AP fallback")
            if bring_up_ap():
                ap_active = True
                publish_status(client, 'ap_fallback', AP_FALLBACK_CONNECTION,
                               current_ip(), False, True)

        _sleep(AUTOCONNECT_RETRY_SEC, lambda: running)

    log.info("Wi-Fi auto-connector shutting down...")
    if client:
        client.loop_stop()
        client.disconnect()
    log.info("Wi-Fi auto-connector stopped")


def _sleep(seconds: float, keep_running) -> None:
    """Interruptible sleep — wakes early on shutdown."""
    end = time.time() + seconds
    while time.time() < end and keep_running():
        time.sleep(0.5)


if __name__ == '__main__':
    main()

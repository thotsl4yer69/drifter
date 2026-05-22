#!/usr/bin/env python3
"""MZ1312 DRIFTER — CaringCaribou CAN-discovery bridge.

A small MQTT-driven wrapper around the `caringcaribou` CLI (installed into
/opt/drifter/venv via the pip package of the same name). The cockpit's
CAN DISCOVERY drawer publishes structured commands on drifter/can/command;
this service translates them into cc subprocess invocations against the
configured CAN interface, parses stdout, and republishes JSON to
drifter/can/discovery.

Every fuzz run also writes a SavvyCAN-compatible CSV to
/opt/drifter/state/can_captures/<unixts>.csv. can_bridge.py continues to
own the raw CAN stream — this module is purely the command + parse layer.

Command schema (drifter/can/command, JSON):
  {"command": "discover_ecus"}
  {"command": "list_services", "ecu_id": 0x7E0}
  {"command": "dump_dids", "ecu_id": 0x7E0}
  {"command": "fuzz_range", "id_start": 0x700, "id_end": 0x7FF}

Response schema (drifter/can/discovery, JSON):
  {"ts": float, "command": str, "interface": str,
   "ok": bool, "results": [...], "error": str?}

The interface name is sourced from can_bridge.find_can_interface() at
startup; if no CAN dongle is connected, the service stays running and
publishes a structured 'no_interface' error to each command (never a
500 — bench tests rely on this contract).

UNCAGED TECHNOLOGY — EST 1991
"""
from __future__ import annotations

import json
import logging
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import paho.mqtt.client as mqtt

from config import MQTT_HOST, MQTT_PORT, make_mqtt_client

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [CAN-DISC] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger(__name__)

# State / capture directories — must match what web_dashboard_handlers
# expects so /api/can/captures/<file> can serve them without a config knob.
CAN_CAPTURE_DIR = Path('/opt/drifter/state/can_captures')

# MQTT topics — wired directly here rather than adding to config.TOPICS so
# can_bridge.py stays unaware of the discovery layer (separate concern).
TOPIC_COMMAND = 'drifter/can/command'
TOPIC_DISCOVERY = 'drifter/can/discovery'
TOPIC_STATUS = 'drifter/can/discovery/status'

# Allowlist of commands this service will execute. Anything else is dropped
# with a structured 'unknown_command' response — the cockpit-side allowlist
# in web_dashboard_handlers is the first gate, this is the second.
ALLOWED_COMMANDS = {
    'discover_ecus', 'list_services', 'dump_dids', 'fuzz_range',
}

# CaringCaribou CLI binary inside the project venv.
CC_BIN = '/opt/drifter/venv/bin/caringcaribou'

# Subprocess timeouts (seconds) — UDS discovery rounds can be slow; fuzz
# runs are bounded by the operator-supplied range.
CC_TIMEOUTS = {
    'discover_ecus': 60.0,
    'list_services': 45.0,
    'dump_dids': 45.0,
    'fuzz_range': 120.0,
}

# SavvyCAN-compatible CSV header. Every fuzz run writes one row per frame
# observed during the cc subprocess lifetime, prefixed with this header.
SAVVYCAN_HEADER = (
    'Time Stamp,ID,Extended,Bus,LEN,D1,D2,D3,D4,D5,D6,D7,D8'
)


def _detect_can_interface() -> Optional[str]:
    """Return the first usable CAN interface name, or None on a bench Pi.

    Mirrors can_bridge.find_can_interface() without importing the bus —
    we just need the name. /sys/class/net is the cheapest probe.
    """
    for iface in ('can0', 'can1', 'slcan0'):
        if Path(f'/sys/class/net/{iface}').exists():
            return iface
    return None


def _hex_int(v) -> Optional[int]:
    """Accept ints or hex strings like '0x7E0' / '7E0'. Returns None on failure."""
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        s = v.strip()
        try:
            return int(s, 16) if s.lower().startswith('0x') else int(s, 0)
        except ValueError:
            try:
                return int(s, 16)
            except ValueError:
                return None
    return None


def _build_cc_args(command: str, interface: str, body: dict) -> Optional[list]:
    """Translate a command body into a caringcaribou CLI argv vector.

    Returns None if the body is malformed for this command — the caller
    then publishes a 'bad_args' structured error.
    """
    base = [CC_BIN, '-i', interface]
    if command == 'discover_ecus':
        # `uds discovery` enumerates UDS-capable arbitration IDs.
        return base + ['uds', 'discovery']
    if command == 'list_services':
        ecu_id = _hex_int(body.get('ecu_id'))
        if ecu_id is None:
            return None
        # `uds services <send_id> <recv_id>` — recv = send + 8 is the
        # standard pairing on legacy 11-bit OBD-II frames.
        return base + ['uds', 'services', hex(ecu_id), hex(ecu_id + 8)]
    if command == 'dump_dids':
        ecu_id = _hex_int(body.get('ecu_id'))
        if ecu_id is None:
            return None
        return base + ['uds', 'dump_dids', hex(ecu_id), hex(ecu_id + 8)]
    if command == 'fuzz_range':
        id_start = _hex_int(body.get('id_start'))
        id_end = _hex_int(body.get('id_end'))
        if id_start is None or id_end is None or id_end < id_start:
            return None
        return base + ['fuzzer', 'random',
                       '--min-id', hex(id_start),
                       '--max-id', hex(id_end)]
    return None


# Regex for a parsed cc stdout row. cc's UDS modules print lines like:
#   "0x7E0 supported (positive response 0x50)"
#   "DID 0xF190 -> 17 chars: WBA12345..."
# We surface raw lines verbatim plus a best-effort tokenisation so the
# cockpit can render a table without needing per-module parsers.
_HEX_TOKEN = re.compile(r'\b0x[0-9A-Fa-f]{1,8}\b')


def _parse_cc_output(command: str, stdout: str) -> list:
    """Tokenise cc stdout into a list of result rows.

    Each row: {raw, hex_tokens[], ts}. We deliberately don't try to be a
    full cc parser — the operator gets every line, and the cockpit picks
    out the hex IDs / response codes via the tokens array.
    """
    rows = []
    now = time.time()
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        tokens = _HEX_TOKEN.findall(stripped)
        rows.append({
            'ts': now,
            'raw': stripped,
            'hex_tokens': tokens,
        })
    return rows


def _write_savvycan_csv(rows: list) -> Optional[str]:
    """Persist a fuzz-run row set as a SavvyCAN CSV. Returns the filename.

    Each row is the raw cc stdout line — we promote any line that parses as
    a CAN frame into a proper CSV record; non-frame lines are dropped from
    the CSV (kept in the MQTT payload so the cockpit shows full output).
    """
    if not rows:
        return None
    CAN_CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    fname = f"{int(time.time())}.csv"
    path = CAN_CAPTURE_DIR / fname
    try:
        with path.open('w') as fh:
            fh.write(SAVVYCAN_HEADER + '\n')
            for row in rows:
                tokens = row.get('hex_tokens') or []
                if not tokens:
                    continue
                # First hex token is the arbitration ID; remaining tokens
                # become data bytes (truncated to 8). cc doesn't surface a
                # timestamp per frame, so we stamp with row['ts'].
                arb_id = tokens[0]
                data = tokens[1:9]
                data += [''] * (8 - len(data))
                fh.write(
                    f"{row['ts']:.6f},{arb_id},0,0,{min(8, len(tokens) - 1)},"
                    + ','.join(data) + '\n'
                )
    except OSError as e:
        log.warning("CSV write failed: %s", e)
        return None
    return fname


def run_command(command: str, body: dict, interface: Optional[str],
                runner=subprocess.run) -> dict:
    """Execute one command and return a structured response payload.

    `runner` is injected so tests can substitute a fake. Returns a dict
    matching the documented drifter/can/discovery schema.
    """
    ts = time.time()
    response = {
        'ts': ts,
        'command': command,
        'interface': interface or '',
        'ok': False,
        'results': [],
    }
    if command not in ALLOWED_COMMANDS:
        response['error'] = 'unknown_command'
        return response
    if not interface:
        response['error'] = 'no_interface'
        return response
    argv = _build_cc_args(command, interface, body or {})
    if argv is None:
        response['error'] = 'bad_args'
        return response
    timeout = CC_TIMEOUTS.get(command, 60.0)
    log.info("cc %s on %s (timeout %.0fs)", command, interface, timeout)
    try:
        completed = runner(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        response['error'] = 'timeout'
        return response
    except FileNotFoundError:
        response['error'] = 'cc_not_installed'
        return response
    except Exception as e:
        response['error'] = f'subprocess_error: {e}'
        return response
    response['returncode'] = completed.returncode
    response['results'] = _parse_cc_output(command, completed.stdout or '')
    if completed.returncode != 0 and completed.stderr:
        response['stderr'] = completed.stderr.strip()[:512]
    if command == 'fuzz_range' and response['results']:
        csv_name = _write_savvycan_csv(response['results'])
        if csv_name:
            response['csv'] = csv_name
    response['ok'] = completed.returncode == 0
    return response


# ── MQTT ──────────────────────────────────────────────────────────────

class CanDiscoveryService:
    """Subscribes to drifter/can/command, runs cc, republishes results."""

    def __init__(self, client: mqtt.Client) -> None:
        self.client = client
        self.interface = _detect_can_interface()
        self._publish_status()

    def _publish_status(self) -> None:
        payload = {
            'ts': time.time(),
            'interface': self.interface or '',
            'cc_bin': CC_BIN,
            'cc_present': Path(CC_BIN).exists(),
        }
        try:
            self.client.publish(TOPIC_STATUS, json.dumps(payload), retain=True)
        except Exception as e:
            log.warning("status publish failed: %s", e)

    def on_message(self, client, userdata, msg) -> None:
        try:
            body = json.loads(msg.payload or b'{}')
        except (ValueError, json.JSONDecodeError):
            log.warning("invalid command JSON")
            return
        if not isinstance(body, dict):
            return
        command = body.get('command') or ''
        if not command:
            return
        # Re-probe the interface each tick — the operator may plug the
        # dongle in mid-session.
        if self.interface is None:
            self.interface = _detect_can_interface()
        response = run_command(command, body, self.interface)
        try:
            client.publish(TOPIC_DISCOVERY, json.dumps(response))
        except Exception as e:
            log.warning("response publish failed: %s", e)


def main() -> int:
    CAN_CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    client = make_mqtt_client('can-discovery')
    service = CanDiscoveryService(client)

    def _on_connect(c, userdata, flags, reason_code, properties=None):
        log.info("connected to MQTT, subscribing to %s", TOPIC_COMMAND)
        c.subscribe(TOPIC_COMMAND)
        service._publish_status()

    client.on_connect = _on_connect
    client.on_message = service.on_message

    running = {'v': True}

    def _stop(*_):
        log.info("shutting down")
        running['v'] = False
        try:
            client.disconnect()
        except Exception:
            pass

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    try:
        client.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
    except Exception as e:
        log.error("MQTT connect failed: %s", e)
        return 1

    client.loop_forever()
    return 0


if __name__ == '__main__':
    sys.exit(main())

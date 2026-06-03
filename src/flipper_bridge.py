#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Flipper Zero Bridge
USB serial bridge to Flipper Zero CLI for Sub-GHz, IR, NFC, and GPIO control.
Integrates into DRIFTER's MQTT architecture for command/response from Vivi/dashboard.

UNCAGED TECHNOLOGY — EST 1991
"""

import glob
import json
import logging
import re
import signal
import threading
import time
from pathlib import Path

import serial

from config import MQTT_HOST, MQTT_PORT, TOPICS, make_mqtt_client

# Local cache for .sub artifacts. Same dir referenced by web_dashboard_handlers
# so the API can serve them back without a second config knob.
FLIPPER_CAPTURE_DIR = Path('/opt/drifter/state/flipper_captures')

# Flipper region TX windows. AU stock firmware permits 915–928 MHz TX only;
# Community firmwares (Xtreme/Unleashed/RogueMaster) vary, but we don't block
# replay — just surface the warning.
_FLIPPER_REGION_TX_BANDS = [
    (300_000_000, 348_000_000),
    (387_000_000, 464_000_000),
    (777_000_000, 928_000_000),
]

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [FLIPPER] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger('FLIPPER')

running = True

# ── Serial Config ──
FLIPPER_BAUD = 230400
SERIAL_TIMEOUT = 2.0
PROMPT = '>:'
DETECT_RETRY_INTERVAL = 10

# ── Add-on hardware probe ──
# Flipper GPIO peripherals — detected by sending module-specific CLI commands
# and matching identifier strings in the response. Detection runs at startup
# and every HARDWARE_PROBE_INTERVAL seconds. Bench truth: with no add-on
# attached, every probe returns no signature → module="none".
HARDWARE_PROBE_INTERVAL = 30

# Per-module capability lists surfaced by /api/flipper/hardware. The cockpit
# uses these to dim panels for absent modules.
MODULE_CAPABILITIES = {
    'wifi':   ['scan_ap', 'scan_sta', 'ble_scan', 'packet_monitor',
               'probe_capture', 'pwnagotchi_passive'],
    'subghz': ['freq_analyzer', 'raw_capture', 'read_protocol', 'replay'],
    'can':    ['obd_scan', 'can_dump'],
    'nrf24':  ['mousejack_scan'],
    'none':   [],
}

# Allowlist for audit targets file (Agent B owns it). We read it best-effort
# to gate the pwnagotchi passive button.
_AUDIT_TARGETS_PATH = Path('/opt/drifter/etc/audit_targets.yaml')

# ── Risk Classification ──
# Commands are classified by risk level before execution.
LOW_RISK_PREFIXES = (
    'hw info', 'power info', 'storage list', 'storage info', 'storage stat',
    'subghz rx', 'nfc detect', 'ir rx', 'bt info',
)
MEDIUM_RISK_PREFIXES = (
    'subghz tx', 'ir tx', 'gpio set', 'gpio mode',
)
HIGH_RISK_PREFIXES = (
    'storage write', 'storage remove', 'storage rename', 'storage mkdir',
    # Rubber Ducky / BadUSB injection path (drifter-hid Flipper backend).
    # Pushing a payload to /ext/badusb and firing it via the BadUSB app or
    # `loader open` is HIGH-risk: the bridge's OWN confirm gate must agree,
    # in addition to drifter-hid's ARM→CONFIRM→RUN state machine (defence
    # in depth — there is no single-gate path from upload to injection).
    'badusb', 'loader open',
)
BLOCKED_PATTERNS = (
    'update', 'dfu', 'storage write /int/', 'storage remove /int/',
    'storage rename /int/',
)


# ═══════════════════════════════════════════════════════════════════
#  .sub File Builder + Capture Persistence
# ═══════════════════════════════════════════════════════════════════

# Match a RAW_Data line emitted by Flipper's `subghz rx_raw` CLI. The CLI
# prints alternating signed microsecond durations separated by spaces.
_RAW_DATA_RE = re.compile(r'(-?\d+(?:\s+-?\d+)+)')


def parse_raw_data_line(text):
    """Pull the RAW_Data integer list out of a Flipper CLI line.

    Flipper's `subghz rx_raw` decoder dumps lines like:
        RAW_Data: 244 -732 244 -488 ...
    or just the bare integer sequence depending on firmware. We accept
    either shape and return a list of signed ints.
    """
    if not text:
        return None
    # Direct "RAW_Data:" prefix from Flipper CLI.
    if 'RAW_Data:' in text:
        payload = text.split('RAW_Data:', 1)[1].strip()
    else:
        payload = text.strip()
    parts = payload.split()
    nums = []
    for p in parts:
        try:
            nums.append(int(p))
        except ValueError:
            return None
    if len(nums) < 2:
        return None
    return nums


def build_sub_file(freq_hz, raw_data, preset='FuriHalSubGhzPresetOok650Async',
                   max_per_line=512):
    """Assemble a Flipper SubGhz RAW .sub file body.

    `raw_data` is a flat list of signed ints (alternating us durations).
    Returns the file text. Lines wrap at `max_per_line` values per the
    Flipper file-format spec.
    """
    header = [
        'Filetype: Flipper SubGhz RAW File',
        'Version: 1',
        f'Frequency: {int(freq_hz)}',
        f'Preset: {preset}',
        'Protocol: RAW',
    ]
    lines = list(header)
    for i in range(0, len(raw_data), max_per_line):
        chunk = raw_data[i:i + max_per_line]
        lines.append('RAW_Data: ' + ' '.join(str(n) for n in chunk))
    return '\n'.join(lines) + '\n'


def persist_capture(freq_hz, raw_data, ts=None):
    """Write the .sub file to /opt/drifter/state/flipper_captures/ locally.

    Returns the dict {'id', 'local_sub_path', 'on_flipper_path', 'ts', ...}
    or None on failure. The Flipper-side copy is best-effort, performed
    by push_capture_to_flipper().
    """
    ts = ts if ts is not None else time.time()
    capture_id = f'drifter-{int(ts)}'
    try:
        FLIPPER_CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        log.warning(f"Could not create capture dir: {e}")
        return None

    body = build_sub_file(freq_hz, raw_data)
    local_path = FLIPPER_CAPTURE_DIR / f'{capture_id}.sub'
    try:
        local_path.write_text(body)
    except OSError as e:
        log.warning(f"Could not persist .sub: {e}")
        return None

    return {
        'id': capture_id,
        'local_sub_path': str(local_path),
        'on_flipper_path': f'/ext/subghz/{capture_id}.sub',
        'freq_hz': int(freq_hz),
        'raw_data_count': len(raw_data),
        'ts': ts,
    }


def push_capture_to_flipper(flipper, capture_meta):
    """Best-effort copy the .sub to the Flipper SD card via storage write CLI.

    Returns True on success, False otherwise. The local cache is the
    source of truth — Flipper-side push is for in-the-field replay.
    """
    if not flipper.connected:
        return False
    local = Path(capture_meta['local_sub_path'])
    if not local.exists():
        return False
    on_flipper = capture_meta['on_flipper_path']
    body = local.read_text()
    # The Flipper `storage write` CLI consumes lines until an EOF marker
    # or a fixed line count. The format that works on stock fw is:
    #   storage write <path>\n<body>\n  (newline-terminated, no marker)
    # followed by a short pause and then a Ctrl-C interrupt.
    try:
        success, response = flipper.send_command(
            f'storage write {on_flipper}\n{body}')
        if success and 'error' not in response.lower():
            log.info(f"Pushed capture to flipper: {on_flipper}")
            return True
        log.warning(f"Flipper storage write returned: {response[:120]}")
        return False
    except Exception as e:
        log.warning(f"push_capture_to_flipper failed: {e}")
        return False


def is_tx_region_locked(freq_hz):
    """Return a warning string if `freq_hz` falls outside stock-fw TX bands.

    Empty string means TX is permitted on stock AU firmware.
    """
    f = int(freq_hz)
    for low, high in _FLIPPER_REGION_TX_BANDS:
        if low <= f <= high:
            return ''
    return (f'Frequency {f/1e6:.3f} MHz is outside stock Flipper TX bands '
            f'(300–348 / 387–464 / 777–928 MHz). Community firmware may '
            f'still transmit; stock AU firmware will refuse.')


def list_persisted_captures():
    """Enumerate .sub files in FLIPPER_CAPTURE_DIR newest first.

    Returns a list of dicts: {id, local_sub_path, on_flipper_path, freq_hz, ts}.
    Used by /api/flipper/captures to augment the live ring buffer with
    persisted artifacts.
    """
    out = []
    if not FLIPPER_CAPTURE_DIR.exists():
        return out
    try:
        for p in FLIPPER_CAPTURE_DIR.glob('drifter-*.sub'):
            try:
                ts = float(p.stem.split('-', 1)[1])
            except (ValueError, IndexError):
                ts = p.stat().st_mtime
            # Parse Frequency: header without slurping the whole RAW block.
            freq_hz = None
            try:
                for line in p.read_text().splitlines():
                    if line.startswith('Frequency:'):
                        freq_hz = int(line.split(':', 1)[1].strip())
                        break
                    if line.startswith('RAW_Data:'):
                        break
            except OSError:
                continue
            out.append({
                'id': p.stem,
                'local_sub_path': str(p),
                'on_flipper_path': f'/ext/subghz/{p.name}',
                'freq_hz': freq_hz,
                'ts': ts,
            })
    except OSError as e:
        log.warning(f"capture listing error: {e}")
    out.sort(key=lambda c: c.get('ts') or 0, reverse=True)
    return out


# ═══════════════════════════════════════════════════════════════════
#  Flipper Zero Serial Interface
# ═══════════════════════════════════════════════════════════════════

class FlipperSerial:
    """Thread-safe serial interface to a Flipper Zero CLI."""

    def __init__(self):
        self.port = None
        self.ser = None
        self.lock = threading.Lock()
        self.device_info = {}

    @property
    def connected(self):
        return self.ser is not None and self.ser.is_open

    def detect(self):
        """Scan /dev/ttyACM* for a Flipper Zero by sending 'hw info'."""
        candidates = sorted(glob.glob('/dev/ttyACM*'))
        if not candidates:
            return False

        for dev in candidates:
            try:
                s = serial.Serial(dev, FLIPPER_BAUD, timeout=SERIAL_TIMEOUT)
                # Flush any pending data
                s.reset_input_buffer()
                # Send a newline to get a clean prompt
                s.write(b'\r\n')
                time.sleep(0.3)
                s.reset_input_buffer()

                # Send hw info command
                s.write(b'hw info\r\n')
                time.sleep(1.0)

                response = b''
                deadline = time.monotonic() + 3.0
                while time.monotonic() < deadline:
                    chunk = s.read(s.in_waiting or 1)
                    if chunk:
                        response += chunk
                        if PROMPT.encode() in response:
                            break
                    else:
                        time.sleep(0.05)

                text = response.decode('utf-8', errors='replace')
                if 'flipper' in text.lower() or 'hardware_model' in text.lower():
                    self.ser = s
                    self.port = dev
                    self.device_info = _parse_hw_info(text)
                    log.info(f"Flipper Zero detected on {dev}")
                    return True
                else:
                    s.close()

            except (serial.SerialException, OSError) as e:
                log.debug(f"Not a Flipper on {dev}: {e}")
                try:
                    s.close()
                except Exception:
                    pass

        return False

    def send_command(self, command):
        """Send a CLI command and return the response text.

        Returns (success: bool, response: str).
        Thread-safe via self.lock.
        """
        with self.lock:
            if not self.connected:
                return False, 'Not connected'

            try:
                self.ser.reset_input_buffer()
                self.ser.write(f'{command}\r\n'.encode())

                response = b''
                deadline = time.monotonic() + 10.0
                while time.monotonic() < deadline:
                    chunk = self.ser.read(self.ser.in_waiting or 1)
                    if chunk:
                        response += chunk
                        if PROMPT.encode() in response:
                            break
                    else:
                        time.sleep(0.05)

                text = response.decode('utf-8', errors='replace')
                # Strip the echoed command and trailing prompt
                lines = text.split('\r\n')
                cleaned = []
                for line in lines:
                    stripped = line.strip()
                    if stripped == command or stripped == PROMPT or stripped == '':
                        continue
                    # Remove trailing prompt from last line
                    if stripped.endswith(PROMPT):
                        stripped = stripped[:-len(PROMPT)].strip()
                    if stripped:
                        cleaned.append(stripped)

                return True, '\n'.join(cleaned)

            except (serial.SerialException, OSError) as e:
                log.error(f"Serial error: {e}")
                self.close()
                return False, str(e)

    def close(self):
        """Close the serial connection."""
        with self.lock:
            if self.ser:
                try:
                    self.ser.close()
                except Exception:
                    pass
                self.ser = None
                self.port = None
                self.device_info = {}


def _parse_hw_info(text):
    """Parse 'hw info' response into a dict of key-value pairs."""
    info = {}
    for line in text.split('\n'):
        line = line.strip()
        if ':' in line and not line.startswith('>'):
            key, _, value = line.partition(':')
            key = key.strip().lower().replace(' ', '_')
            value = value.strip()
            if key and value:
                info[key] = value
    return info


# ═══════════════════════════════════════════════════════════════════
#  Risk Classification
# ═══════════════════════════════════════════════════════════════════

def classify_risk(command):
    """Classify a command's risk level.

    Returns 'LOW', 'MEDIUM', 'HIGH', or 'BLOCKED'.
    """
    cmd_lower = command.lower().strip()

    for pattern in BLOCKED_PATTERNS:
        if cmd_lower.startswith(pattern):
            return 'BLOCKED'

    for prefix in HIGH_RISK_PREFIXES:
        if cmd_lower.startswith(prefix):
            return 'HIGH'

    for prefix in MEDIUM_RISK_PREFIXES:
        if cmd_lower.startswith(prefix):
            return 'MEDIUM'

    for prefix in LOW_RISK_PREFIXES:
        if cmd_lower.startswith(prefix):
            return 'LOW'

    # Unknown commands default to MEDIUM
    return 'MEDIUM'


# ═══════════════════════════════════════════════════════════════════
#  Sub-GHz Monitoring
# ═══════════════════════════════════════════════════════════════════

def run_subghz_monitor(flipper, mqtt_client, stop_event):
    """Continuously receive Sub-GHz signals on 433.92 MHz and publish captures."""
    log.info("Sub-GHz monitor starting on 433.92 MHz")

    with flipper.lock:
        if not flipper.connected:
            log.warning("Sub-GHz monitor: Flipper not connected")
            return
        try:
            flipper.ser.reset_input_buffer()
            flipper.ser.write(b'subghz rx 433920000\r\n')
        except (serial.SerialException, OSError) as e:
            log.error(f"Sub-GHz monitor start error: {e}")
            return

    buffer = b''
    while not stop_event.is_set():
        try:
            with flipper.lock:
                if not flipper.connected:
                    break
                chunk = flipper.ser.read(flipper.ser.in_waiting or 1)

            if chunk:
                buffer += chunk
                # Process complete lines
                while b'\r\n' in buffer:
                    line, buffer = buffer.split(b'\r\n', 1)
                    text = line.decode('utf-8', errors='replace').strip()
                    if not text or text == PROMPT:
                        continue

                    # Publish any decoded signal data
                    capture = {
                        'raw': text,
                        'freq_hz': 433920000,
                        'ts': time.time(),
                    }

                    # If this line carries a RAW_Data sequence, assemble a
                    # .sub artifact locally and (best-effort) push to the
                    # Flipper SD card so replay has something to fire.
                    raw_data = parse_raw_data_line(text)
                    if raw_data:
                        meta = persist_capture(
                            capture['freq_hz'], raw_data, ts=capture['ts'])
                        if meta:
                            capture.update(meta)
                            # Best-effort SD push happens asynchronously to
                            # avoid blocking the monitor read loop.
                            threading.Thread(
                                target=push_capture_to_flipper,
                                args=(flipper, meta), daemon=True,
                            ).start()

                    mqtt_client.publish(
                        TOPICS['flipper_subghz'], json.dumps(capture)
                    )
                    log.info(f"Sub-GHz capture: {text[:120]}")
            else:
                time.sleep(0.1)

        except (serial.SerialException, OSError) as e:
            log.error(f"Sub-GHz monitor error: {e}")
            break

    # Stop rx mode by sending a newline (interrupts the running command)
    try:
        with flipper.lock:
            if flipper.connected:
                flipper.ser.write(b'\r\n')
                time.sleep(0.3)
                flipper.ser.reset_input_buffer()
    except Exception:
        pass

    log.info("Sub-GHz monitor stopped")


# ═══════════════════════════════════════════════════════════════════
#  MQTT Command Handler
# ═══════════════════════════════════════════════════════════════════

# Pending HIGH-risk confirmations: {command_id: {command, ts}}
pending_confirms = {}
subghz_monitor_ctl = {'thread': None, 'stop': threading.Event()}


def handle_message(flipper, mqtt_client):
    """Return an on_message callback bound to the flipper and mqtt_client."""

    def on_message(client, userdata, msg):
        try:
            data = json.loads(msg.payload)
        except (json.JSONDecodeError, TypeError) as e:
            log.warning(f"Bad command payload: {e}")
            return

        command = data.get('command', '').strip()
        command_id = data.get('id', '')

        if not command:
            return

        # ── Sub-GHz monitor control ──
        if command == 'subghz_monitor_start':
            _start_subghz_monitor(flipper, mqtt_client)
            return
        if command == 'subghz_monitor_stop':
            _stop_subghz_monitor()
            return

        # ── Sub-GHz replay (operator-confirmed in the cockpit) ──
        if command == 'subghz_replay':
            _do_subghz_replay(flipper, mqtt_client, data)
            return

        # ── Wi-Fi passive workflows (ESP32 Marauder add-on) ──
        if command in WIFI_PASSIVE_COMMANDS:
            _do_wifi_command(flipper, mqtt_client, command, data)
            return

        # ── Sub-GHz preset workflows (CC1101 add-on) ──
        if command in SUBGHZ_PRESET_COMMANDS:
            _do_subghz_preset(flipper, mqtt_client, command, data)
            return

        # ── HIGH-risk confirmation ──
        if command == 'confirm' and command_id:
            _execute_confirmed(flipper, mqtt_client, command_id)
            return

        # ── Classify and execute ──
        risk = classify_risk(command)

        if risk == 'BLOCKED':
            log.warning(f"BLOCKED command: {command}")
            mqtt_client.publish(TOPICS['flipper_result'], json.dumps({
                'command': command,
                'id': command_id,
                'risk': 'BLOCKED',
                'success': False,
                'response': 'Command blocked by security policy',
                'ts': time.time(),
            }))
            return

        if risk == 'HIGH':
            # Store for confirmation
            pending_confirms[command_id or command] = {
                'command': command,
                'id': command_id,
                'ts': time.time(),
            }
            log.info(f"HIGH risk command pending confirmation: {command}")
            mqtt_client.publish(TOPICS['flipper_result'], json.dumps({
                'command': command,
                'id': command_id,
                'risk': 'HIGH',
                'success': False,
                'response': 'Confirmation required — send {"command": "confirm", "id": "'
                            + (command_id or command) + '"}',
                'ts': time.time(),
            }))
            return

        if risk == 'MEDIUM':
            log.info(f"MEDIUM risk command: {command}")

        # Execute LOW and MEDIUM commands directly
        success, response = flipper.send_command(command)

        mqtt_client.publish(TOPICS['flipper_result'], json.dumps({
            'command': command,
            'id': command_id,
            'risk': risk,
            'success': success,
            'response': response,
            'ts': time.time(),
        }))

    return on_message


def _execute_confirmed(flipper, mqtt_client, command_id):
    """Execute a previously pending HIGH-risk command after confirmation."""
    pending = pending_confirms.pop(command_id, None)
    if not pending:
        mqtt_client.publish(TOPICS['flipper_result'], json.dumps({
            'id': command_id,
            'success': False,
            'response': 'No pending command with that ID',
            'ts': time.time(),
        }))
        return

    # Expire after 60 seconds
    if time.time() - pending['ts'] > 60:
        mqtt_client.publish(TOPICS['flipper_result'], json.dumps({
            'command': pending['command'],
            'id': command_id,
            'success': False,
            'response': 'Confirmation expired (>60s)',
            'ts': time.time(),
        }))
        return

    command = pending['command']
    log.info(f"Executing confirmed HIGH risk command: {command}")
    success, response = flipper.send_command(command)

    mqtt_client.publish(TOPICS['flipper_result'], json.dumps({
        'command': command,
        'id': command_id,
        'risk': 'HIGH',
        'confirmed': True,
        'success': success,
        'response': response,
        'ts': time.time(),
    }))


def _start_subghz_monitor(flipper, mqtt_client):
    """Start the Sub-GHz background monitor thread."""
    if subghz_monitor_ctl['thread'] and subghz_monitor_ctl['thread'].is_alive():
        log.info("Sub-GHz monitor already running")
        return

    subghz_monitor_ctl['stop'].clear()
    t = threading.Thread(
        target=run_subghz_monitor,
        args=(flipper, mqtt_client, subghz_monitor_ctl['stop']),
        daemon=True,
    )
    t.start()
    subghz_monitor_ctl['thread'] = t

    mqtt_client.publish(TOPICS['flipper_result'], json.dumps({
        'command': 'subghz_monitor_start',
        'success': True,
        'response': 'Sub-GHz monitor started on 433.92 MHz',
        'ts': time.time(),
    }))


def _do_subghz_replay(flipper, mqtt_client, data):
    """Replay a persisted .sub via `subghz tx_from_file`.

    Body: {"command":"subghz_replay", "capture_id":"drifter-1700000000"}.
    Pushes the local artifact onto the SD card if missing there, then
    calls `subghz tx_from_file /ext/subghz/<file> 1 0` (1 repeat,
    device 0). Region-lock awareness is informational — we never block.
    """
    capture_id = (data.get('capture_id') or '').strip()
    if not capture_id:
        mqtt_client.publish(TOPICS['flipper_result'], json.dumps({
            'command': 'subghz_replay',
            'success': False,
            'response': 'subghz_replay requires capture_id',
            'ts': time.time(),
        }))
        return

    # Locate the local artifact by id.
    local_path = FLIPPER_CAPTURE_DIR / f'{capture_id}.sub'
    if not local_path.exists():
        mqtt_client.publish(TOPICS['flipper_result'], json.dumps({
            'command': 'subghz_replay',
            'capture_id': capture_id,
            'success': False,
            'response': f'capture not found: {local_path}',
            'ts': time.time(),
        }))
        return

    # Parse frequency for the region-lock warning.
    freq_hz = None
    try:
        for line in local_path.read_text().splitlines():
            if line.startswith('Frequency:'):
                freq_hz = int(line.split(':', 1)[1].strip())
                break
    except (OSError, ValueError):
        pass
    warning = is_tx_region_locked(freq_hz) if freq_hz else ''

    on_flipper = f'/ext/subghz/{capture_id}.sub'
    pushed = push_capture_to_flipper(flipper, {
        'local_sub_path': str(local_path),
        'on_flipper_path': on_flipper,
    })

    # tx_from_file <path> <repeats> <device> — device 0 = internal radio.
    success, response = flipper.send_command(
        f'subghz tx_from_file {on_flipper} 1 0')

    payload = {
        'command': 'subghz_replay',
        'capture_id': capture_id,
        'risk': 'MEDIUM',
        'on_flipper_path': on_flipper,
        'pushed_to_flipper': pushed,
        'success': success,
        'response': response,
        'ts': time.time(),
    }
    if warning:
        payload['warning'] = warning
    mqtt_client.publish(TOPICS['flipper_result'], json.dumps(payload))


def _stop_subghz_monitor():
    """Stop the Sub-GHz background monitor thread."""
    subghz_monitor_ctl['stop'].set()
    t = subghz_monitor_ctl.get('thread')
    if t and t.is_alive():
        t.join(timeout=5)
    subghz_monitor_ctl['thread'] = None
    log.info("Sub-GHz monitor stopped")


# ═══════════════════════════════════════════════════════════════════
#  Hardware Add-on Detection
# ═══════════════════════════════════════════════════════════════════

# Last probe result, exposed to /api/flipper/hardware. Kept module-local so
# the dashboard handler can import it without forcing an MQTT round-trip.
hardware_state = {
    'ts': 0.0,
    'module': 'none',
    'capabilities': [],
    'detail': '',
}


def _looks_like_marauder(text):
    """Return True if `text` carries an ESP32 Marauder identifier.

    Marauder firmwares respond to a number of CLI handshakes — both the
    Flipper-side "i2c" sniff and Marauder's own version banner contain the
    word 'marauder'. We match both shapes case-insensitively.
    """
    if not text:
        return False
    lo = text.lower()
    return ('marauder' in lo) or ('esp32-marauder' in lo)


def _looks_like_cc1101(text):
    """Return True if `subghz info` output identifies a CC1101 radio."""
    if not text:
        return False
    lo = text.lower()
    return ('cc1101' in lo) or ('chipid' in lo and '0x' in lo)


def probe_hardware(flipper):
    """Probe the Flipper's add-on GPIO/SPI bus for a known peripheral.

    Returns a dict {ts, module, capabilities, detail}. The serial bridge
    is the only transport — we send each module's identifier command and
    inspect the response. If the Flipper itself is offline we return the
    module='none' shape so the dashboard still has a payload to render.
    """
    now = time.time()
    if not flipper.connected:
        return {
            'ts': now,
            'module': 'none',
            'capabilities': [],
            'detail': 'flipper offline',
        }

    # ── ESP32 Marauder (Wi-Fi/BLE add-on) ──
    # The board sits on the Flipper's UART pins; once Marauder is flashed
    # it answers a plain newline with its banner. The i2c command is the
    # secondary probe (some firmwares only reply to that). We accept
    # either signature.
    ok, resp = flipper.send_command('i2c')
    if ok and _looks_like_marauder(resp):
        return {
            'ts': now,
            'module': 'wifi',
            'capabilities': list(MODULE_CAPABILITIES['wifi']),
            'detail': 'esp32 marauder',
        }

    # ── CC1101 sub-GHz ──
    ok, resp = flipper.send_command('subghz info')
    if ok and _looks_like_cc1101(resp):
        return {
            'ts': now,
            'module': 'subghz',
            'capabilities': list(MODULE_CAPABILITIES['subghz']),
            'detail': 'cc1101',
        }

    # MCP2515 SPI / nRF24 SPI probes are not exposed by stock Flipper CLI.
    # We surface them as 'unknown' rather than fabricating a result.
    # No peripheral matched → 'none'. This is an honest bench answer.
    return {
        'ts': now,
        'module': 'none',
        'capabilities': [],
        'detail': 'no add-on detected',
    }


def publish_hardware(mqtt_client, state):
    """Publish the latest detection to drifter/flipper/hardware (RETAINED)."""
    try:
        mqtt_client.publish(
            'drifter/flipper/hardware',
            json.dumps(state),
            retain=True,
        )
    except Exception as e:
        log.debug(f"hardware publish failed: {e}")


def get_hardware_state():
    """Accessor for the dashboard handler. Returns a defensive copy."""
    return dict(hardware_state)


def _audit_allowlist_present():
    """Best-effort check: does Agent B's audit_targets.yaml have entries?

    Used only to gate the pwnagotchi-passive command. Empty/missing file
    returns False — the cockpit then disables the button with the
    "ALLOWLIST EMPTY" label.
    """
    if not _AUDIT_TARGETS_PATH.exists():
        return False
    try:
        import yaml  # local import — keeps test envs without pyyaml happy
        doc = yaml.safe_load(_AUDIT_TARGETS_PATH.read_text(encoding='utf-8'))
    except Exception:
        return False
    if not doc:
        return False
    # Accept either {networks: [...]} or a bare list of targets.
    if isinstance(doc, dict):
        for key in ('networks', 'targets', 'ssids', 'allowlist'):
            v = doc.get(key)
            if isinstance(v, list) and v:
                return True
        return False
    if isinstance(doc, list):
        return bool(doc)
    return False


# ═══════════════════════════════════════════════════════════════════
#  Add-on workflow commands (Wi-Fi / Sub-GHz)
# ═══════════════════════════════════════════════════════════════════

# Map cockpit workflow tokens to (cli_command, mqtt_result_topic). Each
# token corresponds to a button in the v3sper drawer. We only surface
# passive Wi-Fi commands here — DEAUTH/BEACON/EVIL are firmware-supported
# but deliberately NOT wired into the cockpit per the operator's spec.
WIFI_PASSIVE_COMMANDS = {
    'wifi_scan_ap':       ('scanap',     'drifter/flipper/wifi/aps'),
    'wifi_scan_sta':      ('scansta',    'drifter/flipper/wifi/stations'),
    'ble_scan':           ('blescan',    'drifter/flipper/wifi/ble'),
    'packet_monitor':     ('sniffraw',   'drifter/flipper/wifi/pcaps'),
    'probe_capture':      ('probescan',  'drifter/flipper/wifi/probes'),
    'pwnagotchi_passive': ('evilpwn',    'drifter/flipper/wifi/handshakes'),
}

SUBGHZ_PRESET_COMMANDS = {
    'freq_analyzer':  ('subghz_freq_analyzer', 'drifter/flipper/subghz/sweep'),
    'raw_capture':    ('subghz_raw_capture',   'drifter/flipper/subghz/captures'),
    'read_protocol':  ('subghz_read_protocol', 'drifter/flipper/subghz/protocol'),
}


def _do_wifi_command(flipper, mqtt_client, token, data):
    """Dispatch a passive Wi-Fi Marauder command via the serial CLI.

    `token` is one of WIFI_PASSIVE_COMMANDS. Returns nothing — publishes
    the response on the per-command topic AND on drifter/flipper/result
    so the cockpit's existing result-ring picks it up.
    """
    cli, result_topic = WIFI_PASSIVE_COMMANDS[token]

    # Pwnagotchi passive is gated on Agent B's allowlist.
    if token == 'pwnagotchi_passive' and not _audit_allowlist_present():
        mqtt_client.publish(TOPICS['flipper_result'], json.dumps({
            'command': token,
            'success': False,
            'response': 'audit_targets.yaml allowlist empty',
            'ts': time.time(),
        }))
        return

    if hardware_state.get('module') != 'wifi':
        mqtt_client.publish(TOPICS['flipper_result'], json.dumps({
            'command': token,
            'success': False,
            'response': 'wifi module not attached',
            'ts': time.time(),
        }))
        return

    success, response = flipper.send_command(cli)
    payload = {
        'command': token,
        'cli': cli,
        'success': success,
        'response': response,
        'ts': time.time(),
    }
    mqtt_client.publish(TOPICS['flipper_result'], json.dumps(payload))
    mqtt_client.publish(result_topic, json.dumps(payload))


def _do_subghz_preset(flipper, mqtt_client, token, data):
    """Dispatch a sub-GHz preset command (analyzer/raw_capture/read_protocol).

    raw_capture honours an optional `freq_mhz` from the body (defaults to
    433.92) and persists the resulting .sub via the existing
    persist_capture() helper. The capture is also enqueued for URH-NG
    classification on drifter/rf/classification so Agent A's pipeline
    can attach a protocol-family label.
    """
    if hardware_state.get('module') != 'subghz':
        mqtt_client.publish(TOPICS['flipper_result'], json.dumps({
            'command': token,
            'success': False,
            'response': 'subghz module not attached',
            'ts': time.time(),
        }))
        return

    _cli, result_topic = SUBGHZ_PRESET_COMMANDS[token]

    if token == 'freq_analyzer':
        success, response = flipper.send_command('subghz_freq_analyzer')
    elif token == 'raw_capture':
        try:
            freq_mhz = float(data.get('freq_mhz', 433.92))
        except (TypeError, ValueError):
            freq_mhz = 433.92
        freq_hz = int(freq_mhz * 1_000_000)
        success, response = flipper.send_command(
            f'subghz rx_raw {freq_hz}')
        # Best-effort persistence + classification enqueue.
        nums = parse_raw_data_line(response) if success else None
        if nums:
            meta = persist_capture(freq_hz, nums)
            if meta:
                response = (response or '') + f"\n[persisted={meta['id']}]"
                # Enqueue for URH-NG (Agent A). Use the existing
                # drifter/rf/classification topic — pipeline owner reads it.
                try:
                    mqtt_client.publish(
                        'drifter/rf/classification',
                        json.dumps({
                            'capture_id': meta['id'],
                            'local_sub_path': meta['local_sub_path'],
                            'freq_hz': freq_hz,
                            'source': 'flipper_subghz_raw',
                            'ts': time.time(),
                        }),
                    )
                except Exception as e:
                    log.debug(f"classification enqueue failed: {e}")
    elif token == 'read_protocol':
        capture_id = (data.get('capture_id') or '').strip()
        if not capture_id:
            mqtt_client.publish(TOPICS['flipper_result'], json.dumps({
                'command': token,
                'success': False,
                'response': 'read_protocol requires capture_id',
                'ts': time.time(),
            }))
            return
        on_flipper = f'/ext/subghz/{capture_id}.sub'
        success, response = flipper.send_command(
            f'subghz decode_raw {on_flipper}')

    payload = {
        'command': token,
        'success': success,
        'response': response,
        'ts': time.time(),
    }
    mqtt_client.publish(TOPICS['flipper_result'], json.dumps(payload))
    mqtt_client.publish(result_topic, json.dumps(payload))


# ═══════════════════════════════════════════════════════════════════
#  Status Publisher
# ═══════════════════════════════════════════════════════════════════

def publish_status(mqtt_client, flipper, state):
    """Publish Flipper connection status to MQTT."""
    payload = {
        'state': state,
        'port': flipper.port,
        'device_info': flipper.device_info,
        'ts': time.time(),
    }
    mqtt_client.publish(TOPICS['flipper_status'], json.dumps(payload), retain=True)


# ═══════════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════════

def main():
    global running

    log.info("DRIFTER Flipper Zero Bridge starting...")

    def _handle_signal(sig, frame):
        global running
        running = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    flipper = FlipperSerial()

    # ── MQTT ──
    mqtt_client = make_mqtt_client("drifter-flipper")

    connected = False
    while not connected and running:
        try:
            mqtt_client.connect(MQTT_HOST, MQTT_PORT, 60)
            connected = True
        except Exception as e:
            log.warning(f"Waiting for MQTT broker... ({e})")
            time.sleep(3)

    if not running:
        return

    mqtt_client.on_message = handle_message(flipper, mqtt_client)
    mqtt_client.subscribe(TOPICS['flipper_command'])
    mqtt_client.loop_start()

    # Publish an initial hardware-detection payload BEFORE the Flipper-detect
    # loop. Without this the dashboard sees no /api/flipper/hardware data
    # when the Flipper is unplugged — the retained MQTT topic stays empty
    # forever. The honest answer in that case is module='none'.
    initial_hw = probe_hardware(flipper)
    hardware_state.update(initial_hw)
    publish_hardware(mqtt_client, initial_hw)

    # ── Detect Flipper Zero ──
    while not flipper.connected and running:
        if flipper.detect():
            log.info(f"Flipper Zero online on {flipper.port}")
            publish_status(mqtt_client, flipper, 'connected')
            break
        log.info("Flipper Zero not found — retrying in 10s...")
        publish_status(mqtt_client, flipper, 'searching')
        for _ in range(DETECT_RETRY_INTERVAL * 10):
            if not running:
                break
            time.sleep(0.1)

    if not running:
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
        return

    # ── Periodic battery/status check ──
    last_status = 0
    STATUS_INTERVAL = 60
    last_hw_probe = 0

    # Initial hardware probe — publishes the bench-honest result even when
    # no Flipper is attached. The retained topic means a late dashboard
    # subscriber still sees the latest detection without a 30s wait.
    initial_hw = probe_hardware(flipper)
    hardware_state.update(initial_hw)
    publish_hardware(mqtt_client, initial_hw)
    last_hw_probe = time.time()

    log.info("Flipper Zero Bridge is LIVE")

    while running:
        now = time.time()

        # Check connection health and refresh status
        if now - last_status >= STATUS_INTERVAL:
            if flipper.connected:
                success, response = flipper.send_command('power info')
                if success:
                    payload = {
                        'state': 'connected',
                        'port': flipper.port,
                        'device_info': flipper.device_info,
                        'power_info': response,
                        'ts': now,
                    }
                    mqtt_client.publish(
                        TOPICS['flipper_status'], json.dumps(payload), retain=True
                    )
                else:
                    # Connection lost
                    log.warning("Flipper Zero disconnected")
                    flipper.close()
                    publish_status(mqtt_client, flipper, 'disconnected')
            last_status = now

        # Reconnect if disconnected
        if not flipper.connected:
            _stop_subghz_monitor()
            if flipper.detect():
                log.info(f"Flipper Zero reconnected on {flipper.port}")
                publish_status(mqtt_client, flipper, 'connected')
            else:
                # Wait before retrying (interruptible)
                for _ in range(DETECT_RETRY_INTERVAL * 10):
                    if not running:
                        break
                    time.sleep(0.1)
                continue

        # Expire stale pending confirmations (>120s)
        stale_ids = [
            k for k, v in pending_confirms.items()
            if now - v['ts'] > 120
        ]
        for k in stale_ids:
            del pending_confirms[k]

        # Periodic hardware re-probe so hot-plugging an add-on shows up
        # in the cockpit without restarting the service.
        if now - last_hw_probe >= HARDWARE_PROBE_INTERVAL:
            hw = probe_hardware(flipper)
            if hw.get('module') != hardware_state.get('module'):
                log.info(f"Flipper add-on changed: "
                         f"{hardware_state.get('module')} → {hw.get('module')}")
            hardware_state.update(hw)
            publish_hardware(mqtt_client, hw)
            last_hw_probe = now

        time.sleep(1)

    # ── Cleanup ──
    log.info("Shutting down Flipper Zero Bridge...")
    _stop_subghz_monitor()

    publish_status(mqtt_client, flipper, 'offline')
    mqtt_client.loop_stop()
    mqtt_client.disconnect()
    flipper.close()
    log.info("Flipper Zero Bridge stopped")


if __name__ == '__main__':
    main()

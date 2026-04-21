#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Flipper Zero Bridge
USB serial bridge to Flipper Zero CLI for Sub-GHz, IR, NFC, and GPIO control.
Integrates into DRIFTER's MQTT architecture for command/response from Vivi/dashboard.

UNCAGED TECHNOLOGY — EST 1991
"""

import signal
import sys
import time
import json
import logging
import threading
import glob

import serial
import paho.mqtt.client as mqtt

from config import MQTT_HOST, MQTT_PORT, TOPICS, LOG_DIR, make_mqtt_client

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
)
BLOCKED_PATTERNS = (
    'update', 'dfu', 'storage write /int/', 'storage remove /int/',
    'storage rename /int/',
)


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
                self.ser.write(f'{command}\r\n'.encode('utf-8'))

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


def _stop_subghz_monitor():
    """Stop the Sub-GHz background monitor thread."""
    subghz_monitor_ctl['stop'].set()
    t = subghz_monitor_ctl.get('thread')
    if t and t.is_alive():
        t.join(timeout=5)
    subghz_monitor_ctl['thread'] = None
    log.info("Sub-GHz monitor stopped")


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

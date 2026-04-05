#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Tool Execution Engine
V3SP3R-style risk-based authorization for LLM tool calls.
Imported by llm_mechanic.py — not a standalone service.
UNCAGED TECHNOLOGY — EST 1991
"""

import re
import json
import logging
import subprocess
import threading
import time
from typing import Optional, Tuple

from config import (
    TOOL_EXEC_TIMEOUT, TOOL_EXEC_LONG_TIMEOUT, TOOL_CONFIRM_TIMEOUT,
    TOPICS,
)

log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
#  Risk Classification
# ═══════════════════════════════════════════════════════════════════

RISK_LEVELS = {
    'LOW': 'auto',
    'MEDIUM': 'confirm',
    'HIGH': 'confirm',
    'BLOCKED': 'refuse',
}

LOW_COMMANDS = [
    'rtl_433', 'rtl_power', 'rtl_fm', 'rtl_test',
    'nmap -sn', 'nmcli dev wifi', 'nmcli general',
    'ip addr', 'ip route', 'ip link',
    'hcitool scan', 'hcitool lescan', 'hcitool dev',
    'bluetoothctl show', 'bluetoothctl devices',
    'cat /proc/', 'cat /sys/', 'uname', 'df -h', 'free -h',
    'iwconfig', 'ifconfig', 'iwlist',
    'dump1090', 'arp -a',
    'systemctl status', 'journalctl -u',
    'mosquitto_pub', 'mosquitto_sub',
    'ls ', 'pwd', 'whoami', 'date', 'uptime',
]

MEDIUM_COMMANDS = [
    'nmap -sV', 'nmap -A', 'nmap -sS', 'nmap -O',
    'tcpdump', 'tshark',
    'airodump-ng', 'airmon-ng',
    'kismet', 'netdiscover', 'arp-scan',
    'bettercap',
    'multimon-ng',
]

HIGH_COMMANDS = [
    'aircrack-ng', 'aireplay-ng', 'wifite',
    'hashcat', 'john',
    'mdk4', 'reaver', 'bully',
    'hydra', 'nikto', 'sqlmap',
    'metasploit', 'msfconsole',
    'macchanger',
    'responder',
]

BLOCKED_PATTERNS = [
    'rm -rf', 'rm -r /', 'mkfs', 'dd if=', 'dd of=/dev',
    'reboot', 'shutdown', 'halt', 'poweroff', 'init 0', 'init 6',
    'curl', 'wget', 'nc -e', 'nc -c', 'ncat -e',
    'python -c', 'python3 -c', 'perl -e', 'ruby -e',
    'eval ', 'exec ', '> /dev/',
    'chmod 777', 'chown root',
    'passwd', 'useradd', 'userdel',
    'iptables -F', 'iptables --flush',
]

# Shell metacharacters that should not appear in user-supplied arguments
_SHELL_META = re.compile(r'[;&|`$(){}]')

# ═══════════════════════════════════════════════════════════════════
#  RF / Network Command Mappings
# ═══════════════════════════════════════════════════════════════════

RF_COMMANDS = {
    'spectrum': 'rtl_power -f 24M:1766M:1M -g 50 -i 10 -1',
    'decode_433': 'timeout {duration_sec} rtl_433 -F json',
    'listen_freq': 'timeout {duration_sec} rtl_fm -f {frequency_mhz}M -M fm -s 12500 -g 50 -',
    'adsb': 'timeout {duration_sec} dump1090 --net --quiet',
    'emergency': 'timeout {duration_sec} rtl_fm -f 156.8M -M fm -s 12500 -g 50 -',
}

NETWORK_COMMANDS = {
    'discover': 'nmap -sn 192.168.1.0/24',
    'port_scan': 'nmap -sV {target}',
    'wifi_list': 'nmcli dev wifi list',
    'bluetooth': 'hcitool scan; timeout 8 hcitool lescan 2>/dev/null',
    'arp': 'arp-scan --localnet 2>/dev/null || arp -a',
}

# ═══════════════════════════════════════════════════════════════════
#  Telemetry Cache (populated by MQTT callbacks)
# ═══════════════════════════════════════════════════════════════════

_telemetry_cache = {}
_telemetry_lock = threading.Lock()


def update_telemetry_cache(topic: str, payload: str):
    """Called from MQTT on_message to keep telemetry current."""
    with _telemetry_lock:
        try:
            _telemetry_cache[topic] = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            _telemetry_cache[topic] = payload


# ═══════════════════════════════════════════════════════════════════
#  Risk Classification
# ═══════════════════════════════════════════════════════════════════

def classify_risk(command: str) -> str:
    """Classify a shell command's risk level by prefix matching."""
    cmd = command.strip()

    # BLOCKED first — any substring match
    for pattern in BLOCKED_PATTERNS:
        if pattern in cmd:
            return 'BLOCKED'

    # HIGH — prefix match
    for prefix in HIGH_COMMANDS:
        if cmd.startswith(prefix):
            return 'HIGH'

    # MEDIUM — prefix match
    for prefix in MEDIUM_COMMANDS:
        if cmd.startswith(prefix):
            return 'MEDIUM'

    # LOW — prefix match
    for prefix in LOW_COMMANDS:
        if cmd.startswith(prefix):
            return 'LOW'

    # Unknown commands default to MEDIUM
    return 'MEDIUM'


# ═══════════════════════════════════════════════════════════════════
#  Shell Execution
# ═══════════════════════════════════════════════════════════════════

def _execute_shell(command: str, timeout: int = TOOL_EXEC_TIMEOUT) -> Tuple[str, int]:
    """Run a shell command via subprocess. Returns (output, return_code)."""
    log.info("EXEC [timeout=%ds]: %s", timeout, command)
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=timeout
        )
        output = (result.stdout + result.stderr).strip()
        if len(output) > 2000:
            output = output[:2000] + '\n... [truncated]'
        return output, result.returncode
    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout}s", -1
    except Exception as e:
        return f"Execution error: {e}", -1


# ═══════════════════════════════════════════════════════════════════
#  Voice Confirmation (MEDIUM / HIGH risk)
# ═══════════════════════════════════════════════════════════════════

def _request_confirmation(command: str, risk: str, mqtt_client) -> bool:
    """
    Publish confirmation request via MQTT for TTS, then wait for
    voice response on the transcript topic.
    Thread-safe: uses an Event for cross-thread signalling.
    """
    if mqtt_client is None:
        log.warning("No MQTT client — auto-denying confirmation for: %s", command)
        return False

    confirmed = threading.Event()
    denied = threading.Event()
    _approve = {'yes', 'do it', 'confirm', 'go ahead', 'approved', 'affirmative'}
    _deny = {'no', 'cancel', 'stop', 'deny', 'abort', 'negative'}

    def _on_response(_client, _userdata, msg):
        try:
            data = json.loads(msg.payload.decode())
            text = data.get('text', '').lower().strip()
        except (json.JSONDecodeError, AttributeError):
            text = msg.payload.decode().lower().strip()

        if any(word in text for word in _approve):
            confirmed.set()
        elif any(word in text for word in _deny):
            denied.set()

    # Subscribe to voice transcript for the response
    transcript_topic = TOPICS.get('voice_transcript', 'drifter/voice/transcript')
    mqtt_client.message_callback_add(transcript_topic, _on_response)
    mqtt_client.subscribe(transcript_topic)

    # Build and publish the confirmation prompt
    if risk == 'HIGH':
        prompt = f"HIGH RISK command: {command}. This could cause damage. Say yes to confirm or no to cancel."
    else:
        prompt = f"Confirm command: {command}. Say yes to proceed or no to cancel."

    voice_topic = TOPICS.get('voice_command', 'drifter/voice/command')
    mqtt_client.publish(voice_topic, json.dumps({
        'type': 'confirm_tool',
        'command': command,
        'risk': risk,
        'prompt': prompt,
    }))

    log.info("CONFIRM [%s]: awaiting voice response for: %s", risk, command)

    # Wait for response or timeout
    start = time.monotonic()
    while time.monotonic() - start < TOOL_CONFIRM_TIMEOUT:
        if confirmed.is_set():
            mqtt_client.message_callback_remove(transcript_topic)
            log.info("CONFIRMED by voice: %s", command)
            return True
        if denied.is_set():
            mqtt_client.message_callback_remove(transcript_topic)
            log.info("DENIED by voice: %s", command)
            return False
        time.sleep(0.1)

    mqtt_client.message_callback_remove(transcript_topic)
    log.warning("TIMEOUT waiting for confirmation: %s", command)
    return False


# ═══════════════════════════════════════════════════════════════════
#  Tool Handlers
# ═══════════════════════════════════════════════════════════════════

def _handle_run_command(arguments: dict, mqtt_client) -> dict:
    """Execute an arbitrary shell command with risk-based gating.

    User-supplied commands are sanitised for shell metacharacters to prevent
    injection.  Internal pre-defined commands (rf_scan, network_scan) bypass
    this check because they are trusted templates.
    """
    command = arguments.get('command', '').strip()
    if not command:
        return {'success': False, 'output': 'No command provided', 'risk_level': 'BLOCKED', 'command': ''}

    # Sanitize: reject shell metacharacter injection in user-provided commands.
    # Internal handlers (_handle_rf_scan, _handle_network_scan) call
    # _execute_shell directly with trusted templates — they skip this check.
    if _SHELL_META.search(command):
        return {
            'success': False,
            'output': 'Command contains disallowed shell metacharacters',
            'risk_level': 'BLOCKED',
            'command': command,
        }

    risk = classify_risk(command)
    log.info("TOOL run_command [%s]: %s", risk, command)

    if risk == 'BLOCKED':
        return {'success': False, 'output': f'Command blocked by security policy: {command}', 'risk_level': risk, 'command': command}

    if risk in ('MEDIUM', 'HIGH'):
        if not _request_confirmation(command, risk, mqtt_client):
            return {'success': False, 'output': f'Command not confirmed (risk={risk}): {command}', 'risk_level': risk, 'command': command}

    timeout = TOOL_EXEC_LONG_TIMEOUT if risk == 'HIGH' else TOOL_EXEC_TIMEOUT
    output, rc = _execute_shell(command, timeout=timeout)
    return {'success': rc == 0, 'output': output, 'risk_level': risk, 'command': command}


def _handle_rf_scan(arguments: dict, mqtt_client) -> dict:
    """Map RF scan mode to a pre-defined shell command and execute."""
    mode = arguments.get('mode', 'decode_433')
    duration = int(arguments.get('duration_sec', 30))
    frequency = arguments.get('frequency_mhz', '433.92')

    template = RF_COMMANDS.get(mode)
    if not template:
        return {'success': False, 'output': f'Unknown RF scan mode: {mode}', 'risk_level': 'LOW', 'command': ''}

    command = template.format(duration_sec=duration, frequency_mhz=frequency)
    risk = classify_risk(command)
    log.info("TOOL rf_scan [%s] mode=%s: %s", risk, mode, command)

    timeout = min(duration + 10, TOOL_EXEC_LONG_TIMEOUT)
    output, rc = _execute_shell(command, timeout=timeout)
    return {'success': rc == 0, 'output': output, 'risk_level': risk, 'command': command}


def _handle_network_scan(arguments: dict, mqtt_client) -> dict:
    """Map network scan mode to a pre-defined shell command and execute.

    We sanitise only the *user-supplied* target parameter (not the trusted
    command template which intentionally uses shell metacharacters like ; and ||).
    """
    mode = arguments.get('mode', 'discover')
    target = arguments.get('target', '192.168.1.0/24')

    # Sanitize target parameter — only allow IP-like strings
    if _SHELL_META.search(target):
        return {'success': False, 'output': 'Invalid target (shell metacharacters)', 'risk_level': 'BLOCKED', 'command': ''}

    template = NETWORK_COMMANDS.get(mode)
    if not template:
        return {'success': False, 'output': f'Unknown network scan mode: {mode}', 'risk_level': 'LOW', 'command': ''}

    # Build command from trusted template + sanitised target.
    # classify_risk runs on the expanded command to apply confirmation gates.
    command = template.format(target=target)
    risk = classify_risk(command)
    log.info("TOOL network_scan [%s] mode=%s: %s", risk, mode, command)

    if risk in ('MEDIUM', 'HIGH'):
        if not _request_confirmation(command, risk, mqtt_client):
            return {'success': False, 'output': f'Scan not confirmed (risk={risk})', 'risk_level': risk, 'command': command}

    output, rc = _execute_shell(command, timeout=TOOL_EXEC_TIMEOUT)
    return {'success': rc == 0, 'output': output, 'risk_level': risk, 'command': command}


def _handle_query_knowledge(arguments: dict) -> dict:
    """Search knowledge bases — no shell execution, always LOW risk."""
    query = arguments.get('query', '')
    if not query:
        return {'success': False, 'output': 'No query provided', 'risk_level': 'LOW', 'command': ''}

    try:
        from mechanic import search as mechanic_search
        results = mechanic_search(query)
        output = json.dumps(results, indent=2, default=str) if results else 'No results found'
        if len(output) > 2000:
            output = output[:2000] + '\n... [truncated]'
        return {'success': True, 'output': output, 'risk_level': 'LOW', 'command': f'kb_search({query!r})'}
    except Exception as e:
        return {'success': False, 'output': f'Knowledge base error: {e}', 'risk_level': 'LOW', 'command': ''}


def _handle_get_vehicle_telemetry(arguments: dict) -> dict:
    """Return live OBD-II telemetry from cache — no shell execution, always LOW."""
    sensor = arguments.get('sensor', 'all')

    with _telemetry_lock:
        if sensor == 'all':
            data = dict(_telemetry_cache)
        else:
            topic = TOPICS.get(sensor)
            if topic and topic in _telemetry_cache:
                data = {sensor: _telemetry_cache[topic]}
            else:
                # Try partial match
                data = {k: v for k, v in _telemetry_cache.items() if sensor in k}

    if not data:
        return {'success': True, 'output': 'No telemetry data available (cache empty)', 'risk_level': 'LOW', 'command': ''}

    output = json.dumps(data, indent=2, default=str)
    if len(output) > 2000:
        output = output[:2000] + '\n... [truncated]'
    return {'success': True, 'output': output, 'risk_level': 'LOW', 'command': f'telemetry({sensor})'}


# ═══════════════════════════════════════════════════════════════════
#  Main Entry Point
# ═══════════════════════════════════════════════════════════════════

def execute_tool(tool_name: str, arguments: dict, mqtt_client=None) -> dict:
    """
    Execute a tool call from the LLM. Dispatches to the appropriate handler
    based on tool_name, applies risk classification, and returns results.
    """
    log.info("TOOL CALL: %s(%s)", tool_name, json.dumps(arguments, default=str))

    handlers = {
        'run_command': lambda: _handle_run_command(arguments, mqtt_client),
        'rf_scan': lambda: _handle_rf_scan(arguments, mqtt_client),
        'network_scan': lambda: _handle_network_scan(arguments, mqtt_client),
        'query_knowledge': lambda: _handle_query_knowledge(arguments),
        'get_vehicle_telemetry': lambda: _handle_get_vehicle_telemetry(arguments),
    }

    handler = handlers.get(tool_name)
    if handler is None:
        return {
            'success': False,
            'output': f'Unknown tool: {tool_name}',
            'risk_level': 'BLOCKED',
            'command': '',
        }

    try:
        result = handler()
    except Exception as e:
        log.exception("Tool execution failed: %s", tool_name)
        result = {
            'success': False,
            'output': f'Tool execution error: {e}',
            'risk_level': 'MEDIUM',
            'command': '',
        }

    log.info("TOOL RESULT [%s] success=%s risk=%s",
             tool_name, result.get('success'), result.get('risk_level'))
    return result

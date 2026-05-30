# tests/test_session_analyst.py
import pytest
import json
import sys
import time
sys.path.insert(0, 'src')

from unittest.mock import patch, MagicMock

SESSION_PAYLOAD = {
    'event': 'end',
    'session_id': '20260315_141022',
    'start_ts': 1000.0, 'end_ts': 2000.0,
    'distance_km': 12.4, 'duration_seconds': 1000.0,
    'max_rpm': 3200.0, 'max_speed': 80.0,
    'max_coolant': 98.0, 'min_voltage': 13.1,
    'warmup_seconds': 480.0,
    'avg_stft_b1': None, 'avg_stft_b2': None,
    'avg_ltft_b1': None, 'avg_ltft_b2': None,
    'idle_rpm_stddev': None,
    'dtcs_seen': '["P0171","P0174"]',
    'alert_count': 2,
}

SAMPLE_ANOMALIES = [
    {'session_id': '20260315_141022', 'ts': 1100.0, 'sensor': 'stft_b1',
     'value': 14.2, 'z_score': 3.8, 'severity': 'high',
     'context_json': '{"rpm": 1200, "coolant": 85.0}'},
]

def test_build_context_packet_contains_key_sections():
    from session_analyst import build_context_packet
    packet = build_context_packet(
        session=SESSION_PAYLOAD,
        anomalies=SAMPLE_ANOMALIES,
        sensor_avgs={'stft_b1': 7.2, 'stft_b2': 6.8, 'voltage': 13.1},
        baseline={'avg_stft_b1': 2.1, 'avg_stft_b2': 1.8, 'warmup_seconds': 320.0,
                  'session_count': 5},
        kb_entries=['KNOWN ISSUE: Intake manifold gasket\nSymptoms: lean codes'],
    )
    assert 'P0171' in packet
    assert 'stft_b1' in packet
    assert '14.2' in packet
    assert 'KNOWN ISSUE' in packet
    assert 'baseline' in packet.lower() or 'avg' in packet.lower()

def test_parse_report_valid_json():
    from session_analyst import parse_report
    raw = '{"primary_suspect": {"diagnosis": "MAF", "confidence": 70, "evidence": "x", "confirm_with": "y"}, "secondary_suspects": [], "watch_items": [], "action_items": [], "safety_critical": false}'
    result = parse_report(raw)
    assert result['parse_error'] is False
    assert result['primary_suspect']['diagnosis'] == 'MAF'

def test_parse_report_invalid_json_sets_error_flag():
    from session_analyst import parse_report
    result = parse_report("This is not JSON at all")
    assert result['parse_error'] is True
    assert 'raw_response' in result

def test_parse_report_extracts_json_from_surrounding_text():
    from session_analyst import parse_report
    raw = 'Here is my analysis: {"primary_suspect": {"diagnosis": "Thermostat"}, "safety_critical": false} Hope this helps!'
    result = parse_report(raw)
    assert result['parse_error'] is False
    assert result['primary_suspect']['diagnosis'] == 'Thermostat'

def test_parse_report_handles_markdown_fences():
    from session_analyst import parse_report
    raw = '```json\n{"primary_suspect": {"diagnosis": "Coil pack"}, "safety_critical": true}\n```'
    result = parse_report(raw)
    assert result['parse_error'] is False
    assert result['primary_suspect']['diagnosis'] == 'Coil pack'

def test_compute_sensor_avgs_from_jsonl(tmp_path):
    from session_analyst import compute_sensor_avgs
    # Write a mock JSONL file
    log_file = tmp_path / "drive_20260315.jsonl"
    records = [
        {'topic': 'drifter/engine/stft1', 'data': {'value': 5.0}, 'ts': 1100.0},
        {'topic': 'drifter/engine/stft1', 'data': {'value': 7.0}, 'ts': 1200.0},
        {'topic': 'drifter/engine/stft2', 'data': {'value': 6.0}, 'ts': 1100.0},
        {'topic': 'drifter/power/voltage', 'data': {'value': 13.5}, 'ts': 1100.0},
        # Outside session range — should be excluded
        {'topic': 'drifter/engine/stft1', 'data': {'value': 99.0}, 'ts': 500.0},
    ]
    with open(log_file, 'w') as f:
        for r in records:
            f.write(json.dumps(r) + '\n')
    avgs = compute_sensor_avgs(log_file, start_ts=1000.0, end_ts=2000.0)
    assert abs(avgs.get('stft_b1', 0) - 6.0) < 0.01  # (5+7)/2
    assert abs(avgs.get('stft_b2', 0) - 6.0) < 0.01
    assert abs(avgs.get('voltage', 0) - 13.5) < 0.01

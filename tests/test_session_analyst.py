# tests/test_session_analyst.py
import json
import sys

sys.path.insert(0, 'src')


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

SAMPLE_INCIDENTS = [{
    'id': 'incident-1',
    'trigger': 1150.0,
    'reasons': ['idle_rpm_collapse'],
    'first_change': {
        'sensor': 'stft_b1', 'offset_s': -2.4,
        'baseline': 2.0, 'value': 18.0, 'delta': 16.0,
    },
    'first_changes': [
        {'sensor': 'stft_b1', 'offset_s': -2.4, 'baseline': 2.0, 'value': 18.0},
        {'sensor': 'maf', 'offset_s': -1.2, 'baseline': 4.2, 'value': 2.0},
        {'sensor': 'rpm', 'offset_s': 0.0, 'baseline': 720, 'value': 480},
    ],
    'correlation_flags': [
        {'flag': 'lean_trim_change_precedes_or_matches_rpm_drop'},
    ],
}]


def test_build_context_packet_contains_key_sections():
    from session_analyst import build_context_packet
    packet = build_context_packet(
        session=SESSION_PAYLOAD,
        anomalies=SAMPLE_ANOMALIES,
        sensor_avgs={'stft_b1': 7.2, 'stft_b2': 6.8, 'voltage': 13.1},
        baseline={'avg_stft_b1': 2.1, 'avg_stft_b2': 1.8, 'warmup_seconds': 320.0,
                  'session_count': 5},
        kb_entries=['KNOWN ISSUE: Intake manifold gasket\nSymptoms: lean codes'],
        incidents=SAMPLE_INCIDENTS,
    )
    assert 'P0171' in packet
    assert 'stft_b1' in packet
    assert '14.2' in packet
    assert 'KNOWN ISSUE' in packet
    assert 'BLACK BOX INCIDENTS' in packet
    assert 'FIRST MATERIAL CHANGE' in packet
    assert 'lean_trim_change_precedes_or_matches_rpm_drop' in packet
    assert 'baseline' in packet.lower() or 'avg' in packet.lower()


def test_session_with_averages_persists_measured_trim_baselines():
    from session_analyst import _session_with_averages

    enriched = _session_with_averages(
        {'session_id': 'S1', 'avg_stft_b1': None, 'avg_ltft_b1': 3.0},
        {'stft_b1': 6.25, 'stft_b2': -1.5, 'ltft_b1': 9.0, 'ltft_b2': 2.5},
    )
    assert enriched['avg_stft_b1'] == 6.25
    assert enriched['avg_stft_b2'] == -1.5
    assert enriched['avg_ltft_b1'] == 3.0  # explicit caller value wins
    assert enriched['avg_ltft_b2'] == 2.5


def test_build_context_packet_formats_missing_voltage_as_unknown():
    from session_analyst import build_context_packet

    session = dict(SESSION_PAYLOAD, min_voltage=None)
    packet = build_context_packet(session, [], {}, None, [])
    assert 'Min voltage: ?V' in packet
    assert '99.0V' not in packet


def test_load_incident_summaries_filters_to_session(tmp_path):
    from session_analyst import load_incident_summaries

    inside = tmp_path / 'incident_inside.json'
    inside.write_text(json.dumps({
        'id': 'inside', 'trigger': 1500.0, 'reasons': ['idle_rpm_collapse']
    }))
    outside = tmp_path / 'incident_outside.json'
    outside.write_text(json.dumps({
        'id': 'outside', 'trigger': 3000.0, 'reasons': ['voltage_collapse']
    }))
    (tmp_path / 'incident_broken.json').write_text('{bad json')

    rows = load_incident_summaries(tmp_path, 1000.0, 2000.0)
    assert [row['id'] for row in rows] == ['inside']


def test_session_end_payload_updates_incident_deadline_before_analysis(monkeypatch):
    import session_analyst

    started = []

    class DummyThread:
        def __init__(self, *args, **kwargs):
            self.kwargs = kwargs

        def start(self):
            started.append(True)

    monkeypatch.setattr(session_analyst.threading, 'Thread', DummyThread)
    analyst = session_analyst.SessionAnalyst.__new__(session_analyst.SessionAnalyst)
    analyst.incident_end_at = 1000.0
    analyst.last_session = None

    msg = type('Msg', (), {
        'topic': session_analyst.TOPICS['drive_session'],
        'payload': json.dumps({
            'event': 'end', 'session_id': 'S1',
            'incident_active': True, 'incident_end_at': 1060.0,
        }).encode(),
    })()
    analyst._on_message(None, None, msg)

    assert analyst.incident_end_at == 1060.0
    assert analyst.last_session['session_id'] == 'S1'
    assert started == [True]


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
    log_file = tmp_path / "drive_20260315.jsonl"
    records = [
        {'topic': 'drifter/engine/stft1', 'data': {'value': 5.0}, 'ts': 1100.0},
        {'topic': 'drifter/engine/stft1', 'data': {'value': 7.0}, 'ts': 1200.0},
        {'topic': 'drifter/engine/stft2', 'data': {'value': 6.0}, 'ts': 1100.0},
        {'topic': 'drifter/power/voltage', 'data': {'value': 13.5}, 'ts': 1100.0},
        {'topic': 'drifter/engine/stft1', 'data': {'value': 99.0}, 'ts': 500.0},
    ]
    with open(log_file, 'w') as f:
        for record in records:
            f.write(json.dumps(record) + '\n')
    avgs = compute_sensor_avgs(log_file, start_ts=1000.0, end_ts=2000.0)
    assert abs(avgs.get('stft_b1', 0) - 6.0) < 0.01
    assert abs(avgs.get('stft_b2', 0) - 6.0) < 0.01
    assert abs(avgs.get('voltage', 0) - 13.5) < 0.01

import gzip
import json
import sys

sys.path.insert(0, 'src')

from blackbox import IncidentBlackBox


def _feed_stable_idle(box, start=1000.0, samples=30):
    for i in range(samples):
        ts = start + i * 0.2
        box.ingest('drifter/engine/coolant', {'value': 90}, ts)
        box.ingest('drifter/vehicle/speed', {'value': 0}, ts + 0.01)
        box.ingest('drifter/engine/throttle', {'value': 12}, ts + 0.02)
        box.ingest('drifter/engine/stft1', {'value': 2}, ts + 0.03)
        box.ingest('drifter/engine/maf', {'value': 4.2}, ts + 0.04)
        box.ingest('drifter/engine/rpm', {'value': 720}, ts + 0.05)


def test_idle_collapse_freezes_pre_fault_context_and_first_mover(tmp_path):
    box = IncidentBlackBox(tmp_path, pre_seconds=20, post_seconds=5, cooldown_seconds=10)
    _feed_stable_idle(box)
    ts = 1006.0
    box.ingest('drifter/engine/stft1', {'value': 18}, ts + 0.10)
    box.ingest('drifter/engine/maf', {'value': 2.0}, ts + 0.20)
    event = box.ingest('drifter/engine/rpm', {'value': 480}, ts + 0.50)

    assert event is not None
    assert 'idle_rpm_collapse' in event['reasons']

    summary = box.finalize(ts + 6, force=True)
    assert summary is not None
    assert summary['first_change']['sensor'] == 'stft_b1'
    assert summary['first_change']['offset_s'] < 0
    flags = {row['flag'] for row in summary['correlation_flags']}
    assert 'lean_trim_change_precedes_or_matches_rpm_drop' in flags
    assert 'airflow_drop_without_prior_throttle_change' in flags

    with open(summary['summary_file']) as fh:
        saved = json.load(fh)
    assert saved['id'] == summary['id']
    with gzip.open(summary['data_file'], 'rt') as fh:
        records = [json.loads(line) for line in fh]
    assert any(r['topic'] == 'drifter/engine/stft1' for r in records)
    assert any(r['topic'] == 'drifter/engine/rpm' for r in records)


def test_manual_capture_bypasses_automatic_cooldown(tmp_path):
    box = IncidentBlackBox(tmp_path, pre_seconds=20, post_seconds=5, cooldown_seconds=90)
    first = box.trigger('manual_capture', 1000, manual=True, source='touchscreen')
    assert first is not None
    box.finalize(1006, force=True)
    second = box.trigger('manual_capture', 1007, manual=True, source='touchscreen')
    assert second is not None


def test_irrelevant_high_volume_rf_topic_is_not_buffered(tmp_path):
    box = IncidentBlackBox(tmp_path)
    box.ingest('drifter/rf/spectrum/raw', {'bins': [1, 2, 3]}, 1000)
    assert box.status()['buffer_records'] == 0


def test_obd_loss_while_engine_was_running_triggers_capture(tmp_path):
    box = IncidentBlackBox(tmp_path, cooldown_seconds=10)
    box.ingest('drifter/engine/rpm', {'value': 720}, 1000)
    event = box.ingest('drifter/obd/status', {
        'state': 'adapter_error', 'reason': 'ELM no longer answers ATI'
    }, 1002)
    assert event is not None
    assert event['reasons'] == ['obd_adapter_error']


def test_anomaly_event_extends_active_incident_instead_of_overwriting_it(tmp_path):
    box = IncidentBlackBox(tmp_path, post_seconds=5, cooldown_seconds=10)
    first = box.trigger('idle_rpm_collapse', 1000)
    event = box.ingest('drifter/anomaly/event', {
        'sensor': 'maf', 'severity': 'high', 'z_score': 3.2
    }, 1002)
    assert event['id'] == first['id']
    assert event['extended'] is True
    assert 'anomaly_maf' in event['reasons']

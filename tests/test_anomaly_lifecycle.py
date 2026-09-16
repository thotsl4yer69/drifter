import json
import sys

sys.path.insert(0, 'src')

import config
import db
from anomaly_monitor import AnomalyMonitor


def _reset_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'DB_PATH', tmp_path / 'test.db')
    monkeypatch.setattr(config, 'REPORTS_DIR', tmp_path / 'reports')
    if hasattr(db._local, 'conn'):
        try:
            db._local.conn.close()
        except Exception:
            pass
        del db._local.conn


class Msg:
    def __init__(self, topic, payload):
        self.topic = topic
        self.payload = json.dumps(payload).encode()


def test_session_lifecycle_processed_without_value_field(tmp_path, monkeypatch):
    _reset_db(tmp_path, monkeypatch)
    monitor = AnomalyMonitor()
    monitor._on_message(None, None, Msg('drifter/session', {
        'event': 'start', 'session_id': 'SESSION-1', 'ts': 1000,
    }))
    assert monitor.current_session_id == 'SESSION-1'
    monitor._on_message(None, None, Msg('drifter/session', {
        'event': 'end', 'session_id': 'SESSION-1', 'ts': 1100,
    }))
    assert monitor.current_session_id is None


def test_published_anomaly_is_available_to_blackbox_bus(tmp_path, monkeypatch):
    _reset_db(tmp_path, monkeypatch)
    monitor = AnomalyMonitor()
    published = []

    class Client:
        def publish(self, topic, payload, *args, **kwargs):
            published.append((topic, json.loads(payload)))

    monitor.client = Client()
    event = {
        'session_id': 'SESSION-1', 'ts': 1001.0, 'sensor': 'maf',
        'value': 2.0, 'z_score': 3.5, 'severity': 'high',
        'context_json': json.dumps({'rpm': 700, 'maf': 2.0}),
    }
    monitor._publish_anomaly(event)
    assert published
    assert published[-1][0] == 'drifter/anomaly/event'
    assert published[-1][1]['sensor'] == 'maf'
    assert published[-1][1]['context']['rpm'] == 700

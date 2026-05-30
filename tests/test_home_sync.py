# tests/test_home_sync.py
"""Regression test for the home_sync topic rewrite."""
import sys
sys.path.insert(0, 'src')

import home_sync


class FakeClient:
    def __init__(self):
        self.published = []

    def publish(self, topic, payload):
        self.published.append((topic, payload))


class FakeMsg:
    def __init__(self, topic, payload=b'x'):
        self.topic = topic
        self.payload = payload


def test_topic_prefix_rewrite_only_replaces_leading_prefix(monkeypatch):
    """Regression: str.replace rewrites every occurrence. A topic segment
    that happens to contain 'drifter/' would get mangled."""
    client = FakeClient()
    monkeypatch.setattr(home_sync, 'home_client', client)
    monkeypatch.setattr(home_sync, 'is_home', True)

    # Pathological topic: mid-string occurrence of "drifter/".
    msg = FakeMsg('drifter/meta/drifter/count')
    home_sync.on_local_message(None, None, msg)
    assert client.published == [
        ('sentient/vehicle/drifter/meta/drifter/count', b'x')
    ]


def test_no_forward_when_not_home(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(home_sync, 'home_client', client)
    monkeypatch.setattr(home_sync, 'is_home', False)
    home_sync.on_local_message(None, None, FakeMsg('drifter/engine/rpm'))
    assert client.published == []


def test_no_forward_when_no_client(monkeypatch):
    monkeypatch.setattr(home_sync, 'home_client', None)
    monkeypatch.setattr(home_sync, 'is_home', True)
    # Must not raise
    home_sync.on_local_message(None, None, FakeMsg('drifter/engine/rpm'))

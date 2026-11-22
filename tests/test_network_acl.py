"""Shared hotspot ACL (config.is_local_peer) and its use on every listener."""
import asyncio
import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))
import config  # noqa: E402


@pytest.mark.parametrize('peer', [
    '127.0.0.1', '10.42.0.1', '10.42.0.77', '::1', '::ffff:10.42.0.9',
    ('10.42.0.5', 51000), ('127.0.0.1', 1, 0, 0),
])
def test_trusted_peers_allowed(peer):
    assert config.is_local_peer(peer)


@pytest.mark.parametrize('peer', [
    '192.168.1.20', '10.42.1.5', '10.420.0.1', '127.0.0.2', '', None,
    (), ('192.168.1.20', 80), '::ffff:192.168.1.20', 'fe80::1',
])
def test_untrusted_peers_rejected(peer):
    assert not config.is_local_peer(peer)


def test_prefixes_and_bind_are_env_overridable(monkeypatch):
    monkeypatch.setenv('DRIFTER_TRUSTED_PREFIXES', '127.0.0.1,192.168.50.')
    monkeypatch.setenv('DRIFTER_BIND_HOST', '10.42.0.1')
    cfg = importlib.reload(config)
    try:
        assert cfg.BIND_HOST == '10.42.0.1'
        assert cfg.is_local_peer('192.168.50.3')
        assert not cfg.is_local_peer('10.42.0.3')
    finally:
        monkeypatch.delenv('DRIFTER_TRUSTED_PREFIXES')
        monkeypatch.delenv('DRIFTER_BIND_HOST')
        importlib.reload(config)


def test_dashboard_and_opsec_delegate_to_shared_acl():
    import opsec_dashboard
    import web_dashboard_handlers
    for mod in (web_dashboard_handlers, opsec_dashboard):
        assert mod._is_local_peer('10.42.0.4')
        assert not mod._is_local_peer('192.168.1.4')


class _FakeWS:
    def __init__(self, peer):
        self.remote_address = peer
        self.sent = []

    async def send(self, msg):
        self.sent.append(msg)

    async def ping(self):
        raise RuntimeError('closed')


def test_websocket_handlers_refuse_untrusted_peer():
    import web_dashboard
    import web_dashboard_state as state
    ws = _FakeWS(('192.168.1.9', 40000))
    before = len(state.ws_clients)
    asyncio.run(web_dashboard.ws_handler(ws))
    asyncio.run(web_dashboard.audio_ws_handler(ws))
    assert ws.sent == []
    assert len(state.ws_clients) == before
    assert ws not in state.audio_ws_clients


def test_websocket_handler_serves_trusted_peer():
    import web_dashboard
    import web_dashboard_state as state
    ws = _FakeWS(('10.42.0.8', 40000))
    state.latest_state['engine_rpm'] = {'value': 800}
    prev = state.running
    state.running = True
    try:
        asyncio.run(web_dashboard.ws_handler(ws))
    finally:
        state.running = prev
        state.latest_state.pop('engine_rpm', None)
    assert any('engine/rpm' in m for m in ws.sent)


# ── Private runtime dirs (replaces fixed /tmp/drifter-* paths) ──

def test_runtime_dir_prefers_systemd_runtime_directory(monkeypatch, tmp_path):
    monkeypatch.setenv('RUNTIME_DIRECTORY', str(tmp_path))
    assert config.runtime_dir('drifter-x').parent == tmp_path


def test_ensure_private_dir_creates_0700(tmp_path):
    d = config.ensure_private_dir(tmp_path / 'a' / 'b')
    assert d.is_dir() and (d.stat().st_mode & 0o777) == 0o700


def test_ensure_private_dir_tightens_loose_perms(tmp_path):
    d = tmp_path / 'loose'
    d.mkdir(mode=0o777)
    d.chmod(0o777)
    config.ensure_private_dir(d)
    assert (d.stat().st_mode & 0o777) == 0o700


def test_ensure_private_dir_refuses_symlink(tmp_path):
    real = tmp_path / 'real'
    real.mkdir()
    link = tmp_path / 'link'
    link.symlink_to(real)
    with pytest.raises(PermissionError):
        config.ensure_private_dir(link)


def test_audio_dirs_no_longer_fixed_tmp():
    import vivi_v2
    import voice_alerts
    for d in (voice_alerts.AUDIO_DIR, vivi_v2.AUDIO_DIR):
        assert str(d) not in ('/tmp/drifter-audio', '/tmp/drifter-vivi2')

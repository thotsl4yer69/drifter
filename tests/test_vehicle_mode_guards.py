"""Bench-only tools must remain inactive on an installed vehicle node."""
import json
import threading
from unittest.mock import MagicMock

import pytest

import config
import fuzz_engine
import replay_engine


@pytest.mark.parametrize('mode,flag,allowed', [('diag', '1', False), ('drive', '1', False),
                                              ('both', '1', False), ('foot', '', False),
                                              ('foot', '1', True), ('invalid', '1', False)])
def test_lab_requires_explicit_opt_in_and_foot_mode(tmp_path, monkeypatch, mode, flag, allowed):
    path = tmp_path / 'mode.state'
    path.write_text(mode + '\n')
    monkeypatch.setattr(config, 'MODE_STATE_PATH', path)
    monkeypatch.setenv('DRIFTER_LAB_MODE', flag)
    assert config.lab_mode_allowed() is allowed


def test_fuzz_does_not_publish_on_vehicle_bus(monkeypatch):
    monkeypatch.setattr(config, 'lab_mode_allowed', lambda: False)
    client = MagicMock()
    fuzz_engine._publish_tick(client, {})
    client.publish.assert_not_called()


def test_replay_refuses_vehicle_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'lab_mode_allowed', lambda: False)
    client = MagicMock()
    replay_engine._replay_session(client, tmp_path / 'no-file', 1, threading.Event())
    assert json.loads(client.publish.call_args.args[1])['reason'] == 'bench_only'


def test_replay_never_reissues_recorded_operator_commands(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'lab_mode_allowed', lambda: True)
    path = tmp_path / 'session.jsonl'
    path.write_text(json.dumps({'ts': 100, 'topic': 'drifter/can/command',
                                'payload': {'command': 'fuzz_range'}}) + '\n')
    client = MagicMock()
    replay_engine._replay_session(client, path, 1, threading.Event())
    assert all(call.args[0] != 'drifter/can/command' for call in client.publish.call_args_list)

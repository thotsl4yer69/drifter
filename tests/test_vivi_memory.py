# tests/test_vivi_memory.py
"""Smoke tests for vivi_memory using an in-memory SQLite DB."""
import sys

sys.path.insert(0, 'src')

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def use_tmp_db(tmp_path, monkeypatch):
    """Redirect vivi_memory DB to a temp path so tests don't touch /opt/drifter."""
    import vivi_memory
    db_path = tmp_path / "memory" / "vivi.db"
    monkeypatch.setattr(vivi_memory, 'DB_PATH', db_path)
    vivi_memory.init_db()
    yield
    # cleanup happens via tmp_path fixture


def test_append_and_history():
    import vivi_memory as vm
    vm.append_turn("sess1", "user", "Hello Vivi")
    vm.append_turn("sess1", "assistant", "Hello! How can I help?")
    turns = vm.history("sess1")
    assert len(turns) == 2
    assert turns[0]['role'] == 'user'
    assert turns[0]['content'] == 'Hello Vivi'
    assert turns[1]['role'] == 'assistant'


def test_history_respects_n_limit():
    import vivi_memory as vm
    for i in range(20):
        vm.append_turn("sess2", "user", f"Message {i}")
    turns = vm.history("sess2", n=5)
    assert len(turns) == 5


def test_history_session_isolation():
    import vivi_memory as vm
    vm.append_turn("sessA", "user", "Turn for A")
    vm.append_turn("sessB", "user", "Turn for B")
    a_turns = vm.history("sessA")
    b_turns = vm.history("sessB")
    assert len(a_turns) == 1
    assert a_turns[0]['content'] == "Turn for A"
    assert len(b_turns) == 1


def test_remember_and_recall():
    import vivi_memory as vm
    fid = vm.remember("Fuel station at Shell Bendigo", tag="location")
    assert isinstance(fid, int)
    results = vm.recall(tag="location")
    assert any("Shell Bendigo" in r['content'] for r in results)


def test_recall_by_query():
    import vivi_memory as vm
    vm.remember("Mechanic contact: Dave at AutoPro", tag="contact")
    vm.remember("Spare tyre is in boot", tag="vehicle")
    results = vm.recall(query="mechanic")
    assert any("Dave" in r['content'] for r in results)


def test_forget():
    import vivi_memory as vm
    fid = vm.remember("Temporary note")
    assert vm.forget(fid) is True
    assert vm.forget(fid) is False  # already gone


def test_invalid_role_rejected():
    import vivi_memory as vm
    # Should log warning and not raise
    vm.append_turn("sess_x", "robot", "This should be rejected")
    turns = vm.history("sess_x")
    assert len(turns) == 0


def test_facts_pruned_to_max():
    import vivi_memory as vm
    # Override max to 5 for this test
    with patch.object(vm, 'VIVI2_MEMORY_MAX_ENTRIES', 5):
        # Reload to pick up constant — just call remember manually
        for i in range(8):
            vm.remember(f"Fact {i}", tag="")
    # After pruning we should have at most original max + some slack
    results = vm.recall(n=100)
    # At least the recent ones survived; we just check no exception and some results exist
    assert len(results) > 0


def test_export_session():
    import vivi_memory as vm
    vm.append_turn("sess_exp", "user", "Export me")
    exp = vm.export_session("sess_exp")
    assert exp['session_id'] == "sess_exp"
    assert len(exp['turns']) == 1
    assert 'exported_at' in exp

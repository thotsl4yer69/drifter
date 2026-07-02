"""Tests for config.atomic_write_json / atomic_write_text.

The vehicle loses power without a clean shutdown routinely, so state writes
must be crash-safe: a reader must never see a truncated/partial file, and a
failed write must not leave a temp turd behind. These are the writers the
audit flagged (calibration, TPMS map, settings, session summary, sync state).
"""
import json

import config


def test_atomic_write_json_roundtrip(tmp_path):
    p = tmp_path / "nested" / "state.json"
    data = {"a": 1, "b": [2, 3], "c": "x"}
    config.atomic_write_json(p, data)
    assert json.loads(p.read_text()) == data


def test_atomic_write_creates_parent_dirs(tmp_path):
    p = tmp_path / "a" / "b" / "c" / "f.json"
    config.atomic_write_json(p, {"ok": True})
    assert p.exists()


def test_atomic_write_leaves_no_temp_file(tmp_path):
    p = tmp_path / "state.json"
    config.atomic_write_json(p, {"n": 1})
    # only the final file, no *.tmp.* siblings
    assert [x.name for x in tmp_path.iterdir()] == ["state.json"]


def test_atomic_write_replaces_existing(tmp_path):
    p = tmp_path / "state.json"
    config.atomic_write_json(p, {"v": 1})
    config.atomic_write_json(p, {"v": 2})
    assert json.loads(p.read_text()) == {"v": 2}


def test_atomic_write_text(tmp_path):
    p = tmp_path / "note.txt"
    config.atomic_write_text(p, "hello world")
    assert p.read_text() == "hello world"


def test_failed_serialization_leaves_no_partial(tmp_path):
    p = tmp_path / "state.json"
    config.atomic_write_json(p, {"v": 1})
    # object() is not JSON-serializable — the write must raise and leave the
    # previous good file intact, with no temp file lingering.
    try:
        config.atomic_write_json(p, {"bad": object()})
    except TypeError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected TypeError on unserializable data")
    assert json.loads(p.read_text()) == {"v": 1}
    assert [x.name for x in tmp_path.iterdir()] == ["state.json"]


def test_save_settings_is_atomic(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "settings.json")
    assert config.save_settings({"voice_cooldown": 20}) is True
    saved = json.loads((tmp_path / "settings.json").read_text())
    assert saved["voice_cooldown"] == 20

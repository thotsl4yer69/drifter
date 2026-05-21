"""Tests for flipper_bridge — .sub builder, capture persistence, region lock."""
from __future__ import annotations

import json
import time
from unittest.mock import MagicMock

import pytest

import flipper_bridge as fb


# ── parse_raw_data_line ───────────────────────────────────────────────

def test_parse_raw_data_line_strips_prefix():
    nums = fb.parse_raw_data_line('RAW_Data: 244 -732 244 -488')
    assert nums == [244, -732, 244, -488]


def test_parse_raw_data_line_accepts_bare_sequence():
    nums = fb.parse_raw_data_line('244 -732 244')
    assert nums == [244, -732, 244]


def test_parse_raw_data_line_rejects_non_numeric():
    assert fb.parse_raw_data_line('hw info reply') is None


def test_parse_raw_data_line_rejects_too_short():
    assert fb.parse_raw_data_line('244') is None


# ── build_sub_file ────────────────────────────────────────────────────

def test_build_sub_file_has_required_headers():
    body = fb.build_sub_file(433920000, [244, -732, 244, -488])
    assert 'Filetype: Flipper SubGhz RAW File' in body
    assert 'Version: 1' in body
    assert 'Frequency: 433920000' in body
    assert 'Preset: FuriHalSubGhzPresetOok650Async' in body
    assert 'Protocol: RAW' in body
    assert 'RAW_Data: 244 -732 244 -488' in body


def test_build_sub_file_wraps_long_raw_at_512():
    long_raw = list(range(1, 1100))  # 1099 ints
    body = fb.build_sub_file(433920000, long_raw, max_per_line=512)
    raw_lines = [ln for ln in body.splitlines() if ln.startswith('RAW_Data:')]
    assert len(raw_lines) == 3  # 512 + 512 + 75
    # First two lines exactly 512 values
    assert len(raw_lines[0].replace('RAW_Data:', '').split()) == 512


# ── persist_capture ───────────────────────────────────────────────────

def test_persist_capture_writes_file_with_correct_shape(tmp_path, monkeypatch):
    monkeypatch.setattr(fb, 'FLIPPER_CAPTURE_DIR', tmp_path)
    ts = 1700000000.0
    meta = fb.persist_capture(433920000, [244, -732, 244, -488], ts=ts)
    assert meta is not None
    assert meta['id'] == f'drifter-{int(ts)}'
    assert meta['on_flipper_path'] == f'/ext/subghz/drifter-{int(ts)}.sub'
    assert meta['local_sub_path'].endswith('.sub')
    written = (tmp_path / f'drifter-{int(ts)}.sub').read_text()
    assert 'RAW_Data: 244 -732 244 -488' in written


# ── is_tx_region_locked ──────────────────────────────────────────────

def test_is_tx_region_locked_blocks_outside_bands():
    msg = fb.is_tx_region_locked(150_000_000)
    assert 'outside' in msg.lower()


def test_is_tx_region_locked_clean_inside_au_band():
    assert fb.is_tx_region_locked(920_000_000) == ''


def test_is_tx_region_locked_passes_433_iSM_band():
    assert fb.is_tx_region_locked(433_920_000) == ''


# ── list_persisted_captures ───────────────────────────────────────────

def test_list_persisted_captures_reads_freq_header(tmp_path, monkeypatch):
    monkeypatch.setattr(fb, 'FLIPPER_CAPTURE_DIR', tmp_path)
    body = fb.build_sub_file(433920000, [244, -732, 244, -488])
    (tmp_path / 'drifter-1700000000.sub').write_text(body)
    captures = fb.list_persisted_captures()
    assert len(captures) == 1
    assert captures[0]['id'] == 'drifter-1700000000'
    assert captures[0]['freq_hz'] == 433920000


def test_list_persisted_captures_empty_when_dir_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(fb, 'FLIPPER_CAPTURE_DIR', tmp_path / 'absent')
    assert fb.list_persisted_captures() == []


# ── subghz_replay command ─────────────────────────────────────────────

def test_subghz_replay_publishes_warning_for_locked_band(tmp_path, monkeypatch):
    monkeypatch.setattr(fb, 'FLIPPER_CAPTURE_DIR', tmp_path)
    body = fb.build_sub_file(150_000_000, [244, -732])
    (tmp_path / 'drifter-42.sub').write_text(body)
    flipper = MagicMock()
    flipper.connected = True
    flipper.send_command = MagicMock(return_value=(True, 'ok'))
    mqtt = MagicMock()
    fb._do_subghz_replay(flipper, mqtt, {'capture_id': 'drifter-42'})
    payload = json.loads(mqtt.publish.call_args_list[-1].args[1])
    assert payload['command'] == 'subghz_replay'
    assert 'warning' in payload
    assert 'outside' in payload['warning'].lower()


def test_subghz_replay_missing_capture_returns_error(tmp_path, monkeypatch):
    monkeypatch.setattr(fb, 'FLIPPER_CAPTURE_DIR', tmp_path)
    flipper = MagicMock()
    flipper.connected = True
    mqtt = MagicMock()
    fb._do_subghz_replay(flipper, mqtt, {'capture_id': 'nonexistent'})
    payload = json.loads(mqtt.publish.call_args_list[-1].args[1])
    assert payload['success'] is False
    assert 'not found' in payload['response']


def test_subghz_replay_missing_capture_id_returns_error():
    flipper = MagicMock()
    mqtt = MagicMock()
    fb._do_subghz_replay(flipper, mqtt, {})
    payload = json.loads(mqtt.publish.call_args_list[-1].args[1])
    assert payload['success'] is False
    assert 'capture_id' in payload['response']

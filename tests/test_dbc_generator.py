"""Tests for dbc_generator id normalization + DBC emission.

The observed (sniffer summary) and classified (decoder) feeds may represent
arbitration ids differently ("0x7E8" vs 2024). Keys must be normalized to a
canonical int so the two maps join — otherwise every signal is UNKNOWN. And a
garbage id must not crash the emit thread.
"""
from __future__ import annotations

import json
import sys
from unittest.mock import MagicMock

sys.path.insert(0, 'src')

import dbc_generator as dbc
from config import TOPICS


def _msg(topic, payload):
    m = MagicMock()
    m.topic = topic
    m.payload = json.dumps(payload).encode()
    return m


def _reset():
    dbc._observed.clear()
    dbc._classified.clear()


def test_parse_id_total_on_garbage():
    assert dbc._parse_id("0x7E8") == 0x7E8
    assert dbc._parse_id("2024") == 2024
    assert dbc._parse_id(2024) == 2024
    assert dbc._parse_id("bus0") is None   # garbage -> None, not a raise
    assert dbc._parse_id("") is None
    assert dbc._parse_id(None) is None


def test_hex_and_int_ids_join_to_signal_name(tmp_path):
    _reset()
    # Sniffer emits hex-string id; decoder emits integer id for the same frame.
    dbc._on_message(None, None, _msg(TOPICS['can_sniff_summary'],
                                     {'ids': [{'id': '0x7E8', 'hz': 10,
                                               'last_data': 'aabbccdd'}]}))
    dbc._on_message(None, None, _msg(TOPICS['can_decode_response'],
                                     {'id': 2024, 'signal_name': 'rpm'}))
    out = tmp_path / "out.dbc"
    dbc._emit_dbc(out)
    text = out.read_text()
    # 0x7E8 == 2024 — the classification must attach, not fall back to UNKNOWN.
    assert 'RPM' in text
    assert 'UNKNOWN' not in text
    assert 'BO_ 2024' in text


def test_garbage_id_does_not_crash_emit(tmp_path):
    _reset()
    dbc._on_message(None, None, _msg(TOPICS['can_sniff_summary'],
                                     {'ids': [{'id': 'bus0', 'hz': 1},
                                              {'id': '0x100', 'hz': 2,
                                               'last_data': 'dead'}]}))
    out = tmp_path / "out.dbc"
    dbc._emit_dbc(out)  # must not raise on the 'bus0' entry
    text = out.read_text()
    assert 'BO_ 256' in text  # 0x100, the one valid id

"""Tests for can_discovery — command allowlist, CSV format, subprocess
invocation. subprocess.run is mocked so the suite never touches a real
can0 interface."""
from __future__ import annotations

import subprocess
import time
from unittest.mock import MagicMock

import can_discovery as cd

# ── _hex_int ──────────────────────────────────────────────────────────

def test_hex_int_accepts_int():
    assert cd._hex_int(0x7E0) == 2016


def test_hex_int_accepts_0x_prefix():
    assert cd._hex_int('0x7E0') == 0x7E0


def test_hex_int_accepts_bare_hex():
    assert cd._hex_int('7E0') == 0x7E0


def test_hex_int_rejects_garbage():
    assert cd._hex_int('not_hex') is None
    assert cd._hex_int(None) is None


# ── _build_cc_args ────────────────────────────────────────────────────

def test_build_cc_args_discover_ecus():
    argv = cd._build_cc_args('discover_ecus', 'can0', {})
    assert argv is not None
    assert argv[0] == cd.CC_BIN
    assert 'can0' in argv
    assert 'discovery' in argv


def test_build_cc_args_list_services_requires_ecu_id():
    assert cd._build_cc_args('list_services', 'can0', {}) is None
    argv = cd._build_cc_args('list_services', 'can0', {'ecu_id': 0x7E0})
    assert argv is not None
    assert 'services' in argv


def test_build_cc_args_fuzz_range_requires_both_ids():
    assert cd._build_cc_args('fuzz_range', 'can0', {'id_start': 0x700}) is None
    argv = cd._build_cc_args('fuzz_range', 'can0',
                             {'id_start': 0x700, 'id_end': 0x7FF})
    assert argv is not None
    assert '--min-id' in argv
    assert '--max-id' in argv


def test_build_cc_args_fuzz_range_rejects_inverted():
    assert cd._build_cc_args('fuzz_range', 'can0',
                             {'id_start': 0x7FF, 'id_end': 0x700}) is None


def test_build_cc_args_unknown_command():
    assert cd._build_cc_args('not_a_real_command', 'can0', {}) is None


# ── run_command — allowlist + interface gate ──────────────────────────

def test_run_command_rejects_unknown():
    response = cd.run_command('arbitrary_cmd', {}, 'can0')
    assert response['ok'] is False
    assert response['error'] == 'unknown_command'


def test_run_command_no_interface():
    response = cd.run_command('discover_ecus', {}, None)
    assert response['ok'] is False
    assert response['error'] == 'no_interface'
    assert response['command'] == 'discover_ecus'


def test_run_command_bad_args():
    """fuzz_range without id range should fail closed, not invoke cc."""
    runner = MagicMock()
    response = cd.run_command('fuzz_range', {}, 'can0', runner=runner)
    assert response['ok'] is False
    assert response['error'] == 'bad_args'
    runner.assert_not_called()


def test_run_command_invokes_cc_with_right_args():
    """The cc subprocess must be called with -i <interface> and the parsed args."""
    fake_completed = MagicMock(
        returncode=0,
        stdout='0x7E0 supported (positive response 0x50)\n',
        stderr='',
    )
    runner = MagicMock(return_value=fake_completed)
    response = cd.run_command('discover_ecus', {}, 'can0', runner=runner)
    runner.assert_called_once()
    argv = runner.call_args[0][0]
    assert argv[0] == cd.CC_BIN
    assert '-i' in argv
    assert 'can0' in argv
    assert response['ok'] is True
    assert response['returncode'] == 0
    assert len(response['results']) == 1
    assert '0x7E0' in response['results'][0]['hex_tokens']


def test_run_command_timeout_returns_structured_error():
    def _fake_runner(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=1)
    response = cd.run_command('discover_ecus', {}, 'can0', runner=_fake_runner)
    assert response['ok'] is False
    assert response['error'] == 'timeout'


def test_run_command_cc_not_installed():
    def _fake_runner(*args, **kwargs):
        raise FileNotFoundError(cd.CC_BIN)
    response = cd.run_command('discover_ecus', {}, 'can0', runner=_fake_runner)
    assert response['ok'] is False
    assert response['error'] == 'cc_not_installed'


# ── _parse_cc_output ──────────────────────────────────────────────────

def test_parse_cc_output_strips_blank_lines():
    stdout = "\n0x7E0 supported\n\nDID 0xF190 -> 17 chars\n"
    rows = cd._parse_cc_output('list_services', stdout)
    assert len(rows) == 2
    assert rows[0]['raw'].startswith('0x7E0')
    assert '0x7E0' in rows[0]['hex_tokens']


def test_parse_cc_output_extracts_hex_tokens():
    rows = cd._parse_cc_output(
        'fuzz_range',
        '0x7E0 0x50 0x01 0x02 — frame echoed back'
    )
    assert len(rows) == 1
    assert rows[0]['hex_tokens'] == ['0x7E0', '0x50', '0x01', '0x02']


# ── _write_savvycan_csv ───────────────────────────────────────────────

def test_savvycan_csv_writes_header_and_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(cd, 'CAN_CAPTURE_DIR', tmp_path)
    rows = [
        {'ts': 1700000000.0, 'raw': '0x7E0 0x50 0x01',
         'hex_tokens': ['0x7E0', '0x50', '0x01']},
        {'ts': 1700000001.0, 'raw': '0x7E8 0x40',
         'hex_tokens': ['0x7E8', '0x40']},
    ]
    fname = cd._write_savvycan_csv(rows)
    assert fname is not None
    assert fname.endswith('.csv')
    content = (tmp_path / fname).read_text()
    lines = content.strip().split('\n')
    assert lines[0] == cd.SAVVYCAN_HEADER
    assert lines[0].startswith('Time Stamp,ID,Extended,Bus,LEN,')
    assert len(lines) == 3
    assert '0x7E0' in lines[1]


def test_savvycan_csv_empty_rows_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(cd, 'CAN_CAPTURE_DIR', tmp_path)
    assert cd._write_savvycan_csv([]) is None


def test_savvycan_csv_skips_rows_without_hex_tokens(tmp_path, monkeypatch):
    monkeypatch.setattr(cd, 'CAN_CAPTURE_DIR', tmp_path)
    rows = [
        {'ts': time.time(), 'raw': 'log line, no hex', 'hex_tokens': []},
    ]
    fname = cd._write_savvycan_csv(rows)
    # File still created (header), no data rows.
    content = (tmp_path / fname).read_text()
    assert content.strip() == cd.SAVVYCAN_HEADER


# ── allowlist (defence-in-depth check) ────────────────────────────────

def test_allowed_commands_are_exactly_four():
    """The cockpit-side allowlist in web_dashboard_handlers matches this
    set verbatim — any drift would let an unknown command leak through."""
    assert {
        'discover_ecus', 'list_services', 'dump_dids', 'fuzz_range',
    } == cd.ALLOWED_COMMANDS

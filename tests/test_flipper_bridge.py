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


# ── Hardware probe / add-on detection ─────────────────────────────────

def test_probe_hardware_returns_none_when_flipper_offline():
    flipper = MagicMock()
    flipper.connected = False
    hw = fb.probe_hardware(flipper)
    assert hw['module'] == 'none'
    assert hw['capabilities'] == []
    assert 'offline' in hw['detail'].lower()


def test_probe_hardware_detects_marauder_via_banner():
    flipper = MagicMock()
    flipper.connected = True
    flipper.send_command = MagicMock(return_value=(True, 'ESP32-Marauder v1.2.3'))
    hw = fb.probe_hardware(flipper)
    assert hw['module'] == 'wifi'
    assert 'scan_ap' in hw['capabilities']
    assert 'pwnagotchi_passive' in hw['capabilities']


def test_probe_hardware_detects_cc1101():
    flipper = MagicMock()
    flipper.connected = True
    # First i2c probe returns nothing useful; second subghz probe matches.
    flipper.send_command = MagicMock(side_effect=[
        (True, 'i2c: no devices found'),
        (True, 'CC1101 chipid 0x14 partnum 0x00'),
    ])
    hw = fb.probe_hardware(flipper)
    assert hw['module'] == 'subghz'
    assert 'freq_analyzer' in hw['capabilities']
    assert 'replay' in hw['capabilities']


def test_probe_hardware_returns_none_when_no_signature():
    flipper = MagicMock()
    flipper.connected = True
    flipper.send_command = MagicMock(return_value=(True, 'unrelated reply'))
    hw = fb.probe_hardware(flipper)
    assert hw['module'] == 'none'
    assert hw['capabilities'] == []


def test_module_capabilities_listed_for_every_module():
    # Per TASK 2.1 the capability list shape is fixed. Guard the contract.
    assert set(fb.MODULE_CAPABILITIES['wifi']) >= {
        'scan_ap', 'scan_sta', 'ble_scan', 'packet_monitor',
        'probe_capture', 'pwnagotchi_passive'}
    assert set(fb.MODULE_CAPABILITIES['subghz']) >= {
        'freq_analyzer', 'raw_capture', 'read_protocol', 'replay'}
    assert fb.MODULE_CAPABILITIES['none'] == []


def test_looks_like_marauder_matches_known_banners():
    assert fb._looks_like_marauder('ESP32-Marauder')
    assert fb._looks_like_marauder('Marauder v2')
    assert not fb._looks_like_marauder('')
    assert not fb._looks_like_marauder('random reply')


def test_looks_like_cc1101_matches_subghz_info():
    assert fb._looks_like_cc1101('Radio: CC1101')
    assert fb._looks_like_cc1101('chipid: 0x14, partnum 0x00')
    assert not fb._looks_like_cc1101('no radio')


def test_publish_hardware_publishes_retained():
    mqtt = MagicMock()
    fb.publish_hardware(mqtt, {'module': 'none', 'capabilities': []})
    args, kwargs = mqtt.publish.call_args
    assert args[0] == 'drifter/flipper/hardware'
    assert kwargs.get('retain') is True


def test_get_hardware_state_returns_defensive_copy():
    fb.hardware_state.update({'module': 'wifi', 'capabilities': ['scan_ap']})
    snapshot = fb.get_hardware_state()
    snapshot['module'] = 'mutated'
    assert fb.hardware_state['module'] == 'wifi'


def test_audit_allowlist_present_false_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(fb, '_AUDIT_TARGETS_PATH', tmp_path / 'absent.yaml')
    assert fb._audit_allowlist_present() is False


def test_audit_allowlist_present_true_with_entries(tmp_path, monkeypatch):
    path = tmp_path / 'audit_targets.yaml'
    path.write_text('networks:\n  - ssid: HOME_NET\n')
    monkeypatch.setattr(fb, '_AUDIT_TARGETS_PATH', path)
    assert fb._audit_allowlist_present() is True


def test_audit_allowlist_present_false_when_empty(tmp_path, monkeypatch):
    path = tmp_path / 'audit_targets.yaml'
    path.write_text('networks: []\n')
    monkeypatch.setattr(fb, '_AUDIT_TARGETS_PATH', path)
    assert fb._audit_allowlist_present() is False


# ── Wi-Fi passive command dispatch ────────────────────────────────────

def test_do_wifi_command_blocks_when_module_not_attached(monkeypatch):
    monkeypatch.setitem(fb.hardware_state, 'module', 'none')
    flipper = MagicMock()
    mqtt = MagicMock()
    fb._do_wifi_command(flipper, mqtt, 'wifi_scan_ap', {})
    payload = json.loads(mqtt.publish.call_args_list[-1].args[1])
    assert payload['success'] is False
    assert 'wifi module not attached' in payload['response']


def test_do_wifi_command_publishes_on_per_topic_when_attached(monkeypatch, tmp_path):
    monkeypatch.setitem(fb.hardware_state, 'module', 'wifi')
    # Marauder is attached → CLI passes through to flipper.send_command.
    flipper = MagicMock()
    flipper.send_command = MagicMock(return_value=(True, 'AP1\nAP2'))
    mqtt = MagicMock()
    fb._do_wifi_command(flipper, mqtt, 'wifi_scan_ap', {})
    topics = [c.args[0] for c in mqtt.publish.call_args_list]
    assert 'drifter/flipper/wifi/aps' in topics


def test_do_wifi_pwnagotchi_blocked_when_allowlist_empty(monkeypatch, tmp_path):
    monkeypatch.setitem(fb.hardware_state, 'module', 'wifi')
    monkeypatch.setattr(fb, '_AUDIT_TARGETS_PATH', tmp_path / 'absent.yaml')
    flipper = MagicMock()
    mqtt = MagicMock()
    fb._do_wifi_command(flipper, mqtt, 'pwnagotchi_passive', {})
    payload = json.loads(mqtt.publish.call_args_list[-1].args[1])
    assert payload['success'] is False
    assert 'allowlist' in payload['response'].lower()


# ── Sub-GHz preset dispatch ──────────────────────────────────────────

def test_do_subghz_preset_blocks_when_module_not_attached(monkeypatch):
    monkeypatch.setitem(fb.hardware_state, 'module', 'none')
    flipper = MagicMock()
    mqtt = MagicMock()
    fb._do_subghz_preset(flipper, mqtt, 'freq_analyzer', {})
    payload = json.loads(mqtt.publish.call_args_list[-1].args[1])
    assert payload['success'] is False
    assert 'subghz module not attached' in payload['response']


def test_do_subghz_preset_freq_analyzer_invokes_cli(monkeypatch):
    monkeypatch.setitem(fb.hardware_state, 'module', 'subghz')
    flipper = MagicMock()
    flipper.send_command = MagicMock(return_value=(True, 'sweep done'))
    mqtt = MagicMock()
    fb._do_subghz_preset(flipper, mqtt, 'freq_analyzer', {})
    flipper.send_command.assert_called_with('subghz_freq_analyzer')
    topics = [c.args[0] for c in mqtt.publish.call_args_list]
    assert 'drifter/flipper/subghz/sweep' in topics


def test_do_subghz_preset_raw_capture_enqueues_classification(monkeypatch, tmp_path):
    monkeypatch.setattr(fb, 'FLIPPER_CAPTURE_DIR', tmp_path)
    monkeypatch.setitem(fb.hardware_state, 'module', 'subghz')
    flipper = MagicMock()
    flipper.send_command = MagicMock(
        return_value=(True, 'RAW_Data: 244 -732 244 -488'))
    mqtt = MagicMock()
    fb._do_subghz_preset(flipper, mqtt, 'raw_capture', {'freq_mhz': 433.92})
    topics = [c.args[0] for c in mqtt.publish.call_args_list]
    # Both the per-topic capture publish AND the URH-NG classification
    # enqueue should land on the bus.
    assert 'drifter/rf/classification' in topics


def test_do_subghz_preset_read_protocol_requires_capture_id(monkeypatch):
    monkeypatch.setitem(fb.hardware_state, 'module', 'subghz')
    flipper = MagicMock()
    mqtt = MagicMock()
    fb._do_subghz_preset(flipper, mqtt, 'read_protocol', {})
    payload = json.loads(mqtt.publish.call_args_list[-1].args[1])
    assert payload['success'] is False
    assert 'capture_id' in payload['response']


def test_wifi_passive_commands_omit_active_attacks():
    # TASK 2.2 contract: passive only. DEAUTH/BEACON/EVIL must NOT be
    # surfaced via the cockpit command set.
    keys = set(fb.WIFI_PASSIVE_COMMANDS.keys())
    for forbidden in ('deauth', 'beacon_spam', 'evil_twin'):
        assert forbidden not in keys

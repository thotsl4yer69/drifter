# tests/test_hid.py
"""Tests for the Rubber Ducky / BadUSB backend (Stage 5: Flipper backend).

Covers:
  * hid_ducky compiler — STRING/STRINGLN, modifier combos, DELAY/DEFAULTDELAY,
    REPEAT, and unknown-token HARD-FAIL (no silent skip).
  * hid_inject ARM→CONFIRM→RUN state machine — happy path, >60s expiry reject,
    wrong-id reject, double-RUN single-shot prevention, no upload→inject path
    that skips CONFIRM, native backend reports not-configured on this host.

Pure / mocked — no hardware, no UDC, no Flipper, no keystrokes injected.
"""
import json
import sys
import time
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, 'src')

import hid_ducky as ducky
import hid_gadget as gadget
import hid_inject as hi
import hid_boot as boot
import diagnose


# ═══════════════════════════════════════════════════════════════════
#  Compiler
# ═══════════════════════════════════════════════════════════════════

def test_compile_string_emits_down_up_per_char():
    p = ducky.compile_ducky('STRING abc')
    # 3 chars → 3 key-down + 3 key-up = 6 reports, 3 keystrokes.
    assert p.keystrokes == 3
    assert p.report_count() == 6


def test_compile_string_shifted_char_sets_shift_modifier():
    p = ducky.compile_ducky('STRING A')
    # First report is the key-down. 'A' = shift + usage 0x04 ('a').
    down, _ = p.reports[0]
    assert down[0] == ducky.MOD_LSHIFT
    assert down[2] == 0x04


def test_compile_stringln_appends_enter():
    base = ducky.compile_ducky('STRING ab')
    withln = ducky.compile_ducky('STRINGLN ab')
    # STRINGLN adds a trailing newline → one extra keystroke (ENTER).
    assert withln.keystrokes == base.keystrokes + 1


def test_compile_modifier_combo_gui_r():
    p = ducky.compile_ducky('GUI r')
    down, _ = p.reports[0]
    assert down[0] == ducky.MOD_LGUI
    assert down[2] == 0x15  # 'r'


def test_compile_ctrl_alt_delete_combines_modifiers():
    p = ducky.compile_ducky('CTRL ALT DELETE')
    down, _ = p.reports[0]
    assert down[0] == (ducky.MOD_LCTRL | ducky.MOD_LALT)
    assert down[2] == 0x4C  # DELETE


def test_compile_bare_named_key_enter():
    p = ducky.compile_ducky('ENTER')
    down, _ = p.reports[0]
    assert down[2] == 0x28


def test_compile_delay_emits_marker_no_report():
    p = ducky.compile_ducky('DELAY 250')
    # One delay marker (report is None), no key-down/up.
    assert p.report_count() == 0
    assert p.reports == [(None, 250)]


def test_compile_defaultdelay_recorded_and_applied():
    p = ducky.compile_ducky('DEFAULTDELAY 40\nSTRING a')
    assert p.default_delay_ms == 40
    # The last report of the STRING instruction carries the default delay.
    assert p.reports[-1][1] == 40


def test_compile_repeat_reemits_previous_instruction():
    p = ducky.compile_ducky('STRING ab\nREPEAT 2')
    # Original STRING = 2 keystrokes; REPEAT 2 re-emits twice → +4 = 6 total.
    assert p.keystrokes == 6


def test_compile_unknown_token_hard_fails_with_line():
    with pytest.raises(ducky.DuckyParseError) as exc:
        ducky.compile_ducky('STRING ok\nFOOBAR baz')
    assert exc.value.line == 2


def test_compile_unmappable_char_hard_fails_no_silent_skip():
    with pytest.raises(ducky.DuckyParseError):
        ducky.compile_ducky('STRING café')  # 'é' not in us layout


def test_compile_bad_delay_arg_hard_fails():
    with pytest.raises(ducky.DuckyParseError):
        ducky.compile_ducky('DELAY notanumber')


def test_compile_repeat_with_no_prior_instruction_fails():
    with pytest.raises(ducky.DuckyParseError):
        ducky.compile_ducky('REPEAT 3')


def test_compile_rejects_non_us_layout():
    with pytest.raises(ducky.DuckyParseError):
        ducky.compile_ducky('STRING a', layout='de')


def test_rem_is_comment_not_keystroke():
    p = ducky.compile_ducky('REM this is a note\nSTRING a')
    assert p.keystrokes == 1


def test_sha256_source_stable():
    assert ducky.sha256_source('STRING a') == ducky.sha256_source('STRING a')
    assert ducky.sha256_source('STRING a') != ducky.sha256_source('STRING b')


# ═══════════════════════════════════════════════════════════════════
#  Native backend — refusal path (Stage 5 never reports ready)
# ═══════════════════════════════════════════════════════════════════

def test_native_status_never_ready_on_this_host():
    ns = hi.native_status()
    assert ns['ready'] is False
    assert 'boot profile' in ns['reason'] or 'stage 6' in ns['reason']


def test_native_status_reports_host_dr_mode(monkeypatch):
    monkeypatch.setattr(hi, 'read_dr_mode', lambda: 'host')
    ns = hi.native_status()
    assert ns['dr_mode'] == 'host'
    assert ns['configured'] is False
    assert ns['boot_profile'] == 'host'
    assert ns['ready'] is False


# ═══════════════════════════════════════════════════════════════════
#  State machine — ARM → CONFIRM → RUN
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def _clear_pending():
    hi.pending_confirms.clear()
    yield
    hi.pending_confirms.clear()


def _store_payload(monkeypatch, tmp_path, payload_id='ducky-1', script='STRING hi',
                   layout='us'):
    monkeypatch.setattr(hi, 'HID_PAYLOAD_DIR', tmp_path)
    (tmp_path / f'{payload_id}.txt').write_text(script, encoding='utf-8')
    (tmp_path / f'{payload_id}.meta.json').write_text(
        json.dumps({'id': payload_id, 'target_layout': layout}), encoding='utf-8')


def _machine_with_fire():
    fired = []

    def fake_fire(payload_id, script, arm_id):
        fired.append((payload_id, arm_id))
        return True, 'fired'

    m = hi.HidStateMachine(MagicMock(), flipper_fire=fake_fire)
    return m, fired


def test_arm_flipper_stores_pending_nothing_fired(monkeypatch, tmp_path):
    _store_payload(monkeypatch, tmp_path)
    m, fired = _machine_with_fire()
    m.handle({'command': 'hid_arm', 'payload_id': 'ducky-1',
              'backend': 'flipper', 'peer': '10.42.0.7'})
    # ARM stored a pending entry. Crucially, NOTHING was fired.
    assert len(hi.pending_confirms) == 1
    assert fired == []


def test_arm_confirm_run_happy_path_fires_once(monkeypatch, tmp_path):
    _store_payload(monkeypatch, tmp_path)
    m, fired = _machine_with_fire()
    m.handle({'command': 'hid_arm', 'payload_id': 'ducky-1',
              'backend': 'flipper', 'peer': '10.42.0.7'})
    arm_id = next(iter(hi.pending_confirms))
    m.handle({'command': 'hid_confirm', 'id': arm_id, 'peer': '10.42.0.7'})
    # RUN fired exactly once, and the pending entry is popped (single-shot).
    assert len(fired) == 1
    assert fired[0][1] == arm_id
    assert arm_id not in hi.pending_confirms


def test_confirm_after_60s_expiry_rejected(monkeypatch, tmp_path):
    _store_payload(monkeypatch, tmp_path)
    m, fired = _machine_with_fire()
    m.handle({'command': 'hid_arm', 'payload_id': 'ducky-1',
              'backend': 'flipper', 'peer': '10.42.0.7'})
    arm_id = next(iter(hi.pending_confirms))
    # Age the pending entry past the 60s window.
    hi.pending_confirms[arm_id]['ts'] = time.time() - 61
    m.handle({'command': 'hid_confirm', 'id': arm_id, 'peer': '10.42.0.7'})
    # Nothing fired; entry expired out.
    assert fired == []
    assert arm_id not in hi.pending_confirms


def test_confirm_wrong_id_rejected(monkeypatch, tmp_path):
    _store_payload(monkeypatch, tmp_path)
    m, fired = _machine_with_fire()
    m.handle({'command': 'hid_arm', 'payload_id': 'ducky-1',
              'backend': 'flipper', 'peer': '10.42.0.7'})
    m.handle({'command': 'hid_confirm', 'id': 'arm-nope', 'peer': '10.42.0.7'})
    assert fired == []
    # The genuine pending entry is untouched.
    assert len(hi.pending_confirms) == 1


def test_double_run_prevented_single_shot(monkeypatch, tmp_path):
    _store_payload(monkeypatch, tmp_path)
    m, fired = _machine_with_fire()
    m.handle({'command': 'hid_arm', 'payload_id': 'ducky-1',
              'backend': 'flipper', 'peer': '10.42.0.7'})
    arm_id = next(iter(hi.pending_confirms))
    m.handle({'command': 'hid_confirm', 'id': arm_id, 'peer': '10.42.0.7'})
    # A replayed confirm with the same id must NOT re-fire (no replay
    # without re-ARM).
    m.handle({'command': 'hid_confirm', 'id': arm_id, 'peer': '10.42.0.7'})
    assert len(fired) == 1


def test_no_upload_to_inject_path_skips_confirm(monkeypatch, tmp_path):
    """There is no command that fires a payload without an ARM+CONFIRM.

    Sending hid_confirm for a payload that was never armed fires nothing.
    The only injection entry point is _run, reachable solely from _confirm
    on a genuine pending id within the window."""
    _store_payload(monkeypatch, tmp_path)
    m, fired = _machine_with_fire()
    # No ARM. Try to confirm a fabricated id straight away.
    m.handle({'command': 'hid_confirm', 'id': 'arm-fabricated',
              'peer': '10.42.0.7'})
    assert fired == []


def test_arm_native_rejected_never_armed(monkeypatch, tmp_path):
    _store_payload(monkeypatch, tmp_path)
    m, fired = _machine_with_fire()
    m.handle({'command': 'hid_arm', 'payload_id': 'ducky-1',
              'backend': 'native', 'peer': '10.42.0.7'})
    # Native is not configured this stage → ARM rejected, nothing pending.
    assert len(hi.pending_confirms) == 0
    assert fired == []


def test_arm_bad_backend_rejected(monkeypatch, tmp_path):
    _store_payload(monkeypatch, tmp_path)
    m, fired = _machine_with_fire()
    m.handle({'command': 'hid_arm', 'payload_id': 'ducky-1',
              'backend': 'bogus', 'peer': '10.42.0.7'})
    assert len(hi.pending_confirms) == 0


def test_arm_missing_payload_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(hi, 'HID_PAYLOAD_DIR', tmp_path)
    m, fired = _machine_with_fire()
    m.handle({'command': 'hid_arm', 'payload_id': 'ducky-absent',
              'backend': 'flipper', 'peer': '10.42.0.7'})
    assert len(hi.pending_confirms) == 0


def test_arm_uncompilable_payload_rejected(monkeypatch, tmp_path):
    _store_payload(monkeypatch, tmp_path, script='FOOBAR x')
    m, fired = _machine_with_fire()
    m.handle({'command': 'hid_arm', 'payload_id': 'ducky-1',
              'backend': 'flipper', 'peer': '10.42.0.7'})
    # A payload that does not compile cannot be armed.
    assert len(hi.pending_confirms) == 0


def test_cancel_pops_pending_no_fire(monkeypatch, tmp_path):
    _store_payload(monkeypatch, tmp_path)
    m, fired = _machine_with_fire()
    m.handle({'command': 'hid_arm', 'payload_id': 'ducky-1',
              'backend': 'flipper', 'peer': '10.42.0.7'})
    arm_id = next(iter(hi.pending_confirms))
    m.handle({'command': 'hid_cancel', 'id': arm_id, 'peer': '10.42.0.7'})
    assert arm_id not in hi.pending_confirms
    assert fired == []


def test_load_payload_path_traversal_guarded(monkeypatch, tmp_path):
    monkeypatch.setattr(hi, 'HID_PAYLOAD_DIR', tmp_path)
    assert hi.load_payload('../etc/passwd') is None
    assert hi.load_payload('a/b') is None
    assert hi.load_payload('') is None


def test_audit_writes_jsonl_and_publishes(monkeypatch, tmp_path):
    monkeypatch.setattr(hi, 'HID_AUDIT_LOG', tmp_path / 'hid_audit.log')
    mqtt = MagicMock()
    hi.audit(mqtt, 'ARM', '10.42.0.7', id='arm-x', payload_id='ducky-1')
    line = (tmp_path / 'hid_audit.log').read_text().strip()
    rec = json.loads(line)
    assert rec['event'] == 'ARM'
    assert rec['peer'] == '10.42.0.7'
    mqtt.publish.assert_called_once()
    topic = mqtt.publish.call_args[0][0]
    assert topic == 'drifter/hid/audit'
    # retained=false on the audit publish.
    assert mqtt.publish.call_args.kwargs.get('retain') is False


def test_run_emits_run_audit_event(monkeypatch, tmp_path):
    _store_payload(monkeypatch, tmp_path)
    monkeypatch.setattr(hi, 'HID_AUDIT_LOG', tmp_path / 'hid_audit.log')
    m, fired = _machine_with_fire()
    m.mqtt = MagicMock()
    m.handle({'command': 'hid_arm', 'payload_id': 'ducky-1',
              'backend': 'flipper', 'peer': '10.42.0.7'})
    arm_id = next(iter(hi.pending_confirms))
    m.handle({'command': 'hid_confirm', 'id': arm_id, 'peer': '10.42.0.7'})
    events = [json.loads(l)['event']
              for l in (tmp_path / 'hid_audit.log').read_text().splitlines()]
    assert 'ARM' in events
    assert 'CONFIRM' in events
    assert 'RUN' in events


def test_default_flipper_fire_relays_high_risk_through_bridge(monkeypatch, tmp_path):
    """The default Flipper fire path goes through drifter/flipper/command
    (storage write + badusb/loader) which the bridge classifies HIGH —
    defence in depth. It never writes /dev/hidg0 or bypasses the bridge."""
    mqtt = MagicMock()
    m = hi.HidStateMachine(mqtt)  # use the real _default_flipper_fire
    ok, detail = m._default_flipper_fire('ducky-1', 'STRING hi', 'arm-abc')
    assert ok is True
    topics = [c[0][0] for c in mqtt.publish.call_args_list]
    assert all(t == 'drifter/flipper/command' for t in topics)
    payloads = [json.loads(c[0][1]) for c in mqtt.publish.call_args_list]
    cmds = [p.get('command', '') for p in payloads]
    assert any(c.startswith('storage write /ext/badusb/') for c in cmds)
    assert any('badusb' in c.lower() or c.startswith('loader open') for c in cmds)


# ═══════════════════════════════════════════════════════════════════
#  Stage 6 — hid_gadget native lifecycle (host-mode refusal path only)
# ═══════════════════════════════════════════════════════════════════

def _host_controller(tmp_path):
    """A GadgetController whose dr_mode reads 'host' (this bench's reality).

    Every gadget op MUST hard-refuse. root is a temp dir so even if a
    refusal regression slipped through, the real configfs is never touched.
    """
    return gadget.GadgetController(
        root=tmp_path / 'usb_gadget',
        hidg_path=tmp_path / 'hidg0',
        udc_dir=tmp_path / 'udc',
        dr_mode_reader=lambda: 'host',
    )


def test_gadget_mode_ok_false_on_host(tmp_path):
    ctrl = _host_controller(tmp_path)
    ok, reason = ctrl.gadget_mode_ok()
    assert ok is False
    assert 'host' in reason


def test_gadget_create_refuses_on_host(tmp_path):
    ctrl = _host_controller(tmp_path)
    with pytest.raises(gadget.GadgetError):
        ctrl.create()
    # Nothing was created on the temp fs.
    assert not (tmp_path / 'usb_gadget' / gadget.GADGET_NAME).exists()


def test_gadget_bind_refuses_on_host(tmp_path):
    ctrl = _host_controller(tmp_path)
    with pytest.raises(gadget.GadgetError):
        ctrl.bind()


def test_gadget_unbind_refuses_on_host(tmp_path):
    ctrl = _host_controller(tmp_path)
    with pytest.raises(gadget.GadgetError):
        ctrl.unbind()


def test_gadget_write_reports_refuses_on_host(tmp_path):
    ctrl = _host_controller(tmp_path)
    frame = bytes([0, 0, 0x04, 0, 0, 0, 0, 0])
    with pytest.raises(gadget.GadgetError):
        ctrl.write_reports([(frame, 0)])
    # No /dev/hidg0-equivalent file was written.
    assert not (tmp_path / 'hidg0').exists()


def test_gadget_teardown_refuses_on_host(tmp_path):
    ctrl = _host_controller(tmp_path)
    with pytest.raises(gadget.GadgetError):
        ctrl.teardown()


def test_gadget_status_readonly_host(tmp_path):
    ctrl = _host_controller(tmp_path)
    st = ctrl.status()
    assert st['dr_mode'] == 'host'
    assert st['configured'] is False
    assert st['boot_profile'] == 'host'
    assert st['bound'] is False


def test_gadget_ready_false_on_host(tmp_path):
    ctrl = _host_controller(tmp_path)
    ready, reason = gadget.gadget_ready(ctrl)
    assert ready is False
    assert 'host' in reason


def test_native_run_blocked_on_host(monkeypatch, tmp_path):
    """A native RUN on this host-mode bench must fail closed — nothing typed.

    We force native ARM to be accepted (readiness mocked ready so the entry
    is stored) but the gadget controller still reads dr_mode=host at RUN and
    hard-refuses, so the result is a BLOCKED event with no frames written."""
    _store_payload(monkeypatch, tmp_path)
    # Force ARM-readiness so a native entry gets stored, but keep the real
    # gadget controller reading host so the RUN op hard-refuses.
    monkeypatch.setattr(hi, 'native_status',
                        lambda: {'ready': True, 'dr_mode': 'host',
                                 'reason': 'mocked-ready'})
    host_ctrl = gadget.GadgetController(
        root=tmp_path / 'usb_gadget',
        hidg_path=tmp_path / 'hidg0',
        udc_dir=tmp_path / 'udc',
        dr_mode_reader=lambda: 'host',
    )
    monkeypatch.setattr(hi, '_gadget_controller', lambda: host_ctrl)
    monkeypatch.setattr(hi, 'HID_AUDIT_LOG', tmp_path / 'hid_audit.log')
    m = hi.HidStateMachine(MagicMock())
    m.handle({'command': 'hid_arm', 'payload_id': 'ducky-1',
              'backend': 'native', 'peer': '10.42.0.7'})
    assert len(hi.pending_confirms) == 1
    arm_id = next(iter(hi.pending_confirms))
    m.handle({'command': 'hid_confirm', 'id': arm_id, 'peer': '10.42.0.7'})
    events = [json.loads(l)['event']
              for l in (tmp_path / 'hid_audit.log').read_text().splitlines()]
    assert 'BLOCKED' in events
    # No HID device file was written on the host-mode bench.
    assert not (tmp_path / 'hidg0').exists()


def test_arm_native_rejected_when_not_ready(monkeypatch, tmp_path):
    """With the real native_status (host bench) a native ARM is refused."""
    _store_payload(monkeypatch, tmp_path)
    monkeypatch.setattr(hi, 'read_dr_mode', lambda: 'host')
    m, fired = _machine_with_fire()
    m.handle({'command': 'hid_arm', 'payload_id': 'ducky-1',
              'backend': 'native', 'peer': '10.42.0.7'})
    assert len(hi.pending_confirms) == 0
    assert fired == []


# ═══════════════════════════════════════════════════════════════════
#  Stage 6 — enable-native / disable-native boot-profile editor
#  (TEMP config path only — NEVER the real /boot config)
# ═══════════════════════════════════════════════════════════════════

_SAMPLE_CONFIG = (
    "# Drifter Pi 5 config\n"
    "dtoverlay=dwc2,dr_mode=host\n"
    "arm_64bit=1\n"
)


def _temp_config(tmp_path):
    cfg = tmp_path / 'config.txt'
    cfg.write_text(_SAMPLE_CONFIG, encoding='utf-8')
    return cfg


def test_enable_native_writes_fenced_block(tmp_path):
    cfg = _temp_config(tmp_path)
    changed = boot.enable_native(cfg, check_root=False)
    assert changed is True
    text = cfg.read_text()
    assert boot.BLOCK_START in text
    assert boot.BLOCK_END in text
    assert 'dr_mode=peripheral' in text
    assert 'modules-load=dwc2,libcomposite' in text


def test_enable_native_does_not_reboot(tmp_path, monkeypatch):
    """enable_native is a pure file edit — it never invokes a reboot.

    Guard: os.system / subprocess must not be called by the editor path."""
    cfg = _temp_config(tmp_path)
    called = []
    monkeypatch.setattr(boot.os, 'system',
                        lambda *a, **k: called.append(a) or 0)
    boot.enable_native(cfg, check_root=False)
    assert called == []


def test_enable_native_idempotent_on_rerun(tmp_path):
    cfg = _temp_config(tmp_path)
    boot.enable_native(cfg, check_root=False)
    first = cfg.read_text()
    # Re-run: must be a no-op (returns False) and the file is unchanged —
    # exactly ONE managed block, never duplicated.
    changed = boot.enable_native(cfg, check_root=False)
    assert changed is False
    second = cfg.read_text()
    assert first == second
    assert second.count(boot.BLOCK_START) == 1
    assert second.count(boot.BLOCK_END) == 1


def test_disable_native_removes_block(tmp_path):
    cfg = _temp_config(tmp_path)
    boot.enable_native(cfg, check_root=False)
    removed = boot.disable_native(cfg, check_root=False)
    assert removed is True
    text = cfg.read_text()
    assert boot.BLOCK_START not in text
    assert boot.BLOCK_END not in text
    assert 'dr_mode=peripheral' not in text
    # Original non-managed lines survive.
    assert 'arm_64bit=1' in text


def test_disable_native_idempotent_when_absent(tmp_path):
    cfg = _temp_config(tmp_path)
    removed = boot.disable_native(cfg, check_root=False)
    assert removed is False
    assert cfg.read_text() == _SAMPLE_CONFIG


def test_enable_then_disable_round_trips(tmp_path):
    cfg = _temp_config(tmp_path)
    original = cfg.read_text()
    boot.enable_native(cfg, check_root=False)
    boot.disable_native(cfg, check_root=False)
    # The managed block is gone; the meaningful original lines are intact.
    final = cfg.read_text()
    assert 'arm_64bit=1' in final
    assert boot.BLOCK_START not in final
    assert original.splitlines()[0] in final


def test_enable_native_requires_root_by_default(tmp_path, monkeypatch):
    cfg = _temp_config(tmp_path)
    monkeypatch.setattr(boot.os, 'geteuid', lambda: 1000)
    with pytest.raises(boot.BootProfileError):
        boot.enable_native(cfg)  # check_root defaults True
    # Refused before any write — the block was NOT added.
    assert boot.BLOCK_START not in cfg.read_text()


def test_enable_native_missing_config_errors(tmp_path):
    with pytest.raises(boot.BootProfileError):
        boot.enable_native(tmp_path / 'nope.txt', check_root=False)


def test_cli_main_enable_disable_temp_config(tmp_path, capsys, monkeypatch):
    cfg = _temp_config(tmp_path)
    monkeypatch.setattr(boot.os, 'geteuid', lambda: 0)
    rc = boot.main(['enable-native', '--config', str(cfg)])
    assert rc == 0
    out = capsys.readouterr().out
    # The reboot warning must name the USB-C power-port hazard.
    assert 'REBOOT' in out
    assert 'POWER INPUT' in out
    assert boot.BLOCK_START in cfg.read_text()
    rc = boot.main(['disable-native', '--config', str(cfg)])
    assert rc == 0
    assert boot.BLOCK_START not in cfg.read_text()


# ═══════════════════════════════════════════════════════════════════
#  Stage 6 — diagnose warns on dr_mode != host while in drive mode
# ═══════════════════════════════════════════════════════════════════

def test_diagnose_warns_dr_mode_peripheral_in_drive(monkeypatch):
    monkeypatch.setattr(diagnose, '_active_mode', lambda: 'drive')
    monkeypatch.setattr(gadget, 'read_dr_mode', lambda: 'peripheral')
    r = diagnose.check_hid_dr_mode()
    assert r.ok is False
    assert r.fatal is False  # warn-only / non-fatal
    assert 'WARNING' in r.message
    assert 'peripheral' in r.message


def test_diagnose_ok_dr_mode_host_in_drive(monkeypatch):
    monkeypatch.setattr(diagnose, '_active_mode', lambda: 'drive')
    monkeypatch.setattr(gadget, 'read_dr_mode', lambda: 'host')
    r = diagnose.check_hid_dr_mode()
    assert r.ok is True


def test_diagnose_no_warn_dr_mode_peripheral_in_foot(monkeypatch):
    """In foot mode the operator may legitimately be running native HID —
    no drive-mode warning."""
    monkeypatch.setattr(diagnose, '_active_mode', lambda: 'foot')
    monkeypatch.setattr(gadget, 'read_dr_mode', lambda: 'peripheral')
    r = diagnose.check_hid_dr_mode()
    assert r.ok is True

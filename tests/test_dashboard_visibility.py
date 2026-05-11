"""Tests for hardware-gated dashboard visibility.

The dashboard must never show ambiguous `--` placeholders for hardware
that isn't physically connected. These tests enforce:

1. The DOM contains the gating elements (tpms-rf-down card, wd-bt-row).
2. The CSS contains the rules that hide/show via body.{rf,bt,can}-down.
3. The legacy bare-em-dash placeholders that pre-dated this audit have
   been replaced with human copy.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / 'src'
DASHBOARD_HTML = (SRC / 'web_dashboard_html.py').read_text(encoding='utf-8')
OPSEC_HTML = (SRC / 'opsec_dashboard.py').read_text(encoding='utf-8')


def test_tpms_rf_down_card_present_in_dom():
    assert 'id="tpms-rf-down"' in DASHBOARD_HTML, \
        'TPMS RF-down replacement card missing from served HTML'
    assert 'tpms-rf-down-detail' in DASHBOARD_HTML
    assert 'tpms-rf-down-action' in DASHBOARD_HTML


def test_rf_down_css_hides_tpms_grid():
    assert 'body.rf-down .tpms-grid{display:none}' in DASHBOARD_HTML, \
        'body.rf-down must hide the TPMS grid'
    assert 'body.rf-down .tpms-rf-down{display:flex}' in DASHBOARD_HTML, \
        'body.rf-down must show the RF replacement card'


def test_wd_bt_row_wraps_bt_count():
    """When BT probe is disconnected, the bullet+BT count must hide as one unit
    so the layout doesn't show a dangling • between WiFi and a hidden count."""
    pattern = re.compile(
        r'class="wd-bt-row"[^>]*>.*?id="wd-bt-count"',
        re.DOTALL,
    )
    assert pattern.search(DASHBOARD_HTML), \
        'wd-bt-count must be wrapped in .wd-bt-row for body.bt-down hiding'
    assert 'body.bt-down .wd-bt-row{display:none}' in DASHBOARD_HTML


def test_wd_bt_row_has_no_inline_display_style():
    """An inline style="display:..." attribute would beat the body.bt-down
    cascade (inline specificity wins), silently breaking the hide path.
    The display:contents layout must live in the stylesheet."""
    inline = re.compile(r'class="wd-bt-row"[^>]*style="[^"]*display:')
    assert not inline.search(DASHBOARD_HTML), \
        'wd-bt-row must not have an inline display style; it defeats body.bt-down'
    assert '.wd-bt-row{display:contents}' in DASHBOARD_HTML, \
        'wd-bt-row needs display:contents via stylesheet (not inline)'


def test_apply_hw_body_classes_handles_three_gates():
    """The JS toggle helper must drive can-down, rf-down, and bt-down."""
    assert 'function applyHwBodyClasses' in DASHBOARD_HTML
    for cls in ("'can-down'", "'rf-down'", "'bt-down'"):
        assert cls in DASHBOARD_HTML, f'missing body class toggle for {cls}'


def test_no_bare_em_dash_in_known_placeholder_ids():
    """The four placeholders this audit replaced must no longer be bare em-dashes."""
    for src, ids in [
        (DASHBOARD_HTML, ['cp-drive-id', 'cp-pp-body']),
        (OPSEC_HTML, ['wardrive-status']),
    ]:
        for elem_id in ids:
            pattern = re.compile(
                rf'id="{re.escape(elem_id)}"[^>]*>\s*—\s*<',
            )
            assert not pattern.search(src), \
                f'{elem_id} still renders bare em-dash; expected human copy'


def test_human_copy_replaces_em_dashes():
    """The replacement copy must be the strings agreed in the plan."""
    assert 'no active drive' in DASHBOARD_HTML, 'cp-drive-id copy missing'
    assert 'No persistent contacts.' in DASHBOARD_HTML, 'cp-pp-body copy missing'
    assert 'awaiting wardrive...' in OPSEC_HTML, 'wardrive-status copy missing'


def test_skeleton_shimmer_class_and_keyframes_present():
    assert '.skel{' in DASHBOARD_HTML
    assert '@keyframes skel-shimmer' in DASHBOARD_HTML


def test_system_metric_cells_seeded_with_skeleton():
    """cpu/disk/mem/uptime must render skel until first watchdog payload arrives,
    so the operator can distinguish 'still loading' from 'data missing'."""
    for elem_id in ('v-cpu-temp', 'v-disk', 'v-mem', 'v-uptime'):
        pattern = re.compile(
            rf'id="{re.escape(elem_id)}"[^>]*><span class="skel"',
        )
        assert pattern.search(DASHBOARD_HTML), \
            f'{elem_id} missing initial .skel placeholder'


def test_sessions_list_uses_skeleton_not_loading_text():
    pattern = re.compile(r'id="sessions-list"[^>]*>Loading\.\.\.')
    assert not pattern.search(DASHBOARD_HTML), \
        'sessions-list should use skeleton rows, not literal "Loading..." text'
    sessions_pattern = re.compile(
        r'id="sessions-list"[^>]*>(?:<span class="skel[^"]*"></span>\s*){3}',
    )
    assert sessions_pattern.search(DASHBOARD_HTML), \
        'sessions-list should seed three .skel rows'


def test_opsec_quick_tiles_has_initial_hint():
    """Empty <div> at first paint reads as a broken UI; needs initial hint copy."""
    pattern = re.compile(
        r'id="quick-tiles"[^>]*>\s*<div class="prompt"',
    )
    assert pattern.search(OPSEC_HTML), \
        'quick-tiles must seed a "// awaiting probe set..." hint'

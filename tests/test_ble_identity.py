# tests/test_ble_identity.py
"""
MZ1312 DRIFTER — Phase 4.8.1 stable-identity tests
UNCAGED TECHNOLOGY — EST 1991
"""
from __future__ import annotations

import pytest

import ble_identity as bi


def _det(**overrides) -> dict:
    base = {
        'target': '',
        'mac': '00:25:DF:11:22:33',
        'manufacturer_id': '',
        'adv_name': '',
    }
    base.update(overrides)
    return base


def test_identity_uses_mfr_and_name_when_stable():
    ident, conf = bi.compute_identity(_det(
        manufacturer_id='0x0006', adv_name='Bose QC45 Steve',
    ))
    assert ident == 'mfr:0x0006|name:Bose QC45 Steve'
    assert conf == pytest.approx(0.9)


def test_identity_falls_back_to_mac_for_axon():
    ident, conf = bi.compute_identity(_det(
        target='axon-class', mac='00:25:DF:11:22:33',
    ))
    assert ident == 'mac:00:25:DF:11:22:33'
    assert conf == pytest.approx(0.85)
    # Phase 4.5 target name 'axon' should resolve the same.
    ident2, _ = bi.compute_identity(_det(target='axon', mac='00:25:DF:aa:bb:cc'))
    assert ident2 == 'mac:00:25:DF:aa:bb:cc'


def test_identity_uses_mac_for_tile():
    ident, conf = bi.compute_identity(_det(
        target='tile', mac='AA:BB:CC:DD:EE:FF',
    ))
    assert ident == 'mac:AA:BB:CC:DD:EE:FF'
    assert conf == pytest.approx(0.85)


def test_identity_acknowledges_weak_for_airtag():
    ident, conf = bi.compute_identity(_det(
        target='airtag', manufacturer_id='0x004C', adv_name='',
    ))
    assert ident == 'mfr:0x004C|name:anon'
    assert conf == pytest.approx(0.4)
    # find-my alias works the same when the name is missing or generic.
    ident2, conf2 = bi.compute_identity(_det(
        target='find-my', manufacturer_id='0x004C', adv_name='iPhone',
    ))
    # 'iPhone' is in GENERIC_NAMES, so branch 1 doesn't fire and the
    # airtag/find-my branch handles it with confidence 0.4.
    assert ident2 == 'mfr:0x004C|name:iPhone'
    assert conf2 == pytest.approx(0.4)


def test_generic_names_dont_anchor_identity():
    # iPhone is a generic name → should fall through to the OUI
    # fallback branch (no target match), not the strong fingerprint.
    ident, conf = bi.compute_identity(_det(
        manufacturer_id='0x004C', adv_name='iPhone',
    ))
    assert not ident.startswith('mfr:0x004C|name:iPhone')
    # Branch 5 fallback yields confidence 0.2.
    assert conf == pytest.approx(0.2)


def test_short_name_does_not_anchor_identity():
    # len(name) <= 3 fails the strong-fingerprint guard.
    ident, conf = bi.compute_identity(_det(
        manufacturer_id='0x0006', adv_name='Bo',
    ))
    assert not ident.startswith('mfr:0x0006|name:Bo')
    assert conf == pytest.approx(0.2)


def test_confidence_scores_match_branch():
    branches = [
        ({'manufacturer_id': '0x06', 'adv_name': 'unique-name'}, 0.9),
        ({'target': 'axon', 'mac': '00:25:DF:00:00:01'}, 0.85),
        ({'target': 'tile', 'mac': '11:22:33:44:55:66'}, 0.85),
        ({'target': 'airtag', 'manufacturer_id': '0x4C'}, 0.4),
        ({'target': 'mystery', 'mac': '01:02:03:04:05:06'}, 0.2),
    ]
    for d, expected in branches:
        _, conf = bi.compute_identity(_det(**d))
        assert conf == pytest.approx(expected), f"branch {d}: {conf} != {expected}"


def test_fallback_uses_oui_and_target():
    ident, conf = bi.compute_identity(_det(
        target='unknown-class', mac='AA:BB:CC:DD:EE:FF',
    ))
    assert ident == 'mac-prefix:AA:BB:CC|target:unknown-class'
    assert conf == pytest.approx(0.2)

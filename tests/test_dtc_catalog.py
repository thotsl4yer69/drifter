# tests/test_dtc_catalog.py
"""
DTC catalog: generic OBD-II base + per-vehicle overlay keyed off the active
profile. The X-Type overlay must stay behaviour-exact; any other car gets the
generic base.
"""
import sys

import pytest

sys.path.insert(0, 'src')

import dtc_catalog
import vehicle_profile as vp


@pytest.fixture(autouse=True)
def _reset_profile():
    # Applies to functions AND class methods (setup_function does not), so a
    # test that set_active()s another car can't leak into the next test file.
    vp.reload()
    yield
    vp.set_active(None)


class TestGenericDecode:
    def test_lean_bank1(self):
        e = dtc_catalog.decode_generic('P0171')
        assert e['desc'] == 'System Too Lean — Bank 1'
        assert e['severity'] == 'AMBER'
        assert e['cause'] and e['action']

    def test_cylinder_misfire(self):
        assert dtc_catalog.decode_generic('P0303')['desc'] == 'Cylinder 3 Misfire Detected'

    def test_comms_loss_is_red(self):
        e = dtc_catalog.decode_generic('U0100')
        assert e['severity'] == 'RED'
        assert 'Communication' in e['desc']

    def test_category_fallback_for_unlisted_generic(self):
        # P0abc not in the curated table -> structural category description.
        e = dtc_catalog.decode_generic('P0455')
        assert e is not None and e['desc']

    def test_manufacturer_specific_returns_none(self):
        # 2nd digit 1 = manufacturer-specific -> no portable meaning.
        assert dtc_catalog.decode_generic('P1131') is None

    def test_malformed_returns_none(self):
        assert dtc_catalog.decode_generic('P9999') is None
        assert dtc_catalog.decode_generic('') is None
        assert dtc_catalog.decode_generic(None) is None


class TestOverlaySelection:
    def test_default_profile_is_xtype_overlay(self):
        overlay = dtc_catalog.active_overlay()
        assert 'P0301' in overlay
        # X-Type-specific cause prose, not the generic wording.
        assert 'coil' in overlay['P0301']['cause'].lower()

    def test_lookup_prefers_overlay_for_xtype(self):
        # X-Type overlay wins -> its Jaguar-specific cause, behaviour-exact.
        entry = dtc_catalog.lookup('P0301')
        assert 'coil pack' in entry['cause'].lower()

    def test_lookup_generic_when_overlay_lacks_code(self):
        # P0128 is not in the X-Type overlay -> generic base.
        entry = dtc_catalog.lookup('P0128')
        assert entry is not None
        assert 'Thermostat' in entry['desc']

    def test_other_vehicle_has_no_overlay_but_gets_generic(self):
        vp.set_active({'make': 'Toyota', 'model': 'Corolla', 'engine': '1.8 I4'})
        assert dtc_catalog.active_overlay() == {}
        # Overlay-only lookup misses...
        assert dtc_catalog.lookup('P0301', generic_fallback=False) is None
        # ...but the generic base still describes it (no Jaguar-specific cause).
        entry = dtc_catalog.lookup('P0301')
        assert 'Misfire' in entry['desc']
        assert 'coil pack' not in entry['cause'].lower()

    def test_unknown_code_is_none(self):
        assert dtc_catalog.lookup('P9999') is None

# tests/test_safety_engine_profile.py
"""
safety_engine per-vehicle limits: over-rev from the profile redline and coolant
amber/red derived from the engine's normal operating high. The X-Type default
must be unchanged (98°C normal-high → 108/115).
"""
import sys

import pytest

sys.path.insert(0, 'src')

import safety_engine as se
import vehicle_profile as vp


@pytest.fixture(autouse=True)
def _reset_profile():
    vp.reload()
    se._apply_profile()
    yield
    vp.set_active(None)
    se._apply_profile()


def test_xtype_default_coolant_thresholds_unchanged():
    assert se.SAFETY_CFG['coolant_amber_c'] == 108.0
    assert se.SAFETY_CFG['coolant_red_c'] == 115.0


def test_xtype_overrev_from_redline():
    from config import REDLINE_RPM
    assert se.SAFETY_CFG['overrev_rpm'] == REDLINE_RPM


def test_coolant_thresholds_scale_with_profile():
    vp.set_active({"engine_params": {"coolant_normal_high": 105}})
    se._apply_profile()
    assert se.SAFETY_CFG['coolant_amber_c'] == 115.0   # 105 + 10
    assert se.SAFETY_CFG['coolant_red_c'] == 122.0     # 105 + 17


def test_overrev_scales_with_profile_redline():
    vp.set_active({"redline_rpm": 7200})
    se._apply_profile()
    assert se.SAFETY_CFG['overrev_rpm'] == 7200

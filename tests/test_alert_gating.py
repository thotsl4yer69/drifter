"""Powertrain/topology gating of alert_engine rules.

The rule SET must adapt to the active vehicle: full set for the shipped V6 (and
any unidentified car), combustion rules suppressed on EVs, dual-bank rules
suppressed on single-bank engines. Locks the multi-vehicle behaviour so a
future edit can't silently run V6 rules on a Tesla.
"""
import alert_engine as ae
import vehicle_profile as vp


def teardown_function(_):
    vp.set_active(None)
    vp.reload()
    ae._apply_profile()


def test_default_vehicle_runs_all_rules():
    vp.set_active(None)
    ae._apply_profile()
    assert set(ae.ACTIVE_RULES) == set(ae.ALL_RULES)


def test_ev_suppresses_combustion_rules():
    vp.set_active({"make": "Tesla", "model": "Model 3", "fuel_type": "ev"})
    ae._apply_profile()
    active = set(ae.ACTIVE_RULES)
    # only the universal (12V / DTC / TPMS) rules survive
    assert active == ae._UNIVERSAL_RULES
    # explicit spot-checks: no fuel-trim / coolant / MAF rules on an EV
    assert ae.rule_vacuum_leak_bank1 not in active
    assert ae.rule_coolant_critical not in active
    assert ae.rule_xtype_maf_degradation not in active
    # but tyre + DTC + 12V electrical still run
    assert ae.rule_tpms_low_pressure in active
    assert ae.rule_active_dtcs in active
    assert ae.rule_alternator in active


def test_inline4_suppresses_dual_bank_rules():
    vp.set_active({"make": "Toyota", "engine": "1.8 I4", "fuel_type": "petrol",
                   "cylinder_count": 4})
    ae._apply_profile()
    active = set(ae.ACTIVE_RULES)
    assert ae.rule_bank_imbalance not in active
    assert ae.rule_vacuum_leak_both not in active
    # single-bank fuel-trim rule still runs (combustion engine)
    assert ae.rule_vacuum_leak_bank1 in active


def test_v6_keeps_dual_bank_rules():
    vp.set_active({"make": "Jaguar", "engine": "2.5 V6", "fuel_type": "petrol",
                   "cylinder_count": 6})
    ae._apply_profile()
    assert ae.rule_bank_imbalance in ae.ACTIVE_RULES


def test_profile_thresholds_repoint_engine_values():
    vp.set_active({"fuel_type": "petrol", "engine": "2.0 I4",
                   "thresholds": {"overrev_rpm": 7200},
                   "engine_params": {"coolant_normal_high": 105}})
    ae._apply_profile()
    assert ae.THRESHOLDS["overrev_rpm"] == 7200
    assert ae.COOLANT_NORMAL_HIGH == 105

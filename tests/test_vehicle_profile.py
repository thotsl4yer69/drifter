"""vehicle_profile — the active-vehicle single source of truth.

Guards two things: (1) with NO active profile, every accessor returns exactly
the historical config.py value (backward compatibility — the rest of the suite
is the regression check), and (2) an overlaid profile merges correctly, with
powertrain/topology derivation for EVs, inline engines, and diesels.
"""
import json

import config
import vehicle_profile as vp


def setup_function(_):
    vp.reload()  # start each test from a clean cache


def teardown_function(_):
    vp.set_active(None)
    vp.reload()


# ── Backward compatibility: no profile == config values ──────────────────────

def test_no_profile_thresholds_equal_config(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "VEHICLE_PROFILE_FILE", tmp_path / "none.yaml")
    vp.reload()
    assert vp.thresholds() == config.THRESHOLDS
    assert vp.calibration() == config.CALIBRATION_DEFAULTS


def test_no_profile_identity_is_config_vehicle(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "VEHICLE_PROFILE_FILE", tmp_path / "none.yaml")
    vp.reload()
    ident = vp.identity()
    assert ident["make"] == config.VEHICLE_MAKE
    assert ident["model"] == config.VEHICLE_MODEL
    assert ident["year"] == config.VEHICLE_YEAR


def test_config_vehicle_is_v6_two_banks_combustion(tmp_path, monkeypatch):
    # The shipped config vehicle is a petrol V6 → 2 banks, combustion.
    monkeypatch.setattr(config, "VEHICLE_PROFILE_FILE", tmp_path / "none.yaml")
    vp.reload()
    assert vp.bank_count() == 2
    assert vp.is_combustion() is True
    assert vp.is_ev() is False


# ── Overlay + powertrain/topology derivation ─────────────────────────────────

def test_overlay_merges_thresholds_keywise():
    vp.set_active({"thresholds": {"coolant_red": 999}})
    t = vp.thresholds()
    assert t["coolant_red"] == 999          # overridden
    assert t["stft_lean_idle"] == config.THRESHOLDS["stft_lean_idle"]  # inherited


def test_overlay_none_values_ignored():
    vp.set_active({"make": None, "model": "Corolla"})
    assert vp.spec("make") == config.VEHICLE_MAKE   # None ignored → base kept
    assert vp.spec("model") == "Corolla"


def test_inline4_petrol_single_bank():
    vp.set_active({"make": "Toyota", "model": "Corolla", "engine": "1.8 I4",
                   "fuel_type": "petrol", "cylinder_count": 4})
    assert vp.bank_count() == 1
    assert vp.is_combustion() is True


def test_ev_has_no_banks_and_not_combustion():
    vp.set_active({"make": "Tesla", "model": "Model 3", "fuel_type": "ev"})
    assert vp.is_ev() is True
    assert vp.is_combustion() is False
    assert vp.bank_count() == 0


def test_hybrid_and_diesel_flags():
    vp.set_active({"fuel_type": "hybrid"})
    assert vp.is_hybrid() is True and vp.is_combustion() is True
    vp.set_active({"fuel_type": "diesel"})
    assert vp.is_diesel() is True


def test_explicit_bank_count_wins_over_derivation():
    vp.set_active({"engine": "2.5 V6", "bank_count": 1})
    assert vp.bank_count() == 1  # authoritative, not re-derived to 2


def test_display_name():
    vp.set_active({"make": "Toyota", "model": "Corolla", "year": 2015,
                   "engine": "1.8 I4"})
    assert vp.display_name() == "2015 Toyota Corolla 1.8 I4"


# ── Disk round-trip via reload() ─────────────────────────────────────────────

def test_reload_reads_active_profile_file(tmp_path, monkeypatch):
    prof = tmp_path / "vehicle.yaml"
    prof.write_text(json.dumps({
        "make": "Honda", "model": "Civic", "year": 2018, "engine": "1.5 I4 turbo",
        "fuel_type": "petrol", "cylinder_count": 4,
        "thresholds": {"overrev_rpm": 6800},
    }))
    monkeypatch.setattr(config, "VEHICLE_PROFILE_FILE", prof)
    vp.reload()
    assert vp.spec("make") == "Honda"
    assert vp.bank_count() == 1
    assert vp.thresholds()["overrev_rpm"] == 6800
    # a threshold the profile didn't set still falls through to config
    assert vp.thresholds()["coolant_red"] == config.THRESHOLDS["coolant_red"]

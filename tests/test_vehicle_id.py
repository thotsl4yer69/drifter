"""vehicle_id profile resolution — deterministic VIN decode + precedence.

The LLM path (generate_profile) is monkeypatched out so these stay offline.
"""
import vehicle_id


def test_decoded_seed_from_vin():
    seed = vehicle_id._decoded_seed("SAJEA51D44XD39283")
    assert seed["make"] == "Jaguar"
    assert seed["year"] == 2004


def test_resolve_no_vin_falls_back_to_defaults():
    prof = vehicle_id.resolve_profile(None)
    assert prof["vin"] == "unknown"
    assert prof["source"] == "defaults"
    # The unidentified fallback must be GENERIC, not the X-Type's numbers.
    assert prof["make"] == "Unknown"
    assert prof["make"] != "Jaguar"


def test_config_vehicle_defaults_are_generic():
    import config
    assert config.VEHICLE_DEFAULTS["make"] == "Unknown"
    assert config.VEHICLE_DEFAULTS["fuel_type"] == "petrol"
    assert config.VEHICLE_DEFAULTS.get("known_issues") == []


def test_resolve_unknown_vin_uses_deterministic_decode(monkeypatch):
    # No local profile, no AI — a valid VIN still yields make/year offline.
    monkeypatch.setattr(vehicle_id, "load_profile", lambda vin: None)
    monkeypatch.setattr(vehicle_id, "generate_profile", lambda vin: None)
    prof = vehicle_id.resolve_profile("5YJ3E1EA7JF000000")  # Tesla Model 3, 2018
    assert prof["make"] == "Tesla"
    assert prof["year"] == 2018
    assert prof["source"] == "vin-decoded"


def test_local_profile_wins_over_decode(monkeypatch):
    # A hand-authored local YAML can correct/extend the decode and is trusted.
    monkeypatch.setattr(vehicle_id, "load_profile",
                        lambda vin: {"make": "Custom", "model": "Widget",
                                     "year": 1999, "source": "local"})
    prof = vehicle_id.resolve_profile("5YJ3E1EA7JF000000")
    assert prof["make"] == "Custom"     # local overrides deterministic decode
    assert prof["model"] == "Widget"
    assert prof["source"] == "local"


def test_ai_profile_does_not_override_vin_make_year(monkeypatch):
    # An AI guess must not override the VIN-authoritative make/year.
    monkeypatch.setattr(vehicle_id, "load_profile", lambda vin: None)
    monkeypatch.setattr(vehicle_id, "generate_profile",
                        lambda vin: {"make": "WRONG", "year": 1900,
                                     "engine": "2.0 I4", "source": "ai_generated"})
    prof = vehicle_id.resolve_profile("5YJ3E1EA7JF000000")
    assert prof["make"] == "Tesla"      # deterministic decode wins
    assert prof["year"] == 2018
    assert prof["engine"] == "2.0 I4"   # but AI-supplied specs are kept


# ── VIN parse from a reassembled Mode 09 PID 02 ISO-TP payload ──

def _vin_payload(vin, with_count=True):
    head = [0x49, 0x02] + ([0x01] if with_count else [])
    return bytes(head + [ord(c) for c in vin])


def test_parse_vin_standard_with_count_byte():
    vin = "SAJEA51D44XD39283"
    assert vehicle_id._parse_vin_payload(_vin_payload(vin)) == vin


def test_parse_vin_without_count_byte():
    # Some ECUs omit the data-item count; the parser tolerates both offsets.
    vin = "5YJ3E1EA7JF000000"
    assert vehicle_id._parse_vin_payload(_vin_payload(vin, with_count=False)) == vin


def test_parse_vin_rejects_wrong_service():
    # A Mode 01 reply (0x41) is not a VIN response.
    assert vehicle_id._parse_vin_payload(bytes([0x41, 0x0C, 0x1A, 0xF8])) is None


def test_parse_vin_rejects_short_or_empty():
    assert vehicle_id._parse_vin_payload(None) is None
    assert vehicle_id._parse_vin_payload(b'') is None
    assert vehicle_id._parse_vin_payload(_vin_payload("TOOSHORT")) is None

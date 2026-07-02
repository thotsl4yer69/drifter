"""Deterministic offline VIN decoder — make / region / year / validity."""
import vin_decoder as vd


def test_validity():
    assert vd.is_valid("SAJEA51D44XD39283")          # the shipped Jaguar VIN
    assert not vd.is_valid(None)
    assert not vd.is_valid("TOOSHORT")
    assert not vd.is_valid("SAJEA51D44XD3928I")        # contains illegal 'I'
    assert not vd.is_valid("SAJEA51D44XD3928_")        # illegal char


def test_decode_jaguar():
    d = vd.decode("SAJEA51D44XD39283")
    assert d["valid"] is True
    assert d["make"] == "Jaguar"          # WMI SAJ
    assert d["region"] == "Europe"        # first char 'S'
    assert d["wmi"] == "SAJ"
    assert d["year"] == 2004              # char 10 '4' → 2004 (old cycle, digit pos7)


def test_decode_common_makes():
    assert vd.decode_make("1HGCM82633A004352") == "Honda (USA)"
    assert vd.decode_make("JTDKB20U073000000") == "Toyota"
    assert vd.decode_make("5YJ3E1EA7JF000000") == "Tesla"
    assert vd.decode_make("WBA3A5C50CF000000") == "BMW"
    assert vd.decode_make("KMHD35LE8DU000000") == "Hyundai"


def test_region_ranges():
    assert vd.decode_region("JTDKB20U073000000") == "Asia"     # J
    assert vd.decode_region("1HGCM82633A004352") == "North America"  # 1
    assert vd.decode_region("WVWZZZ1JZXW000000") == "Europe"   # W
    assert vd.decode_region("6G1FK5E52DL000000") == "Oceania"  # 6


def test_modern_year_letter_cycle():
    # Char 10 'J' with a letter at char 7 → modern cycle 2018.
    d = vd.decode("5YJ3E1EA7JF000000")  # Tesla Model 3, char10=J, char7=A
    assert d["year"] == 2018


def test_unknown_wmi_still_gives_region_and_year():
    d = vd.decode("ZZZ1234567AB00000")  # unknown WMI, valid structure
    assert d["valid"] is True
    assert d["make"] is None
    assert d["region"] == "Europe"       # 'Z'
    assert d["year"] is not None


def test_decode_never_raises_on_garbage():
    for bad in (None, "", "123", "!!!", "x" * 40):
        out = vd.decode(bad)
        assert out["valid"] is False
        assert out["make"] is None

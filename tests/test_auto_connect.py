"""Tests for auto_connect nmcli terse parsing (colon-in-SSID handling)."""
from __future__ import annotations

import sys

sys.path.insert(0, 'src')

from auto_connect import parse_active_ssid, parse_wifi_scan, pick_target_ssid


def test_parse_wifi_scan_basic():
    out = "HomeNet\nCafe Wifi\n\nMZ1312_DRIFTER\n"
    assert parse_wifi_scan(out) == {"HomeNet", "Cafe Wifi", "MZ1312_DRIFTER"}


def test_parse_wifi_scan_unescapes_colon_ssid():
    # nmcli -t escapes ':' in an SSID as '\:'
    out = "Net\\:5G\nPlain\n"
    visible = parse_wifi_scan(out)
    assert "Net:5G" in visible
    assert pick_target_ssid(visible, ["Net:5G"]) == "Net:5G"


def test_parse_active_ssid_simple():
    out = "no:HomeNet\nyes:Cafe Wifi\n"
    assert parse_active_ssid(out) == "Cafe Wifi"


def test_parse_active_ssid_with_colon_in_ssid():
    # ACTIVE:SSID terse line; the SSID itself contains an (escaped) colon.
    out = "no:Other\nyes:Net\\:5G\n"
    assert parse_active_ssid(out) == "Net:5G"


def test_parse_active_ssid_none_when_no_active():
    assert parse_active_ssid("no:A\nno:B\n") is None

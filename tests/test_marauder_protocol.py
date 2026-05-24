import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import marauder_protocol as mp


class TestPassiveBuilders:
    def test_cmd_scan_ap(self):
        assert mp.cmd_scan_ap() == "scanap\r\n"

    def test_cmd_scan_sta(self):
        assert mp.cmd_scan_sta() == "scansta\r\n"

    def test_cmd_scan_probes(self):
        assert mp.cmd_scan_probes() == "sniffprobe\r\n"

    def test_cmd_stop(self):
        assert mp.cmd_stop() == "stopscan\r\n"


class TestEventParserScaffold:
    def test_parse_event_unknown_line_returns_unknown_type(self):
        """Unknown lines must return {type:'unknown', raw:...}, NOT None.
        This makes firmware drift observable instead of silent."""
        result = mp.parse_event("some line we have never seen before xyzzy")
        assert result == {"type": "unknown", "raw": "some line we have never seen before xyzzy"}

    def test_parse_event_empty_line_returns_none(self):
        """Empty / whitespace-only lines are pure noise — return None."""
        assert mp.parse_event("") is None
        assert mp.parse_event("   ") is None
        assert mp.parse_event("\r\n") is None

    def test_parse_event_strips_trailing_whitespace(self):
        result = mp.parse_event("some line we have never seen before xyzzy\r\n")
        assert result == {"type": "unknown", "raw": "some line we have never seen before xyzzy"}


class TestParseAP:
    def test_parse_ap_typical_line(self):
        line = "RSSI: -67 Ch: 6 BSSID: aa:bb:cc:dd:ee:ff ESSID: CoffeeShop"
        ev = mp.parse_event(line)
        assert ev["type"] == "ap"
        assert ev["rssi"] == -67
        assert ev["ch"] == 6
        assert ev["bssid"] == "aa:bb:cc:dd:ee:ff"
        assert ev["ssid"] == "CoffeeShop"
        assert "ts" in ev

    def test_parse_ap_ssid_with_spaces(self):
        line = "RSSI: -45 Ch: 11 BSSID: 11:22:33:44:55:66 ESSID: My Home Wi-Fi 5GHz"
        ev = mp.parse_event(line)
        assert ev["type"] == "ap"
        assert ev["ssid"] == "My Home Wi-Fi 5GHz"

    def test_parse_ap_hidden_ssid(self):
        """Marauder shows hidden SSIDs as empty string after ESSID:"""
        line = "RSSI: -82 Ch: 1 BSSID: 99:88:77:66:55:44 ESSID: "
        ev = mp.parse_event(line)
        assert ev["type"] == "ap"
        assert ev["ssid"] == ""

    def test_parse_ap_negative_rssi_bounds(self):
        ev = mp.parse_event("RSSI: -100 Ch: 13 BSSID: aa:bb:cc:dd:ee:ff ESSID: x")
        assert ev["rssi"] == -100


class TestParseSTA:
    def test_parse_sta_typical_line(self):
        line = "RSSI: -82 BSSID: aa:bb:cc:dd:ee:ff STA: 11:22:33:44:55:66 ESSID: CoffeeShop"
        ev = mp.parse_event(line)
        assert ev["type"] == "station"
        assert ev["rssi"] == -82
        assert ev["ap_bssid"] == "aa:bb:cc:dd:ee:ff"
        assert ev["sta_mac"] == "11:22:33:44:55:66"
        assert ev["ssid"] == "CoffeeShop"

    def test_parse_sta_does_not_match_ap_pattern(self):
        """STA lines lack 'Ch:' — must not be claimed by the AP parser."""
        line = "RSSI: -82 BSSID: aa:bb:cc:dd:ee:ff STA: 11:22:33:44:55:66 ESSID: x"
        ev = mp.parse_event(line)
        assert ev["type"] == "station"  # not 'ap'

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


class TestParseProbe:
    def test_parse_probe_typical_line(self):
        line = 'Probe req: 11:22:33:44:55:66 -> "MyHomeWifi"'
        ev = mp.parse_event(line)
        assert ev["type"] == "probe"
        assert ev["sta_mac"] == "11:22:33:44:55:66"
        assert ev["looking_for_ssid"] == "MyHomeWifi"

    def test_parse_probe_with_arrow_unicode(self):
        """Some Marauder builds emit U+2192 (→), others ASCII ->. Both must parse."""
        line = 'Probe req: aa:bb:cc:dd:ee:ff → "Starbucks WiFi"'
        ev = mp.parse_event(line)
        assert ev["type"] == "probe"
        assert ev["looking_for_ssid"] == "Starbucks WiFi"

    def test_parse_probe_empty_ssid(self):
        line = 'Probe req: aa:bb:cc:dd:ee:ff -> ""'
        ev = mp.parse_event(line)
        assert ev["type"] == "probe"
        assert ev["looking_for_ssid"] == ""


class TestActiveWifiBuilders:
    def test_cmd_attack_deauth_single_no_target(self):
        assert mp.cmd_attack_deauth() == "attack -t deauth\r\n"

    def test_cmd_attack_deauth_single_with_target(self):
        assert mp.cmd_attack_deauth(target_idx=3, mode="single") == \
            "attack -t deauth -a 3\r\n"

    def test_cmd_attack_deauth_all(self):
        assert mp.cmd_attack_deauth(mode="all") == "attack -t deauth -c\r\n"

    def test_cmd_attack_deauth_detect(self):
        assert mp.cmd_attack_deauth_detect() == "attack -t deauth -d\r\n"

    def test_cmd_attack_beacon_random(self):
        assert mp.cmd_attack_beacon(mode="random") == "attack -t beacon -r\r\n"

    def test_cmd_attack_beacon_rickroll(self):
        assert mp.cmd_attack_beacon(mode="rickroll") == "attack -t rickroll\r\n"

    def test_cmd_attack_beacon_list(self):
        assert mp.cmd_attack_beacon(mode="list", list_idx=2) == \
            "attack -t beacon -l 2\r\n"

    def test_cmd_attack_beacon_list_requires_idx(self):
        import pytest
        with pytest.raises(ValueError, match="list_idx"):
            mp.cmd_attack_beacon(mode="list")

    def test_cmd_attack_probe_flood(self):
        assert mp.cmd_attack_probe_flood(list_idx=1) == "attack -t probe -l 1\r\n"


class TestParseActiveEvents:
    def test_parse_deauth_seen(self):
        line = "Deauth detected from aa:bb:cc:dd:ee:ff -> 11:22:33:44:55:66"
        ev = mp.parse_event(line)
        assert ev["type"] == "deauth_seen"
        assert ev["from_mac"] == "aa:bb:cc:dd:ee:ff"
        assert ev["to_mac"] == "11:22:33:44:55:66"

    def test_parse_deauth_tx(self):
        line = "Sent deauth pkt #1240 target=aa:bb:cc:dd:ee:ff"
        ev = mp.parse_event(line)
        assert ev["type"] == "deauth_tx"
        assert ev["pkt_n"] == 1240
        assert ev["target_bssid"] == "aa:bb:cc:dd:ee:ff"

    def test_parse_beacon_tx(self):
        line = 'Sent beacon pkt #42 ssid="ACME-Pentest-Guest"'
        ev = mp.parse_event(line)
        assert ev["type"] == "beacon_tx"
        assert ev["pkt_n"] == 42
        assert ev["ssid"] == "ACME-Pentest-Guest"


class TestBLEBuilders:
    def test_cmd_ble_scan_all(self):
        assert mp.cmd_ble_scan("all") == "blescan -t all\r\n"

    def test_cmd_ble_scan_airtag(self):
        assert mp.cmd_ble_scan("airtag") == "blescan -t airtag\r\n"

    def test_cmd_ble_scan_skim(self):
        assert mp.cmd_ble_scan("skim") == "blescan -t skim\r\n"

    def test_cmd_ble_scan_unknown_raises(self):
        import pytest
        with pytest.raises(ValueError, match="ble scan"):
            mp.cmd_ble_scan("bogus")

    def test_cmd_ble_spam_variants(self):
        assert mp.cmd_ble_spam("swift") == "blespam -t swift\r\n"
        assert mp.cmd_ble_spam("samsung") == "blespam -t samsung\r\n"
        assert mp.cmd_ble_spam("apple") == "blespam -t apple\r\n"
        assert mp.cmd_ble_spam("all") == "blespam -t all\r\n"

    def test_cmd_ble_spam_unknown_raises(self):
        import pytest
        with pytest.raises(ValueError, match="ble spam"):
            mp.cmd_ble_spam("bogus")


class TestParseBLEEvents:
    def test_parse_airtag(self):
        line = "BLE: AirTag spotted aa:bb:cc:dd:ee:ff RSSI -55"
        ev = mp.parse_event(line)
        assert ev["type"] == "airtag"
        assert ev["mac"] == "aa:bb:cc:dd:ee:ff"
        assert ev["rssi"] == -55

    def test_parse_skimmer(self):
        line = "BLE: skimmer fingerprint aa:bb:cc:dd:ee:ff"
        ev = mp.parse_event(line)
        assert ev["type"] == "skimmer"
        assert ev["mac"] == "aa:bb:cc:dd:ee:ff"

    def test_parse_ble_device(self):
        line = 'BLE: device aa:bb:cc:dd:ee:ff name="Galaxy Buds Pro" RSSI -72'
        ev = mp.parse_event(line)
        assert ev["type"] == "ble_device"
        assert ev["mac"] == "aa:bb:cc:dd:ee:ff"
        assert ev["name"] == "Galaxy Buds Pro"
        assert ev["rssi"] == -72

    def test_parse_ble_device_no_name(self):
        line = 'BLE: device aa:bb:cc:dd:ee:ff name="" RSSI -85'
        ev = mp.parse_event(line)
        assert ev["type"] == "ble_device"
        assert ev["name"] == ""


class TestEvilPortalBuilders:
    def test_cmd_evilportal_start(self):
        assert mp.cmd_evilportal_start("ACME-Pentest") == \
            'evilportal -s "ACME-Pentest"\r\n'

    def test_cmd_evilportal_start_escapes_quotes_in_ssid(self):
        """SSIDs with embedded quotes get sanitized — never inject CLI."""
        result = mp.cmd_evilportal_start('Acme " Test')
        # Quote-stripped to prevent CLI injection
        assert '"' not in result.replace('evilportal -s ', '').rstrip('\r\n').strip('"')

    def test_cmd_evilportal_stop(self):
        assert mp.cmd_evilportal_stop() == "evilportal -s stop\r\n"

    def test_cmd_evilportal_load_template_chunks(self):
        """Template upload returns a LIST of chunks (Marauder CLI has line-length limits)."""
        html = b"<html><body>%s</body></html>" % (b"x" * 2000)
        chunks = mp.cmd_evilportal_load_template(html)
        assert isinstance(chunks, list)
        assert len(chunks) >= 1
        for c in chunks:
            assert c.endswith("\r\n")
        # Last chunk is the "upload-complete" sentinel
        assert chunks[-1].strip() == "evilportal -p commit"


class TestParsePortalEvents:
    def test_parse_portal_client_connect(self):
        line = "Portal client connected mac=aa:bb:cc:dd:ee:ff"
        ev = mp.parse_event(line)
        assert ev["type"] == "portal_client_connect"
        assert ev["mac"] == "aa:bb:cc:dd:ee:ff"

    def test_parse_cred_capture_returns_sentinel(self):
        line = 'Captured: user=alice pass=hunter2 email=a@b.c'
        ev = mp.parse_event(line)
        assert ev["type"] == "cred_capture"
        assert ev["fields"] == {"user": "alice", "pass": "hunter2", "email": "a@b.c"}

    def test_parse_cred_capture_with_url_encoded(self):
        line = 'Captured: user=alice%40acme pass=p%40ss'
        ev = mp.parse_event(line)
        assert ev["type"] == "cred_capture"
        # Values kept as-emitted; URL-decode is operator's job
        assert ev["fields"]["user"] == "alice%40acme"

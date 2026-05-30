import sys
from pathlib import Path
from unittest.mock import MagicMock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from marauder_features import active_wifi as aw


class TestDeauthDetect:
    def test_start_detect_sends_correct_command(self):
        transport = MagicMock()
        transport.mode = "direct"
        result = aw.start_deauth_detect(transport, duration_s=60)
        transport.send.assert_called_once_with("attack -t deauth -d\r\n")
        assert result["ok"] is True
        assert result["duration_s"] == 60

    def test_no_hardware_refused(self):
        transport = MagicMock()
        transport.mode = "none"
        result = aw.start_deauth_detect(transport, duration_s=60)
        assert result["ok"] is False
        transport.send.assert_not_called()


class TestDeauthAttack:
    def test_attack_with_bssid_in_allowlist(self):
        transport = MagicMock()
        transport.mode = "direct"
        scope = {"wifi": [{"bssid": "aa:bb:cc:dd:ee:ff"}], "ble": [], "evilportal": []}
        result = aw.start_deauth_attack(
            transport, scope,
            bssid="aa:bb:cc:dd:ee:ff", ssid="ACME-Pentest", duration_s=60,
        )
        assert result["ok"] is True
        # Marauder firmware doesn't take a raw BSSID; the bridge sends the
        # no-target form and relies on the operator having pre-selected.
        transport.send.assert_called_once_with("attack -t deauth\r\n")

    def test_attack_out_of_scope_refused(self):
        transport = MagicMock()
        transport.mode = "direct"
        scope = {"wifi": [{"bssid": "aa:bb:cc:dd:ee:ff"}], "ble": [], "evilportal": []}
        result = aw.start_deauth_attack(
            transport, scope,
            bssid="11:22:33:44:55:66", ssid="NotAuthorized", duration_s=60,
        )
        assert result["ok"] is False
        assert "allowlist" in result["response"].lower() or "no match" in result["response"].lower()
        transport.send.assert_not_called()

    def test_attack_duration_capped_at_300(self):
        transport = MagicMock()
        transport.mode = "direct"
        scope = {"wifi": [{"bssid": "aa:bb:cc:dd:ee:ff"}], "ble": [], "evilportal": []}
        result = aw.start_deauth_attack(
            transport, scope,
            bssid="aa:bb:cc:dd:ee:ff", ssid="x", duration_s=9999,
        )
        assert result["duration_s"] == 300


class TestBeaconSpam:
    def test_beacon_spam_random_refused(self):
        transport = MagicMock()
        transport.mode = "direct"
        result = aw.start_beacon_spam(transport, allowlist_scope={"wifi": []},
                                       mode="random", duration_s=60)
        assert result["ok"] is False
        assert "random" in result["response"].lower()
        transport.send.assert_not_called()

    def test_beacon_spam_rickroll_refused_when_no_wildcard(self):
        transport = MagicMock()
        transport.mode = "direct"
        result = aw.start_beacon_spam(transport, allowlist_scope={"wifi": []},
                                       mode="rickroll", duration_s=60)
        assert result["ok"] is False
        assert "rickroll" in result["response"].lower()
        transport.send.assert_not_called()

    def test_beacon_spam_rickroll_allowed_with_wildcard(self):
        transport = MagicMock()
        transport.mode = "direct"
        scope = {"wifi": [{"ssid": "*"}], "ble": [], "evilportal": []}
        result = aw.start_beacon_spam(transport, scope,
                                       mode="rickroll", duration_s=60)
        assert result["ok"] is True
        transport.send.assert_called_once_with("attack -t rickroll\r\n")

    def test_beacon_spam_list_all_in_scope(self, tmp_path):
        list_path = tmp_path / "list.txt"
        list_path.write_text("ACME-Pentest\nACME-Guest\n")
        transport = MagicMock()
        transport.mode = "direct"
        scope = {"wifi": [{"ssid": "ACME-Pentest"}, {"ssid": "ACME-Guest"}],
                 "ble": [], "evilportal": []}
        result = aw.start_beacon_spam(transport, scope, mode="list",
                                       beacon_list_path=str(list_path),
                                       list_idx=0, duration_s=60)
        assert result["ok"] is True

    def test_beacon_spam_list_partial_out_of_scope_refused(self, tmp_path):
        list_path = tmp_path / "list.txt"
        list_path.write_text("ACME-Pentest\nNotAllowed\n")
        transport = MagicMock()
        transport.mode = "direct"
        scope = {"wifi": [{"ssid": "ACME-Pentest"}], "ble": [], "evilportal": []}
        result = aw.start_beacon_spam(transport, scope, mode="list",
                                       beacon_list_path=str(list_path),
                                       list_idx=0, duration_s=60)
        assert result["ok"] is False
        assert "NotAllowed" in result["response"]
        transport.send.assert_not_called()


class TestProbeFlood:
    def test_probe_flood_all_in_scope(self, tmp_path):
        list_path = tmp_path / "list.txt"
        list_path.write_text("AcmeWifi\nAcmeGuest\n")
        transport = MagicMock()
        transport.mode = "direct"
        scope = {"wifi": [{"ssid": "AcmeWifi"}, {"ssid": "AcmeGuest"}],
                 "ble": [], "evilportal": []}
        result = aw.start_probe_flood(transport, scope,
                                       beacon_list_path=str(list_path),
                                       list_idx=0, duration_s=30)
        assert result["ok"] is True
        transport.send.assert_called_once_with("attack -t probe -l 0\r\n")

    def test_probe_flood_partial_out_of_scope_refused(self, tmp_path):
        list_path = tmp_path / "list.txt"
        list_path.write_text("AcmeWifi\nBadGuest\n")
        transport = MagicMock()
        transport.mode = "direct"
        scope = {"wifi": [{"ssid": "AcmeWifi"}], "ble": [], "evilportal": []}
        result = aw.start_probe_flood(transport, scope,
                                       beacon_list_path=str(list_path),
                                       list_idx=0, duration_s=30)
        assert result["ok"] is False
        assert "BadGuest" in result["response"]

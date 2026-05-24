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

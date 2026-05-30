import sys
from pathlib import Path
from unittest.mock import MagicMock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from marauder_features import ble


class TestBLEScan:
    def test_scan_all(self):
        transport = MagicMock()
        transport.mode = "direct"
        result = ble.start_scan(transport, mode="all", duration_s=30)
        assert result["ok"] is True
        transport.send.assert_called_once_with("blescan -t all\r\n")

    def test_scan_airtag(self):
        transport = MagicMock()
        transport.mode = "direct"
        result = ble.start_scan(transport, mode="airtag", duration_s=30)
        transport.send.assert_called_once_with("blescan -t airtag\r\n")

    def test_scan_skim(self):
        transport = MagicMock()
        transport.mode = "direct"
        result = ble.start_scan(transport, mode="skim", duration_s=30)
        transport.send.assert_called_once_with("blescan -t skim\r\n")

    def test_unknown_mode_refused(self):
        transport = MagicMock()
        transport.mode = "direct"
        result = ble.start_scan(transport, mode="bogus", duration_s=30)
        assert result["ok"] is False


class TestBLESpam:
    def _scope_authorized(self):
        return {"wifi": [], "evilportal": [],
                "ble": [{"area_authorized": True, "area_label": "test lab"}]}

    def test_spam_swift_authorized(self):
        transport = MagicMock(); transport.mode = "direct"
        result = ble.start_spam(transport, self._scope_authorized(),
                                mode="swift", duration_s=60)
        assert result["ok"] is True
        assert result["area_label_at_runtime"] == "test lab"

    def test_spam_unauthorized_refused(self):
        transport = MagicMock(); transport.mode = "direct"
        scope = {"wifi": [], "ble": [], "evilportal": []}
        result = ble.start_spam(transport, scope, mode="swift", duration_s=60)
        assert result["ok"] is False
        transport.send.assert_not_called()

    def test_spam_duration_capped_at_300(self):
        transport = MagicMock(); transport.mode = "direct"
        result = ble.start_spam(transport, self._scope_authorized(),
                                mode="swift", duration_s=9999)
        assert result["duration_s"] == 300

    def test_spam_apple_emits_collateral_warning_first_time(self):
        transport = MagicMock(); transport.mode = "direct"
        ble.reset_collateral_warning_state()  # test helper
        result = ble.start_spam(transport, self._scope_authorized(),
                                mode="apple", duration_s=60, acked_warning=True)
        assert result["ok"] is True
        assert result["collateral_warning_emitted"] is True

    def test_spam_apple_requires_warning_ack_on_first_run(self):
        transport = MagicMock(); transport.mode = "direct"
        ble.reset_collateral_warning_state()
        result = ble.start_spam(transport, self._scope_authorized(),
                                mode="apple", duration_s=60, acked_warning=False)
        assert result["ok"] is False
        assert "collateral" in result["response"].lower()
        transport.send.assert_not_called()

import sys
from pathlib import Path
from unittest.mock import MagicMock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from marauder_features import passive


class TestPassiveScanStart:
    def test_start_ap_sends_correct_command(self):
        transport = MagicMock()
        transport.mode = "direct"
        result = passive.start_scan(transport, mode="ap", duration_s=30)
        assert result["ok"] is True
        transport.send.assert_called_once_with("scanap\r\n")
        assert result["mode"] == "ap"
        assert result["duration_s"] == 30

    def test_start_sta_sends_correct_command(self):
        transport = MagicMock()
        transport.mode = "direct"
        result = passive.start_scan(transport, mode="sta", duration_s=10)
        transport.send.assert_called_once_with("scansta\r\n")

    def test_start_probe_sends_correct_command(self):
        transport = MagicMock()
        transport.mode = "direct"
        passive.start_scan(transport, mode="probe", duration_s=10)
        transport.send.assert_called_once_with("sniffprobe\r\n")

    def test_unknown_mode_refused(self):
        transport = MagicMock()
        transport.mode = "direct"
        result = passive.start_scan(transport, mode="bogus", duration_s=10)
        assert result["ok"] is False
        assert "unknown mode" in result["response"].lower()
        transport.send.assert_not_called()

    def test_duration_capped_at_600(self):
        transport = MagicMock()
        transport.mode = "direct"
        result = passive.start_scan(transport, mode="ap", duration_s=9999)
        assert result["duration_s"] == 600

    def test_no_hardware_refused(self):
        transport = MagicMock()
        transport.mode = "none"
        result = passive.start_scan(transport, mode="ap", duration_s=30)
        assert result["ok"] is False
        assert "no transport" in result["response"].lower()
        transport.send.assert_not_called()


class TestPassiveScanStop:
    def test_stop_sends_stopscan(self):
        transport = MagicMock()
        transport.mode = "direct"
        passive.stop_scan(transport)
        transport.send.assert_called_once_with("stopscan\r\n")

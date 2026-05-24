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

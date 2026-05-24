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

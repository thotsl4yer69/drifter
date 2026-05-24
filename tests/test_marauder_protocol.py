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

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import marauder_bridge as mb


class TestClassifyRisk:
    def test_low_passive_scans(self):
        for cmd in ["scan_ap", "scan_sta", "scan_probes", "stop",
                    "deauth_detect", "ble_scan_all", "ble_scan_airtag",
                    "ble_scan_skim"]:
            assert mb.classify_risk(cmd) == "LOW", f"{cmd} should be LOW"

    def test_med_select_channel(self):
        for cmd in ["select_ap", "channel_hop", "scan_param"]:
            assert mb.classify_risk(cmd) == "MED", f"{cmd} should be MED"

    def test_high_active_attacks(self):
        for cmd in ["deauth_attack", "beacon_spam_list",
                    "beacon_spam_random", "beacon_spam_rickroll",
                    "probe_flood",
                    "ble_spam_swift_pair", "ble_spam_easy_setup",
                    "ble_spam_apple_proximity", "ble_spam_all",
                    "evilportal_start"]:
            assert mb.classify_risk(cmd) == "HIGH", f"{cmd} should be HIGH"

    def test_unknown_defaults_to_high(self):
        """Unknown commands fail closed — treated as HIGH so they get gated."""
        assert mb.classify_risk("totally_made_up_command") == "HIGH"

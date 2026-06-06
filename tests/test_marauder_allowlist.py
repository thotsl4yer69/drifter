import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import marauder_allowlist as ma


class TestLoadAllowlist:
    def test_load_missing_file_returns_empty(self, tmp_path):
        result = ma.load_marauder_allowlist(tmp_path / "nonexistent.yaml")
        assert result == {"wifi": [], "ble": [], "evilportal": []}

    def test_load_empty_marauder_block(self, tmp_path):
        p = tmp_path / "audit.yaml"
        p.write_text("networks: []\nmarauder:\n  wifi: []\n  ble: []\n  evilportal: []\n")
        result = ma.load_marauder_allowlist(p)
        assert result == {"wifi": [], "ble": [], "evilportal": []}

    def test_load_no_marauder_block_at_all(self, tmp_path):
        """If audit_targets.yaml has only the legacy wifi-audit 'networks'
        key (older deploys), marauder treats it as fully-empty scope."""
        p = tmp_path / "audit.yaml"
        p.write_text("networks: []\n")
        result = ma.load_marauder_allowlist(p)
        assert result == {"wifi": [], "ble": [], "evilportal": []}

    def test_load_populated_wifi(self, tmp_path):
        p = tmp_path / "audit.yaml"
        p.write_text(textwrap.dedent("""
            marauder:
              wifi:
                - ssid: "ACME-Pentest"
                - bssid: "aa:bb:cc:dd:ee:ff"
              ble: []
              evilportal: []
        """))
        result = ma.load_marauder_allowlist(p)
        assert result["wifi"] == [
            {"ssid": "ACME-Pentest"},
            {"bssid": "aa:bb:cc:dd:ee:ff"},
        ]

    def test_load_malformed_yaml_returns_empty(self, tmp_path):
        """Malformed YAML must NOT crash the service. Empty scope is safe."""
        p = tmp_path / "audit.yaml"
        p.write_text("marauder:\n  wifi:\n    - {malformed")
        result = ma.load_marauder_allowlist(p)
        assert result == {"wifi": [], "ble": [], "evilportal": []}


class TestIsTargetAllowedWifi:
    def _scope(self, wifi_entries):
        return {"wifi": wifi_entries, "ble": [], "evilportal": []}

    def test_empty_wifi_refuses_everything(self):
        ok, reason = ma.is_target_allowed(
            self._scope([]), "wifi", ssid="anything", bssid="aa:bb:cc:dd:ee:ff"
        )
        assert ok is False
        assert "empty" in reason.lower()

    def test_ssid_match_allows(self):
        ok, reason = ma.is_target_allowed(
            self._scope([{"ssid": "ACME-Pentest"}]),
            "wifi", ssid="ACME-Pentest", bssid="aa:bb:cc:dd:ee:ff",
        )
        assert ok is True
        assert reason == "matched ssid=ACME-Pentest"

    def test_bssid_match_allows(self):
        ok, _ = ma.is_target_allowed(
            self._scope([{"bssid": "aa:bb:cc:dd:ee:ff"}]),
            "wifi", ssid="WhateverSSID", bssid="aa:bb:cc:dd:ee:ff",
        )
        assert ok is True

    def test_bssid_match_is_case_insensitive(self):
        ok, _ = ma.is_target_allowed(
            self._scope([{"bssid": "AA:BB:CC:DD:EE:FF"}]),
            "wifi", ssid="x", bssid="aa:bb:cc:dd:ee:ff",
        )
        assert ok is True

    def test_no_match_refuses(self):
        ok, reason = ma.is_target_allowed(
            self._scope([{"ssid": "ACME-Pentest"}]),
            "wifi", ssid="SomeoneElsesWiFi", bssid="99:88:77:66:55:44",
        )
        assert ok is False
        assert "no match" in reason.lower()


class TestIsTargetAllowedBLE:
    def test_empty_ble_refuses(self):
        ok, reason = ma.is_target_allowed(
            {"wifi": [], "ble": [], "evilportal": []},
            "ble", mac="aa:bb:cc:dd:ee:ff", action="scan",
        )
        assert ok is False
        assert "empty" in reason.lower()

    def test_specific_mac_allows_for_targeted_action(self):
        scope = {"wifi": [], "ble": [{"mac": "aa:bb:cc:dd:ee:ff"}],
                 "evilportal": []}
        ok, reason = ma.is_target_allowed(
            scope, "ble", mac="aa:bb:cc:dd:ee:ff", action="targeted",
        )
        assert ok is True

    def test_specific_mac_does_NOT_allow_indiscriminate_spam(self):
        """Per-MAC scope only authorizes targeted operations. Spam
        requires the area_authorized entry."""
        scope = {"wifi": [], "ble": [{"mac": "aa:bb:cc:dd:ee:ff"}],
                 "evilportal": []}
        ok, reason = ma.is_target_allowed(
            scope, "ble", mac="aa:bb:cc:dd:ee:ff", action="spam",
        )
        assert ok is False
        assert "area_authorized" in reason

    def test_area_authorized_allows_spam(self):
        scope = {"wifi": [],
                 "ble": [{"area_authorized": True, "area_label": "ACME lab 204"}],
                 "evilportal": []}
        ok, reason = ma.is_target_allowed(
            scope, "ble", mac=None, action="spam",
        )
        assert ok is True
        assert "ACME lab 204" in reason

    def test_area_authorized_without_label_refused(self):
        """Operator must provide an area_label — friction point."""
        scope = {"wifi": [],
                 "ble": [{"area_authorized": True}],
                 "evilportal": []}
        ok, reason = ma.is_target_allowed(
            scope, "ble", mac=None, action="spam",
        )
        assert ok is False
        assert "area_label" in reason


class TestIsTargetAllowedEvilPortal:
    def test_empty_evilportal_refuses(self):
        ok, reason = ma.is_target_allowed(
            {"wifi": [], "ble": [], "evilportal": []},
            "evilportal", ssid="x", template="t",
        )
        assert ok is False
        assert "empty" in reason.lower()

    def test_ssid_template_pair_match(self):
        scope = {"wifi": [], "ble": [],
                 "evilportal": [{"ssid": "ACME-Guest", "template": "acme-guest",
                                 "max_captures": 50, "authorized_use": "contract X"}]}
        ok, reason = ma.is_target_allowed(scope, "evilportal",
                                           ssid="ACME-Guest", template="acme-guest")
        assert ok is True

    def test_ssid_match_but_template_mismatch_refused(self):
        scope = {"wifi": [], "ble": [],
                 "evilportal": [{"ssid": "ACME-Guest", "template": "acme-guest"}]}
        ok, reason = ma.is_target_allowed(scope, "evilportal",
                                           ssid="ACME-Guest", template="OTHER")
        assert ok is False

    def test_template_match_but_ssid_mismatch_refused(self):
        scope = {"wifi": [], "ble": [],
                 "evilportal": [{"ssid": "ACME-Guest", "template": "acme-guest"}]}
        ok, reason = ma.is_target_allowed(scope, "evilportal",
                                           ssid="OTHER", template="acme-guest")
        assert ok is False

    def test_bridge_gate_template_name_key_matches(self):
        """The bridge allowlist gate forwards raw command args, whose template
        key is `template_name` (not `template`). An authorized pair must still
        match via that key — otherwise the gate refuses every authorized portal
        before the feature-level gate is reached."""
        scope = {"wifi": [], "ble": [],
                 "evilportal": [{"ssid": "ACME-Guest", "template": "acme-guest"}]}
        ok, reason = ma.is_target_allowed(scope, "evilportal",
                                           ssid="ACME-Guest", template_name="acme-guest")
        assert ok is True, reason

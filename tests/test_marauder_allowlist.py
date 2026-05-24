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

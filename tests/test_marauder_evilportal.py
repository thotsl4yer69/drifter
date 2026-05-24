import json
import sys
from pathlib import Path
from unittest.mock import MagicMock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from marauder_features import evilportal as ep


def _make_template(tmp_path, name="acme-guest"):
    tdir = tmp_path / name
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "portal.html").write_text(
        '<html><body><form method=post>'
        '<input name=user><input name=pass>'
        '<input type=hidden name=cb value="{{captive_post_url}}">'
        '</form></body></html>'
    )
    (tdir / "meta.yaml").write_text(
        'ssid_default: "ACME-Guest"\n'
        'description: "ACME guest"\n'
        'authorized_use: "test only"\n'
        'created: "2026-05-24"\n'
    )
    return tdir


class TestTemplateValidator:
    def test_valid_template_loads(self, tmp_path):
        tdir = _make_template(tmp_path)
        html = ep.load_and_validate_template(tdir)
        assert b"{{captive_post_url}}" in html

    def test_missing_placeholder_rejected(self, tmp_path):
        tdir = tmp_path / "bad"; tdir.mkdir()
        (tdir / "portal.html").write_text("<html><body>no placeholder</body></html>")
        import pytest
        with pytest.raises(ValueError, match="captive_post_url"):
            ep.load_and_validate_template(tdir)

    def test_oversize_template_rejected(self, tmp_path):
        tdir = tmp_path / "huge"; tdir.mkdir()
        big = "x" * (70 * 1024) + " {{captive_post_url}} "
        (tdir / "portal.html").write_text(big)
        import pytest
        with pytest.raises(ValueError, match="64.?KB|too large"):
            ep.load_and_validate_template(tdir)

    def test_external_script_rejected(self, tmp_path):
        tdir = tmp_path / "exfil"; tdir.mkdir()
        (tdir / "portal.html").write_text(
            '<html><script src="http://evil.com/exfil.js"></script>'
            ' {{captive_post_url}} </html>'
        )
        import pytest
        with pytest.raises(ValueError, match="script"):
            ep.load_and_validate_template(tdir)


class TestPortalStart:
    def test_start_authorized_pair(self, tmp_path):
        _make_template(tmp_path / "templates")
        transport = MagicMock(); transport.mode = "direct"
        scope = {"wifi": [], "ble": [],
                 "evilportal": [{"ssid": "ACME-Guest", "template": "acme-guest",
                                 "max_captures": 50,
                                 "authorized_use": "test contract"}]}
        result = ep.start(transport, scope,
                          template_root=tmp_path / "templates",
                          ssid="ACME-Guest", template_name="acme-guest",
                          duration_s=600)
        assert result["ok"] is True
        assert result["session_id"]
        # Transport got the chunks plus the start command
        assert transport.send.called
        # Last call is the start
        last = transport.send.call_args_list[-1].args[0]
        assert "evilportal -s" in last
        assert "ACME-Guest" in last

    def test_start_unauthorized_refused(self, tmp_path):
        _make_template(tmp_path / "templates")
        transport = MagicMock(); transport.mode = "direct"
        scope = {"wifi": [], "ble": [], "evilportal": []}
        result = ep.start(transport, scope,
                          template_root=tmp_path / "templates",
                          ssid="ACME-Guest", template_name="acme-guest",
                          duration_s=600)
        assert result["ok"] is False
        transport.send.assert_not_called()

    def test_start_duration_capped_at_1800(self, tmp_path):
        _make_template(tmp_path / "templates")
        transport = MagicMock(); transport.mode = "direct"
        scope = {"wifi": [], "ble": [],
                 "evilportal": [{"ssid": "ACME-Guest", "template": "acme-guest",
                                 "max_captures": 50, "authorized_use": "x"}]}
        result = ep.start(transport, scope,
                          template_root=tmp_path / "templates",
                          ssid="ACME-Guest", template_name="acme-guest",
                          duration_s=99999)
        assert result["duration_s"] == 1800


class TestPortalStop:
    def test_stop_sends_stop_command(self):
        transport = MagicMock(); transport.mode = "direct"
        result = ep.stop(transport)
        assert result["ok"] is True
        transport.send.assert_called_with("evilportal -s stop\r\n")

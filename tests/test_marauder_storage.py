import json
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import marauder_storage as ms


class TestSessionWriter:
    def test_start_creates_jsonl_file(self, tmp_path):
        s = ms.SessionWriter(state_root=tmp_path)
        sid = s.start(category="scans", mode="ap")
        assert isinstance(sid, str) and len(sid) >= 8
        scan_file = tmp_path / "scans" / f"{sid}.jsonl"
        assert scan_file.exists()

    def test_append_writes_one_jsonl_line_per_event(self, tmp_path):
        s = ms.SessionWriter(state_root=tmp_path)
        sid = s.start(category="scans", mode="ap")
        s.append(sid, {"type": "ap", "bssid": "aa:bb:..", "rssi": -67})
        s.append(sid, {"type": "ap", "bssid": "11:22:..", "rssi": -55})
        scan_file = tmp_path / "scans" / f"{sid}.jsonl"
        lines = scan_file.read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["bssid"] == "aa:bb:.."

    def test_end_writes_index_entry(self, tmp_path):
        s = ms.SessionWriter(state_root=tmp_path)
        sid = s.start(category="scans", mode="ap")
        s.append(sid, {"type": "ap", "bssid": "x", "rssi": -1})
        s.end(sid)
        index = json.loads((tmp_path / "sessions.json").read_text())
        assert any(e["id"] == sid for e in index["sessions"])
        entry = next(e for e in index["sessions"] if e["id"] == sid)
        assert entry["event_count"] == 1
        assert entry["mode"] == "ap"
        assert entry["ended_ts"] is not None
        assert entry["started_ts"] <= entry["ended_ts"]

    def test_double_end_is_noop(self, tmp_path):
        """Second end() must not corrupt the index entry."""
        s = ms.SessionWriter(state_root=tmp_path)
        sid = s.start(category="scans", mode="ap")
        s.end(sid)
        first_end = json.loads((tmp_path / "sessions.json").read_text())["sessions"][0]["ended_ts"]
        time.sleep(0.05)
        s.end(sid)
        second_end = json.loads((tmp_path / "sessions.json").read_text())["sessions"][0]["ended_ts"]
        assert first_end == second_end  # invariant: ended_ts immutable


class TestAuditSessionRecord:
    def test_write_attack_record_round_trip(self, tmp_path):
        ms.write_attack_audit(state_root=tmp_path, record={
            "id": "abc123",
            "operator_ip": "10.42.0.5",
            "started_ts": 1779600000.0,
            "ended_ts": 1779600060.0,
            "mode": "deauth_attack",
            "target_bssid": "aa:bb:cc:dd:ee:ff",
            "target_ssid": "ACME-Pentest-Guest",
            "allowlist_path": "/opt/drifter/etc/audit_targets.yaml",
            "allowlist_sha256": "deadbeef",
            "confirm_token_consumed": "tok-uuid",
            "packets_sent": 1240,
            "transport": "direct",
            "marauder_fw_banner": "Marauder v0.13.4",
            "stop_reason": "duration_elapsed",
        })
        files = list((tmp_path / "attacks").glob("*.json"))
        assert len(files) == 1
        data = json.loads(files[0].read_text())
        assert data["id"] == "abc123"
        assert data["allowlist_sha256"] == "deadbeef"

    def test_write_attack_record_rejects_missing_required_fields(self, tmp_path):
        """Invariant: every audit record contains the documented required
        fields, or it doesn't get written. Catches a class of caller bugs
        where attack lifecycle code forgets a field."""
        import pytest
        with pytest.raises(ValueError, match="missing required"):
            ms.write_attack_audit(state_root=tmp_path, record={
                "id": "abc",
                # missing operator_ip, mode, etc.
            })


class TestPortalStorage:
    def test_write_capture_creates_0600_file(self, tmp_path):
        sid = "abc123"
        path = ms.write_portal_capture(state_root=tmp_path, session_id=sid,
                                        fields={"user": "alice", "pass": "x"})
        # File exists at expected location
        assert path == tmp_path / "evilportal" / f"captures-{sid}.jsonl"
        assert path.exists()
        # Mode is 0600 (owner read/write only)
        import os
        mode = oct(os.stat(path).st_mode & 0o777)
        assert mode == "0o600", f"capture file mode should be 0o600, got {mode}"
        # Content is one JSON object per line
        lines = path.read_text().strip().split("\n")
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["fields"] == {"user": "alice", "pass": "x"}
        assert "ts" in data

    def test_write_capture_appends_subsequent_calls(self, tmp_path):
        sid = "abc123"
        ms.write_portal_capture(state_root=tmp_path, session_id=sid, fields={"x": "1"})
        ms.write_portal_capture(state_root=tmp_path, session_id=sid, fields={"x": "2"})
        path = tmp_path / "evilportal" / f"captures-{sid}.jsonl"
        lines = path.read_text().strip().split("\n")
        assert len(lines) == 2

    def test_write_portal_audit_required_fields(self, tmp_path):
        ms.write_portal_audit(state_root=tmp_path, record={
            "id": "p1", "operator_ip": "10.42.0.5",
            "started_ts": 1.0, "ended_ts": 2.0,
            "ssid": "ACME", "template_name": "acme-guest",
            "template_sha256": "abc", "allowlist_sha256": "def",
            "allowlist_entry": {"ssid": "ACME", "template": "acme-guest"},
            "duration_s": 60, "transport": "direct",
            "captures_count": 0, "captures_file": "captures-p1.jsonl",
            "captures_revealed_at": [],
            "captures_wiped": False, "stop_reason": "duration_elapsed",
        })
        f = tmp_path / "evilportal" / "p1.json"
        assert f.exists()
        data = json.loads(f.read_text())
        assert data["template_sha256"] == "abc"

    def test_write_portal_audit_missing_field_raises(self, tmp_path):
        import pytest
        with pytest.raises(ValueError, match="missing required"):
            ms.write_portal_audit(state_root=tmp_path, record={"id": "p1"})

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

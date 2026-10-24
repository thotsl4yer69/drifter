from __future__ import annotations

import hashlib
import json

import recon_indexer as recon


def _configure_tmp(monkeypatch, tmp_path):
    root = tmp_path / "recon"
    monkeypatch.setattr(recon, "RECON_DIR", root)
    monkeypatch.setattr(recon, "SESSION_DIR", root / "sessions")
    monkeypatch.setattr(recon, "MEDIA_DIR", root / "media")
    return root


def test_ledger_chains_records_and_hashes_media(monkeypatch, tmp_path):
    root = _configure_tmp(monkeypatch, tmp_path)
    ledger = recon.ReconLedger(now=100.0)
    ledger.update_gps({"lat": -36.75, "lon": 144.28, "accuracy": 3.0})

    media = root / "media" / "frame.jpg"
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b"recon-evidence")

    first = ledger.append("vision", {"objects": [{"class": "person"}]},
                          media_path=str(media), now=101.0)
    second = ledger.append("plate", {"plate": "ABC123"}, now=102.0)

    assert first["prev_hash"] == "0" * 64
    assert first["media_sha256"] == hashlib.sha256(b"recon-evidence").hexdigest()
    assert first["gps"]["lat"] == -36.75
    assert second["prev_hash"] == first["hash"]
    assert ledger.chain_head == second["hash"]
    assert ledger.event_count == 2

    lines = [json.loads(line) for line in ledger.path.read_text().splitlines()]
    assert [line["seq"] for line in lines] == [1, 2]
    assert lines[1]["prev_hash"] == lines[0]["hash"]


def test_media_outside_recon_root_is_never_hashed(monkeypatch, tmp_path):
    _configure_tmp(monkeypatch, tmp_path)
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"not-owned-by-recon")
    assert recon._safe_media_path(str(outside)) is None


def test_status_exposes_session_chain_head_and_count(monkeypatch, tmp_path):
    _configure_tmp(monkeypatch, tmp_path)
    ledger = recon.ReconLedger(now=100.0)
    ledger.append("session_start", {"mode": "recon"}, now=100.1)
    status = ledger.status()
    assert status["state"] == "online"
    assert status["session_id"].startswith("recon-")
    assert status["event_count"] == 1
    assert status["evidence_count"] == 0
    assert status["chain_head"] == ledger.chain_head
    assert status["chain_head"] != "0" * 64



def test_session_ids_do_not_collide_for_rapid_restarts(monkeypatch, tmp_path):
    _configure_tmp(monkeypatch, tmp_path)
    monkeypatch.setattr(recon.os, "getpid", lambda: 1234)
    first = recon.ReconLedger(now=100.000001)
    second = recon.ReconLedger(now=100.000002)
    assert first.session_id != second.session_id
    assert first.path != second.path


def test_evidence_count_excludes_session_anchors(monkeypatch, tmp_path):
    _configure_tmp(monkeypatch, tmp_path)
    ledger = recon.ReconLedger(now=100.0)
    ledger.append("session_start", {"mode": "recon"}, now=100.1)
    ledger.append("vision", {"objects": [{"class": "person"}]}, now=100.2)
    assert ledger.event_count == 2
    assert ledger.evidence_count == 1

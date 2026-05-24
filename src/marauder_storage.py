"""MZ1312 DRIFTER — Marauder bridge module: session JSONL writer + sessions.json index.

See docs/superpowers/specs/2026-05-24-marauder-bridge-design.md §4.3, §6.
"""

import json
import os
import threading
import time
import uuid
from pathlib import Path


class SessionWriter:
    """Writes per-session JSONL files + maintains the sessions.json index.

    Append-only — once a session is end()ed, its index entry's ended_ts
    is immutable (double-end is a no-op).
    """

    def __init__(self, state_root: Path | str = "/opt/drifter/state/marauder"):
        self.root = Path(state_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._open_files: dict[str, "object"] = {}  # session_id → file handle
        self._meta: dict[str, dict] = {}  # session_id → metadata dict

    def start(self, *, category: str, mode: str) -> str:
        """Open a new session. Returns the session_id."""
        sid = uuid.uuid4().hex
        cat_dir = self.root / category
        cat_dir.mkdir(parents=True, exist_ok=True)
        path = cat_dir / f"{sid}.jsonl"
        fh = path.open("a", buffering=1)  # line-buffered
        with self._lock:
            self._open_files[sid] = fh
            self._meta[sid] = {
                "id": sid,
                "category": category,
                "mode": mode,
                "started_ts": time.time(),
                "ended_ts": None,
                "event_count": 0,
                "file_path": str(path),
            }
        return sid

    def append(self, session_id: str, event: dict) -> None:
        with self._lock:
            fh = self._open_files.get(session_id)
            if fh is None:
                return  # session already closed; drop event
            fh.write(json.dumps(event, separators=(",", ":")) + "\n")
            self._meta[session_id]["event_count"] += 1

    def end(self, session_id: str) -> None:
        with self._lock:
            meta = self._meta.get(session_id)
            if not meta or meta["ended_ts"] is not None:
                return  # already ended — no-op
            fh = self._open_files.pop(session_id, None)
            if fh:
                try:
                    fh.flush()
                    fh.close()
                except Exception:
                    pass
            meta["ended_ts"] = time.time()
            self._append_to_index(meta)

    def _append_to_index(self, meta: dict) -> None:
        idx_path = self.root / "sessions.json"
        try:
            existing = json.loads(idx_path.read_text())
        except Exception:
            existing = {"sessions": []}
        # If the session is already in the index, do NOT overwrite (immutable end_ts)
        if any(e["id"] == meta["id"] for e in existing["sessions"]):
            return
        existing["sessions"].append(meta)
        # Atomic write
        tmp = idx_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(existing, indent=2))
        os.replace(tmp, idx_path)


ATTACK_REQUIRED_FIELDS = {
    "id", "operator_ip", "started_ts", "ended_ts", "mode",
    "allowlist_path", "allowlist_sha256",
    "confirm_token_consumed", "transport", "stop_reason",
}


PORTAL_REQUIRED_FIELDS = {
    "id", "operator_ip", "started_ts", "ended_ts",
    "ssid", "template_name", "template_sha256",
    "allowlist_sha256", "allowlist_entry",
    "transport", "captures_count", "captures_file",
    "stop_reason",
}


def write_portal_capture(*, state_root: Path | str, session_id: str,
                         fields: dict) -> Path:
    """Append one captured form post to the session's JSONL.

    File is 0600, owned by the running process's user. This bypasses
    MQTT entirely — captured content is never published on the bus.
    """
    root = Path(state_root)
    out_dir = root / "evilportal"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"captures-{session_id}.jsonl"
    line = json.dumps({"fields": fields, "ts": time.time()},
                      separators=(",", ":")) + "\n"
    # O_APPEND + 0600 on first create
    fd = os.open(out_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)
    # Re-apply 0600 in case existing file had different mode
    os.chmod(out_path, 0o600)
    return out_path


def write_portal_audit(*, state_root: Path | str, record: dict) -> Path:
    """Write a portal session audit record. Required-field invariant."""
    missing = PORTAL_REQUIRED_FIELDS - set(record.keys())
    if missing:
        raise ValueError(f"missing required portal-audit fields: {sorted(missing)}")
    root = Path(state_root)
    out_dir = root / "evilportal"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{record['id']}.json"
    out_path.write_text(json.dumps(record, indent=2))
    return out_path


def write_attack_audit(*, state_root: Path | str, record: dict) -> Path:
    """Write a HIGH-risk attack session record to attacks/<id>.json.

    Raises ValueError if the record is missing required fields. Returns
    the written file path. Idempotent — overwrites existing file with
    same id (the lifecycle ensures only one writer per session).
    """
    missing = ATTACK_REQUIRED_FIELDS - set(record.keys())
    if missing:
        raise ValueError(f"missing required attack-audit fields: {sorted(missing)}")
    root = Path(state_root)
    out_dir = root / "attacks"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{record['id']}.json"
    out_path.write_text(json.dumps(record, indent=2))
    return out_path

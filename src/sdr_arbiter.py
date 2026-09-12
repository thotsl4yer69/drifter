#!/usr/bin/env python3
"""Cross-process RTL-SDR ownership for DRIFTER field operations.

The Pi has one RTL-SDR but several consumers (rtl_433, spectrum survey,
IQ capture, signal hunt, rfaudio and ADS-B).  This module gives on-demand
operators a real cross-process lease instead of relying only on sleeps and
"device busy" retries.

A lease is advisory: legacy workers that have not yet adopted the helper are
still coordinated through their existing MQTT pause/resume contract.  New field
operations and rfaudio can use this lock directly, making the transition safe
and incremental.
"""
from __future__ import annotations

import fcntl
import json
import os
import time
from pathlib import Path

STATE_DIR = Path(os.getenv("DRIFTER_STATE_DIR", "/opt/drifter/state"))
LOCK_PATH = STATE_DIR / ".rtl_sdr.lock"
OWNER_PATH = STATE_DIR / "rtl_sdr_owner.json"


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def read_owner() -> dict:
    try:
        data = json.loads(OWNER_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


class SDRLease:
    """Non-reentrant cross-process advisory lease for the single RTL-SDR."""

    def __init__(self, owner: str, *, detail: str = "", timeout: float = 6.0):
        self.owner = str(owner or "unknown")[:64]
        self.detail = str(detail or "")[:160]
        self.timeout = max(0.0, float(timeout))
        self._fh = None
        self.acquired = False
        self.acquired_at = 0.0

    def acquire(self) -> bool:
        if self.acquired:
            return True
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self._fh = open(LOCK_PATH, "a+", encoding="utf-8")
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    self._fh.close()
                    self._fh = None
                    return False
                time.sleep(0.08)
        self.acquired = True
        self.acquired_at = time.time()
        _atomic_json(
            OWNER_PATH,
            {
                "owner": self.owner,
                "detail": self.detail,
                "pid": os.getpid(),
                "acquired_at": self.acquired_at,
                "ts": time.time(),
            },
        )
        return True

    def release(self) -> None:
        if not self._fh:
            self.acquired = False
            return
        try:
            current = read_owner()
            if current.get("pid") == os.getpid() and current.get("owner") == self.owner:
                _atomic_json(
                    OWNER_PATH,
                    {
                        "owner": "idle",
                        "detail": "",
                        "pid": None,
                        "released_by": self.owner,
                        "released_at": time.time(),
                        "ts": time.time(),
                    },
                )
        finally:
            try:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            finally:
                self._fh.close()
                self._fh = None
                self.acquired = False

    def __enter__(self):
        if not self.acquire():
            current = read_owner()
            who = current.get("owner") or "another worker"
            raise TimeoutError(f"RTL-SDR busy: {who}")
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False

#!/usr/bin/env python3
"""rfaudio entrypoint with cross-process RTL-SDR ownership.

The mature rfaudio worker keeps its existing MQTT contract and retry behaviour;
this wrapper only adds the same lease used by field survey/hunt/capture so two
processes can no longer both believe they own the dongle.
"""
from __future__ import annotations

import logging

import rfaudio
from sdr_arbiter import SDRLease, read_owner

log = logging.getLogger("drifter.field-rfaudio")
_lease: SDRLease | None = None
_orig_start = rfaudio._stream.start
_orig_stop = rfaudio._stream.stop


def _start(freq_mhz: float, mode: str, gain: float) -> bool:
    global _lease
    # Retunes within rfaudio keep the existing lease; a fresh session takes it.
    if _lease is None or not _lease.acquired:
        candidate = SDRLease("rfaudio", detail=f"{freq_mhz:.5f} MHz {mode}", timeout=8.0)
        if not candidate.acquire():
            log.warning("RTL-SDR busy: %s", read_owner().get("owner", "unknown"))
            return False
        _lease = candidate
    ok = _orig_start(freq_mhz, mode, gain)
    if not ok and _lease is not None:
        _lease.release()
        _lease = None
    return ok


def _stop() -> None:
    global _lease
    try:
        _orig_stop()
    finally:
        if _lease is not None:
            _lease.release()
            _lease = None


rfaudio._stream.start = _start
rfaudio._stream.stop = _stop

if __name__ == "__main__":
    try:
        rfaudio.main()
    finally:
        _stop()

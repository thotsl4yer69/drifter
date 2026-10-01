#!/usr/bin/env python3
"""
MZ1312 DRIFTER — RECON Evidence Indexer

Builds a local append-only evidence ledger for the camera/Hailo RECON persona.
The general DRIFTER logger still records all MQTT traffic; this service adds a
session-oriented, hash-chained index over evidence-bearing perception and ALPR
events so an operator can review what was captured without scrubbing raw logs.

This is an integrity aid, not a legal conclusion about authenticity.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import signal
import time
from datetime import datetime, timezone
from pathlib import Path

from config import DRIFTER_DIR, MQTT_HOST, MQTT_PORT, TOPICS, make_mqtt_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [RECON] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

RECON_DIR = DRIFTER_DIR / "recon"
SESSION_DIR = RECON_DIR / "sessions"
MEDIA_DIR = RECON_DIR / "media"
MAX_MEDIA_HASH_BYTES = max(
    1_048_576,
    int(os.getenv("DRIFTER_RECON_MAX_HASH_BYTES", str(256 * 1024 * 1024))),
)


def _canonical(value: dict) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _safe_media_path(raw: object) -> Path | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        root = RECON_DIR.resolve()
        path = Path(raw).resolve(strict=False)
        path.relative_to(root)
    except (OSError, ValueError):
        return None
    return path


def _sha256_file(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        if not path.is_file():
            return None
        if path.stat().st_size > MAX_MEDIA_HASH_BYTES:
            return None
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


class ReconLedger:
    def __init__(self, now: float | None = None) -> None:
        now = time.time() if now is None else float(now)
        stamp = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        # Microseconds + PID prevents two rapid service restarts from appending a
        # new zero-based hash chain into the same ledger filename.
        self.session_id = f"recon-{stamp}-{os.getpid()}"
        self.started_at = now
        self.sequence = 0
        self.chain_head = "0" * 64
        self.event_count = 0
        self.evidence_count = 0
        self.gps: dict = {}
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        self.path = SESSION_DIR / f"{self.session_id}.jsonl"

    def update_gps(self, data: object) -> None:
        if not isinstance(data, dict):
            return
        lat = data.get("lat")
        lon = data.get("lon", data.get("lng"))
        if lat is None or lon is None:
            return
        self.gps = {
            "lat": lat,
            "lon": lon,
            "accuracy": data.get("accuracy", data.get("acc")),
            "mode": data.get("mode"),
        }

    def append(
        self,
        kind: str,
        payload: dict,
        *,
        media_path: object = None,
        now: float | None = None,
    ) -> dict:
        ts = time.time() if now is None else float(now)
        self.sequence += 1
        safe_media = _safe_media_path(media_path)
        media_hash = _sha256_file(safe_media)
        body = {
            "session_id": self.session_id,
            "seq": self.sequence,
            "ts": ts,
            "kind": str(kind),
            "gps": self.gps or None,
            "media_path": str(safe_media) if safe_media is not None else None,
            "media_sha256": media_hash,
            "payload": payload,
            "prev_hash": self.chain_head,
        }
        digest = hashlib.sha256(_canonical(body)).hexdigest()
        record = {**body, "hash": digest}
        line = _canonical(record) + b"\n"
        with self.path.open("ab", buffering=0) as handle:
            handle.write(line)
            os.fsync(handle.fileno())
        self.chain_head = digest
        self.event_count += 1
        if kind not in {"session_start", "session_end"}:
            self.evidence_count += 1
        return record

    def status(self, *, state: str = "online") -> dict:
        return {
            "state": state,
            "session_id": self.session_id,
            "started_at": self.started_at,
            "event_count": self.event_count,
            "evidence_count": self.evidence_count,
            "chain_head": self.chain_head,
            "ledger_path": str(self.path),
            "ts": time.time(),
        }


def main() -> None:
    log.info("DRIFTER RECON evidence indexer starting...")
    ledger = ReconLedger()
    running = True

    def _stop(_sig, _frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    client = make_mqtt_client("drifter-recon-index")

    def publish_status(state: str = "online") -> None:
        client.publish(
            TOPICS["recon_status"],
            json.dumps(ledger.status(state=state)),
            qos=1,
            retain=True,
        )

    def on_message(_client, _userdata, msg) -> None:
        try:
            data = json.loads(msg.payload)
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
            return
        if msg.topic == TOPICS["gps_fix"]:
            ledger.update_gps(data)
            return
        if not isinstance(data, dict):
            return

        kind = None
        media_path = None
        if msg.topic == TOPICS["vision_object"]:
            # Only evidence-bearing frames are indexed. Normal DRIVE detections
            # deliberately omit evidence_path, so they cannot leak into RECON.
            media_path = data.get("evidence_path")
            if not media_path:
                return
            kind = "vision"
        elif msg.topic == TOPICS["alpr_plate"]:
            kind = "plate"
            media_path = data.get("evidence_path")
        elif msg.topic == TOPICS.get("dashcam_clip"):
            kind = "clip"
            media_path = data.get("path")
        else:
            return

        try:
            record = ledger.append(kind, data, media_path=media_path)
        except (OSError, ValueError, TypeError) as exc:
            log.warning("evidence append failed: %s", exc)
            return

        client.publish(
            TOPICS["recon_event"],
            json.dumps({
                "session_id": record["session_id"],
                "seq": record["seq"],
                "kind": record["kind"],
                "hash": record["hash"],
                "media_path": record["media_path"],
                "ts": record["ts"],
            }),
        )
        publish_status()
        log.info("indexed %s #%s %s", kind, record["seq"], record["hash"][:12])

    client.on_message = on_message
    while running:
        try:
            client.connect(MQTT_HOST, MQTT_PORT, 60)
            break
        except Exception as exc:
            log.warning("Waiting for MQTT broker... (%s)", exc)
            time.sleep(3)
    if not running:
        return

    client.subscribe([
        (TOPICS["gps_fix"], 0),
        (TOPICS["vision_object"], 0),
        (TOPICS["alpr_plate"], 0),
        (TOPICS["dashcam_clip"], 0),
    ])
    client.loop_start()

    # The session-start record anchors the chain even if no detections occur.
    ledger.append("session_start", {"mode": "recon"})
    publish_status()
    log.info("RECON session %s -> %s", ledger.session_id, ledger.path)

    while running:
        time.sleep(1)

    try:
        ledger.append("session_end", {"reason": "service_stop"})
    except OSError:
        pass
    publish_status("offline")
    client.loop_stop()
    client.disconnect()
    log.info("RECON evidence indexer stopped")


if __name__ == "__main__":
    main()

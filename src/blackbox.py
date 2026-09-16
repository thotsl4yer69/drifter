#!/usr/bin/env python3
"""DRIFTER deterministic vehicle black-box incident capture."""
from __future__ import annotations

import gzip
import json
import math
import os
import statistics
import time
from collections import defaultdict, deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PREFIXES = (
    "drifter/engine/", "drifter/vehicle/", "drifter/power/", "drifter/diag/",
    "drifter/obd/", "drifter/alert/", "drifter/anomaly/", "drifter/trip/",
    "drifter/system/",
)
EXACT = {
    "drifter/snapshot", "drifter/gps/fix", "drifter/network/status",
    "drifter/boot/status", "drifter/incident/trigger",
}
SENSORS = {
    "rpm": "drifter/engine/rpm",
    "coolant": "drifter/engine/coolant",
    "stft_b1": "drifter/engine/stft1",
    "stft_b2": "drifter/engine/stft2",
    "ltft_b1": "drifter/engine/ltft1",
    "ltft_b2": "drifter/engine/ltft2",
    "maf": "drifter/engine/maf",
    "throttle": "drifter/engine/throttle",
    "load": "drifter/engine/load",
    "voltage": "drifter/power/voltage",
    "iat": "drifter/engine/iat",
    "map": "drifter/engine/map",
    "speed": "drifter/vehicle/speed",
}
FLOORS = {
    "rpm": (120.0, .15), "coolant": (3.0, .03),
    "stft_b1": (8.0, 0), "stft_b2": (8.0, 0),
    "ltft_b1": (8.0, 0), "ltft_b2": (8.0, 0),
    "maf": (1.0, .25), "throttle": (8.0, .20), "load": (12.0, .25),
    "voltage": (.4, .03), "iat": (5.0, .10), "map": (8.0, .15),
    "speed": (5.0, .20),
}
TOPIC_TO_SENSOR = {topic: name for name, topic in SENSORS.items()}


def _num(data: Any) -> float | None:
    value = data.get("value") if isinstance(data, dict) else data
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _med(values: list[float]) -> float | None:
    return float(statistics.median(values)) if values else None


def _mad(values: list[float], center: float) -> float:
    return float(statistics.median(abs(x - center) for x in values)) if values else 0.0


def _slug(text: str) -> str:
    text = "".join(c.lower() if c.isalnum() else "_" for c in text)
    return "_".join(x for x in text.split("_") if x)[:48] or "incident"


class IncidentBlackBox:
    """90s pre-fault ring + post-fault tail + deterministic first-mover analysis."""

    def __init__(
        self, incident_dir: Path, *, pre_seconds: float = 90,
        post_seconds: float = 45, max_records: int = 15000,
        cooldown_seconds: float = 90,
    ):
        self.incident_dir = Path(incident_dir)
        self.pre_seconds = max(10.0, float(pre_seconds))
        self.post_seconds = max(5.0, float(post_seconds))
        self.buffer = deque(maxlen=max(1000, int(max_records)))
        self.cooldown_seconds = max(10.0, float(cooldown_seconds))
        self.active: dict | None = None
        self.last_completed: dict | None = None
        self.last_trigger: dict[str, float] = {}
        self.values: dict[str, float] = {}
        self.rpm_window: deque[tuple[float, float]] = deque(maxlen=100)
        self.last_engine_ts = 0.0

    @staticmethod
    def relevant_topic(topic: str) -> bool:
        return topic in EXACT or topic.startswith(PREFIXES)

    def _prune(self, now: float) -> None:
        cutoff = now - self.pre_seconds
        while self.buffer and self.buffer[0]["ts"] < cutoff:
            self.buffer.popleft()

    def _update(self, rec: dict) -> None:
        sensor = TOPIC_TO_SENSOR.get(rec["topic"])
        value = _num(rec["data"])
        if not sensor or value is None:
            return
        ts = rec["ts"]
        self.values[sensor] = value
        if sensor == "rpm":
            self.rpm_window.append((ts, value))
            if value >= 450:
                self.last_engine_ts = ts
            cutoff = ts - 12
            while self.rpm_window and self.rpm_window[0][0] < cutoff:
                self.rpm_window.popleft()

    def trigger(
        self, reason: str, ts: float | None = None, *, source: str = "automatic",
        note: str = "", severity: str = "warning", manual: bool = False,
    ) -> dict | None:
        ts = float(ts or time.time())
        reason = _slug(reason)
        if self.active:
            is_new_reason = reason not in self.active["reasons"]
            if is_new_reason:
                self.active["reasons"].append(reason)
            if note and note not in self.active["notes"]:
                self.active["notes"].append(note[:300])
            # Do not let a sustained condition extend the incident forever.
            # Only a materially new trigger opens another post-fault tail.
            if is_new_reason:
                self.active["end_at"] = max(self.active["end_at"], ts + self.post_seconds)
            return {"active": True, "id": self.active["id"],
                    "reasons": list(self.active["reasons"]),
                    "extended": is_new_reason, "ts": ts}

        if not manual and ts - self.last_trigger.get(reason, 0) < self.cooldown_seconds:
            return None
        self.last_trigger[reason] = ts
        stamp = datetime.fromtimestamp(ts, UTC).strftime("%Y%m%dT%H%M%S")
        self.active = {
            "id": f"{stamp}-{reason}", "trigger": ts, "source": source,
            "severity": severity, "reasons": [reason],
            "notes": [note[:300]] if note else [], "end_at": ts + self.post_seconds,
            "records": list(self.buffer),
        }
        return {"active": True, "id": self.active["id"], "trigger": ts,
                "source": source, "severity": severity, "reasons": [reason],
                "pre_seconds": self.pre_seconds, "post_seconds": self.post_seconds, "ts": ts}

    def _automatic(self, rec: dict) -> dict | None:
        topic, data, ts = rec["topic"], rec["data"], rec["ts"]
        if topic == "drifter/incident/trigger":
            p = data if isinstance(data, dict) else {}
            return self.trigger(str(p.get("reason") or "manual_capture"), ts,
                source="touchscreen", note=str(p.get("note") or ""),
                severity=str(p.get("severity") or "info"), manual=True)

        if topic == "drifter/anomaly/event" and isinstance(data, dict):
            return self.trigger(f"anomaly_{data.get('sensor', 'vehicle')}", ts,
                source="anomaly_monitor", note=json.dumps(data, separators=(",", ":"))[:300],
                severity=str(data.get("severity") or "warning"))

        if topic == "drifter/alert/message" and isinstance(data, dict):
            try:
                level = int(data.get("level", 0) or 0)
            except (TypeError, ValueError):
                level = 0
            if level >= 2:
                return self.trigger(f"alert_{data.get('name') or 'vehicle'}", ts,
                    source="alert_engine", note=str(data.get("message") or ""),
                    severity="critical" if level >= 3 else "warning")

        if topic == "drifter/obd/status" and isinstance(data, dict):
            state = str(data.get("state") or "").lower()
            if state in {"adapter_error", "bus_unreachable"} and ts - self.last_engine_ts < 10:
                return self.trigger(f"obd_{state}", ts, source="obd_health",
                    note=str(data.get("reason") or ""))

        sensor, value = TOPIC_TO_SENSOR.get(topic), _num(data)
        if sensor is None or value is None:
            return None
        coolant = self.values.get("coolant", 0)
        speed = self.values.get("speed", 0)
        throttle = self.values.get("throttle", 0)
        engine_recent = ts - self.last_engine_ts < 8

        if sensor == "coolant" and value >= 108:
            return self.trigger("coolant_critical", ts, source="blackbox",
                                note=f"coolant={value:.1f}C", severity="critical")
        if sensor == "voltage" and engine_recent and value < 11.7:
            return self.trigger("voltage_collapse", ts, source="blackbox",
                                note=f"voltage={value:.2f}V", severity="critical")
        if sensor in {"ltft_b1", "ltft_b2"} and coolant >= 60 and abs(value) >= 25:
            return self.trigger(f"{sensor}_extreme", ts, source="blackbox",
                                note=f"{sensor}={value:.1f}%")
        if sensor in {"stft_b1", "stft_b2"} and coolant >= 60 and abs(value) >= 25:
            return self.trigger(f"{sensor}_excursion", ts, source="blackbox",
                                note=f"{sensor}={value:.1f}%")

        if sensor == "rpm" and coolant >= 60 and speed <= 2:
            prior = [v for t, v in self.rpm_window
                     if ts - t <= 8 and t < ts - .25 and v >= 350]
            baseline = _med(prior)
            if baseline and 550 <= baseline <= 1000 and value <= baseline - 180 and throttle <= 20:
                return self.trigger("idle_rpm_collapse", ts, source="blackbox",
                    note=f"rpm {baseline:.0f}->{value:.0f}, speed={speed:.1f}")
            vals = [v for t, v in self.rpm_window if ts - t <= 6 and v >= 350]
            if len(vals) >= 8 and max(vals) - min(vals) >= 200 and (_med(vals) or 9999) <= 1000:
                return self.trigger("idle_instability", ts, source="blackbox",
                                    note=f"rpm spread={max(vals)-min(vals):.0f}")
        return None

    def ingest(self, topic: str, data: Any, ts: float | None = None) -> dict | None:
        ts = float(ts or time.time())
        if not self.relevant_topic(topic):
            return None
        rec = {"ts": ts, "topic": topic, "data": data}
        self.buffer.append(rec)
        self._prune(ts)
        if self.active:
            self.active["records"].append(rec)
        self._update(rec)
        return self._automatic(rec)

    def _changes(self, records: list[dict], trigger: float) -> list[dict]:
        series: dict[str, list[tuple[float, float]]] = defaultdict(list)
        for rec in records:
            sensor = TOPIC_TO_SENSOR.get(rec.get("topic", ""))
            value = _num(rec.get("data"))
            if sensor and value is not None:
                series[sensor].append((float(rec["ts"]), value))
        out = []
        for sensor, points in series.items():
            base = [v for t, v in points if trigger - self.pre_seconds <= t <= trigger - 5]
            if len(base) < 5:
                base = [v for t, v in points if t < trigger]
            if len(base) < 3:
                continue
            center = _med(base)
            if center is None:
                continue
            abs_floor, rel_floor = FLOORS.get(sensor, (1.0, .2))
            threshold = max(abs_floor, abs(center) * rel_floor, _mad(base, center) * 4)
            for t, value in points:
                if t < trigger - 12:
                    continue
                delta = value - center
                if abs(delta) >= threshold:
                    out.append({"sensor": sensor, "offset_s": round(t-trigger, 2),
                        "baseline": round(center, 3), "value": round(value, 3),
                        "delta": round(delta, 3), "direction": "up" if delta > 0 else "down",
                        "threshold": round(threshold, 3)})
                    break
        return sorted(out, key=lambda x: x["offset_s"])

    @staticmethod
    def _flags(changes: list[dict]) -> list[dict]:
        s = {x["sensor"]: x for x in changes}
        flags = []
        rpm, throttle, voltage, maf = s.get("rpm"), s.get("throttle"), s.get("voltage"), s.get("maf")
        trims = [s.get(x) for x in ("stft_b1", "stft_b2", "ltft_b1", "ltft_b2")]
        if rpm and rpm["direction"] == "down":
            if voltage and voltage["direction"] == "down" and voltage["offset_s"] <= rpm["offset_s"]:
                flags.append({"flag": "electrical_change_precedes_rpm_drop", "evidence": [voltage, rpm]})
            if maf and maf["direction"] == "down" and (not throttle or throttle["offset_s"] > maf["offset_s"]):
                flags.append({"flag": "airflow_drop_without_prior_throttle_change", "evidence": [maf, rpm]})
            lean = [x for x in trims if x and x["direction"] == "up" and x["delta"] >= 8]
            if lean and (not throttle or throttle["offset_s"] > lean[0]["offset_s"]):
                flags.append({"flag": "lean_trim_change_precedes_or_matches_rpm_drop",
                              "evidence": lean[:2] + [rpm]})
            if throttle and throttle["offset_s"] <= rpm["offset_s"]:
                flags.append({"flag": "commanded_throttle_change_precedes_rpm_change",
                              "evidence": [throttle, rpm]})
        b1 = s.get("stft_b1") or s.get("ltft_b1")
        b2 = s.get("stft_b2") or s.get("ltft_b2")
        if b1 and b2 and b1["direction"] == b2["direction"] == "up":
            flags.append({"flag": "both_banks_move_lean_together", "evidence": [b1, b2]})
        elif bool(b1 and b1["direction"] == "up") ^ bool(b2 and b2["direction"] == "up"):
            flags.append({"flag": "bank_specific_trim_divergence",
                          "evidence": [x for x in (b1, b2) if x]})
        return flags

    def _write(self, incident: dict, summary: dict) -> tuple[Path, Path]:
        self.incident_dir.mkdir(parents=True, exist_ok=True)
        base = self.incident_dir / f"incident_{incident['id']}"
        data_path, summary_path = Path(str(base)+".jsonl.gz"), Path(str(base)+".json")
        tmp = Path(str(data_path)+f".tmp.{os.getpid()}")
        with gzip.open(tmp, "wt") as fh:
            for rec in incident["records"]:
                fh.write(json.dumps(rec, separators=(",", ":"), default=str) + "\n")
        os.replace(tmp, data_path)
        tmp = Path(str(summary_path)+f".tmp.{os.getpid()}")
        with open(tmp, "w") as fh:
            json.dump(summary, fh, indent=2, default=str)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, summary_path)
        return data_path, summary_path

    def finalize(self, now: float | None = None, *, force: bool = False) -> dict | None:
        if not self.active:
            return None
        now = float(now or time.time())
        if not force and now < self.active["end_at"]:
            return None
        incident, self.active = self.active, None
        changes = self._changes(incident["records"], incident["trigger"])
        summary = {
            "id": incident["id"], "trigger": incident["trigger"], "completed": now,
            "source": incident["source"], "severity": incident["severity"],
            "reasons": incident["reasons"], "notes": incident["notes"],
            "pre_seconds": self.pre_seconds,
            "post_seconds": round(now - incident["trigger"], 2),
            "record_count": len(incident["records"]),
            "first_change": changes[0] if changes else None,
            "first_changes": changes[:12],
            "correlation_flags": self._flags(changes),
        }
        data_path, summary_path = self._write(incident, summary)
        summary["data_file"], summary["summary_file"] = str(data_path), str(summary_path)
        self.last_completed = summary
        return summary

    def status(self) -> dict:
        base = {"buffer_records": len(self.buffer), "pre_seconds": self.pre_seconds,
                "post_seconds": self.post_seconds, "ts": time.time()}
        if self.active:
            return {**base, "active": True, "id": self.active["id"],
                    "trigger": self.active["trigger"], "reasons": list(self.active["reasons"]),
                    "end_at": self.active["end_at"]}
        return {**base, "active": False, "last": self.last_completed}

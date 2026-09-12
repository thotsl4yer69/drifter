#!/usr/bin/env python3
"""Field-first passive RTL-SDR survey, hunt, listen and IQ capture.

This module sits above the mature rf_monitor/rfaudio workers. It turns the
single RTL-SDR into an operator instrument instead of exposing raw rtl_power
primitives: broad survey -> ranked candidates -> targeted zoom, proximity hunt,
listen and SigMF IQ capture. All operations are receive-only.
"""
from __future__ import annotations

import json
import logging
import math
import os
import statistics
import subprocess
import threading
import time
from collections import deque
from collections.abc import Iterable
from pathlib import Path

from config import MQTT_HOST, MQTT_PORT, TOPICS, atomic_write_json, make_mqtt_client
from sdr_arbiter import SDRLease, read_owner

log = logging.getLogger("drifter.rfops")

CMD = "drifter/rf/ops/command"
OPS = "drifter/rf/ops"
FINDINGS = "drifter/rf/findings"
HUNT = "drifter/rf/hunt"
CAPTURE = "drifter/rf/capture"
ZOOM = "drifter/rf/spectrum/zoom"
SUMMARY = "drifter/rf/spectrum/summary"
CLASSIFY = "drifter/rf/classification"

STATE = Path(os.getenv("DRIFTER_STATE_DIR", "/opt/drifter/state"))
CAPDIR = STATE / "rf_captures"
FINDINGS_FILE = STATE / "rf_findings.json"
BASELINE_FILE = STATE / "rf_baseline.json"
REGION = (os.getenv("DRIFTER_RF_REGION", "AU") or "AU").upper()
SURVEY_TIMEOUT_S = float(os.getenv("DRIFTER_RF_SURVEY_TIMEOUT", "135"))


def clamp(value, lo, hi):
    return max(lo, min(hi, value))


def valid_freq(value):
    try:
        freq = float(value)
    except (TypeError, ValueError):
        return None
    return freq if 24.0 <= freq <= 1766.0 else None


def band_context(freq_mhz: float, region: str = REGION) -> str:
    """Return frequency context, never a claim about who/what is transmitting."""
    f = float(freq_mhz)
    if 87.5 <= f <= 108.0:
        return "FM broadcast band"
    if 118.0 <= f <= 137.0:
        return "airband"
    if 156.0 <= f <= 163.0:
        return "marine VHF range"
    if 433.05 <= f <= 434.79:
        return "433 MHz short-range/ISM-LIPD range"
    if region == "AU" and 476.4 <= f <= 477.5:
        return "AU UHF-CB range"
    if region == "AU" and 915.0 <= f <= 928.0:
        return "AU 915-928 MHz ISM/LIPD range"
    if 1089.0 <= f <= 1091.0:
        return "1090 MHz ADS-B range"
    if 1574.0 <= f <= 1577.0:
        return "GNSS L1 range"
    if 703.0 <= f <= 960.0 or 1710.0 <= f <= 1766.0:
        return "cellular-band energy"
    if 144.0 <= f <= 148.0:
        return "2 m amateur range"
    if 430.0 <= f <= 450.0:
        return "70 cm/UHF shared range"
    return "unlabelled spectrum"


def parse_rtl_power(lines: Iterable[str]) -> list[dict]:
    bins: list[dict] = []
    for line in lines:
        parts = line.strip().split(",")
        if len(parts) < 7:
            continue
        try:
            low_hz = float(parts[2])
            step_hz = float(parts[4])
            values = [float(x) for x in parts[6:] if x.strip()]
        except ValueError:
            continue
        for index, db in enumerate(values):
            if math.isfinite(db):
                bins.append({"freq_hz": low_hz + index * step_hz, "db": db})
    return bins


def summary_candidates(summary: dict, margin_db: float = 10.0,
                       max_count: int = 7) -> list[dict]:
    """Rank broad-sweep peaks by delta over the sweep's median noise floor."""
    groups = []
    for item in summary.get("bins", []) if isinstance(summary, dict) else []:
        try:
            freq_mhz = float(item["freq_hz"]) / 1e6
            level = float(item.get("level_db_max", item["level_db_mean"]))
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(level):
            groups.append((freq_mhz, level))
    if not groups:
        return []

    floor = statistics.median(level for _, level in groups)
    ranked = sorted(
        (
            {
                "freq_mhz": freq,
                "peak_db": level,
                "noise_db": floor,
                "delta_db": level - floor,
            }
            for freq, level in groups
            if level - floor >= margin_db
        ),
        key=lambda item: item["delta_db"],
        reverse=True,
    )

    selected = []
    for candidate in ranked:
        # One targeted scan per broad neighbourhood; otherwise a wide carrier
        # creates several adjacent candidates and wastes most of the field run.
        if any(abs(candidate["freq_mhz"] - old["freq_mhz"]) < 5.0
               for old in selected):
            continue
        selected.append(candidate)
        if len(selected) >= max_count:
            break
    return selected


def analyse_bins(bins: list[dict], seed_mhz: float | None = None) -> dict | None:
    clean = [
        item for item in bins
        if isinstance(item.get("freq_hz"), (int, float))
        and isinstance(item.get("db"), (int, float))
        and math.isfinite(float(item["db"]))
    ]
    if not clean:
        return None

    floor = statistics.median(float(item["db"]) for item in clean)
    peak = max(clean, key=lambda item: float(item["db"]))
    peak_db = float(peak["db"])
    delta = peak_db - floor
    threshold = floor + max(6.0, delta * 0.45)
    hot = [item for item in clean if float(item["db"]) >= threshold]
    bandwidth_khz = 0.0
    if hot:
        bandwidth_khz = (
            max(float(item["freq_hz"]) for item in hot)
            - min(float(item["freq_hz"]) for item in hot)
        ) / 1000.0
    freq_mhz = float(peak["freq_hz"]) / 1e6
    return {
        "freq_mhz": round(freq_mhz, 5),
        "seed_mhz": seed_mhz,
        "peak_db": round(peak_db, 1),
        "noise_db": round(floor, 1),
        "delta_db": round(delta, 1),
        "bandwidth_khz": round(bandwidth_khz, 1),
        "context": band_context(freq_mhz),
        # Confidence here means confidence that this is an energy peak above
        # local noise. It is intentionally NOT protocol/identity confidence.
        "confidence": round(clamp((delta - 5.0) / 25.0, 0.0, 0.99), 2),
    }


class RFOps:
    def __init__(self):
        self.client = make_mqtt_client("drifter-rf-ops")
        self.client.on_message = self.on_message
        self.running = False
        self.lock = threading.Lock()
        self.worker: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.hunt_stop = threading.Event()
        self.pending_survey = False
        self.survey_generation = 0
        self.findings: deque[dict] = deque(maxlen=50)
        self.gps: dict = {}
        self._load_findings()

    def _load_findings(self) -> None:
        try:
            payload = json.loads(FINDINGS_FILE.read_text(encoding="utf-8"))
            for item in payload.get("findings", []):
                if isinstance(item, dict):
                    self.findings.append(item)
        except (OSError, ValueError, TypeError):
            pass

    def publish(self, topic: str, payload: dict, retain: bool = False) -> None:
        self.client.publish(topic, json.dumps(payload), qos=1 if retain else 0,
                            retain=retain)

    def ops(self, mode: str, stage: str, progress: int = 0, **extra) -> None:
        self.publish(OPS, {
            "mode": mode,
            "stage": stage,
            "progress": int(clamp(progress, 0, 100)),
            "owner": read_owner().get("owner", "idle"),
            "region": REGION,
            "ts": time.time(),
            **extra,
        }, True)

    def publish_findings(self) -> None:
        payload = {
            "findings": list(self.findings)[:12],
            "count": len(self.findings),
            "region": REGION,
            "ts": time.time(),
        }
        try:
            atomic_write_json(FINDINGS_FILE, payload)
        except OSError:
            pass
        self.publish(FINDINGS, payload, True)

    def upsert(self, finding: dict) -> None:
        now = time.time()
        freq = finding.get("freq_mhz")
        finding.setdefault("first_seen", now)
        finding["last_seen"] = now
        finding.setdefault("status", "NEW")

        for old in list(self.findings):
            try:
                same = freq is not None and abs(float(old.get("freq_mhz")) - float(freq)) <= 0.04
            except (TypeError, ValueError):
                same = False
            if not same:
                continue
            # Repeated TPMS/rtl_433 frames can arrive many times a second. Do
            # not turn them into a flash-write loop just to refresh last_seen.
            if now - float(old.get("last_seen") or 0) < 5.0:
                return
            finding["first_seen"] = old.get("first_seen", finding["first_seen"])
            self.findings.remove(old)
            break

        self.findings.appendleft(finding)
        self.publish_findings()

    def pause_legacy(self, wait_s: float = 2.0) -> None:
        self.publish(TOPICS.get("rf_command", "drifter/rf/command"), {
            "command": "pause_rtl_433",
            "reason": "rf_ops",
            "ts": time.time(),
        })
        # rf_monitor must flush/close rtl_433 or rtl_power before we claim USB.
        time.sleep(max(0.0, wait_s))

    def resume_legacy(self) -> None:
        self.publish(TOPICS.get("rf_command", "drifter/rf/command"), {
            "command": "resume_rtl_433",
            "reason": "rf_ops",
            "ts": time.time(),
        })

    def start_worker(self, fn, *args, mode: str) -> bool:
        with self.lock:
            if self.pending_survey and mode != "survey":
                self.ops(mode, "busy", 0, error="survey is still using the RTL-SDR")
                return False
            if self.worker is not None and self.worker.is_alive():
                self.ops(mode, "busy", 0, error="another RF operation is active")
                return False
            self.stop_event.clear()
            self.worker = threading.Thread(
                target=fn,
                args=args,
                daemon=True,
                name=f"rfops-{mode}",
            )
            self.worker.start()
            return True

    @staticmethod
    def sweep(center_mhz: float, span_mhz: float, bin_hz: int,
              timeout: float = 15.0) -> list[dict]:
        low = max(24.0, center_mhz - span_mhz / 2.0)
        high = min(1766.0, center_mhz + span_mhz / 2.0)
        command = [
            "rtl_power",
            "-f", f"{low:.6f}M:{high:.6f}M:{max(1000, int(bin_hz))}",
            "-i", "1",
            "-1",
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        if result.returncode != 0:
            return []
        return parse_rtl_power(result.stdout.splitlines())

    # ── Survey ──────────────────────────────────────────────────────
    def survey(self) -> None:
        with self.lock:
            if self.pending_survey or (self.worker is not None and self.worker.is_alive()):
                self.ops("survey", "busy", 0, error="another RF operation is active")
                return
            self.pending_survey = True
            self.survey_generation += 1
            generation = self.survey_generation

        # A survey owns the radio mission. Stop audio first, then ask the mature
        # rf_monitor to perform its one broad forced sweep. The forced summary
        # is our trigger for targeted zooms.
        self.publish(TOPICS.get("rfaudio_command", "drifter/rfaudio/command"), {
            "action": "stop", "ts": time.time(),
        })
        self.ops("survey", "broad sweep", 5,
                 message="mapping spectrum before targeted zoom")
        self.publish(TOPICS.get("rf_command", "drifter/rf/command"), {
            "command": "force_spectrum",
            "requested_by": "rf_ops",
            "ts": time.time(),
        })
        threading.Thread(
            target=self._survey_timeout_guard,
            args=(generation,),
            daemon=True,
            name="rfops-survey-timeout",
        ).start()

    def _survey_timeout_guard(self, generation: int) -> None:
        time.sleep(SURVEY_TIMEOUT_S)
        if self.pending_survey and generation == self.survey_generation:
            self.stop_survey(timeout=True)

    def stop_survey(self, timeout: bool = False) -> None:
        self.pending_survey = False
        self.stop_event.set()
        # pause kills an in-flight force_spectrum subprocess in rf_monitor;
        # resume immediately restores normal TPMS monitoring afterwards.
        self.pause_legacy(1.2)
        self.resume_legacy()
        self.ops(
            "idle",
            "survey timed out" if timeout else "survey stopped",
            0 if timeout else 100,
            **({"error": "broad sweep produced no forced summary; retry or reset RF"}
               if timeout else {}),
        )

    def deep_survey(self, summary: dict) -> None:
        candidates = summary_candidates(summary)
        if not candidates:
            self.pending_survey = False
            self.ops("idle", "survey complete", 100,
                     message="no peaks cleared adaptive threshold")
            return

        self.pause_legacy()
        lease = SDRLease("rf-survey", detail=f"{len(candidates)} targeted zooms", timeout=8)
        if not lease.acquire():
            self.pending_survey = False
            self.ops("idle", "survey blocked", 0,
                     error=f"RTL-SDR busy: {read_owner().get('owner', 'unknown')}")
            self.resume_legacy()
            return

        try:
            baseline = self._baseline()
            total = len(candidates)
            for index, seed in enumerate(candidates):
                if self.stop_event.is_set():
                    break
                self.ops(
                    "survey",
                    f"zoom {index + 1}/{total}",
                    25 + int(70 * index / max(1, total)),
                    freq_mhz=seed["freq_mhz"],
                )
                finding = analyse_bins(
                    self.sweep(seed["freq_mhz"], 4.0, 25_000),
                    seed["freq_mhz"],
                )
                if finding is None or finding["delta_db"] < 6.0:
                    continue
                finding.update({
                    "id": f"rf-{int(time.time())}-{round(finding['freq_mhz'] * 1000)}",
                    "status": "KNOWN" if self._known(finding["freq_mhz"], baseline) else "NEW",
                    "source": "adaptive-survey",
                })
                self.upsert(finding)
        finally:
            lease.release()
            self.resume_legacy()
            self.pending_survey = False
        self.ops("idle", "survey complete", 100,
                 message=f"{len(self.findings)} findings retained")

    def _baseline(self) -> dict:
        try:
            payload = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    @staticmethod
    def _known(freq_mhz: float, baseline: dict) -> bool:
        for item in baseline.get("findings", []) if isinstance(baseline, dict) else []:
            try:
                if abs(float(item.get("freq_mhz")) - freq_mhz) <= 0.08:
                    return True
            except (TypeError, ValueError):
                pass
        return False

    # ── Hunt / zoom / IQ capture / listen ─────────────────────────
    def hunt(self, freq_mhz: float) -> None:
        self.hunt_stop.clear()
        self.start_worker(self.hunt_worker, freq_mhz, mode="hunt")

    def hunt_worker(self, freq_mhz: float) -> None:
        self.pause_legacy()
        lease = SDRLease("rf-hunt", detail=f"{freq_mhz:.5f} MHz", timeout=8)
        if not lease.acquire():
            self.ops("idle", "hunt blocked", 0,
                     error=f"RTL-SDR busy: {read_owner().get('owner', 'unknown')}")
            self.resume_legacy()
            return
        levels: deque[float] = deque(maxlen=8)
        try:
            while not self.hunt_stop.is_set() and not self.stop_event.is_set():
                finding = analyse_bins(
                    self.sweep(freq_mhz, 0.24, 5_000, 8),
                    freq_mhz,
                )
                if finding:
                    levels.append(float(finding["peak_db"]))
                    trend_db = 0.0 if len(levels) < 2 else levels[-1] - levels[0]
                    self.publish(HUNT, {
                        "active": True,
                        **finding,
                        "trend_db": round(trend_db, 1),
                        "trend": "stronger" if trend_db > 3 else "weaker" if trend_db < -3 else "steady",
                        "samples": list(levels),
                        "ts": time.time(),
                    }, True)
                self.ops("hunt", "tracking", 50, freq_mhz=freq_mhz)
                if self.hunt_stop.wait(0.2):
                    break
        finally:
            lease.release()
            self.resume_legacy()
            self.publish(HUNT, {"active": False, "freq_mhz": freq_mhz, "ts": time.time()}, True)
            self.ops("idle", "hunt stopped", 100)

    def capture(self, freq_mhz: float, duration_s: float = 15.0) -> None:
        self.start_worker(
            self.capture_worker,
            freq_mhz,
            clamp(float(duration_s), 1.0, 30.0),
            mode="capture",
        )

    def capture_worker(self, freq_mhz: float, duration_s: float) -> None:
        self.pause_legacy()
        lease = SDRLease(
            "rf-capture",
            detail=f"{freq_mhz:.5f} MHz/{duration_s:.0f}s",
            timeout=8,
        )
        if not lease.acquire():
            self.ops("idle", "capture blocked", 0,
                     error=f"RTL-SDR busy: {read_owner().get('owner', 'unknown')}")
            self.resume_legacy()
            return

        CAPDIR.mkdir(parents=True, exist_ok=True)
        sample_rate = 250_000
        stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
        stem = f"{stamp}_{freq_mhz:.5f}MHz"
        data_path = CAPDIR / f"{stem}.sigmf-data"
        meta_path = CAPDIR / f"{stem}.sigmf-meta"
        self.ops("capture", "recording IQ", 20,
                 freq_mhz=freq_mhz, duration_s=duration_s)

        try:
            result = subprocess.run(
                [
                    "rtl_sdr",
                    "-f", str(int(freq_mhz * 1e6)),
                    "-s", str(sample_rate),
                    "-n", str(int(sample_rate * duration_s)),
                    str(data_path),
                ],
                capture_output=True,
                timeout=duration_s + 8,
                check=False,
            )
            if result.returncode != 0 or not data_path.exists():
                error = (result.stderr or b"rtl_sdr failed").decode(errors="replace")[:180]
                raise RuntimeError(error)

            global_meta = {
                "core:datatype": "cu8",
                "core:sample_rate": sample_rate,
                "core:version": "1.0.0",
                "core:description": "DRIFTER passive RTL-SDR field capture",
                "drifter:region": REGION,
            }
            if self.gps and time.time() - float(self.gps.get("ts") or 0) < 120:
                global_meta["drifter:gps"] = {
                    key: self.gps.get(key)
                    for key in ("lat", "lon", "accuracy_m")
                    if self.gps.get(key) is not None
                }
            atomic_write_json(meta_path, {
                "global": global_meta,
                "captures": [{
                    "core:sample_start": 0,
                    "core:frequency": int(freq_mhz * 1e6),
                    "core:datetime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }],
                "annotations": [],
            })
            payload = {
                "ok": True,
                "freq_mhz": round(freq_mhz, 5),
                "duration_s": duration_s,
                "sample_rate": sample_rate,
                "data_file": data_path.name,
                "meta_file": meta_path.name,
                "bytes": data_path.stat().st_size,
                "ts": time.time(),
            }
            self.publish(CAPTURE, payload, True)
            self.ops("idle", "capture saved", 100, **payload)
        except Exception as exc:
            try:
                data_path.unlink(missing_ok=True)
            except OSError:
                pass
            self.publish(CAPTURE, {"ok": False, "error": str(exc), "ts": time.time()}, True)
            self.ops("idle", "capture failed", 0, error=str(exc))
        finally:
            lease.release()
            self.resume_legacy()

    def zoom(self, freq_mhz: float, span_khz: int = 500) -> None:
        self.start_worker(
            self.zoom_worker,
            freq_mhz,
            int(clamp(span_khz, 50, 5000)),
            mode="zoom",
        )

    def zoom_worker(self, freq_mhz: float, span_khz: int) -> None:
        self.pause_legacy()
        lease = SDRLease("rf-zoom", detail=f"{freq_mhz:.5f} MHz", timeout=8)
        if not lease.acquire():
            self.ops("idle", "zoom blocked", 0,
                     error=f"RTL-SDR busy: {read_owner().get('owner', 'unknown')}")
            self.resume_legacy()
            return
        try:
            bins = self.sweep(
                freq_mhz,
                span_khz / 1000.0,
                max(2500, span_khz * 1000 // 120),
                12,
            )
            self.publish(ZOOM, {
                "center_mhz": freq_mhz,
                "span_khz": span_khz,
                "bins": [
                    {"freq_hz": round(item["freq_hz"]), "db": round(item["db"], 1)}
                    for item in bins[:400]
                ],
                "analysis": analyse_bins(bins, freq_mhz),
                "ts": time.time(),
            }, True)
            self.ops("idle", "zoom complete", 100, freq_mhz=freq_mhz)
        finally:
            lease.release()
            self.resume_legacy()

    def listen(self, freq_mhz: float, mode: str = "nfm") -> None:
        if self.pending_survey:
            self.ops("listen", "busy", 0, error="finish or stop survey first")
            return
        with self.lock:
            worker_busy = bool(self.worker is not None and self.worker.is_alive())
        owner = read_owner().get("owner")
        if worker_busy and owner != "rf-hunt":
            self.ops("listen", "busy", 0, error="finish current RF operation first")
            return
        if owner == "rf-hunt":
            self.hunt_stop.set()
            threading.Thread(
                target=self._listen_after_hunt,
                args=(freq_mhz, mode),
                daemon=True,
                name="rfops-listen-handoff",
            ).start()
            return
        self._start_audio(freq_mhz, mode)

    def _listen_after_hunt(self, freq_mhz: float, mode: str) -> None:
        deadline = time.monotonic() + 4.0
        while read_owner().get("owner") == "rf-hunt" and time.monotonic() < deadline:
            time.sleep(0.08)
        self._start_audio(freq_mhz, mode)

    def _start_audio(self, freq_mhz: float, mode: str) -> None:
        self.publish(TOPICS.get("rfaudio_command", "drifter/rfaudio/command"), {
            "action": "start",
            "freq_mhz": freq_mhz,
            "mode": mode,
            "gain": 0,
            "ts": time.time(),
        })
        self.ops("listen", "starting audio", 20,
                 freq_mhz=freq_mhz, demod=mode)

    def recover(self) -> None:
        self.pending_survey = False
        self.survey_generation += 1
        self.hunt_stop.set()
        self.stop_event.set()
        self.publish(TOPICS.get("rfaudio_command", "drifter/rfaudio/command"), {
            "action": "stop", "ts": time.time(),
        })
        self.pause_legacy(1.2)
        self.resume_legacy()
        self.ops("idle", "RF workers reset", 100)

    # ── Command + MQTT ingress ─────────────────────────────────────
    def command(self, payload: dict) -> None:
        action = str(payload.get("action") or "").lower().strip()
        freq = valid_freq(payload.get("freq_mhz")) if action in {
            "hunt_start", "capture", "zoom", "listen",
        } else None

        if action == "survey":
            self.survey()
        elif action == "survey_stop":
            threading.Thread(target=self.stop_survey, daemon=True,
                             name="rfops-stop-survey").start()
        elif action == "hunt_stop":
            self.hunt_stop.set()
            self.stop_event.set()
        elif action in {"hunt_start", "capture", "zoom", "listen"} and freq is None:
            self.ops("idle", "invalid request", 0,
                     error="freq_mhz must be 24-1766")
        elif action == "hunt_start":
            self.hunt(freq)
        elif action == "capture":
            self.capture(freq, payload.get("duration_s", 15))
        elif action == "zoom":
            self.zoom(freq, int(payload.get("span_khz", 500)))
        elif action == "listen":
            mode = str(payload.get("mode") or "nfm").lower()
            self.listen(freq, mode)
        elif action == "listen_stop":
            self.publish(TOPICS.get("rfaudio_command", "drifter/rfaudio/command"), {
                "action": "stop", "ts": time.time(),
            })
            self.ops("idle", "audio stopped", 100)
        elif action == "baseline_save":
            atomic_write_json(BASELINE_FILE, {
                "region": REGION,
                "findings": list(self.findings)[:12],
                "ts": time.time(),
            })
            self.ops("idle", "baseline saved", 100)
        elif action == "recover":
            threading.Thread(target=self.recover, daemon=True,
                             name="rfops-recover").start()
        else:
            self.ops("idle", "invalid request", 0,
                     error=f"unknown RF action: {action or '(empty)'}")

    def on_message(self, client, userdata, msg) -> None:
        try:
            data = json.loads(msg.payload)
        except (ValueError, TypeError):
            return

        if msg.topic == CMD and isinstance(data, dict):
            self.command(data)
            return

        if msg.topic == TOPICS.get("gps_fix", "drifter/gps/fix") and isinstance(data, dict):
            self.gps = dict(data)
            self.gps.setdefault("ts", time.time())
            return

        if msg.topic == SUMMARY and isinstance(data, dict):
            if self.pending_survey and data.get("forced"):
                self.start_worker(self.deep_survey, data, mode="survey")
            return

        if msg.topic == TOPICS.get("rf_signal", "drifter/rf/signals") and isinstance(data, dict):
            raw = data.get("raw") if isinstance(data.get("raw"), dict) else {}
            freq = valid_freq(raw.get("freq") or raw.get("freq_mhz") or 433.92)
            model = str(data.get("model") or "unknown")
            if freq and model.lower() not in {"", "unknown"}:
                self.upsert({
                    "id": f"decode-{data.get('id') or model}-{round(freq * 1000)}",
                    "freq_mhz": round(freq, 5),
                    "context": band_context(freq),
                    "classification": model,
                    "protocol": data.get("protocol") or "",
                    "confidence": 0.9,
                    "status": "KNOWN",
                    "source": "rtl_433",
                })
            return

        if msg.topic == CLASSIFY and isinstance(data, dict):
            candidate = data.get("freq_mhz")
            if candidate is None and data.get("frequency_hz") is not None:
                try:
                    candidate = float(data["frequency_hz"]) / 1e6
                except (TypeError, ValueError):
                    candidate = None
            freq = valid_freq(candidate)
            if freq:
                self.upsert({
                    "id": f"classify-{int(time.time())}-{round(freq * 1000)}",
                    "freq_mhz": round(freq, 5),
                    "context": band_context(freq),
                    "classification": data.get("protocol") or data.get("modulation") or "unknown waveform",
                    "confidence": data.get("confidence", 0.5),
                    "status": "NEW" if data.get("novel") else "UNKNOWN",
                    "source": "classifier",
                })

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        STATE.mkdir(parents=True, exist_ok=True)
        try:
            self.client.connect(MQTT_HOST, MQTT_PORT, 30)
            self.client.subscribe([
                (CMD, 1),
                (SUMMARY, 0),
                (TOPICS.get("rf_signal", "drifter/rf/signals"), 0),
                (CLASSIFY, 0),
                (TOPICS.get("gps_fix", "drifter/gps/fix"), 0),
            ])
            self.client.loop_start()
            self.publish_findings()
            self.ops("idle", "ready", 100)
            log.info("RF operator engine ready")
        except Exception as exc:
            self.running = False
            log.warning("RF operator engine start failed: %s", exc)

    def stop(self) -> None:
        if not self.running:
            return
        self.pending_survey = False
        self.stop_event.set()
        self.hunt_stop.set()
        self.publish(TOPICS.get("rfaudio_command", "drifter/rfaudio/command"), {
            "action": "stop", "ts": time.time(),
        })
        try:
            self.client.loop_stop()
            self.client.disconnect()
        except Exception:
            pass
        self.running = False


_ENGINE: RFOps | None = None


def start() -> RFOps:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = RFOps()
    _ENGINE.start()
    return _ENGINE


def stop() -> None:
    if _ENGINE is not None:
        _ENGINE.stop()

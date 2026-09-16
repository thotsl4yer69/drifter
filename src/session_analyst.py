#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Session Analyst
Post-drive diagnostic analysis using LLM with deterministic fault evidence.
Triggered automatically on drive end via MQTT, or manually via /api/analyse.
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import signal
import threading
import time
from pathlib import Path

import db
import llm_client_v2 as llm_client
import vehicle_profile
from config import (
    ANALYST_BASELINE_SESSIONS,
    LOG_DIR,
    MQTT_HOST,
    MQTT_PORT,
    REPORTS_DIR,
    TOPICS,
    make_mqtt_client,
)
from mechanic import search as kb_search

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [ANALYST] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger(__name__)

INCIDENT_DIR = LOG_DIR / "incidents"
INCIDENT_STATUS_TOPIC = TOPICS.get('incident_status', 'drifter/incident/status')
ANALYST_INCIDENT_SETTLE_MAX_SEC = max(
    0.0, float(os.getenv('DRIFTER_ANALYST_INCIDENT_SETTLE_MAX_SEC', '50'))
)

_DIAGNOSIS_SCHEMA = """{
  "primary_suspect": {
    "diagnosis": "...",
    "confidence": 0-100,
    "evidence": "...",
    "confirm_with": "..."
  },
  "secondary_suspects": [
    {"diagnosis": "...", "confidence": 0-100, "evidence": "..."}
  ],
  "watch_items": ["..."],
  "action_items": ["..."],
  "safety_critical": true/false,
  "safety_note": "..."
}"""


def build_system_prompt() -> str:
    """Build the active-vehicle diagnostic prompt."""
    ki_block = vehicle_profile.known_issues_block()
    ki_section = f"\n{ki_block}" if ki_block else ""
    return (
        f"You are an expert diagnostic technician for the "
        f"{vehicle_profile.prompt_identity()}.\n\n"
        "You receive structured telemetry, anomaly events and deterministic "
        "black-box fault timelines from a live OBD-II/CAN monitoring system "
        "(DRIFTER). Analyse the data and produce a structured diagnosis.\n\n"
        "CRITICAL: Return valid JSON ONLY — no markdown fences, no explanation "
        "outside the JSON.\n\n"
        "JSON structure required:\n"
        f"{_DIAGNOSIS_SCHEMA}\n"
        f"{ki_section}"
        "\nRules:\n"
        "- Be specific to this vehicle; cite known failure modes only where data supports them\n"
        "- Treat BLACK BOX first-change sequencing as measured correlation, not proof of causation\n"
        "- Rank hypotheses by what changed first, then corroborating telemetry, DTCs and baselines\n"
        "- Cite actual values and timing offsets wherever available\n"
        "- Give actionable confirmation tests (smoke test, coil swap, compression, multimeter, etc.)\n"
        "- Flag anything safety-critical immediately with safety_critical: true\n"
        "- Give cost estimates in the operator's local currency\n"
    )


SYSTEM_PROMPT = build_system_prompt()

TOPIC_TO_SENSOR = {
    TOPICS['stft1']: 'stft_b1',
    TOPICS['stft2']: 'stft_b2',
    TOPICS['ltft1']: 'ltft_b1',
    TOPICS['ltft2']: 'ltft_b2',
    TOPICS['rpm']: 'rpm',
    TOPICS['coolant']: 'coolant',
    TOPICS['iat']: 'iat',
    TOPICS['maf']: 'maf',
    TOPICS['throttle']: 'throttle',
    TOPICS['voltage']: 'voltage',
}


def compute_sensor_avgs(log_file: Path, start_ts: float, end_ts: float) -> dict[str, float]:
    """Compute session sensor averages from live or compressed JSONL logs."""
    sums: dict[str, float] = {}
    counts: dict[str, int] = {}
    gz_file = Path(str(log_file) + '.gz')
    if not log_file.exists() and gz_file.exists():
        opener = lambda: gzip.open(gz_file, 'rt')  # noqa: E731
    else:
        opener = lambda: open(log_file)  # noqa: E731
    try:
        with opener() as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    ts = rec.get('ts', 0)
                    if ts < start_ts:
                        continue
                    if ts > end_ts:
                        break
                    sensor = TOPIC_TO_SENSOR.get(rec.get('topic', ''))
                    if sensor is None:
                        continue
                    value = rec.get('data', {}).get('value')
                    if value is not None:
                        sums[sensor] = sums.get(sensor, 0.0) + float(value)
                        counts[sensor] = counts.get(sensor, 0) + 1
                except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                    continue
    except FileNotFoundError:
        log.warning("JSONL log not found: %s", log_file)
    return {k: sums[k] / counts[k] for k in sums if counts.get(k, 0) > 0}


def _session_with_averages(session: dict, sensor_avgs: dict[str, float]) -> dict:
    """Persist measured averages so future sessions have a real baseline."""
    out = dict(session)
    mapping = {
        'avg_stft_b1': 'stft_b1',
        'avg_stft_b2': 'stft_b2',
        'avg_ltft_b1': 'ltft_b1',
        'avg_ltft_b2': 'ltft_b2',
    }
    for field, sensor in mapping.items():
        if out.get(field) is None and sensor in sensor_avgs:
            out[field] = sensor_avgs[sensor]
    return out


def load_incident_summaries(
    incident_dir: Path,
    start_ts: float,
    end_ts: float,
    *,
    margin_s: float = 5.0,
    limit: int = 12,
) -> list[dict]:
    """Load black-box incidents whose trigger belongs to this drive session."""
    incident_dir = Path(incident_dir)
    if not incident_dir.exists():
        return []
    out: list[dict] = []
    for path in incident_dir.glob('incident_*.json'):
        try:
            payload = json.loads(path.read_text())
            trigger = float(payload.get('trigger', 0))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            continue
        if start_ts - margin_s <= trigger <= end_ts + margin_s:
            payload['_summary_file'] = str(path)
            out.append(payload)
    out.sort(key=lambda row: float(row.get('trigger', 0)))
    return out[:max(1, limit)]


def _incident_context_lines(incidents: list[dict], session_start: float) -> list[str]:
    if not incidents:
        return []
    lines = ["", f"BLACK BOX INCIDENTS ({len(incidents)} captured):"]
    for incident in incidents:
        trigger = float(incident.get('trigger', session_start))
        rel = trigger - session_start
        reasons = ', '.join(incident.get('reasons') or ['unspecified'])
        lines.append(f"  {rel:+.1f}s INCIDENT {incident.get('id', '?')} · {reasons}")
        first = incident.get('first_change') or {}
        if first.get('sensor'):
            lines.append(
                "    FIRST MATERIAL CHANGE: "
                f"{first['sensor']} at {float(first.get('offset_s', 0)):+.2f}s relative to trigger · "
                f"{first.get('baseline')} -> {first.get('value')} "
                f"(delta {first.get('delta')})"
            )
        changes = incident.get('first_changes') or []
        if changes:
            ordered = []
            for change in changes[:8]:
                sensor = change.get('sensor', '?')
                offset = float(change.get('offset_s', 0))
                ordered.append(
                    f"{sensor}@{offset:+.2f}s:{change.get('baseline')}->{change.get('value')}"
                )
            lines.append("    ORDERED CHANGES: " + ' | '.join(ordered))
        flags = incident.get('correlation_flags') or []
        if flags:
            lines.append(
                "    CORRELATION FLAGS: "
                + ', '.join(str(flag.get('flag', '')) for flag in flags[:6] if flag.get('flag'))
            )
    return lines


def build_context_packet(
    session: dict,
    anomalies: list[dict],
    sensor_avgs: dict[str, float],
    baseline: dict | None,
    kb_entries: list[str],
    incidents: list[dict] | None = None,
) -> str:
    """Assemble the diagnostic context packet sent to the LLM."""
    session_start = float(session.get('start', session.get('start_ts', 0)) or 0)
    min_voltage = session.get('min_voltage')
    min_voltage_text = '?' if min_voltage is None else min_voltage
    warmup = session.get('warmup_seconds')
    warmup_text = '?' if warmup is None else warmup
    lines = [
        f"VEHICLE: {vehicle_profile.prompt_identity()}",
        "",
        f"SESSION: {session['session_id']}",
        f"  Duration: {int(session.get('duration_seconds', 0) // 60)}m",
        f"  Distance: {session.get('distance_km', 0):.1f} km",
        f"  Max coolant: {session.get('max_coolant', '?')}°C",
        f"  Min voltage: {min_voltage_text}V",
        f"  Warm-up time: {warmup_text}s",
    ]

    dtcs = json.loads(session.get('dtcs_seen') or '[]')
    if dtcs:
        lines.append(f"  Active DTCs: {', '.join(dtcs)}")

    if anomalies:
        lines.append("")
        lines.append(f"ANOMALY EVENTS ({len(anomalies)} detected):")
        for ev in sorted(anomalies, key=lambda e: e['ts']):
            try:
                ctx = json.loads(ev.get('context_json', '{}'))
            except json.JSONDecodeError:
                ctx = {}
            ctx_str = ', '.join(f"{k}={v}" for k, v in list(ctx.items())[:5])
            ts_rel = int(ev['ts'] - session_start)
            lines.append(
                f"  +{ts_rel:04d}s  {ev['sensor']} = {ev['value']} "
                f"(z={ev['z_score']}, {ev['severity']})  [{ctx_str}]"
            )
    else:
        lines.append("ANOMALY EVENTS: None detected")

    lines.extend(_incident_context_lines(incidents or [], session_start))

    lines.append("")
    lines.append("SESSION AVERAGES:")
    for sensor, avg in sorted(sensor_avgs.items()):
        lines.append(f"  {sensor}: {avg:.2f}")

    if baseline and baseline.get('session_count', 0) > 0:
        lines.append("")
        lines.append(f"BASELINE ({baseline['session_count']} prior sessions):")
        compare_fields = [
            ('warmup_seconds', 'Warm-up (s)'),
            ('avg_stft_b1', 'STFT B1 avg (%)'),
            ('avg_stft_b2', 'STFT B2 avg (%)'),
            ('avg_ltft_b1', 'LTFT B1 avg (%)'),
            ('avg_ltft_b2', 'LTFT B2 avg (%)'),
            ('min_voltage', 'Min voltage (V)'),
            ('max_coolant', 'Max coolant (°C)'),
        ]
        for field, label in compare_fields:
            base_val = baseline.get(field)
            cur_val = session.get(field) or sensor_avgs.get(field.replace('avg_', ''))
            if base_val is not None and cur_val is not None:
                delta = float(cur_val) - float(base_val)
                flag = ' ⚠' if abs(delta) > abs(float(base_val)) * 0.2 else ''
                lines.append(
                    f"  {label}: {cur_val:.1f} "
                    f"(baseline {base_val:.1f}, Δ{delta:+.1f}){flag}"
                )

    if kb_entries:
        lines.append("")
        lines.append("RELEVANT VEHICLE KNOWLEDGE:")
        lines.extend(kb_entries)

    return '\n'.join(lines)


def parse_report(raw_text: str) -> dict:
    """Parse LLM JSON response. Sets parse_error=True on failure."""
    text = raw_text.strip()
    if text.startswith('```'):
        text = '\n'.join(text.split('\n')[1:])
        if text.endswith('```'):
            text = text[:-3]
    text = text.strip()
    if not text.startswith('{'):
        start = text.find('{')
        end = text.rfind('}')
        if start != -1 and end > start:
            text = text[start:end + 1]
    try:
        report = json.loads(text)
        report['parse_error'] = False
        report['raw_response'] = raw_text
        return report
    except json.JSONDecodeError:
        return {'parse_error': True, 'raw_response': raw_text}


def _incident_kb_queries(incidents: list[dict]) -> set[str]:
    queries: set[str] = set()
    for incident in incidents:
        for flag in incident.get('correlation_flags') or []:
            name = str(flag.get('flag') or '')
            if 'lean' in name or 'trim' in name:
                queries.add('lean fuel trim')
            if 'airflow' in name or 'maf' in name:
                queries.add('MAF sensor')
            if 'electrical' in name or 'voltage' in name:
                queries.add('alternator voltage')
            if 'rpm' in name or 'idle' in name:
                queries.add('idle instability')
        for reason in incident.get('reasons') or []:
            reason = str(reason)
            if 'idle' in reason or 'rpm' in reason:
                queries.add('idle instability')
            elif 'voltage' in reason:
                queries.add('alternator voltage')
            elif 'coolant' in reason:
                queries.add('coolant temperature')
    return queries


def run_analysis(session: dict) -> dict | None:
    """Run the full post-drive diagnostic analysis pipeline."""
    session_id = session['session_id']
    log.info("Starting analysis for %s", session_id)

    anomalies = db.get_session_anomalies(session_id)
    log.info("  %d anomaly events", len(anomalies))

    start_ts = float(session.get('start', session.get('start_ts', 0)) or 0)
    end_ts = float(session.get('end', session.get('end_ts', time.time())) or time.time())

    date_str = session_id[:8]
    log_file = LOG_DIR / f"drive_{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}.jsonl"
    sensor_avgs = compute_sensor_avgs(log_file, start_ts, end_ts)
    session_for_storage = _session_with_averages(session, sensor_avgs)
    db.insert_session(session_for_storage)

    incidents = load_incident_summaries(INCIDENT_DIR, start_ts, end_ts)
    log.info("  %d black-box incidents", len(incidents))

    baseline = db.get_baseline(
        exclude_session_id=session_id,
        n=ANALYST_BASELINE_SESSIONS,
    )

    kb_queries = _incident_kb_queries(incidents)
    for ev in anomalies:
        sensor = ev.get('sensor', '')
        if 'stft' in sensor or 'ltft' in sensor:
            kb_queries.add('lean fuel trim')
        elif 'rpm' in sensor:
            kb_queries.add('idle instability')
        elif 'coolant' in sensor:
            kb_queries.add('coolant temperature')
        elif 'voltage' in sensor:
            kb_queries.add('alternator voltage')
        elif 'maf' in sensor:
            kb_queries.add('MAF sensor')

    dtcs = json.loads(session.get('dtcs_seen') or '[]')
    for dtc in dtcs[:3]:
        kb_queries.add(dtc)

    kb_entries = []
    for query in list(kb_queries)[:6]:
        results = kb_search(query)
        for result in results[:2]:
            if result.get('type') == 'problem':
                problem = result['data']
                kb_entries.append(
                    f"KNOWN ISSUE: {problem['title']}\n"
                    f"Symptoms: {', '.join(problem.get('symptoms', []))}\n"
                    f"Cause: {problem.get('cause', '')}\n"
                    f"Fix: {problem.get('fix', '')}"
                )

    packet = build_context_packet(
        session_for_storage,
        anomalies,
        sensor_avgs,
        baseline,
        kb_entries,
        incidents=incidents,
    )
    log.info("  Context packet: %d chars", len(packet))

    try:
        llm_result = llm_client.query(packet, build_system_prompt())
    except Exception as exc:
        log.error("LLM call failed: %s", exc)
        return None

    report = parse_report(llm_result['text'])
    report['session_id'] = session_id
    report['generated_at'] = time.time()
    report['model_used'] = llm_result['model']
    report['tokens_used'] = llm_result['tokens']
    report['incidents_used'] = [incident.get('id') for incident in incidents if incident.get('id')]

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / f"report_{session_id}.json"
    report_path.write_text(json.dumps(report, indent=2))
    log.info("  Report saved: %s", report_path.name)

    db.insert_report({
        'session_id': session_id,
        'generated_at': report['generated_at'],
        'model_used': report['model_used'],
        'report_json': json.dumps(report),
        'tokens_used': report['tokens_used'],
    })

    return report


class SessionAnalyst:
    """MQTT-driven service: triggers on session end and manual requests."""

    def __init__(self):
        self.running = True
        self.last_session: dict | None = None
        self.incident_end_at = 0.0
        db.init_db()
        self.client = make_mqtt_client("drifter-session-analyst")
        self.client.on_message = self._on_message

    def _on_message(self, client, userdata, msg):
        try:
            data = json.loads(msg.payload)
            if not isinstance(data, dict):
                return
            topic = msg.topic
            if topic == INCIDENT_STATUS_TOPIC:
                if data.get('active'):
                    try:
                        self.incident_end_at = float(data.get('end_at') or 0.0)
                    except (TypeError, ValueError):
                        self.incident_end_at = 0.0
                else:
                    self.incident_end_at = 0.0
                return
            if topic == TOPICS['drive_session'] and data.get('event') == 'end':
                self.last_session = data
                threading.Thread(
                    target=self._handle_session_end,
                    args=(data, True),
                    daemon=True,
                ).start()
            elif topic == TOPICS.get('analysis_request', 'drifter/analysis/request'):
                if self.last_session:
                    threading.Thread(
                        target=self._handle_session_end,
                        args=(self.last_session, False),
                        daemon=True,
                    ).start()
        except Exception as exc:
            log.warning("Message error: %s", exc)

    def _wait_for_incident_tail(self) -> None:
        """Wait only while the logger says a black-box incident is still active."""
        if ANALYST_INCIDENT_SETTLE_MAX_SEC <= 0:
            return
        budget_end = time.monotonic() + ANALYST_INCIDENT_SETTLE_MAX_SEC
        while self.running:
            remaining_tail = self.incident_end_at - time.time()
            remaining_budget = budget_end - time.monotonic()
            if remaining_tail <= 0 or remaining_budget <= 0:
                return
            time.sleep(min(1.0, remaining_tail + 0.25, remaining_budget))

    def _handle_session_end(self, session: dict, settle_incident: bool = True):
        if settle_incident:
            self._wait_for_incident_tail()
        report = run_analysis(session)
        if report:
            self.client.publish(
                TOPICS.get('analysis_report', 'drifter/analysis/report'),
                json.dumps(report),
            )
            log.info("Analysis complete: %s", session.get('session_id'))

    def start(self):
        log.info("Session Analyst starting...")
        connected = False
        while not connected and self.running:
            try:
                self.client.connect(MQTT_HOST, MQTT_PORT, 60)
                connected = True
            except Exception as exc:
                log.warning("MQTT connect failed: %s", exc)
                time.sleep(3)
        if not self.running:
            return
        self.client.subscribe([
            (TOPICS['drive_session'], 0),
            (TOPICS.get('analysis_request', 'drifter/analysis/request'), 0),
            (INCIDENT_STATUS_TOPIC, 0),
        ])
        self.client.loop_start()
        log.info("Session Analyst LIVE")
        while self.running:
            time.sleep(1)
        self.client.loop_stop()
        self.client.disconnect()


def main():
    analyst = SessionAnalyst()

    def _stop(sig, frame):
        analyst.running = False

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    analyst.start()


if __name__ == '__main__':
    main()

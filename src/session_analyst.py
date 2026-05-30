#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Session Analyst
Post-drive diagnostic analysis using LLM with full context.
Triggered automatically on drive end via MQTT, or manually via /api/analyse.
UNCAGED TECHNOLOGY — EST 1991
"""

import json
import time
import signal
import logging
import threading
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict

import paho.mqtt.client as mqtt

import db
import llm_client
from mechanic import search as kb_search
from config import (
    MQTT_HOST, MQTT_PORT, TOPICS, LOG_DIR,
    REPORTS_DIR, ANALYST_BASELINE_SESSIONS, make_mqtt_client,)
# Note: TOPICS is used in TOPIC_TO_SENSOR below (no hardcoded topic strings)

logging.basicConfig(level=logging.INFO, format='%(asctime)s [ANALYST] %(message)s',
                    datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

# Topic → sensor name mapping (for compute_sensor_avgs)
TOPIC_TO_SENSOR = {
    TOPICS['stft1']:   'stft_b1',
    TOPICS['stft2']:   'stft_b2',
    TOPICS['ltft1']:   'ltft_b1',
    TOPICS['ltft2']:   'ltft_b2',
    TOPICS['rpm']:     'rpm',
    TOPICS['coolant']: 'coolant',
    TOPICS['iat']:     'iat',
    TOPICS['maf']:     'maf',
    TOPICS['throttle']: 'throttle',
    TOPICS['voltage']: 'voltage',
}


def compute_sensor_avgs(log_file: Path, start_ts: float, end_ts: float) -> Dict[str, float]:
    """Read JSONL log and compute per-sensor averages within the session time range.

    Uses running sums instead of accumulating all values in memory,
    so this stays O(1) per sensor even for multi-hour drives.
    """
    sums: Dict[str, float] = {}
    counts: Dict[str, int] = {}
    try:
        with open(log_file) as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    ts = rec.get('ts', 0)
                    if ts < start_ts:
                        continue
                    if ts > end_ts:
                        break  # JSONL is time-ordered — no need to read further
                    sensor = TOPIC_TO_SENSOR.get(rec.get('topic', ''))
                    if sensor is None:
                        continue
                    value = rec.get('data', {}).get('value')
                    if value is not None:
                        sums[sensor] = sums.get(sensor, 0.0) + float(value)
                        counts[sensor] = counts.get(sensor, 0) + 1
                except (json.JSONDecodeError, KeyError):
                    continue
    except FileNotFoundError:
        log.warning(f"JSONL log not found: {log_file}")
    return {k: sums[k] / counts[k] for k in sums if counts.get(k, 0) > 0}


def build_context_packet(
    session: dict,
    anomalies: List[dict],
    sensor_avgs: Dict[str, float],
    baseline: Optional[dict],
    kb_entries: List[str],
) -> str:
    """Assemble the diagnostic context packet to send to the LLM."""
    lines = [
        f"VEHICLE: 2004 Jaguar X-Type 2.5L V6 (AJ-V6)",
        f"",
        f"SESSION: {session['session_id']}",
        f"  Duration: {int(session.get('duration_seconds', 0) // 60)}m",
        f"  Distance: {session.get('distance_km', 0):.1f} km",
        f"  Max coolant: {session.get('max_coolant', '?')}°C",
        f"  Min voltage: {session.get('min_voltage', '?')}V",
        f"  Warm-up time: {session.get('warmup_seconds', '?')}s",
    ]

    # DTCs
    dtcs = json.loads(session.get('dtcs_seen') or '[]')
    if dtcs:
        lines.append(f"  Active DTCs: {', '.join(dtcs)}")

    # Anomaly events
    if anomalies:
        lines.append(f"")
        lines.append(f"ANOMALY EVENTS ({len(anomalies)} detected):")
        for ev in sorted(anomalies, key=lambda e: e['ts']):
            ctx = json.loads(ev.get('context_json', '{}'))
            ctx_str = ', '.join(f"{k}={v}" for k, v in list(ctx.items())[:5])
            ts_rel = int(ev['ts'] - session.get('start', session.get('start_ts', ev['ts'])))
            lines.append(
                f"  +{ts_rel:04d}s  {ev['sensor']} = {ev['value']} "
                f"(z={ev['z_score']}, {ev['severity']})  [{ctx_str}]"
            )
    else:
        lines.append("ANOMALY EVENTS: None detected")

    # Sensor averages
    lines.append(f"")
    lines.append("SESSION AVERAGES:")
    for sensor, avg in sorted(sensor_avgs.items()):
        lines.append(f"  {sensor}: {avg:.2f}")

    # Baseline comparison
    if baseline and baseline.get('session_count', 0) > 0:
        lines.append(f"")
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
                lines.append(f"  {label}: {cur_val:.1f} (baseline {base_val:.1f}, Δ{delta:+.1f}){flag}")

    # KB context
    if kb_entries:
        lines.append(f"")
        lines.append("RELEVANT X-TYPE KNOWLEDGE:")
        for entry in kb_entries:
            lines.append(entry)

    return '\n'.join(lines)


def parse_report(raw_text: str) -> dict:
    """Parse LLM JSON response. Sets parse_error=True on failure."""
    # Strip markdown fences if present
    text = raw_text.strip()
    if text.startswith('```'):
        text = '\n'.join(text.split('\n')[1:])
        if text.endswith('```'):
            text = text[:-3]
    # Extract JSON object if surrounded by extra text
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


def run_analysis(session: dict) -> Optional[dict]:
    """Full analysis pipeline for a completed session."""
    session_id = session['session_id']
    log.info(f"Starting analysis for {session_id}")

    # 1. Insert session row into DB (before baseline query that depends on it)
    db.insert_session(session)

    # 2. Load anomaly events
    anomalies = db.get_session_anomalies(session_id)
    log.info(f"  {len(anomalies)} anomaly events")

    # 3. Compute sensor averages from JSONL
    date_str = session_id[:8]  # YYYYMMDD
    log_file = LOG_DIR / f"drive_{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}.jsonl"
    sensor_avgs = compute_sensor_avgs(
        log_file,
        session.get('start', session.get('start_ts', 0)),
        session.get('end', session.get('end_ts', time.time())),
    )

    # 4. Baseline delta
    baseline = db.get_baseline(exclude_session_id=session_id, n=ANALYST_BASELINE_SESSIONS)

    # 5. KB retrieval — search on unique symptom types
    kb_queries = set()
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
    for query in list(kb_queries)[:5]:
        results = kb_search(query)
        for r in results[:2]:
            if r.get('type') == 'problem':
                p = r['data']
                kb_entries.append(
                    f"KNOWN ISSUE: {p['title']}\n"
                    f"Symptoms: {', '.join(p.get('symptoms', []))}\n"
                    f"Cause: {p.get('cause', '')}\n"
                    f"Fix: {p.get('fix', '')}"
                )

    # 6. Build context packet
    packet = build_context_packet(session, anomalies, sensor_avgs, baseline, kb_entries)
    log.info(f"  Context packet: {len(packet)} chars")

    # 7. Call LLM
    try:
        llm_result = llm_client.query_llm(packet)
    except Exception as e:
        log.error(f"LLM call failed: {e}")
        return None

    # 8. Parse report
    report = parse_report(llm_result['text'])
    report['session_id'] = session_id
    report['generated_at'] = time.time()
    report['model_used'] = llm_result['model']
    report['tokens_used'] = llm_result['tokens']

    # 9. Save
    report_path = REPORTS_DIR / f"report_{session_id}.json"
    report_path.write_text(json.dumps(report, indent=2))
    log.info(f"  Report saved: {report_path.name}")

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
        self.last_session: Optional[dict] = None
        db.init_db()
        self.client = make_mqtt_client("drifter-session-analyst")
        self.client.on_message = self._on_message

    def _on_message(self, client, userdata, msg):
        try:
            data = json.loads(msg.payload)
            topic = msg.topic
            if topic == TOPICS['drive_session'] and data.get('event') == 'end':
                self.last_session = data
                threading.Thread(target=self._handle_session_end, args=(data,), daemon=True).start()
            elif topic == TOPICS.get('analysis_request', 'drifter/analysis/request'):
                if self.last_session:
                    threading.Thread(target=self._handle_session_end,
                                     args=(self.last_session,), daemon=True).start()
        except Exception as e:
            log.warning(f"Message error: {e}")

    def _handle_session_end(self, session: dict):
        report = run_analysis(session)
        if report:
            self.client.publish(
                TOPICS.get('analysis_report', 'drifter/analysis/report'),
                json.dumps(report)
            )
            log.info(f"Analysis complete: {session.get('session_id')}")

    def start(self):
        log.info("Session Analyst starting...")
        connected = False
        while not connected and self.running:
            try:
                self.client.connect(MQTT_HOST, MQTT_PORT, 60)
                connected = True
            except Exception as e:
                log.warning(f"MQTT connect failed: {e}")
                time.sleep(3)
        self.client.subscribe([
            (TOPICS['drive_session'], 0),
            (TOPICS.get('analysis_request', 'drifter/analysis/request'), 0),
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

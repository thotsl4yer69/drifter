#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Continuous Vehicle Learning
Watches anomaly events, DTCs, and AI diagnoses, then asks the LLM cascade
to extract durable observations that should be added to the vehicle KB.
Acts as a slow feedback loop that grows vehicle_kb.db over time.
UNCAGED TECHNOLOGY — EST 1991
"""

import json
import logging
import signal
import threading
import time
from collections import deque
from typing import Optional

import paho.mqtt.client as mqtt

import llm_client_v2
import vehicle_kb
from config import MQTT_HOST, MQTT_PORT, TOPICS

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [LEARN] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

# Rolling event buffer kept in memory
_events: deque = deque(maxlen=200)
_lock = threading.Lock()
_last_learn_ts = 0.0
LEARN_INTERVAL_S = 600.0   # at most once every 10 minutes

LEARN_SYSTEM = (
    "You distill recurring vehicle behaviour into KB-worthy notes. Given a "
    "JSON list of recent diagnostic events, output 0–3 short observations "
    "that would be useful next time. Only include observations the data "
    "strongly supports. JSON only. Schema: "
    "{\"observations\": [{\"topic\": str, \"question\": str, \"text\": str, "
    "\"confidence\": int 0-100, \"sources\": [str]}]}."
)


def _record(event_type: str, payload: dict) -> None:
    with _lock:
        _events.append({
            'type': event_type,
            'ts': time.time(),
            'data': payload,
        })


def _snapshot() -> list:
    with _lock:
        return list(_events)


def _serialise(events: list) -> str:
    # Trim large dicts so the prompt doesn't blow up
    trimmed = []
    for e in events:
        item = {'type': e['type'], 'ts': int(e['ts'])}
        data = e.get('data') or {}
        if e['type'] == 'diagnosis' and isinstance(data, dict):
            diag = data.get('diagnosis') or {}
            item['primary'] = diag.get('primary_suspect', {})
            item['safety_critical'] = diag.get('safety_critical', False)
        elif e['type'] == 'alert' and isinstance(data, dict):
            item['message'] = (data.get('message') or '')[:200]
            item['level'] = data.get('level')
        elif e['type'] == 'dtc':
            item['codes'] = data.get('stored', []) or []
        elif e['type'] == 'anomaly':
            item['summary'] = {
                k: data.get(k) for k in ('sensor', 'value', 'z_score', 'severity')
            }
        trimmed.append(item)
    return json.dumps(trimmed, default=str)


def _run_learning(client: mqtt.Client) -> None:
    global _last_learn_ts
    events = _snapshot()
    if len(events) < 8:
        return
    _last_learn_ts = time.time()
    log.info(f"Learning pass over {len(events)} events")
    try:
        result = llm_client_v2.query_json(
            _serialise(events), LEARN_SYSTEM, max_tokens=500,
        )
    except Exception as e:
        log.warning(f"Learn LLM call failed: {e}")
        return
    if result.get('parse_error') or not result.get('json'):
        log.warning("Learn response did not parse")
        return

    observations = result['json'].get('observations') or []
    for obs in observations:
        topic = obs.get('topic', 'learned')
        question = obs.get('question', 'observation')
        text = obs.get('text', '')
        if not text:
            continue
        client.publish(TOPICS['kb_update'], json.dumps({
            'topic': topic,
            'question': question,
            'text': text,
            'confidence': obs.get('confidence', 60),
            'sources': obs.get('sources', []),
            'source': 'continuous_learning',
            'ts': time.time(),
        }))
        client.publish(TOPICS['learn_event'], json.dumps({
            'observation': obs,
            'ts': time.time(),
        }))
    log.info(f"Emitted {len(observations)} observations")


def on_message(client, userdata, msg) -> None:
    topic = msg.topic
    try:
        data = json.loads(msg.payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return

    if topic == TOPICS['alert_message']:
        _record('alert', data if isinstance(data, dict) else {})
    elif topic == TOPICS['dtc']:
        _record('dtc', data if isinstance(data, dict) else {})
    elif topic == TOPICS['ai_diag_response']:
        _record('diagnosis', data if isinstance(data, dict) else {})
    elif topic == TOPICS['anomaly_event']:
        _record('anomaly', data if isinstance(data, dict) else {})
    elif topic == TOPICS['drive_session']:
        # End of trip = good moment to learn
        if isinstance(data, dict) and data.get('event') == 'end':
            threading.Thread(target=_run_learning, args=(client,), daemon=True).start()


def main() -> None:
    log.info("DRIFTER Vehicle Learning starting...")
    vehicle_kb.init_db()

    running = True

    def _handle_signal(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    client = mqtt.Client(client_id="drifter-learn")
    client.on_message = on_message

    connected = False
    while not connected and running:
        try:
            client.connect(MQTT_HOST, MQTT_PORT, 60)
            connected = True
        except Exception as e:
            log.warning(f"Waiting for MQTT broker... ({e})")
            time.sleep(3)

    if not running:
        return

    client.subscribe([
        (TOPICS['alert_message'], 0),
        (TOPICS['dtc'], 0),
        (TOPICS['ai_diag_response'], 0),
        (TOPICS['anomaly_event'], 0),
        (TOPICS['drive_session'], 0),
    ])
    client.loop_start()
    log.info("Vehicle Learning LIVE")

    while running:
        # Background timer — also learn periodically when many events accumulate
        if time.time() - _last_learn_ts > LEARN_INTERVAL_S and len(_snapshot()) >= 30:
            _run_learning(client)
        time.sleep(5)

    client.loop_stop()
    client.disconnect()
    log.info("Vehicle Learning stopped")


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Vehicle Knowledge Base
Per-vehicle KB built lazily by the LLM cascade. Stores known failure modes,
torque specs, fluid capacities, and bullet-point repair guidance for the
detected VIN. Falls back to the legacy mechanic.py X-Type KB when no
vehicle-specific entry exists.
UNCAGED TECHNOLOGY — EST 1991
"""

import json
import logging
import signal
import sqlite3
import threading
import time
from pathlib import Path
from typing import List, Optional

import paho.mqtt.client as mqtt

import llm_client_v2
from config import (
    MQTT_HOST, MQTT_PORT, TOPICS,
    KB_DIR, VEHICLE_PROFILE_FILE,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [KB] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

DB_PATH = KB_DIR / "vehicle_kb.db"
_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vin TEXT,
    make TEXT,
    model TEXT,
    year INTEGER,
    topic TEXT NOT NULL,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    confidence INTEGER,
    source TEXT,
    ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kb_vin ON entries(vin);
CREATE INDEX IF NOT EXISTS idx_kb_topic ON entries(topic);
"""

KB_SYSTEM = (
    "You are a master mechanic knowledge base. Given a vehicle profile and a question, "
    "produce a concise, accurate answer specific to that vehicle. Cite known failure "
    "modes by part. JSON only. Schema: {\"answer\": str, \"confidence\": int 0-100, "
    "\"sources\": [str], \"caveats\": [str]}."
)


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _lock, _connect() as conn:
        conn.executescript(SCHEMA)


def _load_profile() -> dict:
    if not VEHICLE_PROFILE_FILE.exists():
        return {}
    try:
        return json.loads(VEHICLE_PROFILE_FILE.read_text())
    except Exception as e:
        log.debug(f"profile read: {e}")
        return {}


def lookup(vin: str, topic: str, question: str) -> Optional[dict]:
    """Find a cached entry that matches the question. Returns None if missing."""
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT * FROM entries WHERE vin=? AND topic=? AND question=? "
            "ORDER BY ts DESC LIMIT 1",
            (vin or '', topic, question),
        ).fetchone()
    return dict(row) if row else None


def store(vin: str, profile: dict, topic: str, question: str, answer: dict,
          source: str = "ai") -> int:
    """Persist a new entry and return its row id."""
    with _lock, _connect() as conn:
        cursor = conn.execute(
            "INSERT INTO entries (vin, make, model, year, topic, question, answer, "
            "confidence, source, ts) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                vin or '',
                profile.get('make', ''),
                profile.get('model', ''),
                int(profile.get('year', 0) or 0),
                topic,
                question[:500],
                json.dumps(answer)[:4000],
                int(answer.get('confidence', 0)),
                source,
                time.time(),
            ),
        )
        return cursor.lastrowid


def ask(question: str, topic: str = "general") -> dict:
    """Look up or generate an answer for the active vehicle."""
    profile = _load_profile()
    vin = profile.get('vin', '')
    cached = lookup(vin, topic, question)
    if cached:
        try:
            return {
                'answer': json.loads(cached['answer']),
                'cached': True,
                'id': cached['id'],
                'ts': cached['ts'],
            }
        except json.JSONDecodeError:
            pass

    user_prompt = (
        f"VEHICLE: {profile.get('year')} {profile.get('make')} {profile.get('model')} "
        f"({profile.get('engine', '')})\n"
        f"TOPIC: {topic}\n"
        f"QUESTION: {question}"
    )
    try:
        result = llm_client_v2.query_json(user_prompt, KB_SYSTEM, max_tokens=500)
    except Exception as e:
        log.warning(f"KB lookup failed: {e}")
        return {'answer': None, 'error': str(e), 'cached': False}

    if result.get('parse_error') or not result.get('json'):
        return {'answer': None, 'parse_error': True, 'raw': result.get('text'), 'cached': False}

    answer = result['json']
    row_id = store(vin, profile, topic, question, answer,
                   source=result.get('backend', 'ai'))
    return {'answer': answer, 'cached': False, 'id': row_id, 'ts': time.time()}


def _handle_query(client: mqtt.Client, payload: dict) -> None:
    question = payload.get('question') or payload.get('q')
    if not question:
        return
    topic = payload.get('topic', 'general')
    req_id = payload.get('id') or ''
    result = ask(question, topic)
    client.publish(TOPICS['kb_response'], json.dumps({
        'id': req_id, 'question': question, 'topic': topic,
        'result': result, 'ts': time.time(),
    }))


def on_message(client, userdata, msg) -> None:
    topic = msg.topic
    try:
        data = json.loads(msg.payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return
    if topic == TOPICS['kb_query'] and isinstance(data, dict):
        threading.Thread(target=_handle_query, args=(client, data), daemon=True).start()
    elif topic == TOPICS['kb_update'] and isinstance(data, dict):
        # Allow external services (vehicle_learn) to push facts directly
        profile = _load_profile()
        store(profile.get('vin', ''), profile,
              data.get('topic', 'learned'),
              data.get('question', 'observation'),
              {
                  'answer': data.get('text', ''),
                  'confidence': data.get('confidence', 70),
                  'sources': data.get('sources', []),
                  'caveats': data.get('caveats', []),
              },
              source=data.get('source', 'learn'))


def main() -> None:
    log.info("DRIFTER Vehicle KB starting...")
    init_db()

    running = True

    def _handle_signal(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    client = mqtt.Client(client_id="drifter-kb")
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

    client.subscribe([(TOPICS['kb_query'], 0), (TOPICS['kb_update'], 0)])
    client.loop_start()
    log.info("Vehicle KB LIVE")

    while running:
        time.sleep(1)

    client.loop_stop()
    client.disconnect()
    log.info("Vehicle KB stopped")


if __name__ == '__main__':
    main()

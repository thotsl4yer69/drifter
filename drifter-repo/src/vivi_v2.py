#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Vivi v2
Claude-backed voice and chat brain. Streams responses, keeps persistent
memory via vivi_memory, and can speak proactively when safety alerts fire
or new KB knowledge appears. Falls back through Groq and Ollama via
llm_client_v2 when offline. Reuses Piper TTS and faster-whisper STT when
available, otherwise serves text only.
UNCAGED TECHNOLOGY — EST 1991
"""

import base64
import json
import logging
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

import paho.mqtt.client as mqtt

import llm_client_v2
import vivi_memory
from config import (
    MQTT_HOST, MQTT_PORT, TOPICS,
    DRIFTER_DIR, PIPER_MODEL, PIPER_MODEL_DIR,
    VEHICLE, VEHICLE_YEAR, VEHICLE_MODEL, VEHICLE_ENGINE,
    VIVI2_HISTORY_TURNS, VIVI2_STREAMING, VIVI2_PROACTIVE_COOLDOWN_S,
    VIVI2_PERSONALITY_FILE,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [VIVI2] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

AUDIO_DIR = Path("/tmp/drifter-vivi2")
PIPER_MODEL_PATH = PIPER_MODEL_DIR / f"{PIPER_MODEL}.onnx"

# ── Default personality (overridden by /opt/drifter/vivi_personality.txt) ──
DEFAULT_PERSONALITY = f"""You are Vivi — the v2 brain of DRIFTER, riding in a {VEHICLE_YEAR} {VEHICLE_MODEL} \
({VEHICLE_ENGINE}). You know this car cold: AJ-V6 quirks, plastic thermostat housing failures, \
coil packs, Haldex coupling, JF506E gearbox. You also handle Spotify, navigation, trip stats, \
crash response, and sentry mode now.

Personality: confident, direct, a little flirty — never vague. Reply for voice: 1–3 sentences \
unless asked for detail. Quote live telemetry when it's relevant. Lead with the risk on safety \
issues. British English. Speak as the car ("my coolant's at 92, all good").

When the user asks you to remember something, call out that you've stored it. When you need to \
hand off (navigation, music, diagnostics), say so plainly."""

_session_id = uuid.uuid4().hex[:12]
_telemetry: dict = {}
_safety_alert: dict = {}
_active_dtcs: list = []
_proactive_last: dict = {}
_mqtt_client: Optional[mqtt.Client] = None
_personality_cache: Optional[str] = None


def _load_personality() -> str:
    global _personality_cache
    if _personality_cache is not None:
        return _personality_cache
    try:
        if VIVI2_PERSONALITY_FILE.exists():
            _personality_cache = VIVI2_PERSONALITY_FILE.read_text().strip()
            return _personality_cache
    except Exception as e:
        log.debug(f"personality load: {e}")
    _personality_cache = DEFAULT_PERSONALITY
    return _personality_cache


def _telemetry_context() -> str:
    if not _telemetry:
        return ""
    keys = [
        ('rpm', 'RPM', ''), ('coolant', 'Coolant', '°C'),
        ('voltage', 'Battery', 'V'), ('speed', 'Speed', 'km/h'),
        ('load', 'Load', '%'), ('stft1', 'STFT B1', '%'),
        ('stft2', 'STFT B2', '%'), ('ltft1', 'LTFT B1', '%'),
        ('ltft2', 'LTFT B2', '%'), ('iat', 'IAT', '°C'),
        ('maf', 'MAF', 'g/s'),
    ]
    lines = [
        f"{label}: {_telemetry[k]}{unit}"
        for k, label, unit in keys if k in _telemetry
    ]
    return "Live telemetry:\n" + '\n'.join(lines) if lines else ""


def _facts_context() -> str:
    facts = vivi_memory.recall(n=5)
    if not facts:
        return ""
    return "Things to remember:\n" + '\n'.join(f"- {f['content']}" for f in facts)


def _history_context() -> str:
    turns = vivi_memory.history(_session_id, n=VIVI2_HISTORY_TURNS)
    if not turns:
        return ""
    return '\n'.join(
        f"{t['role'].upper()}: {t['content']}" for t in turns
    )


def _build_prompt(user_text: str) -> str:
    parts = []
    tel = _telemetry_context()
    if tel:
        parts.append(tel)
    facts = _facts_context()
    if facts:
        parts.append(facts)
    if _safety_alert:
        parts.append(f"Recent safety alert: {_safety_alert.get('message', '')[:300]}")
    if _active_dtcs:
        parts.append(f"Active DTCs: {', '.join(_active_dtcs[:5])}")
    hist = _history_context()
    if hist:
        parts.append("Recent conversation:\n" + hist)
    parts.append(f"USER: {user_text}")
    return '\n\n'.join(parts)


def _publish_status(status: str) -> None:
    if _mqtt_client:
        _mqtt_client.publish(TOPICS['vivi2_status'], json.dumps({
            'status': status,
            'session_id': _session_id,
            'ts': time.time(),
        }))


def _publish_stream_chunk(chunk: str) -> None:
    if _mqtt_client:
        try:
            _mqtt_client.publish(TOPICS['vivi2_stream'], json.dumps({
                'delta': chunk,
                'session_id': _session_id,
                'ts': time.time(),
            }))
        except Exception:
            pass


def _publish_response(query: str, response: str, backend: str) -> None:
    if _mqtt_client:
        _mqtt_client.publish(TOPICS['vivi2_response'], json.dumps({
            'query': query,
            'response': response,
            'backend': backend,
            'session_id': _session_id,
            'ts': time.time(),
        }))


def ask(user_text: str, stream: bool = VIVI2_STREAMING) -> dict:
    """Run a single turn. Returns dict with response + backend + tokens."""
    log.info(f"Query: {user_text[:100]}")
    _publish_status("thinking")

    vivi_memory.append_turn(_session_id, "user", user_text)

    prompt = _build_prompt(user_text)
    system = _load_personality()

    try:
        if stream:
            result = llm_client_v2.stream(
                prompt, system, max_tokens=400, on_token=_publish_stream_chunk,
            )
        else:
            result = llm_client_v2.query(prompt, system, max_tokens=400, cache=False)
    except Exception as e:
        log.error(f"LLM cascade failed: {e}")
        result = {
            "text": "I'm offline right now — every LLM backend is down. Try again shortly.",
            "backend": "fallback",
            "model": "static",
            "tokens": 0,
        }

    response = result.get('text', '').strip()
    vivi_memory.append_turn(_session_id, "assistant", response)
    _publish_response(user_text, response, result.get('backend', '?'))
    return {**result, 'response': response}


def speak(text: str) -> None:
    if not text:
        return
    AUDIO_DIR.mkdir(exist_ok=True)
    wav_path = AUDIO_DIR / "vivi2.wav"

    try:
        model_arg = str(PIPER_MODEL_PATH) if PIPER_MODEL_PATH.exists() else PIPER_MODEL
        proc = subprocess.Popen(
            ['piper', '--model', model_arg, '--output_file', str(wav_path)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        proc.communicate(input=text.encode(), timeout=15)
    except FileNotFoundError:
        log.debug("piper not installed — text-only response")
        return
    except subprocess.TimeoutExpired:
        log.warning("Piper TTS timeout")
        return
    except Exception as e:
        log.warning(f"TTS error: {e}")
        return

    # Best-effort local playback
    try:
        check = subprocess.run(['aplay', '-l'], capture_output=True, text=True, timeout=2)
        if 'card' in check.stdout.lower() and wav_path.exists():
            subprocess.run(['aplay', '-q', str(wav_path)], capture_output=True, timeout=30)
    except Exception:
        pass

    if wav_path.exists() and _mqtt_client:
        try:
            data = wav_path.read_bytes()
            _mqtt_client.publish(TOPICS['audio_wav'], json.dumps({
                'text': text[:300],
                'wav_b64': base64.b64encode(data).decode(),
                'source': 'vivi2',
                'ts': time.time(),
            }))
        except Exception as e:
            log.debug(f"WAV publish failed: {e}")


def _handle_query(text: str) -> None:
    result = ask(text)
    _publish_status("speaking")
    speak(result['response'])
    _publish_status("idle")


def _maybe_proactive(reason: str, text: str) -> None:
    last = _proactive_last.get(reason, 0.0)
    if time.time() - last < VIVI2_PROACTIVE_COOLDOWN_S:
        return
    _proactive_last[reason] = time.time()
    log.info(f"Proactive [{reason}]: {text[:80]}")
    if _mqtt_client:
        _mqtt_client.publish(TOPICS['vivi2_proactive'], json.dumps({
            'reason': reason,
            'text': text,
            'ts': time.time(),
        }))
    _publish_status("speaking")
    speak(text)
    _publish_status("idle")


def on_message(client, userdata, msg) -> None:
    topic = msg.topic
    try:
        payload = json.loads(msg.payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload = msg.payload.decode('utf-8', errors='replace')

    if topic == TOPICS['vivi2_query']:
        text = payload if isinstance(payload, str) else payload.get('text', payload.get('query', ''))
        if text:
            threading.Thread(target=_handle_query, args=(text,), daemon=True).start()
    elif topic == TOPICS['snapshot']:
        if isinstance(payload, dict):
            _telemetry.update(payload)
    elif topic == TOPICS['safety_alert']:
        if isinstance(payload, dict):
            global _safety_alert
            _safety_alert = payload
            if payload.get('level', 0) >= 3:  # RED
                _maybe_proactive(
                    f"safety_{payload.get('key', 'unknown')}",
                    payload.get('message', 'Safety alert — pull over when safe.'),
                )
    elif topic == TOPICS['dtc']:
        if isinstance(payload, dict):
            global _active_dtcs
            _active_dtcs = payload.get('stored', []) or []
    elif topic == TOPICS['vivi2_memory']:
        # Manual remember/forget via UI
        if isinstance(payload, dict):
            action = payload.get('action')
            if action == 'remember':
                content = payload.get('content', '').strip()
                if content:
                    fact_id = vivi_memory.remember(content, payload.get('tag', ''))
                    log.info(f"Remembered #{fact_id}: {content[:60]}")
            elif action == 'forget':
                fid = payload.get('id')
                if isinstance(fid, int):
                    vivi_memory.forget(fid)


def main() -> None:
    global _mqtt_client

    log.info("DRIFTER Vivi v2 starting...")
    vivi_memory.init_db()

    running = True

    def _handle_signal(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    _mqtt_client = mqtt.Client(client_id="drifter-vivi2")
    _mqtt_client.on_message = on_message

    connected = False
    while not connected and running:
        try:
            _mqtt_client.connect(MQTT_HOST, MQTT_PORT, 60)
            connected = True
        except Exception as e:
            log.warning(f"Waiting for MQTT broker... ({e})")
            time.sleep(3)

    if not running:
        return

    _mqtt_client.subscribe([
        (TOPICS['vivi2_query'], 0),
        (TOPICS['snapshot'], 0),
        (TOPICS['safety_alert'], 0),
        (TOPICS['dtc'], 0),
        (TOPICS['vivi2_memory'], 0),
    ])
    _mqtt_client.loop_start()

    _publish_status("starting")
    time.sleep(1)
    speak(f"Vivi v2 online. {VEHICLE_YEAR} {VEHICLE_MODEL}, ready when you are.")
    _publish_status("idle")
    log.info("Vivi v2 LIVE")

    while running:
        time.sleep(0.5)

    log.info("Vivi v2 shutting down...")
    _publish_status("offline")
    _mqtt_client.loop_stop()
    _mqtt_client.disconnect()
    log.info("Vivi v2 stopped")


if __name__ == '__main__':
    main()

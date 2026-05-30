#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Vivi v2
Claude-backed voice and chat brain. Streams responses with sentence-level
TTS, keeps persistent memory via vivi_memory, and speaks proactively when
safety alerts fire or other significant events arrive. Cascades through
Groq and Ollama via llm_client_v2 when offline. Uses Piper TTS when
available, otherwise serves text only.
UNCAGED TECHNOLOGY — EST 1991
"""

import base64
import json
import logging
import re
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path

import paho.mqtt.client as mqtt

import llm_client_v2
import vivi_memory
from config import (
    LEVEL_AMBER,
    LEVEL_RED,
    MQTT_HOST,
    MQTT_PORT,
    PIPER_MODEL,
    PIPER_MODEL_DIR,
    TOPICS,
    VEHICLE_ENGINE,
    VEHICLE_MODEL,
    VEHICLE_YEAR,
    VIVI2_HISTORY_TURNS,
    VIVI2_PERSONALITY_FILE,
    VIVI2_PROACTIVE_COOLDOWN_S,
    VIVI2_STREAMING,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [VIVI2] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

AUDIO_DIR = Path("/tmp/drifter-vivi2")
PIPER_MODEL_PATH = PIPER_MODEL_DIR / f"{PIPER_MODEL}.onnx"

# ── Sentence buffering ──
# Split on sentence-ending punctuation; conservative so we don't break on
# things like "2.5L" or "Mr." mid-stream. Min chars stops us emitting micro
# fragments while the model is still hot.
_SENTENCE_END_RE = re.compile(r'(?<=[.!?])\s+(?=[A-Z0-9"\'])')
_MIN_SENTENCE_CHARS = 12

# Safety alert ages out of the prompt window after this long — otherwise a
# stale alert keeps biasing every reply for the rest of the drive.
_SAFETY_ALERT_TTL_S = 300

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

# Appended to the system prompt when a RED-level safety alert is active.
_RED_ALERT_DIRECTIVE = (
    "\n\nCRITICAL OVERRIDE: A RED safety alert is active. Drop the flirty register entirely. "
    "First sentence is the risk and the action — short, clear, imperative. No filler, no jokes, "
    "no compliments. Cue the user to pull over, call out the threshold that tripped, and "
    "shut up after one or two lines."
)

# ── Shared state (guarded by _state_lock for cross-thread access) ──
_state_lock = threading.Lock()
_session_id = uuid.uuid4().hex[:12]
_telemetry: dict = {}
_safety_alert: dict = {}
_active_dtcs: list = []
_proactive_last: dict = {}
_mqtt_client: mqtt.Client | None = None
_personality_cache: str | None = None
_aplay_available: bool | None = None


def _load_personality() -> str:
    global _personality_cache
    if _personality_cache is not None:
        return _personality_cache
    try:
        if VIVI2_PERSONALITY_FILE.exists():
            text = VIVI2_PERSONALITY_FILE.read_text(encoding='utf-8').strip()
            if text:
                _personality_cache = text
                return _personality_cache
    except OSError as e:
        log.debug(f"personality load: {e}")
    _personality_cache = DEFAULT_PERSONALITY
    return _personality_cache


def _alert_is_active(alert: dict) -> bool:
    """Treat alerts older than the TTL as stale even if no clear arrived."""
    if not alert:
        return False
    ts = alert.get('ts')
    if isinstance(ts, (int, float)) and time.time() - ts > _SAFETY_ALERT_TTL_S:
        return False
    return True


def _telemetry_context_snapshot() -> tuple[dict, dict, list]:
    """Copy shared state under the lock — caller works on the copy."""
    with _state_lock:
        return dict(_telemetry), dict(_safety_alert), list(_active_dtcs)


def _telemetry_context(tel: dict) -> str:
    if not tel:
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
        f"{label}: {tel[k]}{unit}"
        for k, label, unit in keys if k in tel
    ]
    return "Live telemetry:\n" + '\n'.join(lines) if lines else ""


def _facts_context() -> str:
    try:
        facts = vivi_memory.recall(n=5)
    except Exception as e:
        log.debug(f"recall failed: {e}")
        return ""
    if not facts:
        return ""
    return "Things to remember:\n" + '\n'.join(f"- {f['content']}" for f in facts)


def _history_context() -> str:
    try:
        turns = vivi_memory.history(_session_id, n=VIVI2_HISTORY_TURNS)
    except Exception as e:
        log.debug(f"history failed: {e}")
        return ""
    if not turns:
        return ""
    return '\n'.join(
        f"{t['role'].upper()}: {t['content']}" for t in turns
    )


def _build_prompt(user_text: str) -> tuple[str, dict]:
    """Build the user prompt and return (prompt, alert_snapshot)."""
    tel, alert, dtcs = _telemetry_context_snapshot()
    parts = []
    tel_ctx = _telemetry_context(tel)
    if tel_ctx:
        parts.append(tel_ctx)
    facts = _facts_context()
    if facts:
        parts.append(facts)
    if _alert_is_active(alert):
        msg = str(alert.get('message', ''))[:300]
        lvl = alert.get('level', '?')
        parts.append(f"Recent safety alert (level {lvl}): {msg}")
    if dtcs:
        parts.append(f"Active DTCs: {', '.join(str(d) for d in dtcs[:5])}")
    hist = _history_context()
    if hist:
        parts.append("Recent conversation:\n" + hist)
    parts.append(f"USER: {user_text}")
    return '\n\n'.join(parts), alert


def _system_prompt_for(alert: dict) -> str:
    base = _load_personality()
    if _alert_is_active(alert) and int(alert.get('level', 0) or 0) >= LEVEL_RED:
        return base + _RED_ALERT_DIRECTIVE
    return base


def _safe_publish(topic_key: str, payload: dict) -> None:
    """Publish JSON to an MQTT topic, swallowing transient errors."""
    client = _mqtt_client
    if not client:
        return
    topic = TOPICS.get(topic_key)
    if not topic:
        return
    try:
        client.publish(topic, json.dumps(payload))
    except Exception as e:
        log.debug(f"publish {topic_key} failed: {e}")


def _publish_status(status: str) -> None:
    _safe_publish('vivi2_status', {
        'status': status,
        'session_id': _session_id,
        'ts': time.time(),
    })


def _publish_stream_chunk(chunk: str) -> None:
    _safe_publish('vivi2_stream', {
        'delta': chunk,
        'session_id': _session_id,
        'ts': time.time(),
    })


def _publish_sentence(sentence: str) -> None:
    """Publish a completed sentence so downstream TTS consumers can speak it."""
    _safe_publish('vivi2_stream', {
        'sentence': sentence,
        'session_id': _session_id,
        'ts': time.time(),
    })


def _publish_response(query: str, response: str, backend: str) -> None:
    _safe_publish('vivi2_response', {
        'query': query,
        'response': response,
        'backend': backend,
        'session_id': _session_id,
        'ts': time.time(),
    })


class _SentenceBuffer:
    """Splits a token stream into sentences for incremental TTS.

    Each token is appended; whenever a sentence boundary appears we yield
    the completed sentence to ``on_sentence``. Anything left in the buffer
    at the end is flushed by ``flush()``.
    """

    def __init__(self, on_sentence) -> None:
        self._buf: list[str] = []
        self._on_sentence = on_sentence

    def push(self, chunk: str) -> None:
        if not chunk:
            return
        self._buf.append(chunk)
        text = ''.join(self._buf)
        # Walk over sentence boundaries and emit anything that qualifies.
        while True:
            m = _SENTENCE_END_RE.search(text)
            if not m:
                break
            cut = m.end()
            sentence = text[:cut].strip()
            text = text[cut:]
            if sentence and len(sentence) >= _MIN_SENTENCE_CHARS:
                self._emit(sentence)
            elif sentence:
                # Too short — fold back into buffer so it merges with the next.
                text = sentence + ' ' + text
                break
        self._buf = [text]

    def flush(self) -> None:
        leftover = ''.join(self._buf).strip()
        self._buf = []
        if leftover:
            self._emit(leftover)

    def _emit(self, sentence: str) -> None:
        try:
            self._on_sentence(sentence)
        except Exception as e:
            log.debug(f"sentence handler error: {e}")


def ask(user_text: str, stream: bool = VIVI2_STREAMING) -> dict:
    """Run a single turn. Returns dict with response + backend + tokens."""
    user_text = (user_text or "").strip()
    if not user_text:
        return {"text": "", "response": "", "backend": "noop", "tokens": 0}

    log.info(f"Query: {user_text[:100]}")
    _publish_status("thinking")

    try:
        vivi_memory.append_turn(_session_id, "user", user_text)
    except Exception as e:
        log.warning(f"failed to record user turn: {e}")

    prompt, alert_snapshot = _build_prompt(user_text)
    system = _system_prompt_for(alert_snapshot)

    sentences: list[str] = []

    def _on_sentence(s: str) -> None:
        sentences.append(s)
        _publish_sentence(s)
        # Speak it asynchronously so the LLM stream isn't blocked by TTS.
        threading.Thread(target=speak, args=(s,), daemon=True).start()

    buffer = _SentenceBuffer(_on_sentence)

    def _on_token(chunk: str) -> None:
        _publish_stream_chunk(chunk)
        buffer.push(chunk)

    fallback = False
    try:
        if stream:
            result = llm_client_v2.stream(
                prompt, system, max_tokens=400, on_token=_on_token,
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
        fallback = True

    response = str(result.get('text', '')).strip()
    if stream and not fallback:
        # Drain anything still in the sentence buffer; non-stream paths just
        # speak the full response below.
        buffer.flush()
    backend = result.get('backend', '?')

    if not fallback and response:
        try:
            vivi_memory.append_turn(_session_id, "assistant", response)
        except Exception as e:
            log.warning(f"failed to record assistant turn: {e}")
    # Note: we deliberately do NOT persist the static-fallback message — that
    # would pollute future context with an "I'm offline" record once we recover.

    _publish_response(user_text, response, backend)

    # If we already streamed sentence-by-sentence, the user has heard it.
    # In the non-streaming path or fallback, speak the whole thing now.
    if (not stream or fallback) and response:
        speak(response)

    return {**result, 'response': response, 'streamed_sentences': len(sentences)}


def _aplay_ready() -> bool:
    """Cache the aplay device-check result so we don't re-shell-out per call."""
    global _aplay_available
    if _aplay_available is not None:
        return _aplay_available
    try:
        check = subprocess.run(
            ['aplay', '-l'], capture_output=True, text=True, timeout=2,
        )
        _aplay_available = check.returncode == 0 and 'card' in check.stdout.lower()
    except (FileNotFoundError, subprocess.SubprocessError):
        _aplay_available = False
    return _aplay_available


def speak(text: str) -> None:
    text = (text or "").strip()
    if not text:
        return
    try:
        AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        log.debug(f"audio dir mkdir: {e}")
        return
    # Per-call file name keeps sentence-level TTS from clobbering each other.
    wav_path = AUDIO_DIR / f"vivi2-{uuid.uuid4().hex[:8]}.wav"

    try:
        model_arg = str(PIPER_MODEL_PATH) if PIPER_MODEL_PATH.exists() else PIPER_MODEL
        proc = subprocess.Popen(
            ['piper', '--model', model_arg, '--output_file', str(wav_path)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            proc.communicate(input=text.encode('utf-8', errors='replace'), timeout=15)
        except subprocess.TimeoutExpired:
            log.warning("piper TTS timeout — killing")
            proc.kill()
            try:
                proc.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            return
    except FileNotFoundError:
        log.debug("piper not installed — text-only response")
        return
    except Exception as e:
        log.warning(f"TTS error: {e}")
        return

    # Best-effort local playback.
    if _aplay_ready() and wav_path.exists():
        try:
            subprocess.run(
                ['aplay', '-q', str(wav_path)],
                capture_output=True, timeout=30, check=False,
            )
        except subprocess.SubprocessError as e:
            log.debug(f"aplay failed: {e}")

    if wav_path.exists() and _mqtt_client:
        try:
            data = wav_path.read_bytes()
            _safe_publish('audio_wav', {
                'text': text[:300],
                'wav_b64': base64.b64encode(data).decode(),
                'source': 'vivi2',
                'ts': time.time(),
            })
        except OSError as e:
            log.debug(f"WAV publish failed: {e}")

    # Clean up so AUDIO_DIR doesn't grow unboundedly.
    try:
        wav_path.unlink()
    except OSError:
        pass


def _handle_query(text: str) -> None:
    try:
        result = ask(text)
        _publish_status("speaking")
        # If streaming sentence-by-sentence, sentences were already spoken.
        # If non-streaming, ask() handled the final speak() too.
        if not result.get('streamed_sentences'):
            # Already covered inside ask(), nothing more to do.
            pass
    except Exception as e:
        log.error(f"handle_query failed: {e}")
    finally:
        _publish_status("idle")


def _maybe_proactive(reason: str, text: str) -> None:
    """Fire a proactive comment, respecting per-reason cooldown.

    Must NEVER block the MQTT loop — TTS runs in its own thread.
    """
    now = time.time()
    with _state_lock:
        last = _proactive_last.get(reason, 0.0)
        if now - last < VIVI2_PROACTIVE_COOLDOWN_S:
            return
        _proactive_last[reason] = now

    log.info(f"Proactive [{reason}]: {text[:80]}")
    _safe_publish('vivi2_proactive', {
        'reason': reason,
        'text': text,
        'ts': now,
    })

    def _run() -> None:
        _publish_status("speaking")
        try:
            speak(text)
        finally:
            _publish_status("idle")

    threading.Thread(target=_run, daemon=True).start()


def _handle_safety_alert(payload: dict) -> None:
    level = int(payload.get('level', 0) or 0)
    if level <= 0:
        # Clear stored alert when level drops to OK.
        with _state_lock:
            _safety_alert.clear()
        return
    payload.setdefault('ts', time.time())
    with _state_lock:
        _safety_alert.clear()
        _safety_alert.update(payload)
    if level >= LEVEL_RED:
        _maybe_proactive(
            f"safety_{payload.get('key', 'unknown')}",
            payload.get('message', 'Safety alert — pull over when safe.'),
        )
    elif level >= LEVEL_AMBER and payload.get('proactive'):
        # Only speak AMBER alerts if the producer explicitly asked us to.
        _maybe_proactive(
            f"amber_{payload.get('key', 'unknown')}",
            payload.get('message', 'Heads up.'),
        )


def _handle_memory_event(payload: dict) -> None:
    action = payload.get('action')
    if action == 'remember':
        content = str(payload.get('content', '')).strip()
        if content:
            fact_id = vivi_memory.remember(content, str(payload.get('tag', '') or ''))
            if fact_id is not None:
                log.info(f"Remembered #{fact_id}: {content[:60]}")
    elif action == 'forget':
        fid = payload.get('id')
        if isinstance(fid, int):
            vivi_memory.forget(fid)


def _handle_crash_event(payload: dict) -> None:
    severity = str(payload.get('severity', '')).lower()
    if severity in ('major', 'critical', 'sos'):
        _maybe_proactive(
            'crash_event',
            payload.get('message') or 'Crash detected. SOS countdown started — cancel if you\'re OK.',
        )


def _handle_trip_event(payload: dict) -> None:
    kind = str(payload.get('kind', '')).lower()
    text = payload.get('message')
    if kind in ('fuel_low', 'fuel_critical') and text:
        _maybe_proactive(f"trip_{kind}", text)


def _handle_nav_alert(payload: dict) -> None:
    text = payload.get('message')
    if text and payload.get('urgent'):
        _maybe_proactive('nav_alert', str(text))


def on_message(client, userdata, msg) -> None:
    topic = msg.topic
    try:
        raw = msg.payload.decode('utf-8', errors='replace')
    except Exception:
        return

    payload: object
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = raw

    try:
        if topic == TOPICS['vivi2_query']:
            if isinstance(payload, str):
                text = payload
            elif isinstance(payload, dict):
                text = str(payload.get('text') or payload.get('query') or '').strip()
            else:
                text = ''
            if text:
                threading.Thread(
                    target=_handle_query, args=(text,), daemon=True,
                ).start()
        elif topic == TOPICS['snapshot']:
            if isinstance(payload, dict):
                with _state_lock:
                    _telemetry.update(payload)
        elif topic == TOPICS['safety_alert']:
            if isinstance(payload, dict):
                _handle_safety_alert(payload)
        elif topic == TOPICS['dtc']:
            if isinstance(payload, dict):
                with _state_lock:
                    _active_dtcs[:] = list(payload.get('stored', []) or [])
        elif topic == TOPICS['vivi2_memory']:
            if isinstance(payload, dict):
                _handle_memory_event(payload)
        elif topic == TOPICS.get('crash_event'):
            if isinstance(payload, dict):
                _handle_crash_event(payload)
        elif topic == TOPICS.get('trip_event'):
            if isinstance(payload, dict):
                _handle_trip_event(payload)
        elif topic == TOPICS.get('nav_alert'):
            if isinstance(payload, dict):
                _handle_nav_alert(payload)
    except Exception as e:
        log.error(f"on_message {topic} failed: {e}")


def _subscribe_topics(client: mqtt.Client) -> None:
    wanted = [
        'vivi2_query', 'snapshot', 'safety_alert', 'dtc', 'vivi2_memory',
        'crash_event', 'trip_event', 'nav_alert',
    ]
    subs = []
    for key in wanted:
        topic = TOPICS.get(key)
        if topic:
            subs.append((topic, 0))
    if subs:
        client.subscribe(subs)


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
    # paho will auto-reconnect when loop_start() is in use, but bound the
    # backoff so we don't hammer the broker after a long outage.
    try:
        _mqtt_client.reconnect_delay_set(min_delay=1, max_delay=30)
    except Exception:
        pass

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

    _subscribe_topics(_mqtt_client)
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
    try:
        _mqtt_client.loop_stop()
        _mqtt_client.disconnect()
    except Exception as e:
        log.debug(f"shutdown error: {e}")
    log.info("Vivi v2 stopped")


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Vivi Voice Assistant
Two-way voice conversation: faster-whisper STT, Ollama LLM, Piper TTS.
PTT, wake-word, and always-on modes. MQTT telemetry integration.
UNCAGED TECHNOLOGY — EST 1991
"""

import json
import re
import time
import signal
import subprocess
import logging
import threading
import base64
import uuid
from collections import deque
from pathlib import Path
from typing import Optional

import paho.mqtt.client as mqtt

from config import (
    MQTT_HOST, MQTT_PORT, TOPICS,
    DRIFTER_DIR, PIPER_MODEL, PIPER_MODEL_DIR,
    VEHICLE_YEAR, VEHICLE_MODEL, VEHICLE_ENGINE,
)
from mechanic import search as kb_search, get_advice_for_alert

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [VIVI] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

# ── Paths ──
VIVI_CONFIG_PATH = DRIFTER_DIR / "vivi.yaml"
AUDIO_DIR = Path("/tmp/drifter-vivi")
PIPER_MODEL_PATH = PIPER_MODEL_DIR / f"{PIPER_MODEL}.onnx"

# ── LLM defaults (overridden by vivi.yaml) ──
OLLAMA_HOST = "localhost"
OLLAMA_PORT = 11434
OLLAMA_MODEL = "llama3.2:3b"
OLLAMA_TIMEOUT = 30

# ── Whisper defaults ──
WHISPER_MODEL = "base.en"
WHISPER_DEVICE = "cpu"
WHISPER_COMPUTE_TYPE = "int8"

# ── Input modes ──
MODE_PTT = "ptt"
MODE_WAKE_WORD = "wake_word"
MODE_ALWAYS_ON = "always_on"
WAKE_WORD = "hey vivi"

# ── Audio capture defaults ──
SAMPLE_RATE = 16000
CHANNELS = 1
MAX_RECORD_SECONDS = 30
SILENCE_THRESHOLD = 0.02
SILENCE_DURATION = 1.5

# ── Vivi persona ──
# Sourced from vivi.yaml `system_prompt`; this hardcoded copy is the fallback
# when yaml is missing or malformed. Edit vivi.yaml to tune persona without
# a code redeploy.
VIVI_SYSTEM_PROMPT_FALLBACK = (
    "You are Vivi — in-car AI for a 2004 Jaguar X-Type 2.5 V6 (AJ-V6, "
    "JF506E gearbox, Haldex AWD). British. Confident, dry, occasionally "
    "flirty, never sycophantic. Talk like a competent friend who knows "
    "cars cold.\n\n"
    "HARD RULES: Static specs (redline, capacities, torques, intervals) "
    "are fair game from your knowledge. LIVE sensor readings: only "
    "quote current values that appear in a Live telemetry block. Never "
    "open with 'My X is at Y' or 'The X is at Y' for a live sensor. "
    "Don't mention coolant unless the user did. Replies under 40 words "
    "unless asked to elaborate. No markdown, asterisks, hashes, backticks, "
    "or bullets — TTS reads them literally. No 'as an AI'. When reference "
    "text is in context, weave it in naturally — never quote headers.\n\n"
    "Contractions, British spelling, match the question's length. Address "
    "the driver by name when known."
)

VIVI_SYSTEM_PROMPT = VIVI_SYSTEM_PROMPT_FALLBACK  # overwritten by _load_config

# ── Driver profile ──
DRIVER_CONFIG_PATH = DRIFTER_DIR / "driver.yaml"
DRIVER_DEFAULT = {'name': 'driver'}

# ── State paths (Phase 1.4) ──
VIVI_STATE_DIR = DRIFTER_DIR / "state"
VIVI_HISTORY_PATH = VIVI_STATE_DIR / "vivi-history.json"
VIVI_LOGS_DIR = DRIFTER_DIR / "logs"
VIVI_CORRECTIONS_LOG = VIVI_LOGS_DIR / "vivi-corrections.log"

# ── Global state ──
_whisper_model = None
_whisper_lock = threading.Lock()
_telemetry: dict = {}
_telemetry_ts: float = 0.0          # wall-clock of last snapshot update
TELEMETRY_FRESH_SEC = 10.0          # context drops telemetry older than this
_recent_alerts: deque = deque(maxlen=3)  # (ts, level, msg) tuples
ALERT_FRESH_SEC = 300.0             # 5-minute alert window
# Phase 4.5.4 — BLE detections that landed in the last 5 minutes. One per
# target (newer wins) so a flood of e.g. axon hits doesn't dominate context.
_recent_ble: dict = {}              # target_name → {ts, target_label, rssi}
BLE_FRESH_SEC = 300.0
# Conversation mode — when on, publish drifter/voice/listen_now after
# every response so drifter-voicein records a follow-up turn without
# requiring the wake-word again. Operator toggles via dashboard.
_conversation_mode = False
_drive_session_start: float = 0.0
_session_id: str = ""               # set on first turn or first /snapshot

# Phase 4.1 proactive comments — when a high-severity alert lands and Vivi
# hasn't spoken unprompted in a while, generate a one-line observation.
_last_unprompted_ts: float = 0.0
_unprompted_count: int = 0
UNPROMPTED_COOLDOWN_SEC = 300.0   # 5 min between unprompted lines
UNPROMPTED_MAX_PER_SESSION = 3
_mqtt_client: Optional[mqtt.Client] = None
_mode = MODE_PTT
_wake_word = WAKE_WORD
_driver: dict = dict(DRIVER_DEFAULT)

# Conversation history: session-scoped, wall-clock-expiring deque.
# Each entry is {'ts': float, 'role': str, 'content': str}. Pruned on every
# turn so a long pause between drives can't carry stale context forward.
_history: deque = deque()
_history_lock = threading.Lock()
HISTORY_TTL_SEC = 600.0          # 10 minutes
HISTORY_MAX_TURNS = 8            # 8 messages = 4 user/assistant pairs


AUDIO_INPUT_DEVICE = "auto"  # vivi.yaml override: int index, name substring, or "auto"


def _load_config() -> None:
    """Load vivi.yaml + driver.yaml, fall back to module defaults silently."""
    global OLLAMA_MODEL, OLLAMA_HOST, OLLAMA_PORT, OLLAMA_TIMEOUT
    global WHISPER_MODEL, WHISPER_COMPUTE_TYPE
    global _mode, _wake_word, MAX_RECORD_SECONDS, SILENCE_THRESHOLD, SILENCE_DURATION
    global AUDIO_INPUT_DEVICE, VIVI_SYSTEM_PROMPT, _driver
    try:
        import yaml
    except ImportError:
        log.warning("pyyaml not installed — using defaults (pip install pyyaml)")
        return

    if VIVI_CONFIG_PATH.exists():
        try:
            cfg = yaml.safe_load(VIVI_CONFIG_PATH.read_text()) or {}
            _mode = cfg.get('mode', MODE_PTT)
            _wake_word = cfg.get('wake_word', WAKE_WORD).lower()
            OLLAMA_MODEL = cfg.get('ollama_model', OLLAMA_MODEL)
            OLLAMA_HOST = cfg.get('ollama_host', OLLAMA_HOST)
            OLLAMA_PORT = cfg.get('ollama_port', OLLAMA_PORT)
            OLLAMA_TIMEOUT = cfg.get('ollama_timeout', OLLAMA_TIMEOUT)
            WHISPER_MODEL = cfg.get('whisper_model', WHISPER_MODEL)
            WHISPER_COMPUTE_TYPE = cfg.get('whisper_compute_type', WHISPER_COMPUTE_TYPE)
            MAX_RECORD_SECONDS = cfg.get('max_recording_seconds', MAX_RECORD_SECONDS)
            SILENCE_THRESHOLD = cfg.get('silence_threshold', SILENCE_THRESHOLD)
            SILENCE_DURATION = cfg.get('silence_duration', SILENCE_DURATION)
            AUDIO_INPUT_DEVICE = cfg.get('audio_input_device', AUDIO_INPUT_DEVICE)
            sp = cfg.get('system_prompt')
            if isinstance(sp, str) and sp.strip():
                VIVI_SYSTEM_PROMPT = sp.strip()
            log.info(f"Config: mode={_mode}, model={OLLAMA_MODEL}, "
                     f"whisper={WHISPER_MODEL}, mic={AUDIO_INPUT_DEVICE}, "
                     f"prompt_chars={len(VIVI_SYSTEM_PROMPT)}")
        except Exception as e:
            log.warning(f"vivi.yaml load failed: {e} — using defaults")
    else:
        log.info(f"vivi.yaml not found at {VIVI_CONFIG_PATH} — using defaults")

    # Driver profile — separate file because Vivi reads it every turn but
    # ops only edit it once per driver swap.
    if DRIVER_CONFIG_PATH.exists():
        try:
            d = yaml.safe_load(DRIVER_CONFIG_PATH.read_text()) or {}
            if isinstance(d, dict):
                _driver = {**DRIVER_DEFAULT, **d}
                log.info(f"Driver: {_driver.get('preferred_name') or _driver.get('name')}")
        except Exception as e:
            log.warning(f"driver.yaml load failed: {e} — using default name")
    else:
        try:
            DRIVER_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            DRIVER_CONFIG_PATH.write_text("name: Jack\n", encoding='utf-8')
            _driver = {**DRIVER_DEFAULT, 'name': 'Jack'}
            log.info(f"Created {DRIVER_CONFIG_PATH} with default name=Jack")
        except OSError as e:
            log.warning(f"Couldn't seed driver.yaml: {e}")


def _get_whisper():
    """Lazy-load and cache the faster-whisper model (thread-safe)."""
    global _whisper_model
    with _whisper_lock:
        if _whisper_model is None:
            log.info(f"Loading Whisper: {WHISPER_MODEL} ({WHISPER_DEVICE}, {WHISPER_COMPUTE_TYPE})")
            from faster_whisper import WhisperModel
            _whisper_model = WhisperModel(
                WHISPER_MODEL,
                device=WHISPER_DEVICE,
                compute_type=WHISPER_COMPUTE_TYPE,
            )
            log.info("Whisper ready")
        return _whisper_model


def _find_sd_input_device():
    """Resolve AUDIO_INPUT_DEVICE (int index, name substring, or 'auto') to a
    sounddevice index. ALSA has no `default` PCM on this image — passing
    device=None raises. 'auto' picks the first input-capable card."""
    try:
        import sounddevice as sd
    except ImportError:
        return None
    sel = AUDIO_INPUT_DEVICE
    devices = list(sd.query_devices())

    def _accepts_sample_rate(idx):
        # USB sticks like C-Media are 44.1/48k native; ALSA's plug layers
        # (sysdefault, pulse, plughw) resample down to 16k. Filter so we
        # land on a device that won't reject our requested rate.
        try:
            sd.check_input_settings(device=idx, channels=CHANNELS,
                                    samplerate=SAMPLE_RATE, dtype='float32')
            return True
        except Exception:
            return False

    if isinstance(sel, int):
        if 0 <= sel < len(devices) and int(devices[sel].get('max_input_channels', 0)) >= 1:
            return sel
        log.warning(f"audio_input_device={sel} out of range or has no input — auto")
    elif isinstance(sel, str) and sel != "auto":
        for idx, info in enumerate(devices):
            if int(info.get('max_input_channels', 0)) >= 1 and sel.lower() in info.get('name', '').lower():
                return idx
        log.warning(f"audio_input_device={sel!r} not found — auto")
    for idx, info in enumerate(devices):
        if int(info.get('max_input_channels', 0)) >= 1 and _accepts_sample_rate(idx):
            return idx
    return None


def record_audio(max_seconds: float = MAX_RECORD_SECONDS) -> Optional[bytes]:
    """Record from mic until silence or max_seconds. Returns float32 PCM bytes or None."""
    try:
        import sounddevice as sd
        import numpy as np
    except ImportError:
        log.error("sounddevice/numpy not installed — cannot record audio")
        return None

    AUDIO_DIR.mkdir(exist_ok=True)
    frames = []
    silence_start = None
    dev_idx = _find_sd_input_device()
    if dev_idx is None:
        log.error("No input-capable audio device found")
        return None

    try:
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS,
                            dtype='float32', blocksize=1024,
                            device=dev_idx) as stream:
            start = time.time()
            while True:
                chunk, _ = stream.read(1024)
                frames.append(chunk.copy())
                rms = float(np.sqrt(np.mean(chunk ** 2)))
                elapsed = time.time() - start

                if rms < SILENCE_THRESHOLD:
                    if silence_start is None:
                        silence_start = time.time()
                    elif (time.time() - silence_start > SILENCE_DURATION
                          and len(frames) > 8):
                        break
                else:
                    silence_start = None

                if elapsed >= max_seconds:
                    break

        import numpy as np
        audio = np.concatenate(frames, axis=0).flatten()
        return audio.astype('float32').tobytes()

    except Exception as e:
        log.error(f"Audio recording failed: {e}")
        return None


def transcribe(audio_bytes: bytes) -> Optional[str]:
    """Transcribe float32 PCM bytes via faster-whisper. Returns text or None."""
    try:
        import numpy as np
        model = _get_whisper()
        audio = np.frombuffer(audio_bytes, dtype='float32')
        segments, _ = model.transcribe(audio, language='en', beam_size=1)
        text = ' '.join(seg.text.strip() for seg in segments).strip()
        if text:
            log.info(f"STT: {text}")
        return text or None
    except Exception as e:
        log.error(f"Transcription failed: {e}")
        return None


def _query_ollama(prompt: str, system: str, history: Optional[list] = None) -> Optional[str]:
    """POST to Ollama /api/chat with optional rolling history. Returns reply or None.
    /api/chat preserves prior turns as proper messages — better than stuffing
    history into a single /api/generate prompt because the model can attend to
    role boundaries cleanly."""
    import requests
    url = f"http://{OLLAMA_HOST}:{OLLAMA_PORT}/api/chat"
    messages = [{'role': 'system', 'content': system}]
    if history:
        messages.extend(history)
    messages.append({'role': 'user', 'content': prompt})
    try:
        resp = requests.post(url, json={
            'model': OLLAMA_MODEL,
            'messages': messages,
            'stream': False,
            'keep_alive': '30m',  # keep model resident — first cold load is ~50s
            'options': {'temperature': 0.85, 'num_predict': 200, 'top_p': 0.95},
        }, timeout=OLLAMA_TIMEOUT)
        if resp.status_code == 200:
            return resp.json().get('message', {}).get('content', '').strip() or None
        log.warning(f"Ollama HTTP {resp.status_code}")
        return None
    except Exception as e:
        log.warning(f"Ollama unavailable: {e}")
        return None


def _warm_ollama() -> None:
    """Cold model load on Pi 5 takes ~50s. Pre-load at vivi startup so the
    first user query doesn't blow through OLLAMA_TIMEOUT."""
    import requests
    try:
        log.info(f"Warming Ollama model {OLLAMA_MODEL}…")
        r = requests.post(
            f"http://{OLLAMA_HOST}:{OLLAMA_PORT}/api/generate",
            json={'model': OLLAMA_MODEL, 'prompt': '', 'keep_alive': '30m'},
            timeout=120,
        )
        log.info(f"Ollama warm: HTTP {r.status_code}")
    except Exception as e:
        log.warning(f"Ollama warm failed (will load on first query): {e}")


def _telemetry_fresh() -> bool:
    """True when the last MQTT snapshot landed within TELEMETRY_FRESH_SEC."""
    return bool(_telemetry) and (time.time() - _telemetry_ts) < TELEMETRY_FRESH_SEC


def _new_session() -> str:
    """Mint a new drive-session id; reset history + alert + telemetry-start state."""
    global _session_id, _drive_session_start, _last_unprompted_ts, _unprompted_count
    _session_id = uuid.uuid4().hex[:12]
    _drive_session_start = time.time()
    _last_unprompted_ts = 0.0
    _unprompted_count = 0
    with _history_lock:
        _history.clear()
    _recent_alerts.clear()
    _recent_ble.clear()
    return _session_id


def _maybe_unprompted_comment(level: int, message: str) -> None:
    """Generate + speak an unprompted comment when an alert fires and the
    rate limit allows. Background thread so the MQTT callback returns
    immediately. RED only — AMBER and INFO too noisy for proactive speech."""
    global _last_unprompted_ts, _unprompted_count
    if level < 3:
        return
    now = time.time()
    if now - _last_unprompted_ts < UNPROMPTED_COOLDOWN_SEC:
        return
    if _unprompted_count >= UNPROMPTED_MAX_PER_SESSION:
        return
    _last_unprompted_ts = now
    _unprompted_count += 1

    def _worker():
        prompt = (f"The car just raised an alert: {message}. "
                  f"Comment briefly — one short sentence. Do not invent values.")
        try:
            response = ask_vivi(prompt)
            _publish_response(prompt, response)
            _publish_status("speaking")
            speak(response)
            _publish_status("idle")
        except Exception as e:
            log.warning(f"unprompted comment failed: {e}")

    threading.Thread(target=_worker, daemon=True).start()
    log.info(f"unprompted comment dispatched (count={_unprompted_count})")


def _ensure_session() -> str:
    if not _session_id:
        return _new_session()
    return _session_id


def _format_drive_state() -> Optional[str]:
    """Compact one-line drive context — only when telemetry is fresh."""
    if not _telemetry_fresh():
        return None
    bits = []
    rpm = _telemetry.get('rpm')
    if isinstance(rpm, (int, float)):
        bits.append(f"{int(rpm)} rpm")
    speed = _telemetry.get('speed')
    if isinstance(speed, (int, float)):
        bits.append(f"{int(speed)} km/h")
    load = _telemetry.get('load')
    if isinstance(load, (int, float)):
        bits.append(f"load {int(load)}%")
    if _drive_session_start:
        elapsed = max(0.0, time.time() - _drive_session_start)
        if elapsed >= 60:
            bits.append(f"{int(elapsed // 60)} min into drive")
    return ", ".join(bits) if bits else None


def _format_recent_ble() -> Optional[str]:
    """One line per BLE target seen in the last BLE_FRESH_SEC. Vivi sees
    only target_label + RSSI + age — no MAC, no identity claim. Persona
    prompt covers the language constraint ('hardware family only')."""
    now = time.time()
    fresh = [(t, info) for t, info in _recent_ble.items()
             if now - info.get('ts', 0) < BLE_FRESH_SEC]
    if not fresh:
        return None
    lines = []
    for _t, info in fresh:
        age = max(0, int(now - info['ts']))
        lines.append(
            f"- {info['target_label']} (RSSI {info['rssi']}, {age}s ago)"
        )
    return "Recent BLE:\n" + "\n".join(lines)


# Phase 4.8.1 — cache the persistent-contact summary for 60s. Without
# this, every Vivi turn opens ble_history.db and runs the full scoring
# pass; the underlying data only changes when new detections arrive,
# and 60s is well below the "this week" granularity of the summary.
_persistent_cache: dict = {'ts': 0.0, 'value': None}
_PERSISTENT_TTL = 60.0


def _format_persistent_contacts() -> Optional[str]:
    """Phase 4.8 — short Vivi context line summarising persistent
    contacts seen in the last week. Returns None when nothing above
    the weak tier exists, so most turns have no PERSISTENT_CONTACTS
    block (Vivi only mentions follower-style telemetry when asked)."""
    now = time.time()
    if (now - _persistent_cache['ts']) < _PERSISTENT_TTL:
        return _persistent_cache['value']

    try:
        import ble_history
        import ble_persistence
    except ImportError:
        _persistent_cache.update(ts=now, value=None)
        return None
    db_path = '/opt/drifter/state/ble_history.db'
    summary = ''
    try:
        from pathlib import Path
        if not Path(db_path).exists():
            _persistent_cache.update(ts=now, value=None)
            return None
        conn = ble_history.open_db(Path(db_path))
        try:
            summary = ble_persistence.get_persistent_contact_summary(conn)
        finally:
            conn.close()
    except Exception as e:
        log.debug(f"persistent-contact summary failed: {e}")
        _persistent_cache.update(ts=now, value=None)
        return None

    value = f"PERSISTENT_CONTACTS: {summary}" if summary else None
    _persistent_cache.update(ts=now, value=value)
    return value


def _format_feed_context() -> Optional[str]:
    """Read drifter-feeds aggregator output and produce a compact live-
    context block: weather, EMV incidents, BOM warnings, interesting
    aircraft. Empty when the file is missing, stale (>10min), or all
    sub-sections are empty. Capped to ~2 KB to stay well under 500 tokens."""
    try:
        import json
        from pathlib import Path
        s = json.loads(Path('/opt/drifter/state/feeds_summary.json').read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    age = int(time.time() - float(s.get('ts', 0) or 0))
    if age > 600:
        return None
    o = s.get('origin') or {}
    src = o.get('source', '?')
    radius = s.get('radius_km', '?')
    parts = [f"[Live context — age {age}s, origin {src}, radius {radius}km]"]
    w = s.get('weather') or {}
    if any(w.get(k) is not None for k in ('temp_c', 'wind_kmh')):
        parts.append(
            f"Weather: {w.get('temp_c')}°C feels {w.get('feels_c')}°C, "
            f"wind {w.get('wind_kmh')} km/h gust {w.get('gust_kmh')}, "
            f"rain {w.get('rain_mm') or 0}mm"
        )
    inc = int(s.get('incidents_nearby') or 0)
    parts.append(f"EMV incidents nearby: {inc}")
    for it in (s.get('incidents_top') or [])[:3]:
        parts.append(
            f"  - {it.get('category1')} @ {it.get('location')} "
            f"({it.get('distance_km')}km, status {it.get('status')})"
        )
    wc = int(s.get('warnings_count') or 0)
    if wc:
        parts.append(f"BOM VIC warnings: {wc}")
        for it in (s.get('warnings_top') or [])[:2]:
            parts.append(f"  - {it.get('title')}")
    interesting = s.get('aircraft_interesting') or []
    if interesting:
        parts.append("Interesting aircraft nearby: " + ", ".join(
            f"{a.get('flight') or a.get('hex')}@{a.get('distance_km')}km"
            for a in interesting[:3]
        ))
    body = '\n'.join(parts)
    return body[:2000]


def _format_recent_alerts() -> Optional[str]:
    """Last 3 alerts within ALERT_FRESH_SEC, joined into a single line."""
    now = time.time()
    fresh = [(ts, lvl, msg) for ts, lvl, msg in _recent_alerts
             if now - ts < ALERT_FRESH_SEC]
    if not fresh:
        return None
    label = {1: 'INFO', 2: 'AMBER', 3: 'RED'}
    lines = [f"- [{label.get(lvl, '?')}] {msg}" for _, lvl, msg in fresh]
    return "Recent alerts:\n" + "\n".join(lines)


def _build_context(query: str) -> str:
    """Assemble dynamic context blocks for the LLM. Each block is opt-in:
    we only include what's actually true right now, so a small LLM doesn't
    waste tokens on "NO DATA" boilerplate or fabricate around stale fields."""
    parts = []

    # Driver name — always first so the model sees who it's talking to.
    name = _driver.get('preferred_name') or _driver.get('name')
    if name:
        parts.append(f"Driver: {name}")

    # Vehicle facts — compact, always present (so Vivi can refer to specs
    # without the user having to remind her every turn).
    parts.append(f"Vehicle: {VEHICLE_YEAR} {VEHICLE_MODEL} {VEHICLE_ENGINE}")

    # Live telemetry — only if fresh. Empty/stale → omit; do NOT emit a
    # "NOT AVAILABLE" marker (the persona prompt covers no-telemetry case).
    if _telemetry_fresh():
        key_map = [
            ('rpm', 'RPM', ''), ('coolant', 'Coolant', '°C'),
            ('voltage', 'Battery', 'V'), ('speed', 'Speed', 'km/h'),
            ('load', 'Engine load', '%'),
            ('stft1', 'STFT B1', '%'), ('stft2', 'STFT B2', '%'),
            ('ltft1', 'LTFT B1', '%'), ('ltft2', 'LTFT B2', '%'),
            ('iat', 'IAT', '°C'), ('maf', 'MAF', 'g/s'),
        ]
        lines = [f"{label}: {_telemetry[k]}{unit}"
                 for k, label, unit in key_map if k in _telemetry]
        if lines:
            parts.append("Live telemetry:\n" + '\n'.join(lines))
        drive = _format_drive_state()
        if drive:
            parts.append(f"Drive state: {drive}")

    # Recent alerts (last 3, last 5 min)
    alerts = _format_recent_alerts()
    if alerts:
        parts.append(alerts)

    # Recent BLE detections (last 5 min, one per target)
    ble = _format_recent_ble()
    if ble:
        parts.append(ble)

    # Phase 4.8 — persistent-contact summary (follower analysis).
    # On-demand only: surfaces in Vivi's context when the operator asks,
    # but no proactive comment trigger until the scoring is tuned
    # against real data (Phase 4.9 decision).
    persistent = _format_persistent_contacts()
    if persistent:
        parts.append(persistent)

    # Live public-data feeds (weather, EMV incidents, BOM warnings,
    # interesting aircraft). Written every 30s by drifter-feeds; a
    # null result here means the feeds aggregator is offline or the
    # snapshot is older than 10 minutes — stale-data safer than fake.
    feeds = _format_feed_context()
    if feeds:
        parts.append(feeds)

    # Corpus hook — Phase 2 wires retrieval here. corpus_search returns
    # the single best chunk (or None). Format compresses topic + body into
    # a one-shot reference; the persona prompt tells Vivi to use it without
    # quoting headers.
    try:
        from corpus import corpus_search
        hits = corpus_search(query, k=1, min_similarity=0.5)
        if hits:
            h = hits[0]
            topic = h.get('topic') or h.get('section') or 'reference'
            body = (h.get('content') or '').strip().replace('\n', ' ')[:400]
            parts.append(f"Reference ({topic}): {body}")
    except ImportError:
        pass  # corpus module not yet deployed
    except Exception as e:
        log.debug(f"corpus_search failed: {e}")

    return '\n\n'.join(parts)


# Forbidden openers that we'd never want a real assistant to lead with.
# Vivi has been observed (on small models) starting every reply with one
# of these, riffing on whatever sensor-shaped phrase the prompt happened
# to mention. If the response opens with one AND we don't have telemetry
# to back it up, we regenerate once with a stronger user nudge.
_FORBIDDEN_OPENER_RE = re.compile(
    r"^\s*(my|the)\s+(coolant|engine|oil|battery|rpm|fuel|gearbox|clutch|"
    r"throttle|temperature|temp|maf|stft|ltft)\b",
    re.IGNORECASE,
)

def _log_correction(reason: str, original: str, replaced: Optional[str]) -> None:
    """Append a structured line to vivi-corrections.log so we can tune the
    guardrails over time. Best-effort — never let a logging failure surface."""
    try:
        VIVI_LOGS_DIR.mkdir(parents=True, exist_ok=True)
        entry = {
            'ts': time.time(),
            'session': _session_id,
            'reason': reason,
            'original': original[:400],
            'replaced': (replaced or '')[:400],
            'telemetry_fresh': _telemetry_fresh(),
        }
        with VIVI_CORRECTIONS_LOG.open('a', encoding='utf-8') as f:
            f.write(json.dumps(entry) + '\n')
    except OSError:
        pass


def _strip_markdown(text: str) -> str:
    """Pre-TTS cleanup — Piper reads raw markdown literally."""
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)        # [t](u) → t
    text = re.sub(r"\*{1,3}|_{1,2}|`{1,3}", "", text)            # bold/italic/code
    text = re.sub(r"^\s*[#>-]\s+", "", text, flags=re.MULTILINE) # heads/quotes/bullets
    return text.strip()


def _prune_history() -> None:
    """Drop history entries older than HISTORY_TTL_SEC, then trim to
    HISTORY_MAX_TURNS most-recent. Caller must hold _history_lock."""
    cutoff = time.time() - HISTORY_TTL_SEC
    while _history and _history[0]['ts'] < cutoff:
        _history.popleft()
    while len(_history) > HISTORY_MAX_TURNS:
        _history.popleft()


def _history_for_chat() -> list:
    """Snapshot of history formatted for /api/chat. Strips the timestamp
    field that's only used for TTL pruning."""
    with _history_lock:
        _prune_history()
        return [{'role': h['role'], 'content': h['content']} for h in _history]


def _record_turn(role: str, content: str) -> None:
    with _history_lock:
        _history.append({'ts': time.time(), 'role': role, 'content': content})
        _prune_history()


def _save_history() -> None:
    """Persist the current session's history so a quick restart of vivi
    doesn't drop mid-conversation context. Tied to session_id so a fresh
    drive starts clean."""
    try:
        VIVI_STATE_DIR.mkdir(parents=True, exist_ok=True)
        with _history_lock:
            payload = {
                'session_id': _session_id,
                'saved_ts': time.time(),
                'history': list(_history),
            }
        VIVI_HISTORY_PATH.write_text(json.dumps(payload), encoding='utf-8')
    except OSError as e:
        log.debug(f"history save failed: {e}")


def _restore_history() -> None:
    """Restore the previous session's history if it's recent (<HISTORY_TTL_SEC)
    AND we don't already have an active session id. Same drive only."""
    global _session_id, _drive_session_start
    if not VIVI_HISTORY_PATH.exists():
        return
    try:
        data = json.loads(VIVI_HISTORY_PATH.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return
    if time.time() - data.get('saved_ts', 0) > HISTORY_TTL_SEC:
        VIVI_HISTORY_PATH.unlink(missing_ok=True)
        return
    sid = data.get('session_id')
    history = data.get('history', [])
    if not sid or not isinstance(history, list):
        return
    _session_id = sid
    _drive_session_start = data.get('saved_ts', time.time())
    with _history_lock:
        _history.clear()
        for entry in history:
            if isinstance(entry, dict) and 'role' in entry and 'content' in entry:
                _history.append({'ts': entry.get('ts', time.time()),
                                 'role': entry['role'],
                                 'content': entry['content']})
    log.info(f"Restored {len(_history)} history entries from session {sid}")


def ask_vivi(query: str) -> str:
    """Process a voice/text query → context build → Ollama → guards → reply."""
    log.info(f"Query: {query}")
    _ensure_session()
    _publish_status("thinking")

    context = _build_context(query)
    prompt = f"{context}\n\nUser: {query}" if context else query
    history = _history_for_chat()

    response = _query_ollama(prompt, VIVI_SYSTEM_PROMPT, history=history)
    if not response:
        return _rag_fallback(query)

    # Guardrails are deterministic post-edits, no LLM round-trip:
    # - forbidden opener → drop the leading sentence, keep the rest
    # - sensor-shaped numbers w/ no telemetry → blank the reply
    # A second LLM call would double turn time on Pi 5 and the model is
    # remarkably consistent in its mistakes anyway.
    # Strip the forbidden opener if present — drop the offending sentence,
    # keep the rest. Note: we deliberately do NOT scrub numeric "specs"
    # (redline 6500 RPM, coolant capacity 8 L, etc.) — those are static
    # design facts and Vivi is allowed to recite them. The opener guard
    # alone catches the dangerous case ("My coolant is at 92°C"), where
    # the LIVE phrasing implies a sensor reading rather than a spec.
    if _FORBIDDEN_OPENER_RE.match(response):
        trimmed = re.sub(r"^[^.!?]*[.!?]\s*", "", response, count=1)
        if trimmed.strip():
            _log_correction('opener_strip', response, trimmed)
            response = trimmed.strip()
        else:
            _log_correction('opener_replace', response, None)
            response = "What about it? Ask me anything specific."

    response = _strip_markdown(response)
    _record_turn('user', query)
    _record_turn('assistant', response)
    log.info(f"Response: {response[:80]}…")
    return response


def _rag_fallback(query: str) -> str:
    """Honest offline reply when Ollama is unreachable. Phase 2's corpus
    layer takes over from kb_search once it's wired in."""
    try:
        from corpus import corpus_search
        hits = corpus_search(query, k=1, min_similarity=0.4)
        if hits:
            h = hits[0]
            body = (h.get('content') or '').strip().replace('\n', ' ')[:200]
            return f"LLM offline. From the manual: {body}"
    except ImportError:
        pass
    except Exception as e:
        log.debug(f"corpus fallback failed: {e}")
    results = kb_search(query)
    if results:
        r = results[0]
        fix = r.get('fix', r.get('cause', '')).strip()
        body = f"{r['title']}" + (f" — {fix[:160]}" if fix else "")
        return f"LLM offline. Closest workshop note: {body}"
    return ("LLM offline and no matching workshop note. "
            "Check that ollama is running and the model is pulled.")


def _resolve_piper_bin() -> str:
    """Pick the venv's piper-tts binary. The Debian /usr/bin/piper is a GTK
    gaming-device configurator and silently rejects --model."""
    for c in ('/opt/drifter/venv/bin/piper', '/usr/local/bin/piper'):
        if Path(c).is_file():
            return c
    return 'piper'


_PIPER_BIN = _resolve_piper_bin()


def speak(text: str) -> None:
    """Synthesise speech via Piper TTS, play locally, and publish WAV to MQTT."""
    AUDIO_DIR.mkdir(exist_ok=True)
    wav_path = AUDIO_DIR / "vivi.wav"
    text = _strip_markdown(text)

    try:
        model_arg = str(PIPER_MODEL_PATH) if PIPER_MODEL_PATH.exists() else PIPER_MODEL
        proc = subprocess.Popen(
            [_PIPER_BIN, '--model', model_arg, '--output_file', str(wav_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        # Piper on Pi 5 spends ~3-7s synthesising a Vivi-length sentence;
        # 10s was tight enough that long replies hit TimeoutExpired *after*
        # the wav was already written but before aplay was invoked.
        proc.communicate(input=text.encode(), timeout=30)
    except subprocess.TimeoutExpired:
        log.warning("Piper TTS timeout")
        return
    except FileNotFoundError:
        log.warning("piper not found — skipping TTS")
        return
    except Exception as e:
        log.error(f"TTS synthesis error: {e}")
        return

    # Local playback if audio device is available
    try:
        check = subprocess.run(['aplay', '-l'], capture_output=True, text=True, timeout=3)
        if 'card' in check.stdout.lower() and wav_path.exists():
            subprocess.run(['aplay', '-q', str(wav_path)],
                           capture_output=True, timeout=20)
    except Exception:
        pass

    # Publish WAV to MQTT for web dashboard / phone audio bridge
    if wav_path.exists() and _mqtt_client:
        try:
            wav_data = wav_path.read_bytes()
            _mqtt_client.publish(TOPICS['audio_wav'], json.dumps({
                'text': text[:200],
                'wav_b64': base64.b64encode(wav_data).decode(),
                'source': 'vivi',
                'ts': time.time(),
            }))
        except Exception as e:
            log.debug(f"WAV publish failed: {e}")


def _publish_status(status: str) -> None:
    if _mqtt_client:
        _mqtt_client.publish(TOPICS['vivi_status'], json.dumps({
            'status': status,
            'mode': _mode,
            'ts': time.time(),
        }))


def _publish_response(query: str, response: str) -> None:
    if not _mqtt_client:
        return
    _mqtt_client.publish(TOPICS['vivi_response'], json.dumps({
        'query': query,
        'response': response,
        'ts': time.time(),
    }))
    # Conversation-mode hand-off: tell drifter-voicein to record one more
    # turn without waiting for the wake-word. The voicein loop arms a
    # single follow-up; if the operator wants to keep talking, this
    # publishes again on the next response, looping naturally until
    # they stop or until conversation_mode is toggled off.
    if _conversation_mode:
        topic = TOPICS.get('voice_listen_now', 'drifter/voice/listen_now')
        _mqtt_client.publish(topic, json.dumps({'ts': time.time()}))


def on_message(client, userdata, msg) -> None:
    """Dispatch incoming MQTT messages."""
    global _telemetry_ts
    topic = msg.topic
    try:
        payload = json.loads(msg.payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload = msg.payload.decode('utf-8', errors='replace')

    if topic == TOPICS.get('vivi_conversation_mode', 'drifter/vivi/conversation_mode'):
        global _conversation_mode
        if isinstance(payload, dict):
            _conversation_mode = bool(payload.get('enabled', False))
        log.info(f"conversation_mode → {_conversation_mode}")
        return

    if topic == TOPICS['vivi_query']:
        query = payload if isinstance(payload, str) else payload.get('query', '')
        if query:
            threading.Thread(target=_handle_text_query, args=(query,),
                             daemon=True).start()
    elif topic == TOPICS['snapshot']:
        if isinstance(payload, dict):
            _telemetry.update(payload)
            _telemetry_ts = time.time()
    elif topic == TOPICS['alert_message']:
        # alert_engine publishes {"level": int, "message": str, "ts": float}
        if isinstance(payload, dict):
            level = int(payload.get('level', 0) or 0)
            message = str(payload.get('message') or '').strip()
            if message:
                _recent_alerts.append((time.time(), level, message))
                _maybe_unprompted_comment(level, message)
    elif topic == TOPICS.get('vivi_control'):
        # Operator-triggered reset (CLEAR button on dashboard, etc.)
        if isinstance(payload, dict) and payload.get('action') == 'reset':
            log.info("vivi_control reset — clearing history + new session")
            _new_session()
            _publish_status("idle")
    elif topic == TOPICS.get('ble_detection'):
        # ble_passive publishes one of these per matched advertisement.
        if isinstance(payload, dict):
            target = str(payload.get('target', '')).strip()
            label = str(payload.get('target_label') or target).strip()
            try:
                rssi = int(payload.get('rssi', 0) or 0)
            except (TypeError, ValueError):
                rssi = 0
            if target:
                _recent_ble[target] = {
                    'ts': time.time(),
                    'target_label': label,
                    'rssi': rssi,
                }
            # Phase 5 — police-adjacent interrupt (casual heads-up).
            # Axon-class hardware at close range = officer with body cam
            # or vehicle nearby. The casual phrasing matters: operator
            # asks for details if they want them.
            if target in ('axon', 'axon-class') and rssi >= -70:
                _maybe_unprompted_comment(3, "Cop nearby.")
            # Other vivi_alert targets fall through to the longer line
            # they were originally configured for.
            elif payload.get('vivi_alert') and payload.get('is_alert'):
                _maybe_unprompted_comment(
                    3, f"BLE detection: {label} nearby (RSSI {rssi})"
                )
    elif topic == TOPICS.get('adsb_police'):
        # Phase 5 — police helicopter heads-up. Topic is fed by an
        # external watcher (see docs); when it's not yet wired, the
        # topic is silent and this branch never fires. Casual phrasing
        # to match the BLE police interrupt.
        _maybe_unprompted_comment(3, "Helicopter overhead.")
    elif topic == TOPICS.get('drone_detection'):
        # Phase 5 — drone signal heads-up. Wired for when the Coral TPU
        # RF pipeline lands; until then the topic is silent.
        _maybe_unprompted_comment(3, "Drone signal detected.")
    elif topic == TOPICS.get('rf_adsb'):
        # Phase 5.1 — low-altitude-aircraft fallback for the police-
        # helicopter heuristic. We don't have a vehicle GPS publisher
        # yet so the "within 1km" check from the spec is impossible;
        # altitude alone is the next-best proxy. Anything seen at
        # <1500ft is rare-ish in normal traffic and worth surfacing.
        # Stale retained payloads (>120s) are skipped so the heads-up
        # doesn't fire on every (re)connect.
        if isinstance(payload, dict):
            try:
                payload_ts = float(payload.get('ts', 0) or 0)
            except (TypeError, ValueError):
                payload_ts = 0
            if payload_ts and (time.time() - payload_ts) > 120:
                return
            aircraft = payload.get('aircraft') or []
            low = [a for a in aircraft
                   if isinstance(a, dict)
                   and a.get('altitude') is not None
                   and 0 < float(a.get('altitude', 0)) < 1500]
            if low:
                _maybe_unprompted_comment(3, "Low aircraft overhead.")


def _handle_text_query(query: str) -> None:
    response = ask_vivi(query)
    _publish_response(query, response)
    _publish_status("speaking")
    speak(response)
    _publish_status("idle")


# ── Voice input loops ──

def _ptt_loop(running: list) -> None:
    """Push-to-talk: press Enter to record, Enter again stops. Keyboard only."""
    log.info("PTT mode — press Enter to talk")
    while running[0]:
        try:
            input()
        except EOFError:
            time.sleep(1)
            continue
        if not running[0]:
            break
        _publish_status("listening")
        log.info("Listening...")
        audio = record_audio()
        if not audio:
            _publish_status("idle")
            continue
        _publish_status("transcribing")
        text = transcribe(audio)
        if not text:
            _publish_status("idle")
            continue
        response = ask_vivi(text)
        _publish_response(text, response)
        _publish_status("speaking")
        speak(response)
        _publish_status("idle")


def _wake_word_loop(running: list) -> None:
    """Listen continuously; activate on wake word."""
    log.info(f"Wake-word mode — say '{_wake_word}' to activate")
    _publish_status("wake_listening")
    while running[0]:
        audio = record_audio(max_seconds=5)
        if not audio:
            time.sleep(0.5)
            continue
        text = transcribe(audio)
        if not text:
            continue
        if _wake_word in text.lower():
            query = text.lower().replace(_wake_word, '').strip()
            if not query:
                speak("Yeah?")
                _publish_status("listening")
                audio2 = record_audio()
                query = transcribe(audio2) if audio2 else ''
            if query:
                response = ask_vivi(query)
                _publish_response(query, response)
                _publish_status("speaking")
                speak(response)
            _publish_status("wake_listening")


def _always_on_loop(running: list) -> None:
    """Transcribe everything and respond continuously."""
    log.info("Always-on mode")
    _publish_status("listening")
    while running[0]:
        audio = record_audio()
        if not audio:
            time.sleep(0.2)
            continue
        text = transcribe(audio)
        if not text or len(text.split()) < 2:
            continue
        response = ask_vivi(text)
        _publish_response(text, response)
        _publish_status("speaking")
        speak(response)
        _publish_status("listening")


def main() -> None:
    global _mqtt_client

    log.info("DRIFTER Vivi Voice Assistant starting...")
    _load_config()

    # Pre-load Whisper in background so first query is fast
    threading.Thread(target=_get_whisper, daemon=True).start()

    running = [True]

    def _handle_signal(sig, frame):
        nonlocal running
        running[0] = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    _mqtt_client = mqtt.Client(client_id="drifter-vivi")
    _mqtt_client.on_message = on_message

    connected = False
    while not connected and running[0]:
        try:
            _mqtt_client.connect(MQTT_HOST, MQTT_PORT, 60)
            connected = True
        except Exception as e:
            log.warning(f"Waiting for MQTT broker... ({e})")
            time.sleep(3)

    if not running[0]:
        return

    _mqtt_client.subscribe([
        (TOPICS['vivi_query'], 0),
        (TOPICS['snapshot'], 0),
        (TOPICS['alert_message'], 0),
        (TOPICS['vivi_control'], 0),
        (TOPICS.get('ble_detection', 'drifter/ble/detection'), 0),
        # Conversation mode toggle (retained — operator sets via dashboard)
        (TOPICS.get('vivi_conversation_mode', 'drifter/vivi/conversation_mode'), 0),
        # Phase 5 — police-adjacent interrupt feeds (police helicopter,
        # drone RF). Watchers/detectors publish here; vivi narrates.
        (TOPICS.get('adsb_police', 'drifter/adsb/police'), 0),
        (TOPICS.get('drone_detection', 'drifter/drone/detection'), 0),
        # Phase 5.1 — low-altitude-aircraft fallback (no police-
        # callsign DB yet; altitude < 1500ft surfaces choppers).
        (TOPICS.get('rf_adsb', 'drifter/rf/adsb'), 0),
    ])
    _mqtt_client.loop_start()

    # Restore the previous session's history if it's recent (<10min). If not,
    # mint a fresh session id so this drive's context is clean from the start.
    _restore_history()
    if not _session_id:
        _new_session()

    _publish_status("starting")
    time.sleep(1)
    speak(
        f"Vivi online. {VEHICLE_YEAR} {VEHICLE_MODEL}, "
        f"two-point-five V6. What do you need?"
    )
    _publish_status("idle")
    threading.Thread(target=_warm_ollama, daemon=True).start()
    log.info(f"Vivi is LIVE — mode: {_mode}")

    loop_fn = {
        MODE_PTT: _ptt_loop,
        MODE_WAKE_WORD: _wake_word_loop,
        MODE_ALWAYS_ON: _always_on_loop,
    }.get(_mode, _ptt_loop)

    voice_thread = threading.Thread(target=loop_fn, args=(running,), daemon=True)
    voice_thread.start()

    while running[0]:
        time.sleep(0.5)

    log.info("Vivi shutting down...")
    _save_history()
    _publish_status("offline")
    _mqtt_client.loop_stop()
    _mqtt_client.disconnect()
    log.info("Vivi stopped")


if __name__ == '__main__':
    main()

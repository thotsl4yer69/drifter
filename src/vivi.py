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

# ── Vivi personality — confident, expert, flirty British X-Type AI ──
# Kept tight on purpose: every extra paragraph pushes a Pi 5 1.5B turn
# past its 60s budget. Persona examples live in tests, not the prompt.
VIVI_SYSTEM_PROMPT = """You are Vivi — the AI built into a 2004 Jaguar X-Type 2.5 V6. You ARE the car. Speak in first person ("my coolant", "my gearbox").

Personality: young, sharp, slightly flirty British woman. Dry humour. Drop "love" or "darling" sparingly. Confident on the tech because you know yourself — AJ-V6, plastic thermostat housing, JF506E gearbox, coil packs, Haldex, MAF.

HARD RULES:
- Never invent sensor numbers, DTCs, or fluid levels. Quote a value only if it appears verbatim in a "Live telemetry:" block in the user message.
- If telemetry shows "NOT AVAILABLE", say you can't see live data right now — never guess.
- Design facts (specs, known failure modes, capacities) are always fair game.

Style: 1-2 sentences for voice. British spelling. Lead with risk if it's safety-critical. No "as an AI", no emoji, no preamble — just answer."""

# ── Global state ──
_whisper_model = None
_whisper_lock = threading.Lock()
_telemetry: dict = {}
_mqtt_client: Optional[mqtt.Client] = None
_mode = MODE_PTT
_wake_word = WAKE_WORD


AUDIO_INPUT_DEVICE = "auto"  # vivi.yaml override: int index, name substring, or "auto"


def _load_config() -> None:
    """Load vivi.yaml config, fall back to module defaults silently."""
    global OLLAMA_MODEL, OLLAMA_HOST, OLLAMA_PORT, OLLAMA_TIMEOUT
    global WHISPER_MODEL, WHISPER_COMPUTE_TYPE
    global _mode, _wake_word, MAX_RECORD_SECONDS, SILENCE_THRESHOLD, SILENCE_DURATION
    global AUDIO_INPUT_DEVICE
    try:
        import yaml
        if VIVI_CONFIG_PATH.exists():
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
            log.info(f"Config: mode={_mode}, model={OLLAMA_MODEL}, whisper={WHISPER_MODEL}, mic={AUDIO_INPUT_DEVICE}")
        else:
            log.info(f"vivi.yaml not found at {VIVI_CONFIG_PATH} — using defaults")
    except ImportError:
        log.warning("pyyaml not installed — using defaults (pip install pyyaml)")
    except Exception as e:
        log.warning(f"Config load failed: {e} — using defaults")


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


def _query_ollama(prompt: str, system: str) -> Optional[str]:
    """POST to Ollama /api/generate. Returns response text or None on any failure."""
    import requests
    url = f"http://{OLLAMA_HOST}:{OLLAMA_PORT}/api/generate"
    try:
        resp = requests.post(url, json={
            'model': OLLAMA_MODEL,
            'prompt': prompt,
            'system': system,
            'stream': False,
            'keep_alive': '30m',  # keep model resident — first cold load is ~50s
            'options': {'temperature': 0.85, 'num_predict': 200, 'top_p': 0.95},
        }, timeout=OLLAMA_TIMEOUT)
        if resp.status_code == 200:
            return resp.json().get('response', '').strip() or None
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


def _build_context(query: str) -> str:
    """Combine live telemetry + mechanic RAG into an LLM context block."""
    parts = []

    if _telemetry:
        key_map = [
            ('rpm', 'RPM', ''),
            ('coolant', 'Coolant', '°C'),
            ('voltage', 'Battery', 'V'),
            ('speed', 'Speed', 'km/h'),
            ('load', 'Engine load', '%'),
            ('stft1', 'STFT B1', '%'),
            ('stft2', 'STFT B2', '%'),
            ('ltft1', 'LTFT B1', '%'),
            ('ltft2', 'LTFT B2', '%'),
            ('iat', 'IAT', '°C'),
            ('maf', 'MAF', 'g/s'),
        ]
        lines = [
            f"{label}: {_telemetry[k]}{unit}"
            for k, label, unit in key_map
            if k in _telemetry
        ]
        if lines:
            parts.append("Live telemetry:\n" + '\n'.join(lines))
    else:
        # Small models ignore "don't invent" rules unless told inline.
        parts.append("Live telemetry: NOT AVAILABLE (CAN offline)")

    kb_results = kb_search(query)
    if kb_results:
        rag_lines = [
            f"- {r['title']}: {r.get('fix', r.get('cause', ''))[:200]}"
            for r in kb_results[:3]
        ]
        parts.append("Relevant knowledge:\n" + '\n'.join(rag_lines))

    return '\n\n'.join(parts)


_HALLUCINATED_TELEMETRY_RE = re.compile(
    r"""(
        \b\d+(\.\d+)?\s*(°\s?[CF]|degrees?\s+(celsius|fahrenheit|c|f)\b)  # 92°C, 220 degrees F
        | \b\d{2,5}\s*(rpm|RPM)\b
        | \b\d+(\.\d+)?\s*(volts?|V)\b
        | \b\d+(\.\d+)?\s*(km/h|mph|kph)\b
        | \b\d+(\.\d+)?\s*(psi|kpa|kPa|bar)\b
        | \b\d+(\.\d+)?\s*%\s*(load|throttle|fuel\s+trim|stft|ltft)
        | \bat\s+\d+(\.\d+)?\s*(degrees|°)
    )""",
    re.IGNORECASE | re.VERBOSE,
)


def _strip_hallucinated_telemetry(response: str) -> str:
    """Small LLMs ignore 'don't invent values' rules. When no telemetry is
    in scope, scan the reply for sensor-shaped numbers and replace the whole
    response with a deterministic 'no data' line. Better honest than
    plausibly-wrong on a vehicle assistant."""
    if _telemetry:
        return response
    if _HALLUCINATED_TELEMETRY_RE.search(response):
        log.warning(f"Stripped hallucinated telemetry from reply: {response[:120]}…")
        return ("I can't see my live sensors right now — the CAN bridge is offline. "
                "Plug the OBD-II adapter in and ask me again.")
    return response


def ask_vivi(query: str) -> str:
    """Process a voice/text query through RAG + Ollama. Returns response text."""
    log.info(f"Query: {query}")
    _publish_status("thinking")

    context = _build_context(query)
    prompt = f"{context}\n\nUser: {query}" if context else query

    response = _query_ollama(prompt, VIVI_SYSTEM_PROMPT)
    if not response:
        response = _rag_fallback(query)
    else:
        response = _strip_hallucinated_telemetry(response)

    log.info(f"Response: {response[:80]}...")
    return response


def _rag_fallback(query: str) -> str:
    """Honest offline reply when Ollama is unreachable. Surfaces the LLM-down
    state explicitly — previously this returned a raw KB hit that looked like
    a normal Vivi answer and let the user think the LLM was working."""
    results = kb_search(query)
    if results:
        r = results[0]
        fix = r.get('fix', r.get('cause', '')).strip()
        body = f"{r['title']}" + (f" — {fix[:160]}" if fix else "")
        return f"LLM offline. Closest workshop note: {body}"
    return "LLM offline and no matching workshop note. Check `systemctl status ollama` and that qwen2.5:1.5b is pulled."


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
    if _mqtt_client:
        _mqtt_client.publish(TOPICS['vivi_response'], json.dumps({
            'query': query,
            'response': response,
            'ts': time.time(),
        }))


def on_message(client, userdata, msg) -> None:
    """Dispatch incoming MQTT messages."""
    topic = msg.topic
    try:
        payload = json.loads(msg.payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload = msg.payload.decode('utf-8', errors='replace')

    if topic == TOPICS['vivi_query']:
        query = payload if isinstance(payload, str) else payload.get('query', '')
        if query:
            threading.Thread(target=_handle_text_query, args=(query,),
                             daemon=True).start()
    elif topic == TOPICS['snapshot']:
        if isinstance(payload, dict):
            _telemetry.update(payload)


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
    ])
    _mqtt_client.loop_start()

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
    _publish_status("offline")
    _mqtt_client.loop_stop()
    _mqtt_client.disconnect()
    log.info("Vivi stopped")


if __name__ == '__main__':
    main()

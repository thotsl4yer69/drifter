#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Voice Input Service (drifter-voicein)
Microphone → text pipeline: wake word detection + Vosk STT → MQTT transcript.
Runs on Pi 5 in a 2004 Jaguar X-Type running Kali Linux.
UNCAGED TECHNOLOGY — EST 1991
"""

import json
import math
import struct
import signal
import time
import logging
import subprocess
import threading
import numpy as np

import paho.mqtt.client as mqtt

from config import (
    MQTT_HOST, MQTT_PORT, TOPICS, DRIFTER_DIR,
    VOSK_MODEL_DIR, WAKE_WORD_MODEL, WAKE_WORD_THRESHOLD,
    PTT_GPIO_PIN, VOICE_SILENCE_TIMEOUT, VOICE_MAX_RECORD,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [VOICE-IN] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

# ── Audio constants ──
SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK_SIZE = 4000          # 250ms at 16kHz
FORMAT_WIDTH = 2           # int16 = 2 bytes
AMBIENT_CALIBRATION_SEC = 1.0
SILENCE_RMS_FLOOR = 100    # absolute minimum RMS threshold

# ── Globals ──
running = True
mqtt_client = None
gpio_available = False
oww_available = False


# ═══════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════

def rms_energy(audio_bytes):
    """Calculate RMS energy of a chunk of int16 audio."""
    count = len(audio_bytes) // 2
    if count == 0:
        return 0
    shorts = struct.unpack(f'<{count}h', audio_bytes)
    sum_sq = sum(s * s for s in shorts)
    return math.sqrt(sum_sq / count)


def calibrate_silence(stream, seconds=AMBIENT_CALIBRATION_SEC):
    """Read ambient noise for `seconds` and return an RMS threshold."""
    chunks = int((seconds * SAMPLE_RATE) / CHUNK_SIZE)
    energies = []
    for _ in range(max(chunks, 2)):
        data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
        energies.append(rms_energy(data))
    mean_e = sum(energies) / len(energies) if energies else SILENCE_RMS_FLOOR
    threshold = max(mean_e * 1.8, SILENCE_RMS_FLOOR)
    log.info(f"Silence threshold calibrated: {threshold:.0f} (ambient RMS {mean_e:.0f})")
    return threshold


def beep():
    """Play a short acknowledgment beep via aplay."""
    try:
        # Generate a tiny beep WAV in-memory: 0.15s 880Hz sine
        import wave, io
        duration = 0.15
        n_frames = int(SAMPLE_RATE * duration)
        buf = io.BytesIO()
        with wave.open(buf, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            samples = []
            for i in range(n_frames):
                val = int(16000 * math.sin(2 * math.pi * 880 * i / SAMPLE_RATE))
                samples.append(struct.pack('<h', val))
            wf.writeframes(b''.join(samples))
        proc = subprocess.Popen(
            ['aplay', '-q', '-'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        proc.communicate(input=buf.getvalue(), timeout=3)
    except Exception as e:
        log.debug(f"Beep failed (non-critical): {e}")


def publish_transcript(transcript, source="wake_word"):
    """Publish a transcript to MQTT."""
    if not transcript.strip():
        return
    payload = json.dumps({
        "transcript": transcript.strip(),
        "timestamp": time.time(),
        "source": source,
    })
    mqtt_client.publish(TOPICS['voice_transcript'], payload)
    log.info(f"Transcript [{source}]: {transcript.strip()[:80]}")


# ═══════════════════════════════════════════════════════════════════
#  STT Recording (Vosk)
# ═══════════════════════════════════════════════════════════════════

def record_and_transcribe(stream, recognizer, silence_threshold, source="wake_word"):
    """Record audio from stream until silence, then return Vosk transcript."""
    from vosk import KaldiRecognizer

    beep()

    recognizer.AcceptWaveform(b'\x00' * 2)  # reset state
    rec = KaldiRecognizer(recognizer.model, SAMPLE_RATE)

    silence_chunks = 0
    max_silence_chunks = int(VOICE_SILENCE_TIMEOUT * SAMPLE_RATE / CHUNK_SIZE)
    max_record_chunks = int(VOICE_MAX_RECORD * SAMPLE_RATE / CHUNK_SIZE)
    heard_speech = False

    for i in range(max_record_chunks):
        if not running:
            break
        data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
        energy = rms_energy(data)

        if energy >= silence_threshold:
            heard_speech = True
            silence_chunks = 0
        else:
            silence_chunks += 1

        rec.AcceptWaveform(data)

        # Only end on silence after we have heard some speech
        if heard_speech and silence_chunks >= max_silence_chunks:
            break

    result = json.loads(rec.FinalResult())
    text = result.get('text', '').strip()

    if text:
        publish_transcript(text, source)
    else:
        log.info("No speech recognized in utterance")

    return text


# ═══════════════════════════════════════════════════════════════════
#  GPIO Push-to-Talk
# ═══════════════════════════════════════════════════════════════════

def setup_gpio():
    """Set up GPIO for push-to-talk button. Returns True if available."""
    global gpio_available
    try:
        import RPi.GPIO as GPIO
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(PTT_GPIO_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        gpio_available = True
        log.info(f"PTT button ready on GPIO {PTT_GPIO_PIN}")
        return True
    except (ImportError, RuntimeError) as e:
        log.warning(f"GPIO not available — PTT disabled: {e}")
        gpio_available = False
        return False


def check_ptt():
    """Check if PTT button is currently pressed (active low)."""
    if not gpio_available:
        return False
    try:
        import RPi.GPIO as GPIO
        return GPIO.input(PTT_GPIO_PIN) == GPIO.LOW
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════════
#  Wake Word Detection (OpenWakeWord)
# ═══════════════════════════════════════════════════════════════════

def create_wake_word_model():
    """Load OpenWakeWord model. Returns model or None."""
    global oww_available
    try:
        from openwakeword.model import Model
        oww_model = Model(wakeword_models=[WAKE_WORD_MODEL])
        oww_available = True
        log.info(f"Wake word model loaded: {WAKE_WORD_MODEL}")
        return oww_model
    except Exception as e:
        log.warning(f"OpenWakeWord not available — PTT-only mode: {e}")
        oww_available = False
        return None


def check_wake_word(oww_model, audio_chunk):
    """Run wake word detection on an audio chunk. Returns True if triggered."""
    if oww_model is None:
        return False
    try:
        audio_np = np.frombuffer(audio_chunk, dtype=np.int16)
        prediction = oww_model.predict(audio_np)
        for mdl_name, score in prediction.items():
            if score >= WAKE_WORD_THRESHOLD:
                log.info(f"Wake word detected! (score={score:.2f})")
                oww_model.reset()
                return True
    except Exception as e:
        log.debug(f"Wake word check error: {e}")
    return False


# ═══════════════════════════════════════════════════════════════════
#  MQTT
# ═══════════════════════════════════════════════════════════════════

def on_connect(client, userdata, flags, rc):
    """MQTT on-connect callback."""
    if rc == 0:
        log.info("Connected to MQTT broker")
        client.publish(TOPICS['voice_command'], json.dumps({
            "status": "Voice input online",
            "timestamp": time.time(),
        }))
    else:
        log.warning(f"MQTT connect failed (rc={rc})")


def setup_mqtt():
    """Create and connect MQTT client."""
    global mqtt_client
    mqtt_client = mqtt.Client(client_id="drifter-voicein")
    mqtt_client.on_connect = on_connect

    connected = False
    while not connected and running:
        try:
            mqtt_client.connect(MQTT_HOST, MQTT_PORT, 60)
            connected = True
        except Exception as e:
            log.warning(f"Waiting for MQTT broker... ({e})")
            time.sleep(3)

    if connected:
        mqtt_client.loop_start()
    return connected


# ═══════════════════════════════════════════════════════════════════
#  Main Loop
# ═══════════════════════════════════════════════════════════════════

def main():
    global running

    log.info("DRIFTER Voice Input Service starting...")

    def _shutdown(sig, frame):
        global running
        running = False
        log.info(f"Shutdown signal received ({sig})")

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    # ── Vosk model ──
    try:
        from vosk import Model as VoskModel, KaldiRecognizer, SetLogLevel
        SetLogLevel(-1)  # suppress Vosk internal logs
    except ImportError:
        log.error("vosk not installed — run: pip install vosk")
        return

    if not VOSK_MODEL_DIR.exists():
        log.error(f"Vosk model not found at {VOSK_MODEL_DIR}")
        log.error("Download with: wget + unzip into vosk-models/")
        return

    vosk_model = VoskModel(str(VOSK_MODEL_DIR))
    recognizer = KaldiRecognizer(vosk_model, SAMPLE_RATE)
    recognizer.model = vosk_model  # stash reference for record_and_transcribe
    log.info(f"Vosk model loaded: {VOSK_MODEL_DIR.name}")

    # ── MQTT ──
    if not setup_mqtt():
        return

    # ── OpenWakeWord ──
    oww_model = create_wake_word_model()

    # ── GPIO ──
    setup_gpio()

    if not oww_available and not gpio_available:
        log.error("No wake word model AND no GPIO — no trigger method available, exiting")
        return

    # ── PyAudio mic stream ──
    import pyaudio
    pa = pyaudio.PyAudio()
    stream = None

    while running and stream is None:
        try:
            stream = pa.open(
                format=pyaudio.paInt16,
                channels=CHANNELS,
                rate=SAMPLE_RATE,
                input=True,
                frames_per_buffer=CHUNK_SIZE,
            )
            log.info("Microphone stream opened")
        except Exception as e:
            log.error(f"No microphone detected — retrying in 30s: {e}")
            for _ in range(30):
                if not running:
                    break
                time.sleep(1)

    if not running or stream is None:
        pa.terminate()
        return

    # ── Calibrate silence threshold ──
    silence_threshold = calibrate_silence(stream)

    log.info("Voice input LIVE — listening for wake word / PTT")

    # ── Main listen loop ──
    try:
        while running:
            data = stream.read(CHUNK_SIZE, exception_on_overflow=False)

            triggered = False
            source = "wake_word"

            # Check PTT first (higher priority)
            if check_ptt():
                triggered = True
                source = "ptt"
                log.info("PTT button pressed — recording")

            # Check wake word
            if not triggered and oww_available:
                if check_wake_word(oww_model, data):
                    triggered = True
                    source = "wake_word"

            if triggered:
                record_and_transcribe(stream, recognizer, silence_threshold, source)

    except Exception as e:
        log.error(f"Main loop error: {e}")
    finally:
        log.info("Shutting down voice input...")
        if stream is not None:
            stream.stop_stream()
            stream.close()
        pa.terminate()
        if gpio_available:
            try:
                import RPi.GPIO as GPIO
                GPIO.cleanup()
            except Exception:
                pass
        if mqtt_client:
            mqtt_client.loop_stop()
            mqtt_client.disconnect()
        log.info("Voice input stopped")


if __name__ == '__main__':
    main()

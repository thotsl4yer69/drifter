#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Voice Alert System
Speaks diagnostic alerts through the Pi's audio output → Pioneer AUX.
Uses piper TTS (local, no cloud).
UNCAGED TECHNOLOGY — EST 1991
"""

import json
import time
import signal
import subprocess
import logging
import os
import threading
from pathlib import Path
import paho.mqtt.client as mqtt
from config import (MQTT_HOST, MQTT_PORT, TOPICS, VOICE_COOLDOWN, PIPER_MODEL, DRIFTER_DIR,
                   VEHICLE_YEAR, VEHICLE_MODEL, VEHICLE_ENGINE, make_mqtt_client)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [VOICE] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

# ── Config ──
PIPER_MODEL_PATH = DRIFTER_DIR / "piper-models" / f"{PIPER_MODEL}.onnx"
AUDIO_DIR = Path("/tmp/drifter-audio")

# speak() is called from the paho MQTT callback thread. Two critical alerts
# arriving together would otherwise race — both passing the cooldown, both
# spawning piper, and both writing /tmp/drifter-audio/alert.wav
# simultaneously. _speak_lock serialises the subprocess + MQTT publish + the
# read-modify-write of last_voice_time / last_spoken_msg.
_speak_lock = threading.Lock()

last_voice_time = 0
last_spoken_msg = ""
piper_available = False
_mqtt_client = None  # Set in main() — reused by speak() for audio bridge

# Resolve the correct piper TTS binary. The Debian package "piper" is a GTK
# gaming-device configurator, not a TTS engine — so we prefer piper from the
# venv (installed via "pip install piper-tts"), with a couple of common
# fallbacks before /usr/bin/piper.
def _resolve_piper_bin():
    import shutil as _shutil
    candidates = [
        '/opt/drifter/venv/bin/piper',
        str(Path(__file__).resolve().parent / 'venv' / 'bin' / 'piper'),
        '/usr/local/bin/piper',
    ]
    for c in candidates:
        if Path(c).is_file():
            return c
    found = _shutil.which('piper')
    return found if found else 'piper'

PIPER_BIN = _resolve_piper_bin()


def _pub_voice_status(state: str):
    """Publish TTS speaking state to HUD. Safe to call with _mqtt_client=None."""
    if _mqtt_client:
        try:
            _mqtt_client.publish(TOPICS['voice_status'], json.dumps({'state': state, 'ts': time.time()}))
        except Exception:
            pass


def check_piper():
    """Check if piper TTS is available."""
    global piper_available
    try:
        result = subprocess.run([PIPER_BIN, '--help'], capture_output=True, timeout=5)
        # Verify model file exists
        if not PIPER_MODEL_PATH.exists():
            log.warning(f"Piper model not found at {PIPER_MODEL_PATH}")
            log.warning("Run install.sh to download, or place model manually")
        piper_available = True
        log.info("Piper TTS is available")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        # Try with espeak as fallback
        try:
            subprocess.run(['espeak-ng', '--version'], capture_output=True, timeout=5)
            piper_available = False  # Will use espeak fallback
            log.info("Piper not found, using espeak-ng fallback")
        except FileNotFoundError:
            log.warning("No TTS engine found. Voice alerts disabled.")
            log.warning("Install with: sudo apt install piper espeak-ng")
            return False
    return True


def _has_audio_device():
    """Check if any ALSA playback device is available and usable."""
    try:
        result = subprocess.run(
            ['aplay', '-l'], capture_output=True, text=True, timeout=3
        )
        return 'card' in result.stdout.lower()
    except Exception:
        return False


has_local_audio = False  # Set in main after check


def _generate_wav(text, wav_path):
    """Render ``text`` into ``wav_path``. Returns True on success."""
    if piper_available:
        model_arg = str(PIPER_MODEL_PATH) if PIPER_MODEL_PATH.exists() else PIPER_MODEL
        process = subprocess.Popen(
            [PIPER_BIN, '--model', model_arg, '--output_file', str(wav_path)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        process.communicate(input=text.encode(), timeout=10)
        if process.returncode != 0:
            log.warning("Piper TTS failed with exit code %d", process.returncode)
        return wav_path.exists()
    # espeak-ng fallback — writes WAV directly.
    result = subprocess.run(
        ['espeak-ng', '-v', 'en-gb', '-s', '150', '-p', '40',
         '-w', str(wav_path), text],
        timeout=10, capture_output=True,
    )
    return result.returncode == 0 and wav_path.exists()


def _play_local(wav_path):
    """Play ``wav_path`` via aplay. Returns True if playback completed cleanly."""
    if not has_local_audio:
        return False
    result = subprocess.run(
        ['aplay', '-q', str(wav_path)],
        timeout=15, capture_output=True,
    )
    return result.returncode == 0


def _publish_wav(text, wav_path):
    """Publish WAV bytes to the MQTT audio topic for the dashboard bridge."""
    if not (wav_path.exists() and _mqtt_client is not None):
        return False
    try:
        import base64
        _mqtt_client.publish('drifter/audio/wav', json.dumps({
            'text': text[:200],
            'wav_b64': base64.b64encode(wav_path.read_bytes()).decode(),
            'ts': time.time(),
        }))
        return True
    except Exception as e:
        log.debug("WAV publish failed: %s", e)
        return False


def speak(text):
    """Speak text through audio output.

    Serialised via _speak_lock — two concurrent calls used to race on the
    piper subprocess and the /tmp/drifter-audio/alert.wav file. If a
    speak() is already in flight we DROP the new one (new alerts are more
    valuable than queuing stale ones).

    Tries local ALSA playback first (3.5mm/HDMI/USB) and always publishes
    the WAV to MQTT so the phone dashboard audio bridge can play it.
    """
    global last_voice_time, last_spoken_msg

    if not _speak_lock.acquire(blocking=False):
        log.debug("speak() already in progress — dropping: %s", text[:40])
        return

    try:
        now = time.time()
        # Cooldown and repeat checks must live inside the lock so two
        # threads can't both decide "cooldown elapsed" and both fire.
        if now - last_voice_time < VOICE_COOLDOWN:
            return
        if text == last_spoken_msg and now - last_voice_time < 60:
            return

        AUDIO_DIR.mkdir(exist_ok=True)
        wav_path = AUDIO_DIR / "alert.wav"
        spoke = False
        try:
            if _generate_wav(text, wav_path):
                _pub_voice_status('speaking')
                if _play_local(wav_path):
                    spoke = True
                if _publish_wav(text, wav_path):
                    spoke = True

            if spoke:
                last_voice_time = now
                last_spoken_msg = text
                log.info("Spoke: %s...", text[:60])
            else:
                log.warning("TTS generated but no output available for: %s...",
                            text[:40])
        except subprocess.TimeoutExpired:
            log.warning("TTS timeout")
        except Exception as e:
            log.error("TTS error: %s", e)
        finally:
            _pub_voice_status('idle')
    finally:
        _speak_lock.release()


def on_message(client, userdata, msg):
    """Handle alert messages, LLM responses, and voice commands."""
    try:
        data = json.loads(msg.payload)
        topic = msg.topic

        # Alert messages
        level = data.get('level', 0)
        message = data.get('message', '')

        if not message:
            return

        # RED (3) — Critical: immediate speech
        if level >= 3:
            speak("Critical alert. " + message)
        # AMBER (2) — Warning: standard speech
        elif level == 2:
            speak("Warning. " + message)
        # INFO (1) — Only speak warmup/X-Type status messages
        elif level == 1 and any(kw in message.lower() for kw in (
                'warmup complete', 'cold start', 'cold idle')):
            speak(message)

    except (json.JSONDecodeError, KeyError) as e:
        log.warning(f"Bad alert message: {e}")


def main():
    log.info("DRIFTER Voice Alert System starting...")

    running = True

    def _handle_signal(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    if not check_piper():
        log.warning("Continuing without voice — will retry on alert")

    global has_local_audio
    has_local_audio = _has_audio_device()
    if has_local_audio:
        log.info("Local audio device detected — voice will play through hardware")
    else:
        log.info("No local audio device — voice alerts routed to web dashboard")

    global _mqtt_client
    client = make_mqtt_client("drifter-voice")
    _mqtt_client = client  # Reused by speak() for audio bridge publishing
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

    client.subscribe(TOPICS['alert_message'])
    client.loop_start()

    # Startup announcement — X-Type specific
    time.sleep(2)
    speak(f"Drifter online. {VEHICLE_YEAR} {VEHICLE_MODEL} {VEHICLE_ENGINE}. "
          f"All diagnostic rules loaded. Monitoring your Jag.")

    log.info("Voice Alert System is LIVE")

    while running:
        time.sleep(1)

    client.loop_stop()
    client.disconnect()
    log.info("Voice system stopped")


if __name__ == '__main__':
    main()

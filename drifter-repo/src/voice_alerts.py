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
from pathlib import Path
import paho.mqtt.client as mqtt
from config import (MQTT_HOST, MQTT_PORT, TOPICS, VOICE_COOLDOWN, PIPER_MODEL, DRIFTER_DIR,
                   VEHICLE_YEAR, VEHICLE_MODEL, VEHICLE_ENGINE)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [VOICE] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

# ── Config ──
PIPER_MODEL_PATH = DRIFTER_DIR / "piper-models" / f"{PIPER_MODEL}.onnx"
AUDIO_DIR = Path("/tmp/drifter-audio")

last_voice_time = 0
last_spoken_msg = ""
piper_available = False


def check_piper():
    """Check if piper TTS is available."""
    global piper_available
    try:
        result = subprocess.run(['piper', '--help'], capture_output=True, timeout=5)
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


def speak(text):
    """Speak text through audio output."""
    global last_voice_time, last_spoken_msg

    now = time.time()

    # Cooldown check
    if now - last_voice_time < VOICE_COOLDOWN:
        return

    # Don't repeat the same message
    if text == last_spoken_msg and now - last_voice_time < 60:
        return

    AUDIO_DIR.mkdir(exist_ok=True)
    wav_path = AUDIO_DIR / "alert.wav"

    try:
        if piper_available:
            # Piper TTS — use full model path if file exists, else just name
            model_arg = str(PIPER_MODEL_PATH) if PIPER_MODEL_PATH.exists() else PIPER_MODEL
            process = subprocess.Popen(
                ['piper', '--model', model_arg, '--output_file', str(wav_path)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            process.communicate(input=text.encode(), timeout=10)

            if wav_path.exists():
                subprocess.run(
                    ['aplay', '-q', str(wav_path)],
                    timeout=15,
                    capture_output=True
                )
        else:
            # espeak-ng fallback
            subprocess.run(
                ['espeak-ng', '-v', 'en-gb', '-s', '150', '-p', '40', text],
                timeout=10,
                capture_output=True
            )

        last_voice_time = now
        last_spoken_msg = text
        log.info(f"Spoke: {text[:60]}...")

    except subprocess.TimeoutExpired:
        log.warning("TTS timeout")
    except Exception as e:
        log.error(f"TTS error: {e}")


def on_message(client, userdata, msg):
    """Handle alert messages with X-Type aware speech."""
    try:
        data = json.loads(msg.payload)
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

    client = mqtt.Client(client_id="drifter-voice")
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

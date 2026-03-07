#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Voice Alert System
Speaks diagnostic alerts through the Pi's audio output → Pioneer AUX.
Uses piper TTS (local, no cloud).
UNCAGED TECHNOLOGY — EST 1991
"""

import json
import time
import subprocess
import logging
import os
from pathlib import Path
import paho.mqtt.client as mqtt

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [VOICE] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

# ── Config ──
VOICE_COOLDOWN = 15          # Min seconds between voice alerts
PIPER_MODEL = "en_GB-alan-medium"  # British English, fits the Jag
AUDIO_DIR = Path("/tmp/drifter-audio")

last_voice_time = 0
last_spoken_msg = ""
piper_available = False


def check_piper():
    """Check if piper TTS is available."""
    global piper_available
    try:
        result = subprocess.run(['piper', '--help'], capture_output=True, timeout=5)
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
            # Piper TTS
            process = subprocess.Popen(
                ['piper', '--model', PIPER_MODEL, '--output_file', str(wav_path)],
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
    """Handle alert messages."""
    try:
        data = json.loads(msg.payload)
        level = data.get('level', 0)
        message = data.get('message', '')

        # Only speak AMBER and RED alerts
        if level >= 2 and message:
            # Prefix with severity
            prefix = "Warning. " if level == 2 else "Critical alert. "
            speak(prefix + message)

    except (json.JSONDecodeError, KeyError) as e:
        log.warning(f"Bad alert message: {e}")


def main():
    log.info("DRIFTER Voice Alert System starting...")

    if not check_piper():
        log.warning("Continuing without voice — will retry on alert")

    client = mqtt.Client(client_id="drifter-voice")
    client.on_message = on_message

    connected = False
    while not connected:
        try:
            client.connect("localhost", 1883, 60)
            connected = True
        except Exception as e:
            log.warning(f"Waiting for MQTT broker... ({e})")
            time.sleep(3)

    client.subscribe("drifter/alert/message")
    client.loop_start()

    # Startup announcement
    time.sleep(2)
    speak("Drifter online. Monitoring Jaguar vitals.")

    log.info("Voice Alert System is LIVE")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass

    client.loop_stop()
    client.disconnect()
    log.info("Voice system stopped")


if __name__ == '__main__':
    main()

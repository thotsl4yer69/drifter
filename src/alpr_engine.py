#!/usr/bin/env python3
"""
MZ1312 DRIFTER — ALPR Engine
Lightweight automatic licence plate recognition. Subscribes to vision
object detections from vision_engine and runs OCR on candidate vehicle
crops, publishing plate hits with confidence. Falls back to OpenALPR
CLI when fast-plate / EasyOCR is unavailable.
UNCAGED TECHNOLOGY — EST 1991
"""

import json
import logging
import signal
import threading
import time
from collections import deque
from pathlib import Path
from typing import List, Optional

import paho.mqtt.client as mqtt

from config import (
    MQTT_HOST, MQTT_PORT, TOPICS,
    DRIFTER_DIR, ALPR_MIN_CONFIDENCE,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [ALPR] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

CONFIG_PATH = DRIFTER_DIR / "alpr.yaml"
_seen: deque = deque(maxlen=200)
_seen_lock = threading.Lock()


def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        import yaml
        return yaml.safe_load(CONFIG_PATH.read_text()) or {}
    except Exception as e:
        log.warning(f"alpr.yaml load failed: {e}")
        return {}


def _read_plate_easyocr(image) -> List[dict]:
    try:
        import easyocr
    except ImportError:
        return []
    if not hasattr(_read_plate_easyocr, "_reader"):
        _read_plate_easyocr._reader = easyocr.Reader(['en'], gpu=False)  # type: ignore[attr-defined]
    reader = _read_plate_easyocr._reader  # type: ignore[attr-defined]
    try:
        results = reader.readtext(image)
    except Exception as e:
        log.debug(f"easyocr: {e}")
        return []
    out = []
    for _, text, conf in results:
        text = ''.join(c for c in text.upper() if c.isalnum())
        if 4 <= len(text) <= 9 and conf >= ALPR_MIN_CONFIDENCE:
            out.append({'plate': text, 'confidence': float(conf)})
    return out


def _read_plate_openalpr(image_path: Path) -> List[dict]:
    import subprocess
    try:
        proc = subprocess.run(
            ['alpr', '-j', '-n', '3', str(image_path)],
            capture_output=True, text=True, timeout=5,
        )
        data = json.loads(proc.stdout or '{}')
    except FileNotFoundError:
        return []
    except Exception as e:
        log.debug(f"openalpr: {e}")
        return []
    out = []
    for r in data.get('results', []):
        plate = r.get('plate')
        conf = r.get('confidence', 0) / 100.0
        if plate and conf >= ALPR_MIN_CONFIDENCE:
            out.append({'plate': plate, 'confidence': conf})
    return out


def _record(plate: str) -> bool:
    """Return True if the plate is new in our short-term cache."""
    with _seen_lock:
        if plate in _seen:
            return False
        _seen.append(plate)
        return True


def _handle_object_payload(client: mqtt.Client, payload: dict) -> None:
    if not isinstance(payload, dict):
        return
    objects = payload.get('objects') or []
    # When we don't have raw frame access from MQTT we can only honour
    # objects that include a base64 crop. This service expects vision_engine
    # to emit `image_b64` for vehicle detections it wants ALPR on.
    for obj in objects:
        if obj.get('class') not in ('car', 'truck', 'bus', 'motorcycle'):
            continue
        b64 = obj.get('image_b64')
        if not b64:
            continue
        try:
            import base64
            data = base64.b64decode(b64)
        except Exception:
            continue
        try:
            import numpy as np
            import cv2
            arr = np.frombuffer(data, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        except Exception:
            img = None
        if img is None:
            continue
        plates = _read_plate_easyocr(img)
        if not plates:
            # Fallback to OpenALPR via temp file
            try:
                tmp = Path('/tmp/drifter-alpr.jpg')
                cv2.imwrite(str(tmp), img)
                plates = _read_plate_openalpr(tmp)
            except Exception:
                plates = []
        for p in plates:
            if _record(p['plate']):
                client.publish(TOPICS['alpr_plate'], json.dumps({
                    'plate': p['plate'],
                    'confidence': p['confidence'],
                    'context': obj.get('class'),
                    'ts': time.time(),
                }))
                log.info(f"PLATE: {p['plate']} ({p['confidence']:.2f})")


def main() -> None:
    log.info("DRIFTER ALPR Engine starting...")
    _load_config()

    running = True

    def _handle_signal(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    client = mqtt.Client(client_id="drifter-alpr")

    def on_message(_c, _u, msg) -> None:
        try:
            data = json.loads(msg.payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        if msg.topic == TOPICS['vision_object']:
            _handle_object_payload(client, data)

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

    client.subscribe(TOPICS['vision_object'], 0)
    client.loop_start()
    log.info("ALPR Engine LIVE")

    while running:
        time.sleep(1)

    client.loop_stop()
    client.disconnect()
    log.info("ALPR Engine stopped")


if __name__ == '__main__':
    main()

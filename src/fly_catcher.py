#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Fly Catcher ghost ADS-B detector

Loads the Fly Catcher model (Angelina Tsuboi —
github.com/AngelinaTsuboi/Fly_Catcher) at startup, subscribes to the
enriched aircraft stream (drifter/airspace/aircraft from Agent A; falls
back to drifter/feeds/aircraft/snapshot if airspace isn't live yet),
classifies each aircraft, and republishes onto
drifter/airspace/aircraft_classified with the classification fields
mixed in.

Suspicious tracks (suspect=True or genuine_prob<0.3) raise a level-2
alert on drifter/alert/message.

The classifier is graceful: if the model file isn't present on disk
the service publishes everything with classification.unavailable=True
so the cockpit still functions and the operator can drop a model in
later without restarting.

UNCAGED TECHNOLOGY — EST 1991
"""

from __future__ import annotations

import json
import logging
import math
import os
import signal
import time
from pathlib import Path
from typing import Any

from config import MQTT_HOST, MQTT_PORT, TOPICS, make_mqtt_client

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [FLY-CATCHER] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger(__name__)

MODEL_DIR = Path(os.environ.get(
    'FLY_CATCHER_DIR', '/opt/drifter/state/fly_catcher'))
SUSPECT_PROB_THRESHOLD = float(os.environ.get(
    'FLY_CATCHER_SUSPECT_PROB', '0.3'))

TOPIC_IN_PRIMARY = 'drifter/airspace/aircraft'
TOPIC_IN_FALLBACK = 'drifter/feeds/aircraft/snapshot'
TOPIC_OUT = 'drifter/airspace/aircraft_classified'

running = True


def _find_model_file(model_dir: Path) -> Path | None:
    """Locate the Fly Catcher model file. .tflite first because that's the
    Pi-deployable form (loaded via ai-edge-litert, no full TF runtime).
    Other formats kept for completeness — operator may stage a raw h5 here
    before running the convert step."""
    if not model_dir.exists():
        return None
    for ext in ('*.tflite', '*.pkl', '*.joblib', '*.pt', '*.h5'):
        hits = sorted(model_dir.rglob(ext))
        if hits:
            return hits[0]
    return None


class FlyCatcher:
    """Wraps the Fly Catcher model with a defensive .classify() API.

    The model file isn't shipped — clone it into FLY_CATCHER_DIR at
    deploy time. Until then, classify() returns a neutral verdict
    flagged as unavailable, so subscribers see structured data either
    way and we never invent a probability.
    """

    def __init__(self, model_dir: Path = MODEL_DIR) -> None:
        self.model_dir = model_dir
        self.model: Any = None
        self.model_path: Path | None = None
        self.available = False
        # tflite-specific state — used when path.suffix == '.tflite'
        self._tflite_input_idx: int | None = None
        self._tflite_output_idx: int | None = None
        self._tflite_input_shape: tuple | None = None
        self._load()

    def _load(self) -> None:
        path = _find_model_file(self.model_dir)
        if not path:
            log.warning("no Fly Catcher model in %s — classifier disabled, "
                        "rows will pass through unannotated", self.model_dir)
            return
        try:
            if path.suffix == '.tflite':
                # LiteRT (the successor to standalone tflite-runtime) is
                # the only ML dep we want resident on the Pi. The .h5 →
                # .tflite conversion runs once via tools/convert_fly_catcher_to_tflite.py
                from ai_edge_litert.interpreter import Interpreter  # type: ignore
                self.model = Interpreter(model_path=str(path))
                self.model.allocate_tensors()
                input_details = self.model.get_input_details()[0]
                output_details = self.model.get_output_details()[0]
                self._tflite_input_idx = input_details['index']
                self._tflite_output_idx = output_details['index']
                self._tflite_input_shape = tuple(input_details['shape'])
            elif path.suffix in ('.pkl', '.joblib'):
                import joblib  # type: ignore
                self.model = joblib.load(path)
            elif path.suffix == '.pt':
                import torch  # type: ignore
                self.model = torch.load(path, map_location='cpu')
                if hasattr(self.model, 'eval'):
                    self.model.eval()
            elif path.suffix == '.h5':
                # Direct .h5 load needs full TensorFlow, which we
                # deliberately don't ship. Prefer the .tflite path —
                # convert once, drop the .tflite next to the .h5.
                from tensorflow import keras  # type: ignore
                self.model = keras.models.load_model(str(path))
            self.model_path = path
            self.available = True
            log.info("Fly Catcher model loaded from %s", path)
        except Exception as e:
            log.error("failed to load Fly Catcher model %s: %s", path, e)
            self.model = None
            self.available = False

    @staticmethod
    def featurize(aircraft: dict) -> list[float]:
        """Pull the exact 10-feature vector Fly Catcher's CNN was trained on.

        Order locked in by the upstream training notebook
        (CNN_Spoofing_Detector.ipynb preprocess_data):
            alt_baro, gs, track, baro_rate, lat, lon,
            seen_pos, messages, seen, rssi

        alt_baro == "ground" is mapped to 0.0 (matches handle_alt_baro
        upstream). Fields missing from the aircraft record default to 0
        the same way the training code does.
        """
        def num(*keys, default=0.0) -> float:
            for k in keys:
                v = aircraft.get(k)
                if isinstance(v, str) and v.lower() == 'ground':
                    return 0.0
                if isinstance(v, (int, float)) and not math.isnan(float(v)):
                    return float(v)
            return float(default)
        return [
            num('alt_baro', 'alt', 'altitude'),
            num('gs', 'speed', 'ground_speed'),
            num('track', 'true_track'),
            num('baro_rate', 'vert_rate'),
            num('lat'),
            num('lon'),
            num('seen_pos'),
            num('messages'),
            num('seen'),
            num('rssi', 'signal'),
        ]

    def classify(self, aircraft: dict) -> dict:
        if not self.available or self.model is None:
            return {
                'genuine_prob': None,
                'suspect': False,
                'unavailable': True,
            }
        features = self.featurize(aircraft)
        try:
            if self._tflite_input_idx is not None:
                # LiteRT path. Reshape the feature vector to whatever
                # input shape the model expects. The convert step locks
                # this shape in at .tflite-creation time.
                import numpy as np  # type: ignore
                arr = np.asarray(features, dtype=np.float32)
                shape = self._tflite_input_shape or (1, len(features))
                # Pad / truncate to fit the expected element count.
                target_n = 1
                for d in shape:
                    target_n *= max(int(d), 1)
                if arr.size < target_n:
                    arr = np.concatenate(
                        [arr, np.zeros(target_n - arr.size, dtype=np.float32)])
                elif arr.size > target_n:
                    arr = arr[:target_n]
                arr = arr.reshape(shape)
                self.model.set_tensor(self._tflite_input_idx, arr)
                self.model.invoke()
                out = self.model.get_tensor(self._tflite_output_idx)
                # Fly Catcher upstream is a single-sigmoid binary classifier
                # trained with label=1 if aircraft['is_spoofed'] else 0. The
                # raw output is therefore P(spoofed); flip to genuine_prob
                # for the rest of the pipeline. softmax variant kept for
                # future-proofing in case the model architecture changes.
                flat = out.flatten()
                if flat.size == 1:
                    spoofed = float(flat[0])
                    genuine = 1.0 - spoofed
                else:
                    # softmax: first class assumed genuine, last assumed spoofed
                    genuine = float(flat[0])
                genuine = max(0.0, min(1.0, genuine))
            elif hasattr(self.model, 'predict_proba'):
                proba = self.model.predict_proba([features])[0]
                # binary classifier: assume class 1 == genuine
                genuine = float(proba[-1])
            elif hasattr(self.model, 'predict'):
                pred = self.model.predict([features])
                genuine = float(pred[0])
                # Clamp to [0,1] if the model returned a raw score.
                genuine = max(0.0, min(1.0, genuine))
            else:
                return {'genuine_prob': None, 'suspect': False,
                        'unavailable': True}
        except Exception as e:
            log.debug("classify failed: %s", e)
            return {'genuine_prob': None, 'suspect': False,
                    'unavailable': True}
        suspect = genuine < SUSPECT_PROB_THRESHOLD
        return {
            'genuine_prob': round(genuine, 4),
            'suspect': bool(suspect),
            'unavailable': False,
        }


def _aircraft_iter(payload: Any):
    """Normalise the incoming payload into a list of aircraft dicts."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for k in ('aircraft', 'tracks', 'planes'):
            v = payload.get(k)
            if isinstance(v, list):
                return v
    return []


def build_alert_message(classified: list[dict]) -> str | None:
    """Build a level-2 alert line for suspicious tracks, or None."""
    ghosts = [a for a in classified
              if a.get('suspect') and not a.get('unavailable')]
    if not ghosts:
        return None
    a = ghosts[0]
    callsign = (a.get('callsign') or a.get('flight') or '?').strip() or '?'
    icao = (a.get('hex') or a.get('icao') or '?').strip() or '?'
    prob = a.get('genuine_prob')
    prob_s = f'{prob:.2f}' if isinstance(prob, (int, float)) else '?'
    return (f"Possible ghost ADS-B: callsign={callsign} "
            f"icao={icao} prob={prob_s}")


def handle_payload(payload: Any, catcher: FlyCatcher) -> dict:
    """Classify every aircraft in the payload, return the enriched dict."""
    aircraft = _aircraft_iter(payload)
    classified: list[dict] = []
    for a in aircraft:
        if not isinstance(a, dict):
            continue
        verdict = catcher.classify(a)
        merged = {**a, 'classification': verdict,
                  'genuine_prob': verdict.get('genuine_prob'),
                  'suspect': verdict.get('suspect', False)}
        classified.append(merged)
    out = {
        'ts': time.time(),
        'count': len(classified),
        'ghosts': sum(1 for a in classified
                      if a.get('suspect') and not a.get('classification', {}).get('unavailable')),
        'model_available': catcher.available,
        'aircraft': classified,
    }
    return out


def main():
    global running

    def _handle_signal(sig, frame):
        global running
        running = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    catcher = FlyCatcher()

    mqtt_client = make_mqtt_client("drifter-fly-catcher")

    def on_message(client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode('utf-8'))
        except Exception as e:
            log.debug("bad payload on %s: %s", msg.topic, e)
            return
        result = handle_payload(payload, catcher)
        mqtt_client.publish(TOPIC_OUT, json.dumps(result))
        alert = build_alert_message(result['aircraft'])
        if alert:
            mqtt_client.publish(TOPICS['alert_message'], json.dumps({
                'level': 2,
                'message': alert,
                'source': 'fly-catcher',
                'ts': time.time(),
            }))
            log.info(alert)

    mqtt_client.on_message = on_message

    while running:
        try:
            mqtt_client.connect(MQTT_HOST, MQTT_PORT, 60)
            break
        except Exception as e:
            log.warning("Waiting for MQTT broker... (%s)", e)
            time.sleep(3)

    mqtt_client.subscribe(TOPIC_IN_PRIMARY)
    mqtt_client.subscribe(TOPIC_IN_FALLBACK)
    log.info("Fly Catcher LIVE (model_available=%s)", catcher.available)

    mqtt_client.loop_start()
    while running:
        time.sleep(1)
    mqtt_client.loop_stop()
    mqtt_client.disconnect()
    log.info("Fly Catcher stopped")


if __name__ == '__main__':
    main()

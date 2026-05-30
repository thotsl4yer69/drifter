#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Vision Engine (Hailo Pi5)
YOLO object detection on the Hailo-8/8L accelerator. Falls back to a CPU
ONNX runner when Hailo runtime is missing so the same service still publishes
useful object events for testing. Designed to run on a separate Pi5 host
that mqtt-bridges into the main DRIFTER broker.
UNCAGED TECHNOLOGY — EST 1991
"""

import json
import logging
import os
import signal
import threading
import time
from pathlib import Path
from typing import Iterable, List, Optional

import paho.mqtt.client as mqtt

from config import (
    MQTT_HOST, MQTT_PORT, TOPICS,
    DRIFTER_DIR, VISION_MODEL_DIR, VISION_YOLO_MODEL,
    VISION_INPUT_W, VISION_INPUT_H, VISION_CONFIDENCE,
    VISION_CLASSES_OF_INTEREST,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [VISION] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

CONFIG_PATH = DRIFTER_DIR / "vision.yaml"
COCO_LABELS = (
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train",
    "truck", "boat", "traffic light", "fire hydrant", "stop sign",
)


def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        import yaml
        return yaml.safe_load(CONFIG_PATH.read_text()) or {}
    except Exception as e:
        log.warning(f"vision.yaml load failed: {e}")
        return {}


class HailoYolo:
    """Optional Hailo runner — only used when the runtime is available."""

    def __init__(self, model_path: Path) -> None:
        from hailo_platform import (  # type: ignore[import]
            VDevice, HEF, InputVStreamParams, OutputVStreamParams,
        )
        self.hef = HEF(str(model_path))
        self.vdevice = VDevice()
        configure_params = self.hef.create_configure_params_from_hef(self.hef)
        self.network_group = self.vdevice.configure(self.hef, configure_params)[0]
        self.input_params = InputVStreamParams.make_from_network_group(self.network_group)
        self.output_params = OutputVStreamParams.make_from_network_group(self.network_group)

    def infer(self, frame_bgr) -> list:
        # Real Hailo inference left as integration hook; return empty list.
        return []


class OnnxYolo:
    """CPU fallback using onnxruntime — used when Hailo is unavailable."""

    def __init__(self, model_path: Path) -> None:
        try:
            import onnxruntime as ort
            self.session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
            self.input_name = self.session.get_inputs()[0].name
            log.info("ONNX fallback active")
        except Exception as e:
            log.warning(f"ONNX session failed: {e}")
            self.session = None

    def infer(self, frame_bgr) -> list:
        if self.session is None:
            return []
        # Caller resizes/normalises frame; returns raw detections (xyxy, conf, cls)
        try:
            outputs = self.session.run(None, {self.input_name: frame_bgr})
            return [outputs]
        except Exception:
            return []


def _capture_loop(client: mqtt.Client, running_ref: list, detector) -> None:
    try:
        import cv2
    except ImportError:
        log.warning("opencv-python not installed — vision capture disabled")
        return

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        log.warning("camera open failed — vision capture disabled")
        return
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, VISION_INPUT_W)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, VISION_INPUT_H)
    log.info(f"Camera active {VISION_INPUT_W}x{VISION_INPUT_H}")

    while running_ref[0]:
        ok, frame = cap.read()
        if not ok:
            time.sleep(0.1)
            continue
        detections = []
        if detector is not None:
            try:
                detections = detector.infer(frame)
            except Exception as e:
                log.debug(f"infer: {e}")
        # Convert to JSON-friendly objects and filter by class
        objects = []
        for det in detections or []:
            if not isinstance(det, dict):
                continue
            cls = det.get('class')
            conf = det.get('confidence', 0)
            if conf < VISION_CONFIDENCE:
                continue
            if cls and cls not in VISION_CLASSES_OF_INTEREST:
                continue
            objects.append(det)
        if objects:
            client.publish(TOPICS['vision_object'], json.dumps({
                'objects': objects,
                'count': len(objects),
                'ts': time.time(),
            }))
        time.sleep(0.05)

    try:
        cap.release()
    except Exception:
        pass


def _select_detector():
    model = VISION_MODEL_DIR / VISION_YOLO_MODEL
    if model.exists():
        try:
            return HailoYolo(model)
        except Exception as e:
            log.warning(f"Hailo unavailable ({e}) — trying ONNX fallback")
    onnx_model = VISION_MODEL_DIR / "yolov8s.onnx"
    if onnx_model.exists():
        return OnnxYolo(onnx_model)
    log.warning("No vision model found — publishing status only")
    return None


def main() -> None:
    log.info("DRIFTER Vision Engine starting...")
    _load_config()
    detector = _select_detector()

    running = [True]

    def _handle_signal(sig, frame):
        running[0] = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    client = mqtt.Client(client_id="drifter-vision")
    connected = False
    while not connected and running[0]:
        try:
            client.connect(MQTT_HOST, MQTT_PORT, 60)
            connected = True
        except Exception as e:
            log.warning(f"Waiting for MQTT broker... ({e})")
            time.sleep(3)

    if not running[0]:
        return

    client.loop_start()
    client.publish(TOPICS['vision_status'], json.dumps({
        'state': 'online' if detector else 'idle',
        'classes': list(VISION_CLASSES_OF_INTEREST),
        'ts': time.time(),
    }), retain=True)
    log.info("Vision Engine LIVE" if detector else "Vision Engine idle (no model)")

    cap_thread = threading.Thread(
        target=_capture_loop, args=(client, running, detector), daemon=True,
    )
    cap_thread.start()

    while running[0]:
        time.sleep(1)

    client.publish(TOPICS['vision_status'], json.dumps({
        'state': 'offline', 'ts': time.time(),
    }), retain=True)
    client.loop_stop()
    client.disconnect()
    log.info("Vision Engine stopped")


if __name__ == '__main__':
    main()

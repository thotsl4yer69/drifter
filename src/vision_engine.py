#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Vision Engine

Runs optional object detection for the DRIFTER perception pipeline. The CPU
ONNX path performs real YOLOv8 preprocessing, output decoding and class-wise
NMS. Direct Hailo HEF execution remains fail-closed until an exact runtime/model
output contract is validated on the physical Pi/Hailo stack; the supported
Raspberry Pi rpicam Hailo pipeline is treated as a separate hardware path.

Vision is optional and never blocks the OBD/telemetry spine.
UNCAGED TECHNOLOGY — EST 1991
"""

from __future__ import annotations

import json
import logging
import math
import signal
import threading
import time
from pathlib import Path

import numpy as np
import paho.mqtt.client as mqtt

from config import (
    DRIFTER_DIR,
    MQTT_HOST,
    MQTT_PORT,
    TOPICS,
    VISION_CLASSES_OF_INTEREST,
    VISION_CONFIDENCE,
    VISION_INPUT_H,
    VISION_INPUT_W,
    VISION_MODEL_DIR,
    VISION_YOLO_MODEL,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [VISION] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

CONFIG_PATH = DRIFTER_DIR / "vision.yaml"
COCO_LABELS = (
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train",
    "truck", "boat", "traffic light", "fire hydrant", "stop sign", "parking meter",
    "bench", "bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear",
    "zebra", "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase",
    "frisbee", "skis", "snowboard", "sports ball", "kite", "baseball bat",
    "baseball glove", "skateboard", "surfboard", "tennis racket", "bottle",
    "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple",
    "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza", "donut",
    "cake", "chair", "couch", "potted plant", "bed", "dining table", "toilet",
    "tv", "laptop", "mouse", "remote", "keyboard", "cell phone", "microwave",
    "oven", "toaster", "sink", "refrigerator", "book", "clock", "vase",
    "scissors", "teddy bear", "hair drier", "toothbrush",
)
NMS_IOU = 0.45


def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        import yaml
        return yaml.safe_load(CONFIG_PATH.read_text()) or {}
    except Exception as exc:
        log.warning("vision.yaml load failed: %s", exc)
        return {}


def _iou_xyxy(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw = max(0.0, ix2 - ix1)
    ih = max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    denom = area_a + area_b - inter
    return inter / denom if denom > 0 else 0.0


def _decode_yolo_output(
    raw,
    *,
    frame_w: int,
    frame_h: int,
    input_w: int = VISION_INPUT_W,
    input_h: int = VISION_INPUT_H,
    confidence: float = VISION_CONFIDENCE,
    iou_threshold: float = NMS_IOU,
) -> list[dict]:
    """Decode common exported YOLOv8/v5 ONNX outputs into DRIFTER detections.

    Supported tensor forms:
      - YOLOv8: [1, 84, N] or [1, N, 84] => xywh + class scores
      - YOLOv5-style: [1, N, 85] => xywh + objectness + class scores

    Coordinates may be input-pixel or normalised xywh. Results use original
    frame pixel coordinates and class-wise NMS.
    """
    try:
        arr = np.asarray(raw, dtype=np.float32)
    except (TypeError, ValueError):
        return []

    arr = np.squeeze(arr)
    if arr.ndim != 2 or 0 in arr.shape:
        return []

    # Exported YOLOv8 is commonly [84, 8400]; convert to rows=detections.
    if arr.shape[0] <= 128 and arr.shape[1] > arr.shape[0]:
        arr = arr.T
    if arr.ndim != 2 or arr.shape[1] < 6:
        return []

    cols = arr.shape[1]
    # COCO exports are normally 84 (v8) or 85 (v5). For other class counts,
    # use v5 semantics only when the extra objectness column is unambiguous.
    v5_style = cols == len(COCO_LABELS) + 5
    class_start = 5 if v5_style else 4
    if cols <= class_start:
        return []

    sx = float(frame_w) / max(float(input_w), 1.0)
    sy = float(frame_h) / max(float(input_h), 1.0)
    candidates: list[dict] = []

    for row in arr:
        if not np.all(np.isfinite(row[:4])):
            continue
        class_scores = row[class_start:]
        if class_scores.size == 0 or not np.all(np.isfinite(class_scores)):
            continue
        class_id = int(np.argmax(class_scores))
        class_score = float(class_scores[class_id])
        if v5_style:
            objectness = float(row[4])
            if not math.isfinite(objectness):
                continue
            conf = objectness * class_score
        else:
            conf = class_score
        if not math.isfinite(conf) or conf < confidence:
            continue

        cx, cy, width, height = map(float, row[:4])
        if width <= 0 or height <= 0:
            continue

        # Some exporters emit 0..1 coordinates rather than input pixels.
        if max(abs(cx), abs(cy), abs(width), abs(height)) <= 2.0:
            cx *= input_w
            width *= input_w
            cy *= input_h
            height *= input_h

        x1 = max(0.0, min(float(frame_w), (cx - width / 2.0) * sx))
        y1 = max(0.0, min(float(frame_h), (cy - height / 2.0) * sy))
        x2 = max(0.0, min(float(frame_w), (cx + width / 2.0) * sx))
        y2 = max(0.0, min(float(frame_h), (cy + height / 2.0) * sy))
        if x2 <= x1 or y2 <= y1:
            continue

        label = COCO_LABELS[class_id] if class_id < len(COCO_LABELS) else f"class_{class_id}"
        box = (x1, y1, x2, y2)
        candidates.append({
            "class": label,
            "class_id": class_id,
            "confidence": round(conf, 6),
            "bbox": {
                "x1": round(x1, 3),
                "y1": round(y1, 3),
                "x2": round(x2, 3),
                "y2": round(y2, 3),
                "width": round(x2 - x1, 3),
                "height": round(y2 - y1, 3),
                "cx": round((x1 + x2) / 2.0, 3),
                "cy": round((y1 + y2) / 2.0, 3),
            },
            "frame_width": int(frame_w),
            "frame_height": int(frame_h),
            "_box": box,
        })

    # NMS must be class-wise; overlapping car/person detections are not duplicates.
    kept: list[dict] = []
    for candidate in sorted(candidates, key=lambda item: item["confidence"], reverse=True):
        if any(
            existing["class_id"] == candidate["class_id"]
            and _iou_xyxy(existing["_box"], candidate["_box"]) > iou_threshold
            for existing in kept
        ):
            continue
        kept.append(candidate)

    for item in kept:
        item.pop("_box", None)
    return kept


class HailoYolo:
    """Fail-closed marker for direct HEF execution.

    DRIFTER's repository does not define a stable tensor/output contract for its
    HEF. Raspberry Pi's supported rpicam Hailo pipeline performs model-specific
    post-processing externally. Do not claim direct inference from this adapter
    until the physical Hailo/model version is pinned and validated.
    """

    def __init__(self, model_path: Path) -> None:
        raise RuntimeError(
            f"direct Hailo HEF inference is not wired for {model_path.name}; "
            "use the supported rpicam Hailo pipeline or the ONNX fallback"
        )

    def infer(self, frame_bgr) -> list:
        return []


class OnnxYolo:
    """CPU fallback for exported YOLOv8/v5-style ONNX models."""

    def __init__(self, model_path: Path) -> None:
        try:
            import onnxruntime as ort
            self.session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
            self.input_name = self.session.get_inputs()[0].name
            log.info("ONNX fallback active")
        except Exception as exc:
            log.warning("ONNX session failed: %s", exc)
            self.session = None
            self.input_name = None

    def infer(self, frame_bgr) -> list:
        if self.session is None or self.input_name is None:
            return []
        try:
            import cv2
        except ImportError:
            log.warning("opencv-python unavailable for ONNX preprocessing")
            return []
        if frame_bgr is None or not hasattr(frame_bgr, "shape") or len(frame_bgr.shape) < 2:
            return []

        frame_h, frame_w = int(frame_bgr.shape[0]), int(frame_bgr.shape[1])
        if frame_h <= 0 or frame_w <= 0:
            return []

        resized = cv2.resize(frame_bgr, (VISION_INPUT_W, VISION_INPUT_H), interpolation=cv2.INTER_LINEAR)
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
        blob = np.ascontiguousarray(rgb.transpose(2, 0, 1), dtype=np.float32) / 255.0
        blob = np.expand_dims(blob, axis=0)

        outputs = self.session.run(None, {self.input_name: blob})
        if not outputs:
            return []
        return _decode_yolo_output(
            outputs[0],
            frame_w=frame_w,
            frame_h=frame_h,
            input_w=VISION_INPUT_W,
            input_h=VISION_INPUT_H,
            confidence=VISION_CONFIDENCE,
        )


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
    log.info("Camera active %sx%s", VISION_INPUT_W, VISION_INPUT_H)

    while running_ref[0]:
        ok, frame = cap.read()
        if not ok:
            time.sleep(0.1)
            continue
        detections = []
        if detector is not None:
            try:
                detections = detector.infer(frame)
            except Exception as exc:
                log.debug("infer: %s", exc)
        objects = []
        for det in detections or []:
            if not isinstance(det, dict):
                continue
            cls = det.get("class")
            conf = det.get("confidence", 0)
            if conf < VISION_CONFIDENCE:
                continue
            if cls and cls not in VISION_CLASSES_OF_INTEREST:
                continue
            objects.append(det)
        if objects:
            client.publish(TOPICS["vision_object"], json.dumps({
                "objects": objects,
                "count": len(objects),
                "ts": time.time(),
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
        except Exception as exc:
            log.warning("Hailo direct path unavailable (%s) — trying ONNX fallback", exc)
    onnx_model = VISION_MODEL_DIR / "yolov8s.onnx"
    if onnx_model.exists():
        detector = OnnxYolo(onnx_model)
        if detector.session is not None:
            return detector
    log.warning("No usable vision model found — publishing idle status")
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
        except Exception as exc:
            log.warning("Waiting for MQTT broker... (%s)", exc)
            time.sleep(3)

    if not running[0]:
        return

    client.loop_start()
    client.publish(TOPICS["vision_status"], json.dumps({
        "state": "online" if detector else "idle",
        "backend": "onnx" if isinstance(detector, OnnxYolo) else None,
        "classes": list(VISION_CLASSES_OF_INTEREST),
        "ts": time.time(),
    }), retain=True)
    log.info("Vision Engine LIVE (%s)" % type(detector).__name__ if detector else "Vision Engine idle (no usable model)")

    cap_thread = threading.Thread(
        target=_capture_loop, args=(client, running, detector), daemon=True,
    )
    cap_thread.start()

    while running[0]:
        time.sleep(1)

    client.publish(TOPICS["vision_status"], json.dumps({
        "state": "offline", "ts": time.time(),
    }), retain=True)
    client.loop_stop()
    client.disconnect()
    log.info("Vision Engine stopped")


if __name__ == "__main__":
    main()

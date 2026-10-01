#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Vision Engine

Runs optional object detection for the DRIFTER perception pipeline. The CPU
ONNX path performs real YOLOv8 preprocessing, output decoding and class-wise
NMS. Direct Hailo HEF execution uses Raspberry Pi's Picamera2 Hailo wrapper so
model-specific post-processing stays in the supported Hailo stack. ONNX remains
the CPU fallback.

Vision is optional and never blocks the OBD/telemetry spine.
UNCAGED TECHNOLOGY — EST 1991
"""

from __future__ import annotations

import json
import logging
import math
import os
import signal
import sys
import threading
import time
from pathlib import Path

import numpy as np
import paho.mqtt.client as mqtt

from config import (
    DASHCAM_MAX_GB,
    DASHCAM_SEGMENT_SECONDS,
    DEFAULT_MODE,
    DRIFTER_DIR,
    MODE_STATE_PATH,
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

RECON_DIR = DRIFTER_DIR / "recon"
RECON_MEDIA_DIR = RECON_DIR / "media"
RECON_CROP_DIR = RECON_DIR / "crops"
RECON_VIDEO_DIR = RECON_DIR / "video"
RECON_EVIDENCE_INTERVAL_S = max(
    0.25, float(os.getenv("DRIFTER_RECON_EVIDENCE_INTERVAL_SEC", "2.0"))
)
RECON_JPEG_QUALITY = max(
    40, min(95, int(os.getenv("DRIFTER_RECON_JPEG_QUALITY", "82")))
)
RECON_CROP_QUALITY = max(
    40, min(90, int(os.getenv("DRIFTER_RECON_CROP_QUALITY", "72")))
)
RECON_RECORD_VIDEO = os.getenv("DRIFTER_RECON_RECORD_VIDEO", "1").strip().lower() not in {
    "0", "false", "no", "off",
}
RECON_VIDEO_FPS = max(1.0, float(os.getenv("DRIFTER_RECON_VIDEO_FPS", "15")))
RECON_STILL_MAX_GB = max(0.25, float(os.getenv("DRIFTER_RECON_STILL_MAX_GB", "4")))
CAMERA_ID = os.getenv("DRIFTER_CAMERA_ID", "front").strip() or "front"
VEHICLE_CLASSES = {"car", "truck", "bus", "motorcycle"}


def _current_mode() -> str:
    try:
        mode = Path(MODE_STATE_PATH).read_text(encoding="utf-8").strip()
    except OSError:
        return DEFAULT_MODE
    return mode or DEFAULT_MODE


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _prune_media(path: Path, pattern: str, max_bytes: int) -> None:
    """Bound a RECON media class without touching the active output handle."""
    try:
        files = sorted(path.rglob(pattern), key=_safe_mtime)
    except OSError:
        return
    sizes: list[tuple[Path, int]] = []
    used = 0
    for item in files:
        try:
            size = item.stat().st_size
        except OSError:
            continue
        sizes.append((item, size))
        used += size
    target = int(max_bytes * 0.90)
    for item, size in sizes:
        if used <= target:
            break
        try:
            item.unlink()
            used -= size
        except OSError:
            continue


class ReconVideoRecorder:
    """Segmented video writer fed from the already-open vision camera.

    This avoids the old dashcam/vision double-open race on /dev/video0.
    """

    def __init__(self, cv2_module) -> None:
        self.cv2 = cv2_module
        self.writer = None
        self.segment_started = 0.0
        self.path: Path | None = None
        self.retry_after = 0.0

    @property
    def active(self) -> bool:
        return self.writer is not None

    def _open(self, frame, now: float) -> bool:
        if now < self.retry_after:
            return False
        RECON_VIDEO_DIR.mkdir(parents=True, exist_ok=True)
        frame_h, frame_w = int(frame.shape[0]), int(frame.shape[1])
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
        self.path = RECON_VIDEO_DIR / f"{CAMERA_ID}_{stamp}.mp4"
        try:
            fourcc = self.cv2.VideoWriter_fourcc(*"mp4v")
            writer = self.cv2.VideoWriter(
                str(self.path), fourcc, RECON_VIDEO_FPS, (frame_w, frame_h)
            )
            if not writer.isOpened():
                writer.release()
                raise RuntimeError("VideoWriter did not open")
            self.writer = writer
            self.segment_started = now
            _prune_media(
                RECON_VIDEO_DIR,
                "*.mp4",
                int(DASHCAM_MAX_GB * 1024 ** 3),
            )
            return True
        except Exception as exc:
            log.warning("RECON video writer unavailable: %s", exc)
            self.writer = None
            self.retry_after = now + 30.0
            return False

    def write(self, frame, now: float) -> bool:
        if self.writer is not None and now - self.segment_started >= DASHCAM_SEGMENT_SECONDS:
            self.close()
        if self.writer is None and not self._open(frame, now):
            return False
        try:
            self.writer.write(frame)
            return True
        except Exception as exc:
            log.warning("RECON video write failed: %s", exc)
            self.close()
            self.retry_after = now + 30.0
            return False

    def close(self) -> None:
        if self.writer is not None:
            try:
                self.writer.release()
            except Exception:
                pass
        self.writer = None


def _bbox_crop(frame, detection: dict):
    bbox = detection.get("bbox") if isinstance(detection.get("bbox"), dict) else {}
    try:
        h, w = int(frame.shape[0]), int(frame.shape[1])
        x1 = max(0, min(w, int(float(bbox.get("x1", 0)))))
        y1 = max(0, min(h, int(float(bbox.get("y1", 0)))))
        x2 = max(0, min(w, int(float(bbox.get("x2", 0)))))
        y2 = max(0, min(h, int(float(bbox.get("y2", 0)))))
    except (TypeError, ValueError, AttributeError):
        return None
    if x2 <= x1 or y2 <= y1:
        return None
    crop = frame[y1:y2, x1:x2]
    return crop if getattr(crop, "size", 0) else None


def _save_recon_frame(cv2_module, frame, now: float) -> Path | None:
    day = time.strftime("%Y%m%d", time.localtime(now))
    folder = RECON_MEDIA_DIR / day
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
    millis = int((now % 1.0) * 1000)
    path = folder / f"{CAMERA_ID}_{stamp}-{millis:03d}.jpg"
    try:
        ok = cv2_module.imwrite(
            str(path),
            frame,
            [int(cv2_module.IMWRITE_JPEG_QUALITY), RECON_JPEG_QUALITY],
        )
    except Exception as exc:
        log.warning("RECON evidence write failed: %s", exc)
        return None
    return path if ok else None


def _publish_alpr_crops(
    client: mqtt.Client,
    cv2_module,
    frame,
    objects: list[dict],
    *,
    evidence_path: Path,
    now: float,
) -> None:
    """Persist bounded vehicle crops and publish file pointers for ALPR."""
    day = time.strftime("%Y%m%d", time.localtime(now))
    folder = RECON_CROP_DIR / day
    folder.mkdir(parents=True, exist_ok=True)
    emitted = 0
    for index, obj in enumerate(objects):
        if obj.get("class") not in VEHICLE_CLASSES:
            continue
        crop = _bbox_crop(frame, obj)
        if crop is None:
            continue
        # OCR does not need a huge source image; cap width to bound storage/CPU.
        try:
            if crop.shape[1] > 640:
                scale = 640.0 / float(crop.shape[1])
                crop = cv2_module.resize(
                    crop,
                    (640, max(1, int(crop.shape[0] * scale))),
                    interpolation=cv2_module.INTER_AREA,
                )
        except Exception:
            pass
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
        millis = int((now % 1.0) * 1000)
        crop_path = folder / f"{CAMERA_ID}_{stamp}-{millis:03d}_v{index}.jpg"
        try:
            ok = cv2_module.imwrite(
                str(crop_path),
                crop,
                [int(cv2_module.IMWRITE_JPEG_QUALITY), RECON_CROP_QUALITY],
            )
        except Exception:
            ok = False
        if not ok:
            continue
        client.publish(TOPICS["vision_alpr_crop"], json.dumps({
            "crop_path": str(crop_path),
            "evidence_path": str(evidence_path),
            "camera_id": CAMERA_ID,
            "class": obj.get("class"),
            "confidence": obj.get("confidence"),
            "bbox": obj.get("bbox"),
            "ts": now,
        }))
        emitted += 1
        if emitted >= 3:
            break


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

    if arr.ndim == 3 and arr.shape[0] == 1:
        arr = arr[0]
    elif arr.ndim != 2:
        return []
    if arr.ndim != 2 or 0 in arr.shape:
        return []

    # COCO YOLO exports use 84 features (v8: xywh + 80 classes) or
    # 85 features (v5-style: xywh + objectness + 80 classes). Prefer that
    # explicit contract over a size heuristic so small synthetic/edge batches
    # such as [2, 85] are not accidentally transposed.
    feature_dims = {len(COCO_LABELS) + 4, len(COCO_LABELS) + 5}
    if arr.shape[0] in feature_dims and arr.shape[1] not in feature_dims:
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


def _decode_hailo_output(
    raw,
    *,
    frame_w: int,
    frame_h: int,
    confidence: float = VISION_CONFIDENCE,
) -> list[dict]:
    """Decode Picamera2 Hailo.run() object-detection output.

    Raspberry Pi's Hailo helper returns one list per class. Each detection is
    [y0, x0, y1, x1, score] in normalised coordinates after model-specific
    post-processing/NMS.
    """
    if raw is None:
        return []
    results: list[dict] = []
    try:
        classes = list(raw)
    except TypeError:
        return []

    for class_id, detections in enumerate(classes):
        try:
            rows = list(detections)
        except TypeError:
            continue
        for detection in rows:
            try:
                values = np.asarray(detection, dtype=np.float32).reshape(-1)
            except (TypeError, ValueError):
                continue
            if values.size < 5 or not np.all(np.isfinite(values[:5])):
                continue
            y0, x0, y1, x1, score = map(float, values[:5])
            if score < confidence:
                continue
            x1_px = max(0.0, min(float(frame_w), x0 * frame_w))
            y1_px = max(0.0, min(float(frame_h), y0 * frame_h))
            x2_px = max(0.0, min(float(frame_w), x1 * frame_w))
            y2_px = max(0.0, min(float(frame_h), y1 * frame_h))
            if x2_px <= x1_px or y2_px <= y1_px:
                continue
            label = COCO_LABELS[class_id] if class_id < len(COCO_LABELS) else f"class_{class_id}"
            results.append({
                "class": label,
                "class_id": class_id,
                "confidence": round(score, 6),
                "bbox": {
                    "x1": round(x1_px, 3),
                    "y1": round(y1_px, 3),
                    "x2": round(x2_px, 3),
                    "y2": round(y2_px, 3),
                    "width": round(x2_px - x1_px, 3),
                    "height": round(y2_px - y1_px, 3),
                    "cx": round((x1_px + x2_px) / 2.0, 3),
                    "cy": round((y1_px + y2_px) / 2.0, 3),
                },
                "frame_width": int(frame_w),
                "frame_height": int(frame_h),
            })
    return results


def _camera_source():
    config = _load_config()
    raw = os.getenv("DRIFTER_DASHCAM_DEV") or str(config.get("camera_device") or "/dev/video0")
    raw = raw.strip()
    return int(raw) if raw.isdigit() else raw


def _ensure_system_dist_packages() -> None:
    # Picamera2/libcamera/Hailo are normally installed by apt, while DRIFTER's
    # application dependencies live in an isolated venv. Add the distro path
    # only when an optional camera/Hailo import needs it.
    system_dist = "/usr/lib/python3/dist-packages"
    if system_dist not in sys.path:
        sys.path.append(system_dist)


def _picamera_hailo_api():
    """Load distro Picamera2 Hailo helpers."""
    try:
        from picamera2.devices import Hailo, hailo_architecture  # type: ignore[import]
    except ImportError:
        _ensure_system_dist_packages()
        from picamera2.devices import Hailo, hailo_architecture  # type: ignore[import]
    return Hailo, hailo_architecture


def _picamera2_class():
    try:
        from picamera2 import Picamera2  # type: ignore[import]
    except ImportError:
        _ensure_system_dist_packages()
        from picamera2 import Picamera2  # type: ignore[import]
    return Picamera2


def _system_hailo_model() -> Path | None:
    """Return Raspberry Pi's packaged YOLOv8 HEF for the detected accelerator."""
    try:
        _Hailo, hailo_architecture = _picamera_hailo_api()
        arch = str(hailo_architecture() or "").upper()
    except Exception as exc:
        log.debug("Hailo architecture unavailable: %s", exc)
        return None
    names = {
        "HAILO10H": "yolov8m_h10.hef",
        "HAILO8L": "yolov8s_h8l.hef",
        "HAILO8": "yolov8s_h8.hef",
    }
    name = names.get(arch)
    if not name:
        return None
    path = Path("/usr/share/hailo-models") / name
    return path if path.exists() else None


class HailoYolo:
    """Hailo detector using Raspberry Pi's supported Picamera2 device wrapper."""

    def __init__(self, model_path: Path) -> None:
        Hailo, _hailo_architecture = _picamera_hailo_api()
        self._context = Hailo(str(model_path))
        enter = getattr(self._context, "__enter__", None)
        self.hailo = enter() if callable(enter) else self._context
        model_h, model_w, _channels = self.hailo.get_input_shape()
        self.model_h = int(model_h)
        self.model_w = int(model_w)
        self._closed = False
        log.info("Hailo backend active: %s (%sx%s)", model_path.name, self.model_w, self.model_h)

    def infer(self, frame_bgr) -> list:
        if self._closed or frame_bgr is None or not hasattr(frame_bgr, "shape"):
            return []
        try:
            import cv2
        except ImportError:
            log.warning("opencv-python unavailable for Hailo preprocessing")
            return []
        if len(frame_bgr.shape) < 2:
            return []
        frame_h, frame_w = int(frame_bgr.shape[0]), int(frame_bgr.shape[1])
        if frame_h <= 0 or frame_w <= 0:
            return []

        resized = cv2.resize(frame_bgr, (self.model_w, self.model_h), interpolation=cv2.INTER_LINEAR)
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
        output = self.hailo.run(np.ascontiguousarray(rgb))
        return _decode_hailo_output(
            output,
            frame_w=frame_w,
            frame_h=frame_h,
            confidence=VISION_CONFIDENCE,
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        exit_fn = getattr(self._context, "__exit__", None)
        if callable(exit_fn):
            exit_fn(None, None, None)
            return
        close_fn = getattr(self.hailo, "close", None)
        if callable(close_fn):
            close_fn()


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

    source = _camera_source()
    cap = cv2.VideoCapture(source)
    picam2 = None
    use_picamera2 = False

    if cap.isOpened():
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, VISION_INPUT_W)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, VISION_INPUT_H)
        log.info("Camera active via OpenCV source %r at %sx%s", source, VISION_INPUT_W, VISION_INPUT_H)
    else:
        try:
            cap.release()
        except Exception:
            pass
        try:
            Picamera2 = _picamera2_class()
            picam2 = Picamera2()
            camera_config = picam2.create_preview_configuration(
                main={"size": (VISION_INPUT_W, VISION_INPUT_H), "format": "RGB888"},
                controls={"FrameRate": 20},
            )
            picam2.configure(camera_config)
            picam2.start()
            use_picamera2 = True
            log.info("Camera active via Picamera2 at %sx%s", VISION_INPUT_W, VISION_INPUT_H)
        except Exception as exc:
            log.warning(
                "camera unavailable: OpenCV source %r failed and Picamera2 could not start (%s)",
                source,
                exc,
            )
            if picam2 is not None:
                try:
                    picam2.close()
                except Exception:
                    pass
            return

    recorder: ReconVideoRecorder | None = None
    mode = _current_mode()
    last_mode_check = 0.0
    last_evidence = 0.0
    last_prune = 0.0
    last_record_state: str | None = None

    def publish_record_state(state: str, now: float, path: Path | None = None) -> None:
        nonlocal last_record_state
        if state == last_record_state:
            return
        last_record_state = state
        client.publish(TOPICS["dashcam_status"], json.dumps({
            "state": state,
            "owner": "drifter-vision",
            "mode": mode,
            "camera_id": CAMERA_ID,
            "path": str(path) if path else None,
            "ts": now,
        }), retain=True)

    try:
        while running_ref[0]:
            now = time.time()
            if now - last_mode_check >= 0.5:
                next_mode = _current_mode()
                if next_mode != mode:
                    log.info("Vision persona %s -> %s", mode, next_mode)
                    mode = next_mode
                last_mode_check = now
            recon = mode == "recon"

            if use_picamera2:
                try:
                    frame_rgb = picam2.capture_array("main")
                    frame = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
                    ok = frame is not None
                except Exception as exc:
                    log.debug("Picamera2 capture: %s", exc)
                    ok, frame = False, None
            else:
                ok, frame = cap.read()

            if not ok:
                time.sleep(0.1)
                continue

            if recon and RECON_RECORD_VIDEO:
                if recorder is None:
                    recorder = ReconVideoRecorder(cv2)
                recording = recorder.write(frame, now)
                publish_record_state(
                    "recording" if recording else "monitoring",
                    now,
                    recorder.path if recorder else None,
                )
            else:
                if recorder is not None:
                    recorder.close()
                    recorder = None
                publish_record_state("ready" if mode == "drive" else "offline", now)

            detections = []
            if detector is not None:
                try:
                    detections = detector.infer(frame)
                except Exception as exc:
                    log.debug("infer: %s", exc)

            objects: list[dict] = []
            for det in detections or []:
                if not isinstance(det, dict):
                    continue
                cls = det.get("class")
                conf = det.get("confidence", 0)
                if conf < VISION_CONFIDENCE:
                    continue
                if cls and cls not in VISION_CLASSES_OF_INTEREST:
                    continue
                objects.append(dict(det))

            evidence_path: Path | None = None
            if recon and objects and now - last_evidence >= RECON_EVIDENCE_INTERVAL_S:
                evidence_path = _save_recon_frame(cv2, frame, now)
                if evidence_path is not None:
                    last_evidence = now
                    _publish_alpr_crops(
                        client,
                        cv2,
                        frame,
                        objects,
                        evidence_path=evidence_path,
                        now=now,
                    )
                    if now - last_prune >= 60.0:
                        max_still = int(RECON_STILL_MAX_GB * 1024 ** 3)
                        _prune_media(RECON_MEDIA_DIR, "*.jpg", max_still)
                        _prune_media(RECON_CROP_DIR, "*.jpg", max_still)
                        last_prune = now

            if objects:
                payload = {
                    "objects": objects,
                    "count": len(objects),
                    "camera_id": CAMERA_ID,
                    "mode": mode,
                    "ts": now,
                }
                if evidence_path is not None:
                    payload["evidence_path"] = str(evidence_path)
                client.publish(TOPICS["vision_object"], json.dumps(payload))

            time.sleep(0.05)
    finally:
        if recorder is not None:
            recorder.close()
        client.publish(TOPICS["dashcam_status"], json.dumps({
            "state": "offline",
            "owner": "drifter-vision",
            "camera_id": CAMERA_ID,
            "ts": time.time(),
        }), retain=True)
        if use_picamera2 and picam2 is not None:
            try:
                picam2.stop()
            except Exception:
                pass
            try:
                picam2.close()
            except Exception:
                pass
        else:
            try:
                cap.release()
            except Exception:
                pass


def _select_detector():
    configured = VISION_MODEL_DIR / VISION_YOLO_MODEL
    candidates: list[Path] = []
    if configured.exists():
        candidates.append(configured)
    packaged = _system_hailo_model()
    if packaged is not None and packaged not in candidates:
        candidates.append(packaged)

    for model in candidates:
        try:
            return HailoYolo(model)
        except Exception as exc:
            log.warning("Hailo backend unavailable for %s (%s)", model, exc)

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
        "backend": (
            "hailo" if isinstance(detector, HailoYolo)
            else "onnx" if isinstance(detector, OnnxYolo)
            else None
        ),
        "classes": list(VISION_CLASSES_OF_INTEREST),
        "camera_id": CAMERA_ID,
        "mode": _current_mode(),
        "ts": time.time(),
    }), retain=True)
    log.info("Vision Engine LIVE (%s)" % type(detector).__name__ if detector else "Vision Engine idle (no usable model)")

    cap_thread = threading.Thread(
        target=_capture_loop, args=(client, running, detector), daemon=True,
    )
    cap_thread.start()

    while running[0]:
        time.sleep(1)

    cap_thread.join(timeout=3)
    if detector is not None:
        close_fn = getattr(detector, "close", None)
        if callable(close_fn):
            try:
                close_fn()
            except Exception as exc:
                log.warning("detector close failed: %s", exc)
    client.publish(TOPICS["vision_status"], json.dumps({
        "state": "offline", "ts": time.time(),
    }), retain=True)
    client.loop_stop()
    client.disconnect()
    log.info("Vision Engine stopped")


if __name__ == "__main__":
    main()

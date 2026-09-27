"""Regression tests for DRIFTER ONNX vision decoding.

These are software-only tensor/preprocessing checks. They do not establish
camera, Hailo, Pi, road-scene, latency, or safety performance.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

import vision_engine as vision
from vision_engine import HailoYolo, OnnxYolo, _decode_yolo_output


def test_infer_returns_empty_when_no_session():
    obj = OnnxYolo.__new__(OnnxYolo)
    obj.session = None
    obj.input_name = None
    assert obj.infer(object()) == []


def test_yolov8_channel_first_output_decodes_and_nms_suppresses_duplicate():
    raw = np.zeros((1, 84, 100), dtype=np.float32)
    # Two strongly overlapping COCO class 2 = car detections.
    raw[0, 0:4, 0] = [320, 320, 200, 100]
    raw[0, 4 + 2, 0] = 0.90
    raw[0, 0:4, 1] = [325, 320, 200, 100]
    raw[0, 4 + 2, 1] = 0.80

    detections = _decode_yolo_output(raw, frame_w=640, frame_h=640)

    assert len(detections) == 1
    det = detections[0]
    assert det["class"] == "car"
    assert det["class_id"] == 2
    assert det["confidence"] == pytest.approx(0.9)
    assert det["bbox"]["width"] == pytest.approx(200.0)
    assert det["bbox"]["height"] == pytest.approx(100.0)
    assert det["frame_width"] == 640
    assert det["frame_height"] == 640


def test_yolov5_row_major_output_combines_objectness_and_class_score():
    raw = np.zeros((1, 2, 85), dtype=np.float32)
    raw[0, 0, 0:4] = [320, 320, 160, 80]
    raw[0, 0, 4] = 0.8
    raw[0, 0, 5 + 2] = 0.9  # car

    detections = _decode_yolo_output(raw, frame_w=1280, frame_h=720)

    assert len(detections) == 1
    det = detections[0]
    assert det["class"] == "car"
    assert det["confidence"] == pytest.approx(0.72)
    assert det["bbox"]["width"] == pytest.approx(320.0)
    assert det["bbox"]["height"] == pytest.approx(90.0)


def test_single_detection_channel_first_tensor_keeps_feature_axis():
    raw = np.zeros((1, 84, 1), dtype=np.float32)
    raw[0, 0:4, 0] = [320, 320, 100, 50]
    raw[0, 4 + 2, 0] = 0.88

    detections = _decode_yolo_output(raw, frame_w=640, frame_h=640)

    assert len(detections) == 1
    assert detections[0]["class"] == "car"
    assert detections[0]["confidence"] == pytest.approx(0.88)


def test_normalised_coordinates_are_scaled_to_original_frame():
    raw = np.zeros((1, 84, 10), dtype=np.float32)
    raw[0, 0:4, 0] = [0.5, 0.5, 0.25, 0.5]
    raw[0, 4, 0] = 0.95  # person

    detections = _decode_yolo_output(raw, frame_w=1280, frame_h=720)

    assert len(detections) == 1
    box = detections[0]["bbox"]
    assert box["cx"] == pytest.approx(640.0)
    assert box["cy"] == pytest.approx(360.0)
    assert box["width"] == pytest.approx(320.0)
    assert box["height"] == pytest.approx(360.0)


@pytest.mark.parametrize(
    "raw",
    [
        np.array([], dtype=np.float32),
        np.zeros((1, 5, 5), dtype=np.float32),
        np.full((1, 84, 10), np.nan, dtype=np.float32),
    ],
)
def test_malformed_or_nonfinite_outputs_do_not_create_detections(raw):
    assert _decode_yolo_output(raw, frame_w=640, frame_h=640) == []


def test_direct_hailo_adapter_fails_closed_instead_of_claiming_empty_inference():
    with pytest.raises(RuntimeError, match="direct Hailo HEF inference is not wired"):
        HailoYolo(Path("/tmp/yolov8s.hef"))


class _FakeSession:
    def __init__(self, output):
        self.output = output
        self.seen = None

    def run(self, outputs, feed):
        self.seen = (outputs, feed)
        return [self.output]


class _FakeCv2:
    INTER_LINEAR = 1
    COLOR_BGR2RGB = 2

    @staticmethod
    def resize(frame, size, interpolation):
        assert size == (vision.VISION_INPUT_W, vision.VISION_INPUT_H)
        assert interpolation == _FakeCv2.INTER_LINEAR
        # Deterministic 640x640 BGR fixture.
        return np.zeros((vision.VISION_INPUT_H, vision.VISION_INPUT_W, 3), dtype=np.uint8)

    @staticmethod
    def cvtColor(frame, code):
        assert code == _FakeCv2.COLOR_BGR2RGB
        return frame[..., ::-1]


def test_onnx_infer_runs_preprocessing_session_and_decoder(monkeypatch):
    raw = np.zeros((1, 84, 10), dtype=np.float32)
    raw[0, 0:4, 0] = [320, 320, 200, 100]
    raw[0, 4 + 2, 0] = 0.91

    obj = OnnxYolo.__new__(OnnxYolo)
    obj.session = _FakeSession(raw)
    obj.input_name = "images"

    monkeypatch.setitem(__import__("sys").modules, "cv2", _FakeCv2)
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    detections = obj.infer(frame)

    assert len(detections) == 1
    assert detections[0]["class"] == "car"
    feed = obj.session.seen[1]["images"]
    assert feed.shape == (1, 3, 640, 640)
    assert feed.dtype == np.float32

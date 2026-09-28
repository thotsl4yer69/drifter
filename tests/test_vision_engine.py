"""Regression tests for DRIFTER ONNX/Hailo vision decoding.

These are software-only tensor/preprocessing checks. They do not establish
camera, Hailo hardware, Pi, road-scene, latency, or safety performance.
"""
from __future__ import annotations

from pathlib import Path
import sys
import types

import numpy as np
import pytest

import vision_engine as vision
from vision_engine import HailoYolo, OnnxYolo, _decode_hailo_output, _decode_yolo_output


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


def test_hailo_postprocessed_output_maps_normalised_box_to_frame():
    raw = [[] for _ in range(80)]
    raw[2] = [np.array([0.25, 0.20, 0.75, 0.80, 0.92], dtype=np.float32)]

    detections = _decode_hailo_output(raw, frame_w=1000, frame_h=500)

    assert len(detections) == 1
    det = detections[0]
    assert det["class"] == "car"
    assert det["confidence"] == pytest.approx(0.92)
    assert det["bbox"]["x1"] == pytest.approx(200)
    assert det["bbox"]["y1"] == pytest.approx(125)
    assert det["bbox"]["width"] == pytest.approx(600)
    assert det["bbox"]["height"] == pytest.approx(250)


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
        assert interpolation == _FakeCv2.INTER_LINEAR
        return np.zeros((size[1], size[0], 3), dtype=np.uint8)

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



class _FakeHailoRuntime:
    instances = []

    def __init__(self, path):
        self.path = path
        self.closed = False
        self.last_frame = None
        self.__class__.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True

    def get_input_shape(self):
        return (320, 320, 3)

    def run(self, frame):
        self.last_frame = frame
        raw = [[] for _ in range(80)]
        raw[2] = [np.array([0.25, 0.25, 0.75, 0.75, 0.93], dtype=np.float32)]
        return raw


def test_hailo_backend_uses_picamera_wrapper_preprocesses_and_closes(monkeypatch):
    devices = types.ModuleType("picamera2.devices")
    devices.Hailo = _FakeHailoRuntime
    picamera2 = types.ModuleType("picamera2")
    picamera2.devices = devices
    monkeypatch.setitem(sys.modules, "picamera2", picamera2)
    monkeypatch.setitem(sys.modules, "picamera2.devices", devices)
    monkeypatch.setitem(sys.modules, "cv2", _FakeCv2)

    detector = HailoYolo(Path("/tmp/yolov8s.hef"))
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    detections = detector.infer(frame)

    assert len(detections) == 1
    assert detections[0]["class"] == "car"
    assert detector.hailo.last_frame.shape == (320, 320, 3)
    assert detector.hailo.last_frame.flags["C_CONTIGUOUS"]
    detector.close()
    assert detector._context.closed is True


def test_camera_source_honours_env_override(monkeypatch):
    monkeypatch.setenv("DRIFTER_DASHCAM_DEV", "/dev/video7")
    assert vision._camera_source() == "/dev/video7"



@pytest.mark.parametrize(
    "arch,filename",
    [
        ("HAILO8", "yolov8s_h8.hef"),
        ("HAILO8L", "yolov8s_h8l.hef"),
        ("HAILO10H", "yolov8m_h10.hef"),
    ],
)
def test_packaged_hailo_model_tracks_detected_architecture(monkeypatch, arch, filename):
    monkeypatch.setattr(vision, "_picamera_hailo_api", lambda: (object, lambda: arch))
    original_exists = Path.exists
    monkeypatch.setattr(
        Path,
        "exists",
        lambda self: True if self.name == filename else original_exists(self),
    )
    model = vision._system_hailo_model()
    assert model is not None
    assert model.name == filename



def test_capture_loop_falls_back_to_picamera2_and_publishes(monkeypatch):
    running = [True]
    published = []

    class Client:
        def publish(self, topic, payload):
            published.append((topic, payload))

    class Detector:
        def infer(self, _frame):
            return [{"class": "car", "confidence": 0.9, "bbox": {}}]

    class ClosedCapture:
        def isOpened(self):
            return False

        def release(self):
            pass

    class CaptureCv2:
        COLOR_RGB2BGR = 3

        @staticmethod
        def VideoCapture(_source):
            return ClosedCapture()

        @staticmethod
        def cvtColor(frame, _code):
            return frame

    class FakePicamera2:
        instance = None

        def __init__(self):
            self.stopped = False
            self.closed = False
            self.__class__.instance = self

        def create_preview_configuration(self, **kwargs):
            return kwargs

        def configure(self, _config):
            pass

        def start(self):
            pass

        def capture_array(self, _stream):
            running[0] = False
            return np.zeros((640, 640, 3), dtype=np.uint8)

        def stop(self):
            self.stopped = True

        def close(self):
            self.closed = True

    monkeypatch.setitem(sys.modules, "cv2", CaptureCv2)
    monkeypatch.setattr(vision, "_picamera2_class", lambda: FakePicamera2)
    monkeypatch.setattr(vision.time, "sleep", lambda _seconds: None)

    vision._capture_loop(Client(), running, Detector())

    assert len(published) == 1
    assert published[0][0] == vision.TOPICS["vision_object"]
    assert FakePicamera2.instance.stopped is True
    assert FakePicamera2.instance.closed is True

"""OnnxYolo.infer must not run per-frame inference whose output is discarded.

Raw YOLO decoding isn't implemented, so the capture loop filters out anything
that isn't a detection dict. infer() returns [] without invoking the (absent)
session rather than burning CPU on unusable raw tensors.
"""
from __future__ import annotations

import sys

sys.path.insert(0, 'src')

from vision_engine import OnnxYolo


class _ExplodingSession:
    def run(self, *_a, **_k):
        raise AssertionError("session.run must not be called for a no-op infer")


def test_infer_returns_empty_without_running_session():
    obj = OnnxYolo.__new__(OnnxYolo)   # bypass __init__ (no onnxruntime needed)
    obj.session = _ExplodingSession()
    obj.input_name = 'images'
    assert obj.infer(object()) == []


def test_infer_returns_empty_when_no_session():
    obj = OnnxYolo.__new__(OnnxYolo)
    obj.session = None
    assert obj.infer(object()) == []

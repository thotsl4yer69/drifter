from __future__ import annotations

import json
from pathlib import Path

import numpy as np

import alpr_engine as alpr


def test_safe_recon_crop_rejects_paths_outside_recon(monkeypatch, tmp_path):
    root = tmp_path / "recon"
    root.mkdir()
    inside = root / "crops" / "car.jpg"
    inside.parent.mkdir()
    inside.write_bytes(b"x")
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"x")
    monkeypatch.setattr(alpr, "RECON_DIR", root)

    assert alpr._safe_recon_crop(str(inside)) == inside.resolve()
    assert alpr._safe_recon_crop(str(outside)) is None


def test_crop_payload_publishes_plate_with_evidence_pointer(monkeypatch, tmp_path):
    root = tmp_path / "recon"
    crop = root / "crops" / "car.jpg"
    crop.parent.mkdir(parents=True)
    crop.write_bytes(b"jpeg")
    evidence = root / "media" / "frame.jpg"
    evidence.parent.mkdir(parents=True)
    evidence.write_bytes(b"frame")
    monkeypatch.setattr(alpr, "RECON_DIR", root)
    monkeypatch.setattr(alpr, "_read_plate_easyocr",
                        lambda _img: [{"plate": "ABC123", "confidence": 0.91}])
    monkeypatch.setattr(alpr, "_read_plate_openalpr", lambda _path: [])
    monkeypatch.setattr(alpr, "_record", lambda _plate: True)

    class Cv2:
        IMREAD_COLOR = 1

        @staticmethod
        def imread(_path, _mode):
            return np.zeros((20, 40, 3), dtype=np.uint8)

    monkeypatch.setitem(__import__("sys").modules, "cv2", Cv2)
    published = []

    class Client:
        def publish(self, topic, payload):
            published.append((topic, json.loads(payload)))

    alpr._handle_crop_payload(Client(), {
        "crop_path": str(crop),
        "evidence_path": str(evidence),
        "camera_id": "front",
        "class": "car",
        "bbox": {"x1": 1, "y1": 2, "x2": 20, "y2": 10},
    })

    assert len(published) == 1
    topic, payload = published[0]
    assert topic == alpr.TOPICS["alpr_plate"]
    assert payload["plate"] == "ABC123"
    assert payload["evidence_path"] == str(evidence)
    assert payload["crop_path"] == str(crop)

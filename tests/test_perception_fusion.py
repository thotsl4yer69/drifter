import time

from perception_fusion import FusionState, derive_events


def test_vehicle_ahead_requires_motion_and_near_central_box():
    state = FusionState(speed_kph=50)
    obj = {"class": "car", "confidence": 0.9, "bbox": {"cx": 0.5, "height": 0.3}}
    events = derive_events([obj], state, now=100.0)
    assert events and events[0]["kind"] == "vehicle_ahead"


def test_person_ahead_promotes_warn():
    state = FusionState(speed_kph=30)
    obj = {"class": "person", "confidence": 0.92, "bbox": {"cx": 0.48, "height": 0.4}}
    event = derive_events([obj], state, now=100.0)[0]
    assert event["kind"] == "vulnerable_road_user_ahead"
    assert event["severity"] == "warn"


def test_off_axis_object_is_not_promoted():
    state = FusionState(speed_kph=60)
    obj = {"class": "car", "confidence": 0.95, "bbox": {"cx": 0.9, "height": 0.5}}
    assert derive_events([obj], state, now=100.0) == []


def test_low_confidence_is_not_promoted():
    state = FusionState(speed_kph=60)
    obj = {"class": "person", "confidence": 0.2, "bbox": {"cx": 0.5, "height": 0.5}}
    assert derive_events([obj], state, now=100.0) == []


def test_stationary_vehicle_does_not_promote_vehicle_ahead():
    state = FusionState(speed_kph=0)
    obj = {"class": "car", "confidence": 0.99, "bbox": {"cx": 0.5, "height": 0.8}}
    assert derive_events([obj], state, now=100.0) == []


def test_stop_sign_can_promote_without_large_box():
    state = FusionState(speed_kph=35)
    obj = {"class": "stop sign", "confidence": 0.9, "bbox": {"cx": 0.5, "height": 0.08}}
    event = derive_events([obj], state, now=100.0)[0]
    assert event["kind"] == "stop_sign_seen"


def test_pixel_space_central_box_supported():
    state = FusionState(speed_kph=50)
    obj = {
        "class": "truck",
        "confidence": 0.9,
        "frame_width": 640,
        "bbox": {"x1": 220, "x2": 420, "height": 180},
    }
    assert derive_events([obj], state, now=100.0)[0]["kind"] == "vehicle_ahead"

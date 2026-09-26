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

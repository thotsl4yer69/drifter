"""Field gate regressions. No Pi, ECU or network is required by these tests."""
import json
from types import SimpleNamespace

import pytest

import field_acceptance as a


def live_ok():
    return {"obd": {"ok": True}, "display": {"ok": True}, "services_ok": True, "power": {"ok": True}}


@pytest.fixture
def stored(tmp_path, monkeypatch):
    monkeypatch.setattr(a, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(a, "EVIDENCE_DIR", tmp_path / "evidence")
    monkeypatch.setattr(a, "_live_checks", live_ok)
    state = {
        "cold_boots": [{"boot_id": f"boot-{i}", "ok": True} for i in range(10)],
        "telemetry_soak": {"ok": True, "duration_s": 1800, "max_allowed_gap_s": 30},
        "physical": {key: {"ok": True} for key in a.VIM_REQUIRED_PHYSICAL_GATES},
    }
    a._save_state(state)
    return state


def test_no_live_never_signs_off(stored, capsys):
    assert a._status(SimpleNamespace(no_live=True)) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["stored_gates_ready"] is True
    assert report["signoff_ready"] is False
    assert report["live_checked"] is False


def test_complete_live_gate_passes(stored, capsys):
    assert a._status(SimpleNamespace(no_live=False)) == 0
    assert json.loads(capsys.readouterr().out)["signoff_ready"] is True


@pytest.mark.parametrize("value", ["NaN", "Infinity", "nonsense", None, True, -1, 1799])
def test_invalid_stored_duration_never_passes(stored, value, capsys):
    stored["telemetry_soak"]["duration_s"] = value
    a._save_state(stored)
    assert a._status(SimpleNamespace(no_live=False)) == 2


@pytest.mark.parametrize("value", ["NaN", "Infinity", 31, 1, True])
def test_relaxed_stored_gap_never_passes(stored, value, capsys):
    stored["telemetry_soak"]["max_allowed_gap_s"] = value
    a._save_state(stored)
    assert a._status(SimpleNamespace(no_live=False)) == 2


def test_latest_failure_resets_boot_sequence(stored, capsys):
    stored["cold_boots"].append({"boot_id": "bad", "ok": False})
    a._save_state(stored)
    assert a._status(SimpleNamespace(no_live=False)) == 2
    assert json.loads(capsys.readouterr().out)["cold_boots"]["passed"] == 0


def test_ten_passes_after_a_failure_can_pass(stored, capsys):
    stored["cold_boots"].insert(0, {"boot_id": "old-bad", "ok": False})
    a._save_state(stored)
    assert a._status(SimpleNamespace(no_live=False)) == 0


def test_failed_boot_cannot_be_overwritten_by_retry(stored, monkeypatch, capsys):
    stored["cold_boots"] = [{"boot_id": "same", "ok": False, "recorded": "earlier"}]
    a._save_state(stored)
    monkeypatch.setattr(a, "_boot_id", lambda: "same")
    monkeypatch.setattr(a, "_field_dump", lambda: "/private/evidence.txt")
    assert a._record_cold_boot(SimpleNamespace(no_replug=True, note="retry")) == 2
    saved = a._load_state()["cold_boots"]
    assert len(saved) == 1
    assert saved[0]["first_failure"]["recorded"] == "earlier"


def test_duplicate_success_does_not_inflate_count(stored, monkeypatch, capsys):
    stored["cold_boots"] = [{"boot_id": "same", "ok": True}]
    a._save_state(stored)
    monkeypatch.setattr(a, "_boot_id", lambda: "same")
    monkeypatch.setattr(a, "_field_dump", lambda: "/private/evidence.txt")
    assert a._record_cold_boot(SimpleNamespace(no_replug=True, note="retry")) == 0
    assert len(a._load_state()["cold_boots"]) == 1


@pytest.mark.parametrize("seconds,gap", [(float("nan"), 30), (float("inf"), 30), (9, 30), (86401, 30), (10, float("inf")), (10, 31), (10, 1)])
def test_invalid_soak_arguments_do_not_touch_hardware(monkeypatch, seconds, gap):
    def forbidden():
        pytest.fail("invalid arguments must fail before probing hardware")
    monkeypatch.setattr(a, "_live_checks", forbidden)
    assert a._telemetry_soak(SimpleNamespace(seconds=seconds, max_gap=gap)) == 2


def test_corrupt_state_fails_closed(tmp_path):
    path = tmp_path / "bad.json"
    path.write_bytes(b"\xff\xfe")
    assert a._load_state(path) == {}


def test_atomic_write_rejects_nonfinite_and_preserves_previous(tmp_path):
    path = tmp_path / "state.json"
    a._save_state({"ok": False}, path)
    with pytest.raises(ValueError):
        a._save_state({"value": float("nan")}, path)
    assert a._load_state(path) == {"ok": False}
    assert not list(tmp_path.glob("*.tmp.*"))


class FakeClient:
    def __init__(self, clock, mode="normal"):
        self.clock = clock
        self.mode = mode
        self.subscriptions = []
        self.emitted_fault = False

    def subscribe(self, topic):
        self.subscriptions.append(topic)
        return (1 if self.mode == "subscribe-error" else 0, 1)

    def connect(self, *_args):
        return 0

    def loop_start(self):
        self.on_connect(self, None, None, 5 if self.mode == "refused" else 0, None)
        self.emit()

    def loop_stop(self):
        return None

    def disconnect(self):
        self.on_disconnect(self, None, None, 0, None)

    def emit(self):
        if self.mode == "interrupted" and self.clock.now >= 3:
            raise KeyboardInterrupt
        if self.mode == "disconnect" and self.clock.now >= 3 and not self.emitted_fault:
            self.emitted_fault = True
            self.on_disconnect(self, None, None, 1, None)
            self.on_connect(self, None, None, 0, None)
        for topic in a.TELEMETRY_TOPICS.values():
            value = True if self.mode == "boolean" else 1.0
            ts = self.clock.time() - (100 if self.mode == "stale" else 0)
            if self.mode == "future":
                ts += 100
            payload = {"value": value, "ts": ts}
            if self.mode == "synthetic":
                payload["source"] = "replay"
            if self.mode == "malformed-source":
                payload["source"] = ["unexpected", "metadata"]
            msg = SimpleNamespace(topic=topic, payload=json.dumps(payload).encode(), retain=self.mode == "retained")
            self.on_message(self, None, msg)
        if self.mode == "obd-error":
            self.on_message(self, None, SimpleNamespace(topic=a.OBD_STATUS_TOPIC, payload=b'{"state":"reconnecting"}', retain=False))


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.client = None

    def monotonic(self):
        return self.now

    def time(self):
        return 1700000000.0 + self.now

    def sleep(self, seconds):
        self.now += seconds
        self.client.emit()


@pytest.mark.parametrize("mode", ["normal", "retained", "boolean", "stale", "future", "synthetic", "malformed-source", "disconnect", "interrupted", "subscribe-error", "refused", "obd-error"])
def test_mqtt_soak_failure_modes(stored, monkeypatch, mode, capsys):
    clock = FakeClock()
    client = FakeClient(clock, mode)
    clock.client = client
    monkeypatch.setattr(a, "time", clock)
    monkeypatch.setattr(a, "make_mqtt_client", lambda *_args: client)
    rc = a._telemetry_soak(SimpleNamespace(seconds=10, max_gap=30))
    report = json.loads(capsys.readouterr().out)
    assert rc == (0 if mode in {"normal", "malformed-source"} else 2)
    assert report["ok"] is (mode in {"normal", "malformed-source"})
    if mode == "disconnect":
        assert len(client.subscriptions) == 10  # re-subscribed on recovery
    if mode == "normal":
        assert not report["obd_failures"]  # intentional teardown is not a fault
        assert a._status(SimpleNamespace(no_live=False)) == 2  # 10 s is NOT 30 min


def test_postflight_failure_blocks_soak(stored, monkeypatch, capsys):
    clock = FakeClock()
    client = FakeClient(clock)
    clock.client = client
    monkeypatch.setattr(a, "time", clock)
    monkeypatch.setattr(a, "make_mqtt_client", lambda *_args: client)
    bad = live_ok()
    bad["power"] = {"ok": False}
    checks = iter([live_ok(), bad])
    monkeypatch.setattr(a, "_live_checks", lambda: next(checks))
    assert a._telemetry_soak(SimpleNamespace(seconds=10, max_gap=30)) == 2
    report = json.loads(capsys.readouterr().out)
    assert any(f["state"] == "postflight_failed" for f in report["obd_failures"])

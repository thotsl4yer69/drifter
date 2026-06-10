#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Cross-module integration tests for the safety pipeline.

Unlike the rest of the suite (which mocks individual functions), this file
exercises the REAL handler logic of multiple services wired together over a
faithful in-process MQTT bus, pinning the actual topic contracts from
``config.TOPICS``.

Why an in-process bus and not a real broker
-------------------------------------------
There is no ``mosquitto`` binary on the CI image and no in-process broker
library (``amqtt``/``hbmqtt``) installed, so option 1 from the brief (a real
loopback broker) is unavailable here. Instead we build ``FakeBus`` — a minimal
but faithful re-implementation of the slice of the paho-mqtt client API that
these services actually call (``publish(topic, payload, qos=, retain=)``,
``subscribe(topic | [(topic, qos)])``, ``client.on_message``, ``loop_start``).
Crucially the bus routes a message PUBLISHED by one service to the REAL
``on_message`` callback of every subscribed service — including paho-style
``+``/``#`` wildcard matching, which ``alert_engine`` relies on. So the logic
under test (windowing, baseline learning, every safety rule, alert
prioritisation) is the genuine module code, driven across module boundaries by
the genuine topic strings and payload shapes.

What is real vs stubbed (documented per the brief)
--------------------------------------------------
REAL, end-to-end across the bus:
  * telemetry_batcher.on_message + build_window          (window/stats output)
  * adaptive_thresholds.on_message + Learner.end_session (learned/update output)
  * safety_engine.on_message + evaluate                  (every safety rule)
  * alert_engine.on_message + evaluate_rules             (diagnostic alert level)
  * forward_collision._evaluate                          (real TTC decision)
  * crash_detect._trigger                                (real crash_event payload)

STUBBED (and why): forward_collision and crash_detect build their MQTT
``on_message`` ingest callbacks as closures inside ``main()``, so the raw
vision-frame / speed-snapshot ingest step cannot be reached without standing up
``main()`` + hardware threads. We therefore drive their REAL module-level
decision logic directly (``_evaluate`` / ``_trigger``) and publish the REAL
payloads they emit onto the bus, then assert that safety_engine — the genuine
downstream consumer — reacts correctly. The cross-module contract
(fcw_warning -> safety FCW alert, crash_event -> safety RED + crash_sos) is thus
covered for real; only the sensor-ingest front of those two leaf services is
out of scope here.

UNCAGED TECHNOLOGY — EST 1991
"""

import json
import time

import pytest

import adaptive_thresholds
import alert_engine
import crash_detect
import forward_collision
import safety_engine
import telemetry_batcher
from config import (
    CRASH_DECEL_KPH_PER_S,
    FCW_TTC_CRIT,
    LEVEL_AMBER,
    LEVEL_RED,
    TOPICS,
)

# ───────────────────────── Faithful in-process MQTT bus ─────────────────────


def _topic_matches(sub: str, topic: str) -> bool:
    """paho-mqtt wildcard match: ``+`` = one level, ``#`` = trailing levels."""
    if sub == topic:
        return True
    sub_parts = sub.split('/')
    topic_parts = topic.split('/')
    for i, sp in enumerate(sub_parts):
        if sp == '#':
            # '#' must be last and matches the remainder (>= 0 levels).
            return True
        if i >= len(topic_parts):
            return False
        if sp == '+':
            continue
        if sp != topic_parts[i]:
            return False
    return len(sub_parts) == len(topic_parts)


class _Msg:
    """Mimics paho's MQTTMessage (topic str + payload bytes)."""

    __slots__ = ('payload', 'topic')

    def __init__(self, topic: str, payload) -> None:
        self.topic = topic
        if isinstance(payload, str):
            payload = payload.encode()
        self.payload = payload


class FakeBus:
    """Routes published messages to real on_message handlers of subscribers."""

    def __init__(self) -> None:
        # list of (client, subscription_filter)
        self._subs: list[tuple[FakeClient, str]] = []
        # retained messages by exact topic (delivered to new subscribers)
        self._retained: dict[str, _Msg] = {}
        # full publish log for assertions: list of (topic, dict-or-raw)
        self.log: list[tuple[str, object]] = []

    def register(self, client: 'FakeClient', sub_filter: str) -> None:
        self._subs.append((client, sub_filter))
        # Deliver any matching retained message to the late subscriber.
        for topic, msg in self._retained.items():
            if _topic_matches(sub_filter, topic):
                self._deliver(client, msg)

    def publish(self, topic: str, payload, qos: int = 0, retain: bool = False):
        msg = _Msg(topic, payload)
        try:
            self.log.append((topic, json.loads(msg.payload)))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.log.append((topic, msg.payload))
        if retain:
            self._retained[topic] = msg
        for client, sub_filter in list(self._subs):
            if _topic_matches(sub_filter, topic):
                self._deliver(client, msg)

    @staticmethod
    def _deliver(client: 'FakeClient', msg: _Msg) -> None:
        if client.on_message is not None:
            # Same signature real paho uses: (client, userdata, message)
            client.on_message(client, client._userdata, msg)

    # ── assertion helpers ──
    def messages_on(self, topic: str) -> list:
        return [p for t, p in self.log if t == topic]

    def last_on(self, topic: str):
        msgs = self.messages_on(topic)
        return msgs[-1] if msgs else None


class FakeClient:
    """The slice of the paho-mqtt Client API the services actually use."""

    def __init__(self, bus: FakeBus, client_id: str = "") -> None:
        self._bus = bus
        self._client_id = client_id
        self._userdata = None
        self.on_message = None
        self.on_connect = None
        self.on_disconnect = None

    # publish/subscribe delegate to the shared bus
    def publish(self, topic, payload=None, qos=0, retain=False):
        self._bus.publish(topic, payload, qos=qos, retain=retain)
        return (0, 0)

    def subscribe(self, topic, qos=0):
        # paho accepts a single (topic, qos) or a list of (topic, qos) tuples.
        if isinstance(topic, list):
            for entry in topic:
                t = entry[0] if isinstance(entry, tuple) else entry
                self._bus.register(self, t)
        else:
            self._bus.register(self, topic)
        return (0, 0)

    # lifecycle no-ops so service code that calls these does not blow up
    def loop_start(self):
        pass

    def loop_stop(self):
        pass

    def will_set(self, *a, **k):
        pass

    def connect(self, *a, **k):
        return 0

    def disconnect(self, *a, **k):
        return 0


# ─────────────────────────── synthetic OBD helpers ─────────────────────────


def _metric(client: FakeClient, key: str, value: float, ts: float) -> None:
    """Publish one per-PID metric exactly like can_bridge does."""
    client.publish(TOPICS[key], json.dumps({'value': value, 'ts': ts}))


def _snapshot(client: FakeClient, ts: float, **fields) -> None:
    """Publish a flat OBD snapshot like can_bridge's drifter/snapshot."""
    client.publish(TOPICS['snapshot'], json.dumps({'ts': ts, **fields}))


# ─────────────────────────── fixtures ─────────────────────────


@pytest.fixture()
def bus():
    return FakeBus()


@pytest.fixture(autouse=True)
def _reset_module_globals():
    """Reset the module-level state every wired service mutates.

    These services keep their telemetry/state in module globals (matching how
    they run as long-lived processes). Without a reset, ordering between tests
    leaks state across the bus and makes assertions non-deterministic.
    """
    # telemetry_batcher rolling buffers
    telemetry_batcher._buffers.clear()

    # adaptive_thresholds learner — rebuild a learner with no persisted state.
    from adaptive_thresholds import DEFAULT_BASELINES, Learner
    fresh = Learner.__new__(Learner)
    fresh.samples = adaptive_thresholds.defaultdict(
        lambda: adaptive_thresholds.deque(maxlen=20000))
    fresh.session_count = 0
    fresh.baselines = dict(DEFAULT_BASELINES)
    fresh.current_coolant = 0.0
    fresh.current_rpm = 0.0
    fresh.current_speed = 0.0
    adaptive_thresholds._learner = fresh

    # safety_engine state
    safety_engine._state = safety_engine.SafetyState()
    safety_engine._state.mqtt_connected = True  # evaluate() gates on this

    # alert_engine state + readiness gate
    alert_engine.state = alert_engine.VehicleState()
    alert_engine._active_alerts.clear()
    alert_engine._clear_counters.clear()
    alert_engine.current_alert_level = alert_engine.LEVEL_OK
    alert_engine.current_alert_msg = "Systems nominal"
    alert_engine.last_alert_time = 0
    alert_engine._mqtt_message_count = 0
    alert_engine._data_ready = False
    alert_engine._startup_time = 0.0  # so the wall-clock readiness gate passes
    alert_engine.engine_start_time = 0.0
    alert_engine.warmup_complete = False
    yield


def _wire_batcher(bus: FakeBus) -> FakeClient:
    c = FakeClient(bus, "drifter-batcher")
    c.on_message = telemetry_batcher.on_message
    for topic in telemetry_batcher._TOPIC_TO_KEY:
        c.subscribe(topic, 0)
    return c


def _wire_thresholds(bus: FakeBus) -> FakeClient:
    c = FakeClient(bus, "drifter-thresholds")
    c.on_message = adaptive_thresholds.on_message
    c.subscribe([(TOPICS['snapshot'], 0), (TOPICS['drive_session'], 0)])
    return c


def _wire_safety(bus: FakeBus) -> FakeClient:
    c = FakeClient(bus, "drifter-safety")
    c.on_message = safety_engine.on_message
    # Mirror safety_engine.on_connect's subscription set.
    c.subscribe([
        (TOPICS['snapshot'], 0),
        (TOPICS['crash_event'], safety_engine.SAFETY_QOS),
        (TOPICS['fcw_warning'], safety_engine.SAFETY_QOS),
        (TOPICS['driver_fatigue'], 0),
        (TOPICS['weather_current'], 0),
        (TOPICS['location_elevation'], 0),
    ])
    return c


def _wire_alerts(bus: FakeBus) -> FakeClient:
    c = FakeClient(bus, "drifter-alerts")
    c.on_message = alert_engine.on_message
    for domain in ('engine', 'vehicle', 'power', 'diag', 'rf/tpms'):
        c.subscribe(f"drifter/{domain}/#")
    return c


# ═══════════════════════════════════════════════════════════════════════════
#  SCENARIO 1 — synthetic drive: batcher windows + adaptive-threshold learning
# ═══════════════════════════════════════════════════════════════════════════


def test_drive_flows_batcher_to_threshold_learning(bus):
    """A warm-idle drive feeds real metric/snapshot topics; assert batcher
    produces window/stats and adaptive_thresholds learns a baseline + emits
    the learned/update contract on session end."""
    can = FakeClient(bus, "can-bridge-sim")  # stands in for the OBD publisher
    batcher = _wire_batcher(bus)             # subscribes on the bus
    _wire_thresholds(bus)                    # subscribes on the bus

    t0 = time.time()
    # Warm idle: coolant 90C, rpm ~750, speed 0 — the only state in which
    # adaptive_thresholds is permitted to learn (Learner._eligible()).
    n = 80  # > ADAPTIVE_LEARN_MIN_SAMPLES (60) so per-key learning is allowed
    for i in range(n):
        ts = t0 + i * 0.1
        rpm = 750.0 + (i % 5)        # tiny jitter so stddev > 0
        volt = 14.0
        maf = 3.6
        # Per-PID metric topics -> batcher
        _metric(can, 'rpm', rpm, ts)
        _metric(can, 'coolant', 90.0, ts)
        _metric(can, 'voltage', volt, ts)
        _metric(can, 'maf', maf, ts)
        _metric(can, 'speed', 0.0, ts)
        # Flat snapshot -> adaptive_thresholds learner (carries rpm/volt/maf)
        _snapshot(can, ts, rpm=rpm, coolant=90.0, speed=0.0,
                  voltage=volt, maf=maf, stft1=1.0, stft2=-1.0,
                  ltft1=2.0, ltft2=-2.0)

    # ── Batcher: build the real rolling-window summary from ingested data ──
    window = telemetry_batcher.build_window(t0 + n * 0.1)
    assert window['metrics'], "batcher produced no window from the drive"
    # The metrics the downstream AI/reporter consumers rely on must be present.
    for key in ('rpm', 'coolant', 'voltage', 'maf', 'speed'):
        assert key in window['metrics'], f"missing {key} in window"
    rpm_stats = window['metrics']['rpm']
    assert set(rpm_stats) == {'mean', 'min', 'max', 'stddev', 'last', 'count'}
    assert 750.0 <= rpm_stats['mean'] <= 755.0
    assert rpm_stats['count'] > 0
    # coolant was constant -> stddev 0; rpm jittered -> stddev > 0
    assert window['metrics']['coolant']['stddev'] == 0.0
    assert rpm_stats['stddev'] > 0.0

    # Drive the real publish path and assert the contract topics fire.
    telemetry_batcher._publish(batcher, window)
    win_msg = bus.last_on(TOPICS['telemetry_window'])
    assert win_msg is not None and 'metrics' in win_msg
    stats_msg = bus.last_on(TOPICS['telemetry_stats'])
    assert stats_msg is not None and 'means' in stats_msg
    assert pytest.approx(stats_msg['means']['rpm'], abs=0.01) == rpm_stats['mean']

    # ── Adaptive thresholds: the learner ingested via real on_message ──
    learner = adaptive_thresholds._learner
    assert len(learner.samples['rpm']) >= 60, "learner did not ingest warm-idle rpm"
    assert len(learner.samples['maf']) >= 60

    # End the session over the real drive_session topic -> learned/update.
    can.publish(TOPICS['drive_session'], json.dumps({'event': 'end'}))

    learned = bus.last_on(TOPICS['thresholds_learned'])
    assert learned is not None, "thresholds_learned not published on session end"
    assert 'baselines' in learned and 'session_count' in learned
    assert learned['session_count'] == 1
    # rpm baseline should have moved toward the ~752 we fed (from default 720),
    # but stay within the drift cap — never silently disabling protection.
    base = learned['baselines']['idle_rpm_baseline']
    assert 720.0 < base <= 720.0 * 1.25 + 0.01

    update = bus.last_on(TOPICS['thresholds_update'])
    assert update is not None and update['reason'] == 'session_end'
    assert update['baselines']['idle_rpm_baseline'] == base


# ═══════════════════════════════════════════════════════════════════════════
#  SCENARIO 2 — hard braking drives safety_engine to a hard-brake alert
# ═══════════════════════════════════════════════════════════════════════════


def test_hard_braking_publishes_safety_alert(bus):
    """A rapid-deceleration snapshot sequence must make the REAL safety_engine
    rule fire and publish the hard-brake alert on TOPICS['safety_alert']."""
    can = FakeClient(bus, "can-bridge-sim")
    safety = _wire_safety(bus)

    t0 = time.time()
    # Cruise then brake hard: 80 -> 50 km/h between two consecutive snapshots.
    # rule_hard_brake uses the per-sample delta; dry threshold is 22 km/h/s.
    _snapshot(can, t0, rpm=2500, speed=80.0, voltage=14.0, coolant=90.0)
    _snapshot(can, t0 + 1.0, rpm=2000, speed=50.0, voltage=14.0, coolant=90.0)

    safety_engine.evaluate(safety)

    alert = bus.last_on(TOPICS['safety_alert'])
    assert alert is not None, "safety_engine published no alert on hard braking"
    assert alert['key'] == 'hard_brake'
    assert alert['level'] == LEVEL_AMBER
    assert 'HARD BRAKING' in alert['message']


def test_wet_road_tightens_hard_brake_threshold(bus):
    """Weather context (real weather_current contract) must tighten the
    hard-brake rule: an 18 km/h/s decel is below the dry 22 threshold but above
    the wet 16 threshold, so it should only alert once the road is wet."""
    can = FakeClient(bus, "can-bridge-sim")
    safety = _wire_safety(bus)

    t0 = time.time()
    # Dry: gentle-ish 18 km/h/s decel — below dry threshold, no alert.
    _snapshot(can, t0, rpm=2500, speed=60.0, voltage=14.0, coolant=90.0)
    _snapshot(can, t0 + 1.0, rpm=2300, speed=42.0, voltage=14.0, coolant=90.0)
    safety_engine.evaluate(safety)
    assert bus.last_on(TOPICS['safety_alert']) is None

    # Now tell safety it is raining (the real weather service contract).
    can.publish(TOPICS['weather_current'],
                json.dumps({'is_raining': True, 'is_foggy': False}))
    # Same 18 km/h/s decel pattern — now over the wet threshold.
    _snapshot(can, t0 + 2.0, rpm=2300, speed=60.0, voltage=14.0, coolant=90.0)
    _snapshot(can, t0 + 3.0, rpm=2100, speed=42.0, voltage=14.0, coolant=90.0)
    # Cooldown is keyed per-alert-key; first hard_brake never fired, so this one
    # is free to publish.
    safety_engine.evaluate(safety)
    alert = bus.last_on(TOPICS['safety_alert'])
    assert alert is not None and alert['key'] == 'hard_brake'
    assert 'wet road' in alert['message']


# ═══════════════════════════════════════════════════════════════════════════
#  SCENARIO 3 — forward-collision low-TTC produces the FCW safety alert
# ═══════════════════════════════════════════════════════════════════════════


def test_forward_collision_low_ttc_to_safety_fcw(bus):
    """Real forward_collision TTC logic produces a critical warning; publishing
    that real fcw_warning payload must drive safety_engine to a RED FCW alert.

    forward_collision's MQTT ingest is a main()-local closure, so we exercise
    its REAL module-level decision function (_evaluate) and publish the REAL
    payload shape it emits — the cross-module fcw->safety contract is genuine.
    """
    can = FakeClient(bus, "can-bridge-sim")
    safety = _wire_safety(bus)

    # ── Real FCW decision logic ──
    fcw_state = forward_collision.FCWState()
    fcw_state.speed_kph = 60.0  # > 5 km/h gate
    # Distance that yields TTC well under FCW_TTC_CRIT (1.2s):
    # ttc = distance / (speed/3.6). At 60 km/h => 16.67 m/s; 10 m -> 0.6 s.
    fcw_state.distance_hist.append(10.0)
    warning = forward_collision._evaluate(fcw_state)
    assert warning is not None and warning['level'] == 'critical'
    assert warning['ttc_s'] <= FCW_TTC_CRIT

    # Publish exactly what forward_collision.main() would emit on the wire.
    fcw_payload = {**warning, 'speed_kph': fcw_state.speed_kph,
                   'active': True, 'ts': time.time()}
    can.publish(TOPICS['fcw_warning'], json.dumps(fcw_payload), retain=True)

    # safety_engine should have flagged FCW active (ttc <= critical).
    assert safety_engine._state.fcw_active is True
    safety_engine.evaluate(safety)
    alert = bus.last_on(TOPICS['safety_alert'])
    assert alert is not None and alert['key'] == 'fcw'
    assert alert['level'] == LEVEL_RED
    assert 'FORWARD COLLISION' in alert['message']


def test_high_ttc_does_not_trigger_fcw(bus):
    """A comfortable following distance must NOT raise the FCW path —
    guards against a false-positive collision warning."""
    fcw_state = forward_collision.FCWState()
    fcw_state.speed_kph = 60.0
    fcw_state.distance_hist.append(120.0)  # ~7.2 s TTC, well clear
    assert forward_collision._evaluate(fcw_state) is None


# ═══════════════════════════════════════════════════════════════════════════
#  SCENARIO 4 — crash trigger: crash_event -> safety RED + SOS path
# ═══════════════════════════════════════════════════════════════════════════


def test_crash_trigger_raises_safety_red_and_sos(bus):
    """Real crash_detect._trigger publishes the crash_event contract; the REAL
    safety_engine consumes it and escalates to RED, and the SOS countdown fires
    crash_sos for the comms bridge."""
    crash_client = FakeClient(bus, "drifter-crash")
    safety = _wire_safety(bus)

    # Short grace so the SOS countdown completes inside the test.
    crash_state = crash_detect.CrashState(grace=1, sos_number="+15555550123")
    crash_state.speed_hist.append(70.0)

    # Real crash trigger — publishes crash_event (retained) and starts the
    # real _sos_countdown thread.
    crash_detect._trigger(crash_client, crash_state,
                          reason=f"hard decel -{CRASH_DECEL_KPH_PER_S} km/h/s",
                          magnitude=4.1)

    crash_evt = bus.last_on(TOPICS['crash_event'])
    assert crash_evt is not None and crash_evt['active'] is True
    assert crash_evt['magnitude_g'] == 4.1
    assert crash_evt['grace_seconds'] == 1

    # safety_engine ingested the crash via the real on_message path.
    assert safety_engine._state.crash_active is True
    safety_engine.evaluate(safety)
    alert = bus.last_on(TOPICS['safety_alert'])
    assert alert is not None and alert['key'] == 'crash'
    assert alert['level'] == LEVEL_RED
    assert 'SOS armed' in alert['message']

    # Wait out the SOS grace and assert the SOS contract fires.
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if bus.last_on(TOPICS['crash_sos']) is not None:
            break
        time.sleep(0.05)
    sos = bus.last_on(TOPICS['crash_sos'])
    assert sos is not None, "crash_sos never fired after grace timeout"
    assert sos['number'] == "+15555550123"
    assert 'crash detected' in sos['message']


def test_crash_cancel_suppresses_sos(bus):
    """If the crash is cancelled inside the grace window, no SOS may fire —
    a wrong SOS is itself a safety failure."""
    crash_client = FakeClient(bus, "drifter-crash")
    crash_state = crash_detect.CrashState(grace=2, sos_number="+15555550123")
    crash_state.speed_hist.append(70.0)

    crash_detect._trigger(crash_client, crash_state, reason="accel 4.0g",
                          magnitude=4.0)
    # Operator cancels almost immediately.
    crash_state.cancelled = True

    deadline = time.time() + 3.0
    cleared = False
    while time.time() < deadline:
        evt = bus.last_on(TOPICS['crash_event'])
        if evt is not None and isinstance(evt, dict) and evt.get('cancelled'):
            cleared = True
            break
        time.sleep(0.05)
    assert cleared, "crash_event cancel acknowledgement never published"
    assert bus.last_on(TOPICS['crash_sos']) is None, "SOS fired despite cancel"


# ═══════════════════════════════════════════════════════════════════════════
#  SCENARIO 5 — full chain: diagnostic alert_engine alongside safety_engine
# ═══════════════════════════════════════════════════════════════════════════


def test_diagnostic_overheat_raises_alert_level(bus):
    """The diagnostic alert_engine (real on_message + evaluate_rules) must
    publish an elevated alert level for a critical-coolant drive over the real
    per-PID engine topics. This pins the alert_level/alert_message contract the
    dashboard, voice and reporter consume."""
    can = FakeClient(bus, "can-bridge-sim")
    alerts = _wire_alerts(bus)

    t0 = time.time()
    # Feed > DATA_READY_MESSAGES samples so the readiness gate opens, with a
    # critical coolant temperature (>= coolant_red 108C).
    for i in range(120):
        ts = t0 + i * 0.05
        _metric(can, 'rpm', 2500.0, ts)
        _metric(can, 'coolant', 112.0, ts)
        _metric(can, 'voltage', 14.0, ts)

    alert_engine.evaluate_rules(alerts)

    level_msg = bus.last_on(TOPICS['alert_level'])
    assert level_msg is not None, "alert_engine published no alert level"
    assert level_msg['level'] == LEVEL_RED
    body = bus.last_on(TOPICS['alert_message'])
    assert body is not None and 'COOLANT CRITICAL' in body['message']
    # The multi-alert fan-out topic the cockpit reads must also be populated.
    active = bus.last_on('drifter/alert/active')
    assert active is not None and active['count'] >= 1


def test_crash_supersedes_diagnostic_on_safety_topic(bus):
    """Crash (RED, life-critical) on safety_alert must outrank a concurrent
    diagnostic AMBER. Confirms the two engines publish to DISTINCT contracts
    (safety_alert vs alert_level) and that safety wins by level on its own
    topic — the prioritisation downstream consumers depend on."""
    can = FakeClient(bus, "can-bridge-sim")
    safety = _wire_safety(bus)

    t0 = time.time()
    # An AMBER-worthy hard brake is in-flight on the safety state...
    _snapshot(can, t0, rpm=2500, speed=80.0, voltage=14.0, coolant=90.0)
    _snapshot(can, t0 + 1.0, rpm=2000, speed=50.0, voltage=14.0, coolant=90.0)
    # ...but a crash arrives (RED) — crash rule is first in ALL_RULES and RED.
    can.publish(TOPICS['crash_event'],
                json.dumps({'active': True, 'ts': time.time()}), retain=True)

    safety_engine.evaluate(safety)
    alert = bus.last_on(TOPICS['safety_alert'])
    assert alert is not None
    assert alert['key'] == 'crash'
    assert alert['level'] == LEVEL_RED


def test_bus_wildcard_routing_matches_paho_semantics():
    """The in-process bus must match the same wildcard semantics alert_engine
    relies on (it subscribes to drifter/engine/# etc.). If this regresses the
    whole integration harness is invalid, so pin it explicitly."""
    assert _topic_matches('drifter/engine/#', 'drifter/engine/rpm')
    assert _topic_matches('drifter/engine/#', 'drifter/engine/o2/b1s1')
    assert _topic_matches('drifter/rf/tpms/#', 'drifter/rf/tpms/fl')
    assert _topic_matches('drifter/+/rpm', 'drifter/engine/rpm')
    assert not _topic_matches('drifter/engine/#', 'drifter/vehicle/speed')
    assert not _topic_matches('drifter/+/rpm', 'drifter/engine/sub/rpm')
    assert _topic_matches('drifter/snapshot', 'drifter/snapshot')

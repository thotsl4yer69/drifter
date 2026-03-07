"""
Unit tests for DRIFTER alert_engine.py diagnostic rules.
Run with: python -m pytest tests/ -v
"""

from alert_engine import (
    VehicleState,
    LEVEL_OK, LEVEL_INFO, LEVEL_AMBER, LEVEL_RED,
    rule_vacuum_leak_bank1,
    rule_vacuum_leak_both,
    rule_coolant_critical,
    rule_running_rich,
    rule_alternator,
    rule_idle_instability,
    rule_overrev,
)


def make_state(rpm=None, coolant=None, stft1=None, stft2=None,
               voltage=None, speed=None, throttle=None, load=None, n=50):
    """Helper: create a VehicleState populated with n identical readings."""
    state = VehicleState()
    ts = 1000.0
    for i in range(n):
        if rpm is not None:
            state.rpm.append(rpm)
        if coolant is not None:
            state.coolant.append(coolant)
        if stft1 is not None:
            state.stft1.append(stft1)
        if stft2 is not None:
            state.stft2.append(stft2)
        if voltage is not None:
            state.voltage.append(voltage)
        if speed is not None:
            state.speed.append(speed)
        if throttle is not None:
            state.throttle.append(throttle)
        if load is not None:
            state.load.append(load)
        state.timestamps.append(ts + i * 0.1)
    return state


# ── rule_vacuum_leak_bank1 ──────────────────────────────────────────────────

class TestVacuumLeakBank1:
    def test_triggers_when_bank1_lean_at_idle(self):
        state = make_state(rpm=780, stft1=15.0, stft2=2.0)
        result = rule_vacuum_leak_bank1(state)
        assert result is not None
        assert result[0] == LEVEL_AMBER
        assert "Bank 1" in result[1]

    def test_no_trigger_normal_idle(self):
        state = make_state(rpm=800, stft1=2.0, stft2=1.0)
        result = rule_vacuum_leak_bank1(state)
        assert result is None

    def test_no_trigger_when_both_banks_lean(self):
        """Both banks lean → both-bank rule, not bank1-specific."""
        state = make_state(rpm=780, stft1=15.0, stft2=14.0)
        result = rule_vacuum_leak_bank1(state)
        assert result is None  # stft2 >= 5, so bank1-only rule doesn't fire

    def test_no_trigger_at_high_rpm(self):
        state = make_state(rpm=2000, stft1=15.0, stft2=2.0)
        result = rule_vacuum_leak_bank1(state)
        assert result is None

    def test_returns_none_with_no_data(self):
        state = VehicleState()
        result = rule_vacuum_leak_bank1(state)
        assert result is None


# ── rule_vacuum_leak_both ───────────────────────────────────────────────────

class TestVacuumLeakBoth:
    def test_triggers_when_both_banks_lean_at_idle(self):
        state = make_state(rpm=780, stft1=15.0, stft2=14.0)
        result = rule_vacuum_leak_both(state)
        assert result is not None
        assert result[0] == LEVEL_AMBER
        assert "BOTH" in result[1]

    def test_no_trigger_only_one_bank_lean(self):
        state = make_state(rpm=780, stft1=15.0, stft2=2.0)
        result = rule_vacuum_leak_both(state)
        assert result is None

    def test_no_trigger_at_high_rpm(self):
        state = make_state(rpm=2500, stft1=15.0, stft2=15.0)
        result = rule_vacuum_leak_both(state)
        assert result is None


# ── rule_coolant_critical ───────────────────────────────────────────────────

class TestCoolantCritical:
    def test_red_at_108c(self):
        state = make_state(coolant=108)
        result = rule_coolant_critical(state)
        assert result is not None
        assert result[0] == LEVEL_RED
        assert "CRITICAL" in result[1]

    def test_red_above_108c(self):
        state = make_state(coolant=115)
        result = rule_coolant_critical(state)
        assert result is not None
        assert result[0] == LEVEL_RED

    def test_normal_temperature_no_alert(self):
        state = make_state(coolant=95)
        result = rule_coolant_critical(state)
        assert result is None

    def test_no_alert_at_exactly_107(self):
        state = make_state(coolant=107)
        result = rule_coolant_critical(state)
        assert result is None

    def test_amber_when_rising_fast_above_100(self):
        """Rising >2°C/min while above 100°C triggers AMBER."""
        state = VehicleState()
        # Simulate 100 readings from 100→115°C over 10 seconds (fast rise)
        for i in range(100):
            state.coolant.append(100.0 + i * 0.15)
            state.timestamps.append(1000.0 + i * 0.1)
        result = rule_coolant_critical(state)
        assert result is not None
        assert result[0] in (LEVEL_AMBER, LEVEL_RED)

    def test_returns_none_with_no_data(self):
        state = VehicleState()
        result = rule_coolant_critical(state)
        assert result is None


# ── rule_running_rich ───────────────────────────────────────────────────────

class TestRunningRich:
    def test_triggers_when_bank1_sustained_rich(self):
        state = make_state(stft1=-15.0, stft2=0.0, n=200)
        result = rule_running_rich(state)
        assert result is not None
        assert result[0] == LEVEL_AMBER
        assert "rich" in result[1].lower()

    def test_triggers_when_bank2_sustained_rich(self):
        state = make_state(stft1=0.0, stft2=-15.0, n=200)
        result = rule_running_rich(state)
        assert result is not None
        assert result[0] == LEVEL_AMBER

    def test_no_trigger_normal_fuel_trim(self):
        state = make_state(stft1=2.0, stft2=1.5, n=200)
        result = rule_running_rich(state)
        assert result is None

    def test_no_trigger_insufficient_samples(self):
        """Needs at least 150 sustained samples."""
        state = make_state(stft1=-15.0, stft2=0.0, n=100)
        result = rule_running_rich(state)
        assert result is None

    def test_returns_none_with_no_data(self):
        state = VehicleState()
        result = rule_running_rich(state)
        assert result is None


# ── rule_alternator ─────────────────────────────────────────────────────────

class TestAlternator:
    def test_amber_when_undercharging_at_speed(self):
        state = make_state(voltage=12.8, rpm=2000)
        result = rule_alternator(state)
        assert result is not None
        assert result[0] == LEVEL_AMBER
        assert "undercharging" in result[1].lower()

    def test_red_when_voltage_critically_low(self):
        state = make_state(voltage=11.5, rpm=800)
        result = rule_alternator(state)
        assert result is not None
        assert result[0] == LEVEL_RED
        assert "CRITICAL" in result[1]

    def test_no_alert_normal_charging(self):
        state = make_state(voltage=14.2, rpm=2000)
        result = rule_alternator(state)
        assert result is None

    def test_no_alert_low_voltage_at_idle(self):
        """13.1V at idle (<1500 rpm) is borderline but shouldn't trigger."""
        state = make_state(voltage=13.1, rpm=800)
        result = rule_alternator(state)
        assert result is None

    def test_returns_none_with_no_data(self):
        state = VehicleState()
        result = rule_alternator(state)
        assert result is None


# ── rule_idle_instability ───────────────────────────────────────────────────

class TestIdleInstability:
    def test_triggers_when_rpm_swings_at_idle(self):
        state = VehicleState()
        # Alternate between 650 and 900 RPM at idle
        for i in range(150):
            state.rpm.append(650.0 if i % 2 == 0 else 900.0)
            state.timestamps.append(1000.0 + i * 0.1)
        result = rule_idle_instability(state)
        assert result is not None
        assert result[0] == LEVEL_INFO

    def test_no_trigger_stable_idle(self):
        state = make_state(rpm=800, n=150)
        result = rule_idle_instability(state)
        assert result is None

    def test_no_trigger_at_cruise(self):
        """RPM spread at cruise speed should not trigger idle rule."""
        state = VehicleState()
        for i in range(150):
            state.rpm.append(2400.0 if i % 2 == 0 else 2600.0)
            state.timestamps.append(1000.0 + i * 0.1)
        result = rule_idle_instability(state)
        assert result is None

    def test_returns_none_insufficient_data(self):
        state = make_state(rpm=750, n=50)
        result = rule_idle_instability(state)
        assert result is None


# ── rule_overrev ─────────────────────────────────────────────────────────────

class TestOverrev:
    def test_triggers_above_6500(self):
        state = make_state(rpm=7000)
        result = rule_overrev(state)
        assert result is not None
        assert result[0] == LEVEL_RED
        assert "RPM" in result[1]

    def test_no_trigger_at_redline_boundary(self):
        state = make_state(rpm=6500)
        result = rule_overrev(state)
        assert result is None

    def test_no_trigger_normal_rpm(self):
        state = make_state(rpm=3000)
        result = rule_overrev(state)
        assert result is None

    def test_returns_none_with_no_data(self):
        state = VehicleState()
        result = rule_overrev(state)
        assert result is None


# ── VehicleState helpers ─────────────────────────────────────────────────────

class TestVehicleState:
    def test_avg_returns_none_on_empty(self):
        state = VehicleState()
        assert state.avg(state.rpm) is None

    def test_avg_last_n_samples(self):
        state = make_state(rpm=1000, n=100)
        assert state.avg(state.rpm, 50) == 1000.0

    def test_latest_returns_none_on_empty(self):
        state = VehicleState()
        assert state.latest(state.coolant) is None

    def test_latest_returns_most_recent(self):
        state = VehicleState()
        for v in [10, 20, 30]:
            state.rpm.append(v)
        assert state.latest(state.rpm) == 30

    def test_sustained_above(self):
        state = make_state(stft1=15.0, n=100)
        assert state.sustained_above(state.stft1, 12.0, 50) is True

    def test_sustained_above_false_when_one_below(self):
        state = make_state(stft1=15.0, n=99)
        state.stft1.append(10.0)  # one sample below threshold
        assert state.sustained_above(state.stft1, 12.0, 50) is False

    def test_sustained_below(self):
        state = make_state(stft1=-15.0, n=200)
        assert state.sustained_below(state.stft1, -12.0, 150) is True

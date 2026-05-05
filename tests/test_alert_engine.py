#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Alert Engine Unit Tests
Tests all 23 diagnostic rules for correctness.
Run: pytest tests/test_alert_engine.py -v
UNCAGED TECHNOLOGY — EST 1991
"""

import time
import pytest
from collections import deque

from config import (
    LEVEL_OK, LEVEL_INFO, LEVEL_AMBER, LEVEL_RED,
    THRESHOLDS, CALIBRATION_DEFAULTS,
    WARMUP_COOLANT_THRESHOLD, WARMUP_COOLANT_TARGET,
)
from alert_engine import (
    VehicleState,
    rule_vacuum_leak_bank1,
    rule_vacuum_leak_both,
    rule_coolant_critical,
    rule_running_rich,
    rule_alternator,
    rule_idle_instability,
    rule_overrev,
    rule_ltft_drift,
    rule_bank_imbalance,
    rule_intake_temp,
    rule_voltage_overcharge,
    rule_active_dtcs,
    rule_stalled,
    rule_tpms_low_pressure,
    rule_tpms_rapid_loss,
    rule_tpms_temp,
    rule_xtype_thermostat,
    rule_xtype_coil_pack,
    rule_xtype_maf_degradation,
    rule_xtype_throttle_body,
    rule_xtype_cold_start,
    rule_xtype_alternator_age,
    rule_xtype_warmup_progress,
    ALL_RULES,
    calibration,
)
import alert_engine


# ── Fixtures ──

@pytest.fixture(autouse=True)
def reset_state():
    """Reset global state between tests to prevent leakage."""
    alert_engine.engine_start_time = 0.0
    alert_engine.warmup_complete = False
    yield
    alert_engine.engine_start_time = 0.0
    alert_engine.warmup_complete = False

@pytest.fixture
def state():
    """Fresh VehicleState for each test."""
    return VehicleState()


def fill(buf, value, count=100, ts_buf=None, ts_start=None):
    """Fill a deque with `count` copies of `value`."""
    start = ts_start or time.time() - count
    for i in range(count):
        buf.append(value)
        if ts_buf is not None:
            ts_buf.append(start + i)


def fill_range(buf, values, ts_buf=None, ts_start=None):
    """Fill a deque with a list of values."""
    start = ts_start or time.time() - len(values)
    for i, v in enumerate(values):
        buf.append(v)
        if ts_buf is not None:
            ts_buf.append(start + i)


# ── Test: Rule Count ──

def test_rule_count():
    """Verify we have exactly 23 diagnostic rules."""
    assert len(ALL_RULES) == 23


# ── Test: trend() per-sensor timestamp alignment ──

class TestTrendAlignment:
    def test_trend_uses_per_sensor_ts_buf(self, state):
        """trend() must use ts_buf, not the shared timestamps deque.

        Simulate the real scenario: coolant is 1Hz, total messages arrive at
        5Hz (5 PIDs).  Without per-sensor timestamps the time window would be
        5× too short and the slope would be 5× too large.
        """
        base_time = 1_000_000.0
        # Add 50 coolant readings at 1-second intervals (50 s span, +1°C each)
        for i in range(50):
            state.coolant.append(70.0 + i)
            state.coolant_ts.append(base_time + i * 1.0)

        # Pollute shared timestamps: 5 messages per second (250 entries total)
        for i in range(250):
            state.timestamps.append(base_time + i * 0.2)

        # Correct slope: 1°C/s  (49°C rise over 49 s)
        trend_correct = state.trend(state.coolant, ts_buf=state.coolant_ts)
        assert abs(trend_correct - 1.0) < 0.05, f"Expected ~1.0°C/s, got {trend_correct}"

        # Misaligned slope using shared timestamps: 5× faster "time" → slope ~5×
        trend_wrong = state.trend(state.coolant)   # falls back to timestamps
        # The misaligned version should give a very different (much larger) value
        assert abs(trend_wrong) > abs(trend_correct) * 3, (
            f"Misaligned trend should be much larger than {trend_correct}, got {trend_wrong}"
        )

    def test_trend_returns_zero_when_ts_buf_too_short(self, state):
        """trend() with fewer than 10 per-sensor timestamps returns 0."""
        for i in range(5):
            state.coolant.append(80.0 + i)
            state.coolant_ts.append(1_000_000.0 + i)
        assert state.trend(state.coolant, ts_buf=state.coolant_ts) == 0

    def test_trend_epsilon_guard_for_identical_timestamps(self, state):
        """trend() must not divide by zero when all timestamps are identical."""
        t = 1_000_000.0
        for _ in range(20):
            state.coolant.append(90.0)
            state.coolant_ts.append(t)  # all same
        assert state.trend(state.coolant, ts_buf=state.coolant_ts) == 0


# ── Test: None Handling ──

def test_all_rules_return_none_on_empty_state(state):
    """Every rule must return None when no data has been received."""
    for rule in ALL_RULES:
        result = rule(state)
        assert result is None, f"{rule.__name__} should return None on empty state"


# ── Core Rule Tests ──

class TestVacuumLeakBank1:
    def test_triggers_on_bank1_lean(self, state):
        fill(state.stft1, 15.0, 50, state.timestamps)
        fill(state.stft2, 2.0, 50)
        fill(state.rpm, 750, 50)
        fill(state.coolant, 90, 10)
        result = rule_vacuum_leak_bank1(state)
        assert result is not None
        assert result[0] == LEVEL_AMBER
        assert "Bank 1" in result[1]

    def test_suppressed_during_cold_start(self, state):
        fill(state.stft1, 15.0, 50, state.timestamps)
        fill(state.stft2, 2.0, 50)
        fill(state.rpm, 750, 50)
        fill(state.coolant, 40, 10)  # Below WARMUP_COOLANT_THRESHOLD
        result = rule_vacuum_leak_bank1(state)
        assert result is None

    def test_no_trigger_at_normal_stft(self, state):
        fill(state.stft1, 3.0, 50, state.timestamps)
        fill(state.stft2, 2.0, 50)
        fill(state.rpm, 750, 50)
        fill(state.coolant, 90, 10)
        result = rule_vacuum_leak_bank1(state)
        assert result is None


class TestVacuumLeakBoth:
    def test_triggers_both_banks_lean(self, state):
        fill(state.stft1, 15.0, 50, state.timestamps)
        fill(state.stft2, 15.0, 50)
        fill(state.rpm, 750, 50)
        fill(state.coolant, 90, 10)
        result = rule_vacuum_leak_both(state)
        assert result is not None
        assert result[0] == LEVEL_AMBER
        assert "BOTH" in result[1]


class TestCoolantCritical:
    def test_red_at_108(self, state):
        fill(state.coolant, 110, 10, state.coolant_ts)
        result = rule_coolant_critical(state)
        assert result is not None
        assert result[0] == LEVEL_RED

    def test_amber_at_104(self, state):
        fill(state.coolant, 105, 10, state.coolant_ts)
        result = rule_coolant_critical(state)
        assert result is not None
        assert result[0] == LEVEL_AMBER

    def test_ok_at_normal(self, state):
        fill(state.coolant, 92, 10, state.coolant_ts)
        result = rule_coolant_critical(state)
        assert result is None


class TestRunningRich:
    def test_triggers_on_sustained_rich(self, state):
        fill(state.stft1, -14.0, 200, state.timestamps)
        fill(state.stft2, -2.0, 200)
        result = rule_running_rich(state)
        assert result is not None
        assert result[0] == LEVEL_AMBER

    def test_no_trigger_on_short_rich(self, state):
        fill(state.stft1, -14.0, 50, state.timestamps)
        fill(state.stft2, -2.0, 50)
        result = rule_running_rich(state)
        assert result is None


class TestAlternator:
    def test_undercharge_amber(self, state):
        fill(state.voltage, 12.8, 30, state.timestamps)
        fill(state.rpm, 2000, 30)
        result = rule_alternator(state)
        assert result is not None
        assert result[0] == LEVEL_AMBER

    def test_critical_voltage(self, state):
        fill(state.voltage, 11.5, 30, state.timestamps)
        fill(state.rpm, 800, 30)
        result = rule_alternator(state)
        assert result is not None
        assert result[0] == LEVEL_RED

    def test_ok_at_normal_voltage(self, state):
        fill(state.voltage, 14.2, 30, state.timestamps)
        fill(state.rpm, 2000, 30)
        result = rule_alternator(state)
        assert result is None


class TestIdleInstability:
    def test_triggers_on_rpm_spread(self, state):
        for i in range(100):
            state.rpm.append(650 if i % 2 == 0 else 900)
            state.timestamps.append(time.time() - 100 + i)
        result = rule_idle_instability(state)
        assert result is not None
        assert result[0] == LEVEL_INFO


class TestOverrev:
    def test_triggers_above_redline(self, state):
        state.rpm.append(6800)
        result = rule_overrev(state)
        assert result is not None
        assert result[0] == LEVEL_RED

    def test_ok_below_redline(self, state):
        state.rpm.append(6000)
        result = rule_overrev(state)
        assert result is None


class TestLTFTDrift:
    def test_red_on_maxed_ltft(self, state):
        fill(state.ltft1, 26.0, 30, state.timestamps)
        fill(state.ltft2, 1.0, 30)
        result = rule_ltft_drift(state)
        assert result is not None
        assert result[0] == LEVEL_RED

    def test_amber_on_drifted_ltft(self, state):
        fill(state.ltft1, 16.0, 30, state.timestamps)
        fill(state.ltft2, 1.0, 30)
        result = rule_ltft_drift(state)
        assert result is not None
        assert result[0] == LEVEL_AMBER


class TestBankImbalance:
    def test_triggers_on_divergence(self, state):
        fill(state.stft1, 12.0, 50, state.timestamps)
        fill(state.stft2, -5.0, 50)
        fill(state.rpm, 1500, 50)
        result = rule_bank_imbalance(state)
        assert result is not None
        assert result[0] == LEVEL_AMBER


class TestIntakeTemp:
    def test_critical_iat(self, state):
        state.iat.append(70)
        result = rule_intake_temp(state)
        assert result is not None
        assert result[0] == LEVEL_AMBER

    def test_ok_iat(self, state):
        state.iat.append(35)
        result = rule_intake_temp(state)
        assert result is None


class TestVoltageOvercharge:
    def test_overcharge_red(self, state):
        fill(state.voltage, 16.0, 30, state.timestamps)
        fill(state.rpm, 2000, 30)
        result = rule_voltage_overcharge(state)
        assert result is not None
        assert result[0] == LEVEL_RED


class TestActiveDTCs:
    def test_known_dtc(self, state):
        state.active_dtcs = ['P0301']
        result = rule_active_dtcs(state)
        assert result is not None
        assert "P0301" in result[1]
        assert "Cylinder 1 Misfire" in result[1]

    def test_unknown_dtc(self, state):
        state.active_dtcs = ['P9999']
        result = rule_active_dtcs(state)
        assert result is not None
        assert result[0] == LEVEL_AMBER

    def test_no_dtcs(self, state):
        result = rule_active_dtcs(state)
        assert result is None


class TestStalled:
    def test_stall_detected(self, state):
        for _ in range(97):
            state.rpm.append(750)
        for _ in range(3):
            state.rpm.append(0)
        state.voltage.append(12.5)
        result = rule_stalled(state)
        assert result is not None
        assert result[0] == LEVEL_RED


# ── TPMS Rule Tests ──

class TestTPMSLowPressure:
    def test_critical_low(self, state):
        state.tpms = {'fl': {'pressure_psi': 18, 'ts': time.time()}}
        result = rule_tpms_low_pressure(state)
        assert result is not None
        assert result[0] == LEVEL_RED

    def test_warning_low(self, state):
        state.tpms = {'fr': {'pressure_psi': 24, 'ts': time.time()}}
        result = rule_tpms_low_pressure(state)
        assert result is not None
        assert result[0] == LEVEL_AMBER

    def test_ok_pressure(self, state):
        state.tpms = {'fl': {'pressure_psi': 30, 'ts': time.time()}}
        result = rule_tpms_low_pressure(state)
        assert result is None


class TestTPMSRapidLoss:
    def test_rapid_drop(self, state):
        now = time.time()
        state.tpms_history['fl'].append((now - 200, 32))
        state.tpms_history['fl'].append((now - 100, 30))
        state.tpms_history['fl'].append((now, 28))
        state.tpms = {'fl': {'pressure_psi': 28, 'ts': now}}
        result = rule_tpms_rapid_loss(state)
        assert result is not None
        assert result[0] == LEVEL_RED


class TestTPMSTemp:
    def test_critical_temp(self, state):
        state.tpms = {'rl': {'temp_c': 105, 'ts': time.time()}}
        result = rule_tpms_temp(state)
        assert result is not None
        assert result[0] == LEVEL_RED

    def test_warning_temp(self, state):
        state.tpms = {'rr': {'temp_c': 85, 'ts': time.time()}}
        result = rule_tpms_temp(state)
        assert result is not None
        assert result[0] == LEVEL_AMBER


# ── X-Type Specific Rule Tests ──

class TestXTypeThermostat:
    def test_oscillation_detected(self, state):
        # Fill with oscillating coolant temps (swing > 8°C)
        for i in range(200):
            temp = 88 + (6 if i % 20 < 10 else -6)
            state.coolant.append(temp)
            state.timestamps.append(time.time() - 200 + i)
        fill(state.rpm, 2000, 200)
        result = rule_xtype_thermostat(state)
        assert result is not None
        assert result[0] == LEVEL_AMBER
        assert "thermostat" in result[1].lower() or "cycling" in result[1].lower()


class TestXTypeCoilPack:
    def test_rpm_stumble_under_load(self, state):
        # Mix of high and low RPM under load
        for i in range(100):
            state.rpm.append(2800 if i % 5 != 0 else 2500)
            state.timestamps.append(time.time() - 100 + i)
        fill(state.throttle, 25.0, 100)
        fill(state.coolant, 90, 10)
        fill(state.stft1, 8.0, 30)
        fill(state.stft2, 1.0, 30)
        result = rule_xtype_coil_pack(state)
        assert result is not None
        assert result[0] == LEVEL_AMBER

    def test_suppressed_during_cold_start(self, state):
        for i in range(100):
            state.rpm.append(2800 if i % 5 != 0 else 2500)
            state.timestamps.append(time.time() - 100 + i)
        fill(state.throttle, 25.0, 100)
        fill(state.coolant, 40, 10)  # Cold
        result = rule_xtype_coil_pack(state)
        assert result is None


class TestXTypeMAFDegradation:
    def test_low_maf_at_warm_idle(self, state):
        fill(state.maf, 2.0, 50, state.timestamps)
        fill(state.rpm, 720, 50)
        fill(state.coolant, 90, 10)
        fill(state.ltft1, 10.0, 30)
        fill(state.ltft2, 9.0, 30)
        result = rule_xtype_maf_degradation(state)
        assert result is not None
        assert result[0] == LEVEL_AMBER
        assert "MAF" in result[1]


class TestXTypeThrottleBody:
    def test_throttle_load_mismatch(self, state):
        fill(state.throttle, 30.0, 30, state.timestamps)
        fill(state.load, 10.0, 30)
        fill(state.rpm, 1500, 30)
        fill(state.coolant, 90, 10)
        result = rule_xtype_throttle_body(state)
        assert result is not None
        assert result[0] == LEVEL_AMBER

    def test_ok_when_cold(self, state):
        fill(state.throttle, 30.0, 30, state.timestamps)
        fill(state.load, 10.0, 30)
        fill(state.rpm, 1500, 30)
        fill(state.coolant, 50, 10)  # Cold — suppressed
        result = rule_xtype_throttle_body(state)
        assert result is None


class TestXTypeColdStart:
    def test_reports_fast_idle(self, state):
        state.coolant.append(25)
        fill(state.rpm, 1200, 20, state.timestamps)
        result = rule_xtype_cold_start(state)
        assert result is not None
        assert result[0] == LEVEL_INFO
        assert "Cold start" in result[1] or "fast idle" in result[1].lower()

    def test_warns_low_cold_idle(self, state):
        state.coolant.append(20)
        fill(state.rpm, 400, 20, state.timestamps)
        result = rule_xtype_cold_start(state)
        assert result is not None
        assert result[0] == LEVEL_AMBER

    def test_no_trigger_when_warm(self, state):
        state.coolant.append(90)
        fill(state.rpm, 750, 20, state.timestamps)
        result = rule_xtype_cold_start(state)
        assert result is None


class TestXTypeAlternatorAge:
    def test_marginal_voltage_with_falling_trend(self, state):
        # Slowly declining voltage over 200 samples
        now = time.time()
        for i in range(200):
            state.voltage.append(13.4 - i * 0.002)
            state.timestamps.append(now - 200 + i)
            state.voltage_ts.append(now - 200 + i)
        fill(state.rpm, 2000, 200)
        result = rule_xtype_alternator_age(state)
        assert result is not None
        assert result[0] == LEVEL_INFO

    def test_ok_at_healthy_voltage(self, state):
        fill(state.voltage, 14.2, 200, state.timestamps)
        fill(state.rpm, 2000, 200)
        result = rule_xtype_alternator_age(state)
        assert result is None


class TestXTypeWarmupProgress:
    def test_warmup_complete_notification(self, state):
        import alert_engine
        # Simulate engine start
        alert_engine.engine_start_time = time.time() - 300
        alert_engine.warmup_complete = False
        state.coolant.append(WARMUP_COOLANT_TARGET)
        state.rpm.append(750)
        result = rule_xtype_warmup_progress(state)
        assert result is not None
        assert result[0] == LEVEL_INFO
        assert "Warmup complete" in result[1]

#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Alert Engine Hysteresis & Evaluate Tests
Tests the new hysteresis and multi-alert publishing logic.
Run: pytest tests/test_alert_hysteresis.py -v
UNCAGED TECHNOLOGY — EST 1991
"""

import time
import json
import pytest
from unittest.mock import MagicMock, call
from collections import deque

from config import LEVEL_OK, LEVEL_INFO, LEVEL_AMBER, LEVEL_RED, LEVEL_NAMES
import alert_engine


@pytest.fixture(autouse=True)
def reset_alert_state():
    """Reset all alert engine state between tests to prevent leaks."""
    alert_engine.state = alert_engine.VehicleState()
    alert_engine.current_alert_level = LEVEL_OK
    alert_engine.current_alert_msg = "Systems nominal"
    alert_engine.last_alert_time = 0
    alert_engine._active_alerts.clear()
    alert_engine._clear_counters.clear()
    alert_engine.engine_start_time = 0.0
    alert_engine.warmup_complete = False
    yield


def fill(buf, value, count=100, ts_buf=None):
    """Fill a deque with `count` copies of `value`."""
    start = time.time() - count
    for i in range(count):
        buf.append(value)
        if ts_buf is not None:
            ts_buf.append(start + i)


class TestHysteresis:
    """Test that alerts require HYSTERESIS_CYCLES clears before removal."""

    def test_alert_stays_active_during_hysteresis(self):
        """An alert triggered once should not disappear immediately when cleared."""
        mqtt = MagicMock()
        state = alert_engine.state

        # Trigger overheat
        fill(state.coolant, 115.0, 100, state.coolant_ts)
        fill(state.rpm, 800.0, 100)
        state.timestamps = state.coolant_ts

        alert_engine.evaluate_rules(mqtt)
        assert alert_engine.current_alert_level >= LEVEL_AMBER

        # Now coolant drops to normal — but we're in hysteresis window
        state.coolant.clear()
        state.coolant_ts.clear()
        fill(state.coolant, 85.0, 100, state.coolant_ts)
        alert_engine.last_alert_time = 0  # bypass cooldown

        alert_engine.evaluate_rules(mqtt)
        # Alert should STILL be active (hysteresis cycle 1 of 3)
        assert len(alert_engine._active_alerts) > 0 or alert_engine.current_alert_level >= LEVEL_AMBER

    def test_alert_clears_after_hysteresis_cycles(self):
        """After HYSTERESIS_CYCLES clears, the alert should be removed."""
        mqtt = MagicMock()
        state = alert_engine.state

        # Trigger overrev
        fill(state.rpm, 7500.0, 100)
        fill(state.coolant, 90.0, 100, state.coolant_ts)
        state.timestamps = state.coolant_ts

        alert_engine.evaluate_rules(mqtt)
        assert alert_engine.current_alert_level > LEVEL_OK

        # Clear the condition
        state.rpm.clear()
        fill(state.rpm, 800.0, 100)

        # Run enough evaluations to clear hysteresis
        for _ in range(alert_engine.HYSTERESIS_CYCLES + 1):
            alert_engine.last_alert_time = 0
            alert_engine.evaluate_rules(mqtt)

        # Now it should be clear
        assert len(alert_engine._active_alerts) == 0


class TestMultiAlert:
    """Test that multiple simultaneous alerts are published."""

    def test_multiple_alerts_published(self):
        """When multiple rules trigger, all should appear in active alerts."""
        mqtt = MagicMock()
        state = alert_engine.state

        # Trigger both overheat AND overrev simultaneously
        fill(state.coolant, 115.0, 100, state.coolant_ts)
        fill(state.rpm, 7500.0, 100)
        fill(state.stft1, 0.0, 100)
        fill(state.stft2, 0.0, 100)
        fill(state.ltft1, 0.0, 100)
        fill(state.ltft2, 0.0, 100)
        fill(state.voltage, 14.0, 100, state.voltage_ts)
        state.timestamps = state.coolant_ts

        alert_engine.evaluate_rules(mqtt)

        # Check that drifter/alert/active was published with multiple alerts
        active_calls = [
            c for c in mqtt.publish.call_args_list
            if c[0][0] == 'drifter/alert/active'
        ]
        assert len(active_calls) > 0, "No active alerts published"
        payload = json.loads(active_calls[-1][0][1])
        assert payload['count'] >= 2, f"Expected >= 2 alerts, got {payload['count']}"


class TestEvaluateCooldown:
    """Test the ALERT_COOLDOWN mechanism."""

    def test_cooldown_prevents_rapid_fire(self):
        """evaluate_rules should skip if called within ALERT_COOLDOWN."""
        mqtt = MagicMock()

        # First call triggers
        alert_engine.last_alert_time = time.time()
        fill(alert_engine.state.coolant, 115.0, 100, alert_engine.state.coolant_ts)
        fill(alert_engine.state.rpm, 800.0, 100)
        alert_engine.state.timestamps = alert_engine.state.coolant_ts

        # Should skip due to cooldown
        alert_engine.evaluate_rules(mqtt)
        assert mqtt.publish.call_count == 0

    def test_evaluation_runs_after_cooldown(self):
        """evaluate_rules should run after cooldown expires."""
        mqtt = MagicMock()

        # Set last_alert_time in the past (beyond cooldown)
        alert_engine.last_alert_time = time.time() - alert_engine.ALERT_COOLDOWN - 1
        fill(alert_engine.state.coolant, 115.0, 100, alert_engine.state.coolant_ts)
        fill(alert_engine.state.rpm, 800.0, 100)
        alert_engine.state.timestamps = alert_engine.state.coolant_ts

        alert_engine.evaluate_rules(mqtt)
        assert mqtt.publish.call_count > 0

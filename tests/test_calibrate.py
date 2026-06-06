"""Regression test for calibrate idle-RPM baseline.

The idle baseline must be computed from idle-only RPM samples. Earlier it
averaged every RPM reading (including revs/blips during the calibration
window), which pulled the baseline upward and masked real idle-instability
faults downstream.
"""
from __future__ import annotations

import json
import sys
from unittest.mock import MagicMock

sys.path.insert(0, 'src')

from calibrate import IDLE_RPM_MAX, CalibrationCollector


def _msg(topic, value):
    m = MagicMock()
    m.topic = topic
    m.payload = json.dumps({'value': value}).encode()
    return m


def test_idle_rpm_baseline_ignores_revs():
    c = CalibrationCollector()
    # 100 clean idle samples at ~780 RPM...
    for _ in range(100):
        c.on_message(None, None, _msg('drifter/engine/rpm', 780))
    # ...plus a handful of revs that must NOT drag the idle baseline up.
    for _ in range(20):
        c.on_message(None, None, _msg('drifter/engine/rpm', 3500))

    cal = c.compute_calibration()
    # Idle baseline should sit at the idle level, not the mixed average
    # (which would be ~1233 with the revs folded in).
    assert cal['idle_rpm_baseline'] == 780
    assert all(v < IDLE_RPM_MAX for v in c.rpm_idle)
    # The full rpm deque still captured everything (used for stats/len).
    assert len(c.rpm) == 120


def test_idle_baseline_falls_back_to_rpm_when_no_idle_samples():
    c = CalibrationCollector()
    for _ in range(10):
        c.on_message(None, None, _msg('drifter/engine/rpm', 3500))
    cal = c.compute_calibration()
    # No idle samples collected — fall back to the full deque rather than 0.
    assert cal['idle_rpm_baseline'] == 3500

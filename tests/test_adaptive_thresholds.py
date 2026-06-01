# tests/test_adaptive_thresholds.py
"""Smoke tests for adaptive_thresholds: baseline learning and drift cap."""
import sys

sys.path.insert(0, 'src')

from unittest.mock import patch

import pytest


@pytest.fixture()
def fresh_learner():
    """Return a fresh Learner with no persisted state."""
    from adaptive_thresholds import Learner
    with patch('adaptive_thresholds.STATE_FILE') as mock_path:
        mock_path.exists.return_value = False
        learner = Learner()
    return learner


def _warm_idle_learner(learner):
    """Configure learner as if engine is warm and idling."""
    learner.current_coolant = 80.0   # above WARMUP_COOLANT_THRESHOLD (60)
    learner.current_rpm = 750.0      # in 500-1000 range
    learner.current_speed = 0.0


def test_ingest_accepted_when_warm_idle(fresh_learner):
    _warm_idle_learner(fresh_learner)
    fresh_learner.ingest('rpm', 750.0)
    assert len(fresh_learner.samples['rpm']) == 1


def test_ingest_rejected_when_cold(fresh_learner):
    fresh_learner.current_coolant = 40.0  # below warmup threshold
    fresh_learner.current_rpm = 750.0
    fresh_learner.current_speed = 0.0
    fresh_learner.ingest('rpm', 750.0)
    assert len(fresh_learner.samples.get('rpm', [])) == 0


def test_ingest_rejected_when_moving(fresh_learner):
    _warm_idle_learner(fresh_learner)
    fresh_learner.current_speed = 50.0
    fresh_learner.ingest('rpm', 750.0)
    assert len(fresh_learner.samples.get('rpm', [])) == 0


def test_ingest_unknown_key_ignored(fresh_learner):
    _warm_idle_learner(fresh_learner)
    fresh_learner.ingest('unknown_sensor', 42.0)
    assert 'unknown_sensor' not in fresh_learner.samples


def test_end_session_respects_drift_cap(fresh_learner):
    """Baseline should not drift beyond ADAPTIVE_DRIFT_LIMIT from default."""
    from adaptive_thresholds import DEFAULT_BASELINES
    from config import ADAPTIVE_DRIFT_LIMIT
    _warm_idle_learner(fresh_learner)
    # Feed 100 voltage samples far above default (14.2) — e.g. 99.9V (absurd)
    for _ in range(100):
        fresh_learner.ingest('voltage', 99.9)
    baselines = fresh_learner.end_session()
    default = DEFAULT_BASELINES['voltage_baseline']
    scale = max(abs(default), 1.0)
    max_allowed = default + ADAPTIVE_DRIFT_LIMIT * scale
    assert baselines['voltage_baseline'] <= max_allowed + 0.001


def test_end_session_insufficient_samples_no_change(fresh_learner):
    """With fewer samples than the warmup minimum, baselines stay at default."""
    from adaptive_thresholds import DEFAULT_BASELINES
    _warm_idle_learner(fresh_learner)
    # Only 1 sample — way below threshold
    fresh_learner.ingest('rpm', 750.0)
    baselines = fresh_learner.end_session()
    assert baselines['idle_rpm_baseline'] == DEFAULT_BASELINES['idle_rpm_baseline']


def test_end_session_increments_session_count(fresh_learner):
    from config import ADAPTIVE_LEARN_MIN_SAMPLES
    _warm_idle_learner(fresh_learner)
    # Feed enough samples for each key
    samples_per_key = max(ADAPTIVE_LEARN_MIN_SAMPLES, 20)
    for key in ('stft1', 'stft2', 'ltft1', 'ltft2', 'rpm', 'voltage', 'maf'):
        for _ in range(samples_per_key):
            fresh_learner.ingest(key, 0.0)
    initial = fresh_learner.session_count
    fresh_learner.end_session()
    assert fresh_learner.session_count == initial + 1

"""Test fly_catcher genuine/spoofed convention for the sklearn path.

Training convention (documented in-module): label 1 == spoofed, so class 0 is
genuine. The sklearn predict_proba path must therefore read proba[0], not
proba[-1] (which is P(spoofed) and would invert the verdict).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, 'src')

from fly_catcher import FlyCatcher


class _FakeSklearn:
    def __init__(self, proba):
        self._proba = proba

    def predict_proba(self, _X):
        return [self._proba]


def _catcher_with(model):
    c = FlyCatcher(model_dir=Path('/nonexistent-model-dir'))
    c.available = True
    c._tflite_input_idx = None  # force the non-tflite branch
    c.model = model
    return c


def test_genuine_aircraft_not_flagged():
    # proba = [P(genuine)=0.9, P(spoofed)=0.1]
    c = _catcher_with(_FakeSklearn([0.9, 0.1]))
    res = c.classify({'icao': 'abc123', 'lat': -36.7, 'lon': 144.3})
    assert res['genuine_prob'] == 0.9
    assert res['suspect'] is False


def test_spoofed_aircraft_flagged():
    # proba = [P(genuine)=0.1, P(spoofed)=0.9]
    c = _catcher_with(_FakeSklearn([0.1, 0.9]))
    res = c.classify({'icao': 'def456', 'lat': -36.7, 'lon': 144.3})
    assert res['genuine_prob'] == 0.1
    assert res['suspect'] is True

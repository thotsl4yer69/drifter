"""Tests for urh_classifier — heuristic fallback + IQ I/O contract.

The URH-NG path is exercised when URH is importable; the heuristic path
is exercised unconditionally so the bench can validate empty-state
behaviour even without scipy/PyQt installed.
"""
from __future__ import annotations

import math
import struct
from pathlib import Path

import urh_classifier as uc

# ── _read_iq_complex ──────────────────────────────────────────────────

def _write_iq_file(path: Path, samples):
    """Write interleaved float32 I/Q to disk in URH's .complex format."""
    flat = []
    for (i, q) in samples:
        flat.append(i)
        flat.append(q)
    path.write_bytes(struct.pack('<' + 'f' * len(flat), *flat))


def test_read_iq_missing_returns_none(tmp_path):
    assert uc._read_iq_complex(tmp_path / 'missing.complex') is None


def test_read_iq_empty_returns_none(tmp_path):
    p = tmp_path / 'empty.complex'
    p.write_bytes(b'')
    assert uc._read_iq_complex(p) is None


def test_read_iq_roundtrip(tmp_path):
    samples = [(0.1, 0.2), (0.3, 0.4), (0.5, 0.6)]
    p = tmp_path / 'ok.complex'
    _write_iq_file(p, samples)
    got = uc._read_iq_complex(p)
    assert got is not None
    assert len(got) == 3
    assert math.isclose(got[0][0], 0.1, abs_tol=1e-6)
    assert math.isclose(got[2][1], 0.6, abs_tol=1e-6)


# ── _heuristic_classify ───────────────────────────────────────────────

def test_heuristic_classifies_ook_burst_as_ask(tmp_path):
    """A bimodal amplitude signal (clean OOK) should land as ASK."""
    # 256 samples: alternating high-amplitude / low-amplitude bursts.
    samples = []
    for i in range(256):
        if (i // 16) % 2 == 0:
            samples.append((0.8, 0.0))  # high-amplitude lobe
        else:
            samples.append((0.02, 0.0))  # low-amplitude lobe
    result = uc._heuristic_classify(samples)
    assert result['modulation'] == 'ASK'
    assert result['confidence'] > 0.4


def test_heuristic_classifies_noise_with_low_confidence(tmp_path):
    """Random low-amplitude noise should classify as UNKNOWN with conf < 0.3."""
    import random
    random.seed(42)
    samples = [(random.gauss(0, 0.005), random.gauss(0, 0.005))
               for _ in range(256)]
    result = uc._heuristic_classify(samples)
    assert result['modulation'] == 'UNKNOWN'
    assert result['confidence'] < 0.3


def test_heuristic_insufficient_samples():
    result = uc._heuristic_classify([(0.5, 0.5)] * 4)
    assert result['modulation'] == 'UNKNOWN'
    assert result['protocol_guess'] == 'insufficient_samples'


# ── classify() — public contract ──────────────────────────────────────

def test_classify_missing_file_returns_unknown(tmp_path):
    result = uc.classify(tmp_path / 'no.complex', 433_920_000, 250_000)
    assert result['modulation'] == 'UNKNOWN'
    assert result['confidence'] == 0.0
    assert result['frequency_hz'] == 433_920_000
    assert result['protocol_guess'] == 'missing_iq'


def test_classify_with_ook_signal_returns_ask(tmp_path, monkeypatch):
    """End-to-end: write a bimodal IQ file, force the heuristic path, assert ASK."""
    samples = []
    for i in range(256):
        amp = 0.8 if (i // 16) % 2 == 0 else 0.02
        samples.append((amp, 0.0))
    p = tmp_path / 'ook.complex'
    _write_iq_file(p, samples)
    # Force the heuristic path so URH (if installed) doesn't shadow it.
    monkeypatch.setattr(uc, '_urh_available', lambda: False)
    result = uc.classify(p, 433_920_000, 250_000)
    assert result['modulation'] == 'ASK'
    assert result['confidence'] > 0.4


def test_classify_with_noise_low_confidence(tmp_path, monkeypatch):
    import random
    random.seed(7)
    samples = [(random.gauss(0, 0.004), random.gauss(0, 0.004))
               for _ in range(256)]
    p = tmp_path / 'noise.complex'
    _write_iq_file(p, samples)
    monkeypatch.setattr(uc, '_urh_available', lambda: False)
    result = uc.classify(p, 868_000_000, 250_000)
    assert result['confidence'] < 0.3


def test_classify_and_publish_calls_mqtt(tmp_path, monkeypatch):
    from unittest.mock import MagicMock
    p = tmp_path / 'sig.complex'
    _write_iq_file(p, [(0.5, 0.0)] * 32)
    monkeypatch.setattr(uc, '_urh_available', lambda: False)
    client = MagicMock()
    result = uc.classify_and_publish(p, 433_920_000, 250_000, mqtt_client=client)
    assert result['frequency_hz'] == 433_920_000
    client.publish.assert_called_once()
    args, kwargs = client.publish.call_args
    assert args[0] == 'drifter/rf/classification'


def test_classify_and_publish_handles_missing_client(tmp_path, monkeypatch):
    """Publish must be a no-op when mqtt_client is None — not raise."""
    p = tmp_path / 'sig.complex'
    _write_iq_file(p, [(0.5, 0.0)] * 32)
    monkeypatch.setattr(uc, '_urh_available', lambda: False)
    result = uc.classify_and_publish(p, 433_920_000, 250_000, mqtt_client=None)
    assert isinstance(result, dict)
    assert result['frequency_hz'] == 433_920_000

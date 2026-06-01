#!/usr/bin/env python3
"""MZ1312 DRIFTER — URH-NG signal classifier wrapper.

Headless wrapper around Universal Radio Hacker's modulation-analyzer internals.
When rtl_433 emits an UNKNOWN model (or a low-confidence decode), the rf_monitor
dumps a short IQ window to disk and asks this module to classify it.

The wrapper is import-safe even when URH is not installed — every entrypoint
fails closed with a structured payload so the bench can still validate the
empty-state path on a Pi that doesn't ship URH (heavy scipy/numpy/PyQt5 deps
on ARM64). The cockpit renders the "AWAITING UNKNOWN SIGNAL" empty state in
that case and never displays a fake classification — see
feedback_real_data_only.md.

Publishes results to drifter/rf/classification with shape:
  {
    "ts": float,
    "frequency_hz": int,
    "modulation": "ASK"|"FSK"|"PSK"|"MSK"|"UNKNOWN",
    "protocol_guess": str,
    "confidence": float (0.0–1.0),
    "preamble_type": str,
    "encoding": str,
  }

UNCAGED TECHNOLOGY — EST 1991
"""
from __future__ import annotations

import json
import logging
import math
import os
import struct
import time
from pathlib import Path

log = logging.getLogger(__name__)

# Default IQ buffer location — rf_monitor writes 4s windows here on
# unknown-signal trigger. Kept process-local so multiple consumers can
# share the dir without ownership churn.
IQ_BUFFER_DIR = Path('/opt/drifter/state/iq_buffer')

# Probe URH lazily — the heavy scipy/PyQt import cost should only land
# when classify() is actually called, not on module import.
_URH_AVAILABLE: bool | None = None


def _urh_available() -> bool:
    """Return True if URH-NG (or upstream urh) is importable headless."""
    global _URH_AVAILABLE
    if _URH_AVAILABLE is not None:
        return _URH_AVAILABLE
    try:
        # We only need the modulation analyzer — not the Qt GUI.
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        import urh  # noqa: F401
        _URH_AVAILABLE = True
    except Exception as e:
        log.debug("URH not available: %s", e)
        _URH_AVAILABLE = False
    return _URH_AVAILABLE


def _read_iq_complex(path: Path) -> list | None:
    """Load a .complex / .iq file as a list of (I, Q) float pairs.

    Files are interleaved float32 I/Q at the device sample rate. Returns
    None if the file is missing, empty, or malformed.
    """
    try:
        raw = path.read_bytes()
    except OSError as e:
        log.debug("IQ read failed: %s", e)
        return None
    if not raw or len(raw) < 8:
        return None
    # interleaved float32 little-endian
    n_samples = len(raw) // 8
    if n_samples == 0:
        return None
    try:
        flat = struct.unpack('<' + 'f' * (n_samples * 2), raw[:n_samples * 8])
    except struct.error:
        return None
    return [(flat[i], flat[i + 1]) for i in range(0, len(flat), 2)]


def _amplitude_stats(samples: list) -> dict:
    """Compute amplitude envelope statistics for a heuristic fallback."""
    if not samples:
        return {'mean': 0.0, 'var': 0.0, 'n': 0, 'peak': 0.0}
    amps = [math.sqrt(i * i + q * q) for (i, q) in samples]
    n = len(amps)
    mean = sum(amps) / n
    var = sum((a - mean) ** 2 for a in amps) / n
    peak = max(amps)
    return {'mean': mean, 'var': var, 'n': n, 'peak': peak}


def _heuristic_classify(samples: list) -> dict:
    """Cheap fallback classifier when URH isn't installed.

    Discriminates ASK vs noise via amplitude variance: a real OOK/ASK signal
    has a bimodal amplitude distribution (envelope swings high/low), random
    noise has a roughly Gaussian envelope. This is NOT a substitute for URH
    but lets us publish a real result instead of a fake one on a bench Pi
    that lacks the heavy URH deps.
    """
    stats = _amplitude_stats(samples)
    if stats['n'] < 16:
        return {
            'modulation': 'UNKNOWN',
            'protocol_guess': 'insufficient_samples',
            'confidence': 0.0,
            'preamble_type': '',
            'encoding': '',
        }
    mean = stats['mean']
    peak = stats['peak']
    # Bimodality: count samples > 0.6*peak vs < 0.4*peak; a clean ASK
    # signal has both populations heavily occupied.
    high_thresh = 0.6 * peak
    low_thresh = 0.4 * peak
    highs = sum(1 for (i, q) in samples if math.sqrt(i * i + q * q) > high_thresh)
    lows = sum(1 for (i, q) in samples if math.sqrt(i * i + q * q) < low_thresh)
    n = stats['n']
    high_frac = highs / n
    low_frac = lows / n
    # Bimodal signature: both lobes occupied by at least 15% of samples.
    if high_frac > 0.15 and low_frac > 0.15 and peak > 0.05:
        confidence = min(0.85, 0.4 + 2 * min(high_frac, low_frac))
        return {
            'modulation': 'ASK',
            'protocol_guess': 'ook_burst',
            'confidence': round(confidence, 3),
            'preamble_type': '',
            'encoding': 'unknown',
        }
    # Otherwise — looks like noise or a constant-envelope signal we can't
    # discriminate without a real modulation analyzer.
    if mean < 0.02:
        return {
            'modulation': 'UNKNOWN',
            'protocol_guess': 'silence',
            'confidence': 0.0,
            'preamble_type': '',
            'encoding': '',
        }
    return {
        'modulation': 'UNKNOWN',
        'protocol_guess': 'noise',
        'confidence': round(min(0.3, mean * 2), 3),
        'preamble_type': '',
        'encoding': '',
    }


def _urh_classify(iq_path: Path, sample_rate: int) -> dict:
    """Call URH's modulation analyzer headlessly on an IQ file.

    Best-effort import path — URH exposes its protocol analyzer through
    urh.signalprocessing.ProtocolAnalyzer + urh.signalprocessing.Signal.
    Any import failure here drops us into the heuristic path.
    """
    try:
        from urh.signalprocessing.ProtocolAnalyzer import ProtocolAnalyzer
        from urh.signalprocessing.Signal import Signal
    except Exception as e:
        log.debug("URH ProtocolAnalyzer unavailable: %s", e)
        return {}
    try:
        signal = Signal(str(iq_path), 'classified')
        signal.sample_rate = sample_rate
        analyzer = ProtocolAnalyzer(signal)
        analyzer.get_protocol_from_signal()
        # URH's auto-detect returns a ModulationType enum; map to our schema.
        mod_type = getattr(signal, 'modulation_type', 'ASK')
        mod_str = str(mod_type).split('.')[-1].upper()
        if mod_str not in {'ASK', 'FSK', 'PSK', 'MSK'}:
            mod_str = 'UNKNOWN'
        # Confidence is not a first-class URH output; derive a proxy from
        # message count / consistency.
        msgs = getattr(analyzer, 'messages', []) or []
        confidence = min(0.95, 0.5 + 0.05 * len(msgs)) if msgs else 0.3
        protocol_guess = ''
        if msgs:
            # First decoded message's name (or its hex prefix) is the closest
            # thing URH gives us to a protocol_guess.
            first = msgs[0]
            protocol_guess = getattr(first, 'message_type', None) or ''
            if not protocol_guess:
                bits = getattr(first, 'plain_bits_str', '') or ''
                protocol_guess = f'bits:{bits[:16]}' if bits else 'unknown'
        return {
            'modulation': mod_str,
            'protocol_guess': str(protocol_guess) or 'unknown',
            'confidence': round(confidence, 3),
            'preamble_type': (getattr(signal, 'pause_threshold', '') and 'auto') or '',
            'encoding': getattr(signal, 'modulation_type_str', '') or '',
        }
    except Exception as e:
        log.warning("URH classify failed: %s", e)
        return {}


def classify(iq_path, frequency_hz: int, sample_rate: int) -> dict:
    """Classify one IQ window. Returns a fully-formed schema dict.

    Never raises — failures are encoded as modulation=UNKNOWN with
    confidence=0.0 so downstream consumers (MQTT publish, API endpoint)
    always see a well-shaped payload.
    """
    path = Path(iq_path)
    base = {
        'ts': time.time(),
        'frequency_hz': int(frequency_hz),
        'modulation': 'UNKNOWN',
        'protocol_guess': '',
        'confidence': 0.0,
        'preamble_type': '',
        'encoding': '',
    }
    if not path.exists():
        base['protocol_guess'] = 'missing_iq'
        return base

    # Try URH if available; fall back to the cheap heuristic otherwise.
    if _urh_available():
        result = _urh_classify(path, sample_rate)
        if result:
            base.update(result)
            return base
    samples = _read_iq_complex(path)
    if samples is None:
        base['protocol_guess'] = 'unreadable_iq'
        return base
    base.update(_heuristic_classify(samples))
    return base


def classify_and_publish(iq_path, frequency_hz: int, sample_rate: int,
                        mqtt_client=None, topic: str = 'drifter/rf/classification') -> dict:
    """Classify and publish to MQTT. Safe to call from a worker thread."""
    result = classify(iq_path, frequency_hz, sample_rate)
    if mqtt_client is not None:
        try:
            mqtt_client.publish(topic, json.dumps(result), qos=0, retain=False)
        except Exception as e:
            log.warning("classification publish failed: %s", e)
    return result

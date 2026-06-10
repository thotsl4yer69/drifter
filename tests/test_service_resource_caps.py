"""Enforce the RAM-survival OOM-priority + MemoryMax scheme on the unit files.

The 8 GB Pi must never let a runaway heavy service take the whole node down,
and when memory IS exhausted the kernel OOM-killer must sacrifice the heavy
AI/ML/voice services FIRST while keeping the vehicle-diagnostics + safety core
alive. That intent is encoded in the .service files as:

  * diagnostics/safety CORE (config.DIAG_SERVICES, minus offsec)
        -> strongly NEGATIVE OOMScoreAdjust  (protected)
  * heavy/sacrificial services (LLM/STT/ML/vision)
        -> POSITIVE OOMScoreAdjust + a MemoryMax cap  (killed first)

These tests parse the unit files directly so the policy can't silently drift.
The core list is pulled from config.DIAG_SERVICES so the test self-updates when
the diagnostics floor changes. Offensive-security services are deliberately
excluded — another owner controls their resource policy.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, 'src')

import config

REPO = Path(__file__).resolve().parent.parent
SERVICES_DIR = REPO / 'services'

# Heavy, non-offsec RAM consumers that must be sacrificed first under memory
# pressure. Each must declare a MemoryMax cap AND a positive OOMScoreAdjust.
HEAVY_SERVICES = [
    'drifter-vivi',         # voice assistant (out-of-process to ollama)
    'drifter-analyst',      # LLM session diagnostics (out-of-process)
    'drifter-reporter',     # post-drive LLM markdown report (out-of-process)
    'drifter-voicein',      # whisper STT in-process (biggest in-proc consumer)
    'drifter-fly-catcher',  # ADS-B ML model in-process
    'drifter-aidiag',       # Tier-2 LLM diagnostics (out-of-process)
    'drifter-vision',       # YOLO/Hailo vision engine
]

# Offsec / recon services are owned elsewhere; never assert on them here.
OFFSEC_SERVICES = {
    'drifter-marauder', 'drifter-hid', 'drifter-rfaudio', 'drifter-wifi-audit',
    'drifter-kismet', 'drifter-kismet-bridge', 'drifter-wardrive',
    'drifter-flipper', 'drifter-opsec', 'drifter-ghost', 'drifter-ghost-voice',
}

# Diagnostics/safety core = the lean diag floor, minus anything offsec.
CORE_SERVICES = [s for s in config.DIAG_SERVICES if s not in OFFSEC_SERVICES]

_MEM_SUFFIX = {'k': 1024, 'm': 1024**2, 'g': 1024**3, 't': 1024**4}


def _unit_text(name: str) -> str:
    path = SERVICES_DIR / f'{name}.service'
    assert path.exists(), f'missing unit file: {path}'
    return path.read_text()


def _directive(text: str, key: str) -> str | None:
    """Return the last non-comment value of `key` in the unit (systemd
    last-wins), or None if unset."""
    value = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('#') or '=' not in stripped:
            continue
        k, _, v = stripped.partition('=')
        if k.strip() == key:
            value = v.strip()
    return value


def _oom_score(text: str) -> int | None:
    raw = _directive(text, 'OOMScoreAdjust')
    return None if raw is None else int(raw)


def _memory_max_bytes(text: str) -> int | None:
    raw = _directive(text, 'MemoryMax')
    if raw is None:
        return None
    m = re.fullmatch(r'(\d+(?:\.\d+)?)\s*([kKmMgGtT]?)', raw)
    assert m, f'unparseable MemoryMax={raw!r}'
    num, suffix = m.groups()
    return int(float(num) * _MEM_SUFFIX.get(suffix.lower(), 1))


# --- sanity on the lists themselves ----------------------------------------

def test_core_and_heavy_disjoint():
    assert not (set(CORE_SERVICES) & set(HEAVY_SERVICES)), \
        'a service cannot be both diag-core and heavy/sacrificial'


def test_core_list_nonempty():
    # Guards against config.DIAG_SERVICES being emptied/renamed silently.
    assert CORE_SERVICES, 'CORE_SERVICES is empty — DIAG_SERVICES changed?'


# --- heavy services --------------------------------------------------------

def test_heavy_services_have_memory_cap():
    for name in HEAVY_SERVICES:
        text = _unit_text(name)
        cap = _memory_max_bytes(text)
        assert cap is not None, f'{name}: heavy service must set MemoryMax'
        # Sized with headroom but must still cap below the 8G Pi so one
        # runaway can never exhaust it.
        assert cap <= 4 * 1024**3, f'{name}: MemoryMax={cap} too large to protect the Pi'
        assert cap >= 256 * 1024**2, f'{name}: MemoryMax={cap} suspiciously low'


def test_heavy_services_are_sacrificed_first():
    for name in HEAVY_SERVICES:
        score = _oom_score(_unit_text(name))
        assert score is not None, f'{name}: heavy service must set OOMScoreAdjust'
        assert score > 0, \
            f'{name}: heavy service must have POSITIVE OOMScoreAdjust, got {score}'


# --- diagnostics / safety core ---------------------------------------------

def test_core_services_are_oom_protected():
    for name in CORE_SERVICES:
        score = _oom_score(_unit_text(name))
        assert score is not None, f'{name}: core service must set OOMScoreAdjust'
        assert score < 0, \
            f'{name}: diag-core service must have NEGATIVE OOMScoreAdjust, got {score}'


def test_core_protected_more_than_heavy_sacrificed():
    """Every core service must rank strictly below every heavy service for the
    OOM-killer (lower score = killed last)."""
    core_scores = [_oom_score(_unit_text(n)) for n in CORE_SERVICES]
    heavy_scores = [_oom_score(_unit_text(n)) for n in HEAVY_SERVICES]
    assert max(core_scores) < min(heavy_scores), \
        'core OOM scores must all be below heavy OOM scores'

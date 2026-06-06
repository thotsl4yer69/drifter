"""Guard against duplicate surveillance bands (double IMSI-catcher alerts)."""
from __future__ import annotations

import sys

sys.path.insert(0, 'src')

from ghost_protocol import SURVEILLANCE_BANDS_MHZ


def test_no_duplicate_band_ranges():
    ranges = [(b['lo'], b['hi']) for b in SURVEILLANCE_BANDS_MHZ]
    assert len(ranges) == len(set(ranges)), \
        "duplicate (lo, hi) band would fire the same alert twice"


def test_band_names_unique():
    names = [b['name'] for b in SURVEILLANCE_BANDS_MHZ]
    assert len(names) == len(set(names))

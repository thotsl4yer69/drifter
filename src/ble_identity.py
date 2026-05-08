#!/usr/bin/env python3
"""
MZ1312 DRIFTER — BLE stable-identity layer (Phase 4.8.1)

Maps a raw detection to a stable identity string. Pure MAC matching
is useless against modern phones — iOS rotates MAC every ~15 min,
AirTags rotate to defeat tracking by anyone but the owner. Some
classes of device DO have stable MACs:

  - Axon body cams / tasers / holsters (LE has no anti-tracking
    incentive — these report stable factory MACs)
  - Tile trackers (stable by design — Tile WANTS to be findable)
  - Any device that broadcasts a non-generic name + manufacturer_id
    combo: the pair is essentially a serial-number fingerprint

Anything else gets a coarse "device class at this OUI" identity that
is documented as weak. The follower-detection layer downstream applies
filters (detection_count < 3, single-drive only, no-GPS-only) that
weed out most noise even at coarse resolution.

UNCAGED TECHNOLOGY — EST 1991
"""
from __future__ import annotations

# Names that don't anchor identity — too many devices share them.
GENERIC_NAMES = {
    'BLE', 'Device', 'Unknown', '', 'iPhone', 'AirPods', 'AirPods Pro',
    'iPad', 'Mac', 'MacBook', 'Apple Watch', 'Watch', 'Headphones',
    'Speaker', 'Phone', 'Earbuds',
}


def _norm(s: object) -> str:
    return str(s or '').strip()


def compute_identity(detection: dict) -> tuple[str, float]:
    """Return (identity_str, confidence). Confidence is per-branch
    hardcoded:

      0.9  — manufacturer + non-generic name fingerprint
      0.85 — stable-MAC class (axon, tile)
      0.4  — Find My / AirTag (acknowledged weak; rotates anyway)
      0.2  — fallback: OUI + target class
    """
    mfr = _norm(detection.get('manufacturer_id'))
    name = _norm(detection.get('adv_name'))
    target = _norm(detection.get('target'))
    mac = _norm(detection.get('mac'))

    # Branch 1: manufacturer + non-generic name. Strongest fingerprint
    # because the pair is essentially a serial: e.g. mfr=0x0006|name=
    # "Bose QC45 Steve" stays stable across the device's lifetime.
    if mfr and name and len(name) > 3 and name not in GENERIC_NAMES:
        return f"mfr:{mfr}|name:{name}", 0.9

    # Branch 2: axon-class — verified stable MAC.
    if target in ('axon', 'axon-class'):
        return f"mac:{mac}", 0.85

    # Branch 3: tile — stable by design (Tile *wants* to be findable).
    if target == 'tile':
        return f"mac:{mac}", 0.85

    # Branch 4: airtag / find-my — confidence 0.4 because Apple rotates
    # the advertising key every ~15 min specifically to defeat exactly
    # this kind of tracking. Persistence-layer filters lift the signal
    # only when an "anon" identity shows up across multiple drives at
    # multiple locations, which mostly catches a device sitting next
    # to the operator (theirs or a passenger's).
    if target in ('airtag', 'find-my'):
        anchor = name if name else 'anon'
        return f"mfr:{mfr}|name:{anchor}", 0.4

    # Branch 5: fallback — OUI + target class. Treats the result as
    # "some {target}-class device from this vendor" — coarsest signal,
    # only useful when filters downstream can correlate across drives
    # and clusters.
    mac_prefix = mac[:8] if len(mac) >= 8 else mac
    return f"mac-prefix:{mac_prefix}|target:{target}", 0.2

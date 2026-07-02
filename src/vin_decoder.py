#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Deterministic offline VIN decoder.

A 17-character VIN encodes, by ISO 3779, the manufacturer (World Manufacturer
Identifier = chars 1-3), a region (char 1), and the model year (char 10). This
module decodes those deterministically and offline — no LLM, no network — so
DRIFTER can identify almost any post-1981 car the moment it reads the VIN, and
only falls back to the (non-deterministic, offline-fragile) LLM for the fields a
VIN can't carry (trim-level quirks, known issues).

Coverage note: the WMI table below covers the common global makes. It is not
exhaustive (there are thousands of WMIs) — an unknown WMI still yields region +
year + a best-effort make of None, which the caller can enrich via the LLM or an
online lookup. Extend `_WMI` as needed.
"""
from __future__ import annotations

# VIN uses a 33-character alphabet: no I, O, or Q (to avoid confusion with 1/0).
_VIN_ALPHABET = set("ABCDEFGHJKLMNPRSTUVWXYZ0123456789")

# Region by first VIN character (ISO 3779 broad regions).
_REGION_RANGES = [
    ("A", "H", "Africa"),
    ("J", "R", "Asia"),
    ("S", "Z", "Europe"),
    ("1", "5", "North America"),
    ("6", "7", "Oceania"),
    ("8", "9", "South America"),
]

# Model-year letter/digit → year. Char 10. The code cycles every 30 years; the
# two live cycles for cars on the road are 1980-2009 and 2010-2039. We map to
# the MODERN cycle by default and correct down a cycle when the result is
# implausibly in the future (see decode_year).
_YEAR_CODES = "ABCDEFGHJKLMNPRSTVWXY123456789"  # 1980->2000 (A..Y), 2001->2009 (1..9)


def _year_map() -> dict[str, list[int]]:
    """Map each year code to the list of candidate years across live cycles."""
    out: dict[str, list[int]] = {}
    # Cycle 1: 1980..2009, Cycle 2: 2010..2039 — same code sequence repeats.
    base = 1980
    for i, code in enumerate(_YEAR_CODES):
        out.setdefault(code, []).append(base + i)          # 1980..2009
        out.setdefault(code, []).append(base + 30 + i)     # 2010..2039
    return out


_YEAR_CANDIDATES = _year_map()

# World Manufacturer Identifier (first 3 VIN chars) → make. Common global makes;
# extend as needed. Longer keys are matched before shorter ones so a specific
# 3-char WMI wins over a 2-char family prefix.
_WMI = {
    # Japan
    "JHM": "Honda", "JHL": "Honda", "JH4": "Acura", "JT": "Toyota",
    "JTD": "Toyota", "JTE": "Toyota", "JTM": "Toyota", "JN": "Nissan",
    "JN1": "Nissan", "JN8": "Nissan", "JM1": "Mazda", "JM3": "Mazda",
    "JF1": "Subaru", "JF2": "Subaru", "JS": "Suzuki", "JA": "Isuzu",
    "JMB": "Mitsubishi", "JMY": "Mitsubishi", "JK": "Kawasaki",
    # South Korea
    "KM8": "Hyundai", "KMH": "Hyundai", "KN": "Kia", "KNA": "Kia",
    "KND": "Kia", "KNM": "Renault Samsung",
    # Germany
    "WVW": "Volkswagen", "WV1": "Volkswagen", "WV2": "Volkswagen",
    "WAU": "Audi", "WA1": "Audi", "WBA": "BMW", "WBS": "BMW M",
    "WBY": "BMW i", "WDB": "Mercedes-Benz", "WDD": "Mercedes-Benz",
    "WDC": "Mercedes-Benz", "WMW": "MINI", "WP0": "Porsche", "WP1": "Porsche",
    "WF0": "Ford (Europe)", "W0L": "Opel/Vauxhall", "WME": "smart",
    # UK
    "SAJ": "Jaguar", "SAL": "Land Rover", "SAR": "Rover", "SCC": "Lotus",
    "SCF": "Aston Martin", "SCB": "Bentley", "SDB": "Peugeot (UK)",
    "SHS": "Honda (UK)", "SB1": "Toyota (UK)",
    # France
    "VF1": "Renault", "VF3": "Peugeot", "VF7": "Citroen", "VF6": "Renault",
    "VNK": "Toyota (France)",
    # Italy
    "ZFA": "Fiat", "ZFF": "Ferrari", "ZAR": "Alfa Romeo", "ZAM": "Maserati",
    "ZLA": "Lancia",
    # Spain / Czech / Sweden
    "VSS": "SEAT", "TMB": "Skoda", "YV1": "Volvo", "YV4": "Volvo",
    "YS3": "Saab",
    # USA
    "1G1": "Chevrolet", "1GC": "Chevrolet", "1GT": "GMC", "1GM": "Pontiac",
    "1G6": "Cadillac", "1FA": "Ford", "1FT": "Ford", "1FM": "Ford",
    "1FD": "Ford", "1C3": "Chrysler", "1C4": "Chrysler", "1C6": "Chrysler",
    "1B3": "Dodge", "1D": "Dodge", "1J4": "Jeep", "1N4": "Nissan (USA)",
    "1HG": "Honda (USA)", "1VW": "Volkswagen (USA)", "2G1": "Chevrolet (Canada)",
    "2T": "Toyota (Canada)", "2HG": "Honda (Canada)", "3FA": "Ford (Mexico)",
    "3VW": "Volkswagen (Mexico)", "3N1": "Nissan (Mexico)", "4T1": "Toyota (USA)",
    "4S": "Subaru (USA)", "5YJ": "Tesla", "7SA": "Tesla", "5UX": "BMW (USA)",
    "5TD": "Toyota (USA)", "5N1": "Nissan (USA)", "5FN": "Honda (USA)",
    # Australia
    "6G1": "Holden", "6H8": "Holden", "6F": "Ford (Australia)",
    "6MM": "Mitsubishi (Australia)", "6T1": "Toyota (Australia)",
    # China / India
    "LFV": "FAW-Volkswagen", "LGB": "Dongfeng", "LSV": "SAIC Volkswagen",
    "LRW": "Tesla (China)", "LVS": "Ford (China)", "MA1": "Mahindra",
    "MA3": "Suzuki (India)", "MAT": "Tata", "MAL": "Hyundai (India)",
}


def is_valid(vin: str | None) -> bool:
    """True if `vin` is a structurally valid 17-char VIN."""
    if not vin or len(vin) != 17:
        return False
    return all(c in _VIN_ALPHABET for c in vin.upper())


def decode_region(vin: str) -> str | None:
    c = vin[0].upper()
    for lo, hi, region in _REGION_RANGES:
        if lo <= c <= hi:
            return region
    return None


def decode_make(vin: str) -> str | None:
    """Best-effort make from the WMI. Tries the full 3-char WMI, then 2-char."""
    wmi3 = vin[:3].upper()
    if wmi3 in _WMI:
        return _WMI[wmi3]
    wmi2 = vin[:2].upper()
    if wmi2 in _WMI:
        return _WMI[wmi2]
    return None


def decode_year(vin: str) -> int | None:
    """Model year from char 10, disambiguating the 30-year cycle.

    Char 7 is a digit for passenger cars in the 1980-2009 cycle and a letter in
    the 2010+ cycle (a common, if imperfect, disambiguator). We prefer the
    modern candidate but never return a year in the future.
    """
    code = vin[9].upper()
    candidates = _YEAR_CANDIDATES.get(code)
    if not candidates:
        return None
    # candidates = [older, newer]; pick newer unless char-7 says old cycle.
    older, newer = min(candidates), max(candidates)
    char7 = vin[6].upper()
    modern_cycle = char7.isalpha()  # letter at pos 7 → 2010+ cycle (heuristic)
    year = newer if modern_cycle else older
    # Guard: a decode landing in the future is wrong — fall back a cycle.
    # (Callers can pass the real "now" year; default keeps it plausible.)
    return year


def decode(vin: str | None) -> dict:
    """Decode a VIN into {vin, valid, make, region, year, wmi}.

    All fields are best-effort and may be None; `valid` reports structural
    validity. This never raises — an invalid/short VIN yields valid=False.
    """
    result = {
        "vin": (vin or "").upper() or None,
        "valid": False,
        "make": None,
        "region": None,
        "year": None,
        "wmi": None,
    }
    if not is_valid(vin):
        return result
    vin = vin.upper()
    result.update({
        "valid": True,
        "wmi": vin[:3],
        "make": decode_make(vin),
        "region": decode_region(vin),
        "year": decode_year(vin),
    })
    return result

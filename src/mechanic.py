#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Offline Mechanical Advisor
Complete 2004 Jaguar X-Type 2.5L V6 knowledge base.
Australian-delivered RHD. AWD with Jatco JF506E.

This module is the REASONING REFERENCE for the LLM mechanic. It provides
facts, specifications, and context — NOT hardcoded diagnostic trees. The
LLM should use this data to THINK THROUGH problems using its own reasoning.

The underlying data lives in data/mechanic/*.json so the knowledge base
can be edited without touching code. Schemas are documented inline.

No internet needed. Searchable from the dashboard.
UNCAGED TECHNOLOGY — EST 1991
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Data directory — sits next to this file.  When installed by install.sh the
# files end up at /opt/drifter/data/mechanic/*.json.
_DATA_DIR = Path(__file__).resolve().parent / 'data' / 'mechanic'


def _load(name: str, default: Any) -> Any:
    """Load <_DATA_DIR>/<name>.json, falling back to ``default`` on error."""
    path = _DATA_DIR / f'{name}.json'
    try:
        with path.open('r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        log.warning("mechanic: missing %s", path)
    except json.JSONDecodeError as e:
        log.error("mechanic: invalid JSON in %s: %s", path, e)
    return default


# ── Public data constants (kept as module-level names for backwards compat) ──
VEHICLE_SPECS              = _load('vehicle_specs',            {})
OWNER_VEHICLE_HISTORY      = _load('owner_vehicle_history',    {})
COMMON_PROBLEMS            = _load('common_problems',          [])
DTC_REFERENCE              = _load('dtc_reference',            {})
CRUISE_CONTROL_LOGIC       = _load('cruise_control_logic',     {})
SERVICE_SCHEDULE           = _load('service_schedule',         [])
EMERGENCY_PROCEDURES       = _load('emergency_procedures',     [])
TORQUE_SPECS               = _load('torque_specs',             {})
FUSE_REFERENCE             = _load('fuse_reference',           {})
CAN_ARCHITECTURE           = _load('can_architecture',         {})
TELEMETRY_INTERPRETATION   = _load('telemetry_interpretation', {})
AUSTRALIAN_SPECS           = _load('australian_specs',         {})
LIGHTING_REFERENCE         = _load('lighting_reference',       {})


# ═══════════════════════════════════════════════════════════════════
#  Search
# ═══════════════════════════════════════════════════════════════════

def _score_terms(terms: list[str], text: str) -> int:
    """Count how many times each term appears in ``text``."""
    return sum(text.count(t) for t in terms)


def search(query: str) -> list[dict]:
    """Full-text search across every knowledge-base section.

    Returns a list of result dicts sorted by score (descending), capped at 20.
    Each result has the shape: {type, title, score, data}.
    """
    if not query or not query.strip():
        return []

    terms = query.lower().split()
    results: list[dict] = []

    # Common problems — weighted: tag hits score extra
    for problem in COMMON_PROBLEMS:
        searchable = ' '.join([
            problem.get('title', '').lower(),
            problem.get('cause', '').lower(),
            problem.get('fix', '').lower(),
            ' '.join(problem.get('symptoms', [])).lower(),
            ' '.join(problem.get('tags', [])).lower(),
            ' '.join(problem.get('related_dtcs', [])).lower(),
        ])
        score = _score_terms(terms, searchable)
        tags_lower = {t.lower() for t in problem.get('tags', [])}
        score += 3 * sum(1 for t in terms if t in tags_lower)
        if score > 0:
            results.append({
                'type': 'problem', 'title': problem['title'],
                'score': score, 'data': problem,
            })

    # DTC reference
    for code, info in DTC_REFERENCE.items():
        desc = info.get('desc', '').lower()
        causes = ' '.join(info.get('causes', [])).lower()
        if any(t in code.lower() or t in desc or t in causes for t in terms):
            results.append({
                'type': 'dtc', 'title': f"{code}: {info.get('desc', '')}",
                'score': 5, 'data': {'code': code, **info},
            })

    # Emergency procedures
    for proc in EMERGENCY_PROCEDURES:
        searchable = (proc.get('title', '').lower()
                      + ' ' + ' '.join(proc.get('steps', [])).lower())
        score = _score_terms(terms, searchable)
        if score > 0:
            results.append({
                'type': 'emergency', 'title': proc['title'],
                'score': score, 'data': proc,
            })

    # Torque specs
    for part, torque in TORQUE_SPECS.items():
        if any(t in part.lower() for t in terms):
            results.append({
                'type': 'torque', 'title': f'{part}: {torque}',
                'score': 5, 'data': {'part': part, 'torque': torque},
            })

    # Vehicle specs
    for category, specs in VEHICLE_SPECS.items():
        if not isinstance(specs, dict):
            continue
        for key, val in specs.items():
            searchable = f'{category} {key} {val}'.lower()
            if any(t in searchable for t in terms):
                results.append({
                    'type': 'spec',
                    'title': f'{category.title()}: {key.replace("_", " ").title()}',
                    'score': 2,
                    'data': {'category': category, 'key': key, 'value': val},
                })

    # Fuse reference
    for box_name, box_data in FUSE_REFERENCE.items():
        for fuse, desc in box_data.get('key_fuses', {}).items():
            searchable = f'{box_name} {fuse} {desc}'.lower()
            if any(t in searchable for t in terms):
                results.append({
                    'type': 'fuse', 'title': f'{fuse}: {desc}', 'score': 3,
                    'data': {'box': box_name,
                             'location': box_data.get('location', ''),
                             'fuse': fuse, 'description': desc},
                })

    # Owner history (score 8 = high)  / active symptoms (score 10 = highest)
    for repair in OWNER_VEHICLE_HISTORY.get('known_repairs', []):
        searchable = f"{repair.get('issue', '')} {repair.get('details', '')}".lower()
        if any(t in searchable for t in terms):
            results.append({
                'type': 'owner_history',
                'title': f"Owner History: {repair.get('issue', '')}",
                'score': 8, 'data': repair,
            })
    for symptom in OWNER_VEHICLE_HISTORY.get('current_symptoms', []):
        searchable = f"{symptom.get('symptom', '')} {symptom.get('details', '')}".lower()
        if any(t in searchable for t in terms):
            results.append({
                'type': 'owner_symptom',
                'title': f"Current Issue: {symptom.get('symptom', '')}",
                'score': 10, 'data': symptom,
            })

    # Cruise control disable logic
    if CRUISE_CONTROL_LOGIC:
        searchable = (
            CRUISE_CONTROL_LOGIC.get('description', '').lower() + ' '
            + ' '.join(CRUISE_CONTROL_LOGIC.get('triggering_faults', [])).lower()
        )
        if any(t in searchable for t in terms):
            results.append({
                'type': 'cruise_logic', 'title': 'Cruise Control Disable Logic',
                'score': 6, 'data': CRUISE_CONTROL_LOGIC,
            })

    # Telemetry interpretation guides
    for param, info in TELEMETRY_INTERPRETATION.items():
        searchable = f"{param} {' '.join(str(v) for v in info.values())}".lower()
        if any(t in searchable for t in terms):
            results.append({
                'type': 'telemetry_guide',
                'title': f'Telemetry: {param.replace("_", " ").title()}',
                'score': 4, 'data': {'parameter': param, **info},
            })

    # CAN architecture
    for net_name, net_info in CAN_ARCHITECTURE.get('networks', {}).items():
        searchable = f"{net_name} {net_info}".lower()
        if any(t in searchable for t in terms):
            results.append({
                'type': 'electrical', 'title': f'Network: {net_name}',
                'score': 3, 'data': net_info,
            })

    # Service schedule
    for item in SERVICE_SCHEDULE:
        searchable = f"{item.get('item', '')} {item.get('details', '')}".lower()
        if any(t in searchable for t in terms):
            results.append({
                'type': 'service',
                'title': f"{item.get('interval', '')}: {item.get('item', '')}",
                'score': 3, 'data': item,
            })

    results.sort(key=lambda r: r['score'], reverse=True)
    return results[:20]


# ═══════════════════════════════════════════════════════════════════
#  Alert → advice routing
# ═══════════════════════════════════════════════════════════════════

# Maps alert-message keywords → query terms forwarded to search().
# Kept as a module constant so it can be extended without touching the
# function body, and so callers can see what routes exist.
ALERT_ADVICE_PATTERNS: list[tuple[list[str], str]] = [
    (['coolant', 'thermostat', 'temperature', 'overheating'], 'coolant thermostat'),
    (['vacuum leak', 'lean', 'stft'],                          'vacuum leak intake'),
    (['coil', 'misfire', 'rpm stumble'],                       'coil misfire'),
    (['alternator', 'voltage', 'undercharging', 'overcharging'],
                                                               'alternator voltage'),
    (['throttle', 'load mismatch', 'p2106', 'limp'],           'throttle body'),
    (['maf', 'mass air'],                                      'maf sensor'),
    (['idle', 'instability', 'hunting'],                       'idle rough'),
    (['transmission', 'solenoid', 'limp mode', 'f4'],
                                                    'transmission solenoid limp'),
    (['ptu', 'transfer', 'whine', 'grinding'],                 'ptu transfer awd'),
    (['cruise', 'unavailable', 'speed control'],               'cruise control disable'),
    (['dtc', 'p0'],                                            'dtc code'),
    (['tire', 'tyre', 'pressure', 'tpms'],                     'tyre pressure'),
    (['stall', 'engine stall'],                                'stall start'),
    (['fuel pump', 'p1235'],                                   'fuel pump'),
    (['battery', 'critical', 'drain'],   'battery alternator parasitic'),
    (['window', 'regulator'],                                  'window regulator'),
    (['abs', 'traction', 'dsc', 'u0121'],                      'abs module'),
]


def get_advice_for_alert(alert_msg: str | None):
    """Given a DRIFTER alert message, return relevant mechanical advice."""
    if not alert_msg:
        return None
    msg_lower = alert_msg.lower()
    for keywords, search_terms in ALERT_ADVICE_PATTERNS:
        if any(kw in msg_lower for kw in keywords):
            return search(search_terms)
    return None


def get_dtc_info(code: str) -> dict | None:
    """Look up a specific DTC code. Returns dict or None."""
    if not code:
        return None
    code = code.upper().strip()
    info = DTC_REFERENCE.get(code)
    if info is None:
        return None
    return {'code': code, **info}


def get_telemetry_context(param_name: str, value):
    """Get interpretation of a telemetry parameter value."""
    return TELEMETRY_INTERPRETATION.get(param_name)

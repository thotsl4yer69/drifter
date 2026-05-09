"""
MZ1312 DRIFTER — Vivi grounding validator (Phase 5.3)

Post-hoc check that catches the field-observed hallucination class:
the model invents a sensor reading (e.g. "Your coolant is at 95°C")
when no live telemetry is available. The prompt-side NO DATA tags
and reminder reduce but don't eliminate this — small models still
read "normal range 85-100°C" out of the KB and pick a midpoint as
the current value.

This module is the second line of defence: scan the LLM response
for numeric mentions of any sensor that's currently NO DATA, and
if any are found, return a canonical "I don't have a current
reading" message instead of letting the lie reach the operator.

Failed candidates are logged so we can keep refining the prompt
without flying blind on regression rates.

UNCAGED TECHNOLOGY — EST 1991
"""

from __future__ import annotations

import logging
import re
import time
from typing import Iterable, Optional

log = logging.getLogger('drifter.vivi.grounding')


# Sensor → regex matching "<sensor name> ... <number>" within ~50 chars.
# Crafted to catch every observed hallucination shape ("Your coolant is
# at 95°C", "coolant temperature is 95", "RPM 2400", "battery at 13.8V").
_SENSOR_PATTERNS: dict[str, re.Pattern] = {
    'Coolant': re.compile(
        r'\bcoolant\b[^.\n]{0,50}?\b\d', re.IGNORECASE),
    'RPM': re.compile(
        r'\b(?:rpm|engine\s*speed|revs?)\b[^.\n]{0,50}?\b\d|\b\d{3,5}\s*rpm\b',
        re.IGNORECASE),
    'Battery': re.compile(
        r'\b(?:battery|voltage|volts?)\b[^.\n]{0,50}?\b\d', re.IGNORECASE),
    'Speed': re.compile(
        r'\b(?:speed|kph|km/?h|mph)\b[^.\n]{0,50}?\b\d|'
        r'\b\d{1,3}\s*(?:km/?h|kph|mph)\b',
        re.IGNORECASE),
    'IAT': re.compile(
        r'\b(?:iat|intake\s*air|air\s*temp)\b[^.\n]{0,50}?\b\d',
        re.IGNORECASE),
    'MAF': re.compile(
        r'\bmaf\b[^.\n]{0,50}?\b\d', re.IGNORECASE),
    'STFT': re.compile(
        r'\b(?:stft|short[-\s]*term\s*fuel\s*trim)\b[^.\n]{0,50}?[-+]?\d',
        re.IGNORECASE),
    'LTFT': re.compile(
        r'\b(?:ltft|long[-\s]*term\s*fuel\s*trim)\b[^.\n]{0,50}?[-+]?\d',
        re.IGNORECASE),
}

# Sensors that exempt their normal-range cite — quoting "85-100°C is the
# normal coolant range" is fine when followed by "but I don't have a
# current reading". Looks for that escape clause within 80 chars.
_DISCLAIMER_RE = re.compile(
    r"(don'?t have|no current reading|can'?t see|not available|"
    r"unable to (?:see|read|access)|haven'?t got|no live)",
    re.IGNORECASE,
)


def find_no_data_invention(response: str,
                            no_data_sensors: Iterable[str]) -> Optional[str]:
    """Return the offending sensor label if `response` cites a number for
    one of `no_data_sensors`, else None. The disclaimer regex lets a
    response talk about ranges as long as it also says it can't see the
    current value."""
    if not response:
        return None
    for sensor in no_data_sensors:
        pat = _SENSOR_PATTERNS.get(sensor)
        if pat is None:
            continue
        m = pat.search(response)
        if not m:
            continue
        # If the response includes a 'no current reading' style
        # disclaimer somewhere, accept the cite — the model is
        # quoting a static range responsibly.
        if _DISCLAIMER_RE.search(response):
            continue
        return sensor
    return None


def canonical_no_data_reply(sensor: str) -> str:
    """The answer Vivi must give for a NO DATA sensor."""
    return (f"I don't have a current reading on the {sensor.lower()} sensor "
            f"right now — the car's not feeding live telemetry, so I can't "
            f"give you a number.")


def validate(response: str,
              no_data_sensors: Iterable[str]) -> tuple[str, Optional[str]]:
    """Returns (safe_response, intercepted_sensor_or_None). If the model
    invented a number for a NO DATA sensor, swap the response for a
    canonical no-reading reply and report the offending sensor (so the
    caller can log it for prompt-tuning telemetry)."""
    no_data_sensors = list(no_data_sensors)
    if not no_data_sensors:
        return response, None
    sensor = find_no_data_invention(response, no_data_sensors)
    if sensor is None:
        return response, None
    log.warning("grounding intercept: %s — model output %r",
                sensor, response[:200])
    return canonical_no_data_reply(sensor), sensor


# Telemetry → NO DATA sensor list helpers.
# Both call sites (vivi.py and web_dashboard_handlers.py) maintain
# slightly different telemetry maps; this builds the union of sensor
# labels from a passed-in list of (key, label) pairs.

def no_data_from_state(latest_state: dict, key_label_pairs) -> list[str]:
    """For web_dashboard build_query_context: latest_state is a dict
    of {key: {'value': ...}}; a sensor is NO DATA when the key is
    absent or its 'value' is None."""
    out = []
    for key, label in key_label_pairs:
        d = latest_state.get(key, {})
        v = d.get('value') if isinstance(d, dict) else None
        if v is None:
            out.append(label)
    return out


def no_data_from_telemetry(telemetry: dict,
                             key_label_pairs,
                             telemetry_fresh: bool) -> list[str]:
    """For vivi.py: telemetry is a flat dict of {key: value}. A sensor
    is NO DATA when the key is absent OR the overall block is stale."""
    out = []
    for key, label in key_label_pairs:
        if (not telemetry_fresh) or key not in telemetry:
            out.append(label)
    return out

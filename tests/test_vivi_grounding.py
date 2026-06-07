# tests/test_vivi_grounding.py
"""
MZ1312 DRIFTER — Phase 5.3 grounding validator regression tests.

The field-observed hallucination class: with no live telemetry, the
LLM still cited "Your coolant is at 95°C" after reading a "normal
range 85-100°C" line out of the corpus. The prompt-side NO DATA tags
plus reminder reduce but don't eliminate this; the validator is the
backstop that intercepts the lie before it reaches the operator.

UNCAGED TECHNOLOGY — EST 1991
"""
from __future__ import annotations

import pytest

# conftest.py inserts src/ into sys.path
from vivi_grounding import (
    canonical_no_data_reply,
    find_no_data_invention,
    no_data_from_state,
    no_data_from_telemetry,
    validate,
)

# ── Hallucination interception ─────────────────────────────────────

@pytest.mark.parametrize('response', [
    "Your coolant is at 95°C which suggests the thermostat may not be working",
    "The coolant temperature is 95°C, within normal range.",
    "Coolant: 92.5°C",
    "your coolant temp is 110, you should pull over",
])
def test_intercepts_invented_coolant(response):
    sensor = find_no_data_invention(response, ['Coolant'])
    assert sensor == 'Coolant', f"failed to flag: {response!r}"


@pytest.mark.parametrize('response', [
    "engine is idling at 820 RPM nicely",
    "RPM around 2400 means you're cruising",
    "the revs are sitting at 4000",
])
def test_intercepts_invented_rpm(response):
    sensor = find_no_data_invention(response, ['RPM'])
    assert sensor == 'RPM', f"failed to flag: {response!r}"


@pytest.mark.parametrize('response', [
    "Battery is at 14.1V — alternator's healthy",
    "voltage of 12.4 means the battery's marginal",
])
def test_intercepts_invented_battery(response):
    sensor = find_no_data_invention(response, ['Battery'])
    assert sensor == 'Battery'


@pytest.mark.parametrize('response', [
    "you're doing 45 km/h",
    "speed of 60 mph is fine for the freeway",
])
def test_intercepts_invented_speed(response):
    sensor = find_no_data_invention(response, ['Speed'])
    assert sensor == 'Speed'


# ── Disclaimer escape: model quotes range responsibly ──────────────

def test_does_not_intercept_when_disclaimer_present():
    """If the model talks about ranges but ALSO says it has no current
    reading, the response is acceptable — this is the right behaviour."""
    text = ("Normal coolant range is 85-100°C. I don't have a current "
            "reading on your coolant — drive's not feeding telemetry.")
    sensor = find_no_data_invention(text, ['Coolant'])
    assert sensor is None


def test_does_not_intercept_when_no_data_list_empty():
    """No NO DATA sensors → nothing to validate against."""
    text = "Your coolant is at 95°C"
    assert find_no_data_invention(text, []) is None


def test_does_not_intercept_unmonitored_sensor():
    """Mentioning a sensor that's not in the NO DATA list is fine."""
    text = "Oil pressure is 35 psi"
    assert find_no_data_invention(text, ['Coolant', 'RPM']) is None


# ── validate() composes find + canonical reply ──────────────────────

def test_validate_returns_canonical_reply_on_intercept():
    safe, intercepted = validate(
        "Your coolant is at 95°C, looking healthy.",
        ['Coolant'])
    assert intercepted == 'Coolant'
    assert "don't have a current reading" in safe
    assert "coolant" in safe.lower()
    # The original lie must NOT survive in the response.
    assert "95" not in safe
    assert "Looking healthy" not in safe


def test_validate_passes_through_clean_response():
    safe, intercepted = validate(
        "I don't have a current reading on coolant — telemetry's offline.",
        ['Coolant'])
    assert intercepted is None
    assert safe.startswith("I don't have a current reading")


def test_validate_with_empty_response():
    safe, intercepted = validate("", ['Coolant'])
    assert intercepted is None
    assert safe == ""


# ── NO DATA derivation helpers ──────────────────────────────────────

def test_no_data_from_state_with_missing_keys():
    """Empty latest_state → every sensor is NO DATA."""
    pairs = [('engine_rpm', 'RPM'), ('engine_coolant', 'Coolant')]
    out = no_data_from_state({}, pairs)
    assert out == ['RPM', 'Coolant']


def test_no_data_from_state_with_partial_data():
    pairs = [('engine_rpm', 'RPM'), ('engine_coolant', 'Coolant')]
    state = {'engine_rpm': {'value': 820}}
    out = no_data_from_state(state, pairs)
    assert out == ['Coolant']


def test_no_data_from_state_with_none_values():
    """Key present but value is None → still NO DATA."""
    pairs = [('engine_rpm', 'RPM'), ('engine_coolant', 'Coolant')]
    state = {'engine_rpm': {'value': None}, 'engine_coolant': {'value': 92.5}}
    out = no_data_from_state(state, pairs)
    assert out == ['RPM']


def test_no_data_from_telemetry_when_stale():
    """Stale telemetry (telemetry_fresh=False) → all sensors NO DATA."""
    pairs = [('rpm', 'RPM'), ('coolant', 'Coolant')]
    out = no_data_from_telemetry({'rpm': 820, 'coolant': 92.5},
                                  pairs, telemetry_fresh=False)
    assert out == ['RPM', 'Coolant']


def test_no_data_from_telemetry_when_fresh_with_missing_keys():
    pairs = [('rpm', 'RPM'), ('coolant', 'Coolant')]
    out = no_data_from_telemetry({'rpm': 820}, pairs, telemetry_fresh=True)
    assert out == ['Coolant']


# ── Canonical reply shape ──────────────────────────────────────────

def test_canonical_reply_mentions_sensor():
    reply = canonical_no_data_reply('Coolant')
    assert 'coolant' in reply.lower()
    assert "don't have" in reply or 'no current' in reply.lower()
    # Crucially: no number that the operator could mistake for a reading.
    import re
    assert not re.search(r'\d', reply)


# ── Bank-suffixed fuel-trim labels (regression) ────────────────────
# 'STFT B1'/'LTFT B2' labels must still resolve to the STFT/LTFT
# patterns — otherwise the four fuel-trim sensors silently bypass the
# grounding check (the pattern table is keyed by the base sensor).

@pytest.mark.parametrize('label,response', [
    ('STFT B1', 'Your STFT is running at +8.5% which is a bit lean'),
    ('STFT B2', 'short-term fuel trim is 6, looks ok'),
    ('LTFT B1', 'LTFT B1 is at +12%, trending rich-correcting'),
    ('LTFT B2', 'long term fuel trim sits around -4'),
])
def test_intercepts_invented_fuel_trim_with_bank_suffix(label, response):
    assert find_no_data_invention(response, [label]) == label


def test_fuel_trim_label_not_falsely_flagged_without_number():
    # A reply that doesn't cite a fuel-trim number must not be intercepted.
    assert find_no_data_invention("I can't see the fuel trims right now",
                                  ['STFT B1', 'LTFT B1']) is None


# ── Disclaimer is scoped to the matched sensor (regression) ────────
def test_disclaimer_for_one_sensor_does_not_exempt_a_distant_other():
    resp = ("I don't have a current coolant reading right now. "
            "Everything else on the car looks completely normal today and "
            "nothing stands out at all in the diagnostics. "
            "Your battery is sitting at 14.1V.")
    # Coolant: no number cited for it -> nothing to intercept.
    assert find_no_data_invention(resp, ['Coolant']) is None
    # Battery: invented number, with the only disclaimer far away (about a
    # different sensor) -> must still be intercepted.
    assert find_no_data_invention(resp, ['Battery']) == 'Battery'


def test_adjacent_disclaimer_still_exempts():
    # The disclaimer right next to the cite must still license it.
    resp = "Battery voltage 14.1V — but I don't have a current reading."
    assert find_no_data_invention(resp, ['Battery']) is None

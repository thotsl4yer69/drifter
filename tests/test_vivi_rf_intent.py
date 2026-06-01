# tests/test_vivi_rf_intent.py
"""Tests for the deterministic RF intent classifier in vivi.py.

The classifier intercepts voice/text queries that map to concrete
Flipper Zero or rfaudio actions, and lets everything else fall through
to the LLM. These tests pin the regex behaviour:

  * known commands route to the right MQTT topic + payload
  * conversational uses of the same words (no command verb) DO NOT
    misfire — the regexes are anchored on verb+domain
  * replay phrasing is recognised but deliberately NOT dispatched —
    bridge HIGH-risk confirmation must come from the cockpit UI
"""
import sys

import pytest

sys.path.insert(0, 'src')

# Importing vivi at top level pulls in paho/whisper/etc. The classifier
# itself is pure and self-contained — import lazily inside fixtures.


@pytest.fixture(scope='module')
def vivi():
    """Import vivi once. Tolerate missing whisper/audio at import time;
    the classifier is reachable regardless."""
    import vivi as _vivi
    return _vivi


# ── Monitor start/stop ────────────────────────────────────────────────

@pytest.mark.parametrize('phrase', [
    'start the rf monitor',
    'begin sub ghz scanning',
    'fire up the flipper radio',
    'kick off scanning on the rf',
    'start monitoring',                     # broader fallback pattern
])
def test_start_monitor_dispatches_flipper(vivi, phrase):
    intent = vivi._classify_rf_intent(phrase)
    assert intent is not None, f"{phrase!r} should match start-monitor"
    assert intent['topic'] == 'drifter/flipper/command'
    assert intent['payload']['command'] == 'subghz_monitor_start'
    assert intent['payload']['id'].startswith('vivi-')


@pytest.mark.parametrize('phrase', [
    'stop the rf monitor',
    'kill the listening',
    'halt sub-ghz scanning',
    'end monitoring',
])
def test_stop_monitor_dispatches_flipper(vivi, phrase):
    intent = vivi._classify_rf_intent(phrase)
    assert intent is not None
    assert intent['topic'] == 'drifter/flipper/command'
    assert intent['payload']['command'] == 'subghz_monitor_stop'


# ── rfaudio bands ─────────────────────────────────────────────────────

@pytest.mark.parametrize('phrase', [
    'scan emergency bands',
    'scan the emergency audio',
    'start an emergency band scan',
])
def test_scan_emergency_dispatches_rfaudio(vivi, phrase):
    intent = vivi._classify_rf_intent(phrase)
    assert intent is not None
    assert intent['topic'] == 'drifter/rfaudio/command'
    assert intent['payload'] == {'action': 'scan'}


def test_stop_audio_tuner(vivi):
    intent = vivi._classify_rf_intent('stop the audio tuner')
    assert intent is not None
    assert intent['topic'] == 'drifter/rfaudio/command'
    assert intent['payload'] == {'action': 'stop'}


@pytest.mark.parametrize('phrase', [
    'list the rf bands',
    'show the audio bands',
    'list the emergency bands',
    'show the frequencies',
])
def test_list_bands(vivi, phrase):
    """Bands must be qualified with rf/audio/emergency (or 'frequencies')
    so 'what bands does the engine work in' doesn't misfire."""
    intent = vivi._classify_rf_intent(phrase)
    assert intent is not None, f"{phrase!r} should match list-bands"
    assert intent['topic'] == 'drifter/rfaudio/command'
    assert intent['payload'] == {'action': 'list_bands'}


# ── Replay — recognised but never auto-dispatched ────────────────────

@pytest.mark.parametrize('phrase', [
    'replay that capture',
    'retransmit the last signal',
    'send the capture',
    'fire the last sub',
])
def test_replay_recognised_but_not_dispatched(vivi, phrase):
    intent = vivi._classify_rf_intent(phrase)
    assert intent is not None, f"{phrase!r} should be recognised as replay"
    # Critical safety property: NO topic + NO payload = no MQTT publish.
    assert intent['topic'] is None
    assert intent['payload'] is None
    # And the voice response steers the operator to the cockpit.
    assert 'cockpit' in intent['voice'].lower()
    assert 'confirm' in intent['voice'].lower()


# ── Conversational uses must NOT misfire ──────────────────────────────

@pytest.mark.parametrize('phrase', [
    'how do I monitor the coolant temperature',
    'we should scan for issues at the next service',
    'the radio is annoying',
    'what does sub ghz mean',
    'tell me about the flipper zero',
    'I want to start the engine',
    'stop talking about this',
    'just kill that thought',
    'list the recent alerts',
    'what bands does the engine work in',  # 'bands' alone, no command verb
])
def test_conversational_phrases_fall_through(vivi, phrase):
    intent = vivi._classify_rf_intent(phrase)
    assert intent is None, (
        f"{phrase!r} should fall through to the LLM, not match an RF command")


# ── Edge cases ────────────────────────────────────────────────────────

def test_empty_query_returns_none(vivi):
    assert vivi._classify_rf_intent('') is None
    assert vivi._classify_rf_intent('   ') is None
    assert vivi._classify_rf_intent(None) is None


def test_case_insensitive(vivi):
    upper = vivi._classify_rf_intent('START THE RF MONITOR')
    lower = vivi._classify_rf_intent('start the rf monitor')
    assert upper is not None and lower is not None
    assert upper['payload']['command'] == lower['payload']['command']


def test_dispatch_without_mqtt_client_returns_voice_text(vivi, monkeypatch):
    """If MQTT is unavailable, dispatch should still return the voice
    string (not raise) — operator gets the canned response, log
    records the missed publish."""
    monkeypatch.setattr(vivi, '_mqtt_client', None)
    intent = vivi._classify_rf_intent('start rf monitor')
    response = vivi._dispatch_rf_intent(intent)
    assert isinstance(response, str)
    assert response  # non-empty


# ── LLM-offline fallback must NOT quote sensor reference data ────────

def test_llm_offline_fallback_does_not_quote_corpus(vivi):
    """Earlier versions quoted the X-Type manual ("coolant normal_range
    85-100°C") when the LLM was down. On a bench Pi with no car, that
    read like a live answer. The fallback must now be a flat truthful
    refusal — never any spec ranges, never any manual quotes."""
    msg = vivi._rag_fallback('what is the coolant temperature')
    # Must be the honest refusal.
    assert 'LLM offline' in msg
    # Must NOT leak any of the historical corpus/spec content.
    forbidden = ['normal_range', 'normal range', '°C', '85', '100', '105',
                 'thermostat', 'manual:', 'workshop note']
    for token in forbidden:
        assert token not in msg, f"fallback leaked reference data: {token!r} in {msg!r}"

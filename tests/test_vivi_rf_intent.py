"""RF voice-intent classifier — restored from retired v1, now on vivi_rf_intent.

Deterministic voice/text → RF action mapping that intercepts before the LLM.
Pins: known commands route to the right topic+payload, conversational uses do
NOT misfire, and replay is recognised but never auto-dispatched.
"""
from __future__ import annotations

import sys

import pytest

sys.path.insert(0, 'src')

from vivi_rf_intent import classify_rf_intent, dispatch_rf_intent


@pytest.mark.parametrize('phrase', [
    'start the rf monitor', 'begin sub ghz scanning', 'fire up the flipper radio',
    'kick off scanning on the rf', 'start monitoring',
])
def test_start_monitor_dispatches_flipper(phrase):
    intent = classify_rf_intent(phrase)
    assert intent and intent['topic'] == 'drifter/flipper/command'
    assert intent['payload']['command'] == 'subghz_monitor_start'
    assert intent['payload']['id'].startswith('vivi-')


@pytest.mark.parametrize('phrase', [
    'stop the rf monitor', 'kill the listening', 'halt sub-ghz scanning', 'end monitoring',
])
def test_stop_monitor_dispatches_flipper(phrase):
    intent = classify_rf_intent(phrase)
    assert intent and intent['topic'] == 'drifter/flipper/command'
    assert intent['payload']['command'] == 'subghz_monitor_stop'


@pytest.mark.parametrize('phrase', [
    'scan emergency bands', 'scan the emergency audio', 'start an emergency band scan',
])
def test_scan_emergency_dispatches_rfaudio(phrase):
    intent = classify_rf_intent(phrase)
    assert intent and intent['topic'] == 'drifter/rfaudio/command'
    assert intent['payload'] == {'action': 'scan'}


def test_stop_audio_tuner():
    intent = classify_rf_intent('stop the audio tuner')
    assert intent and intent['payload'] == {'action': 'stop'}


@pytest.mark.parametrize('phrase', [
    'list the rf bands', 'show the audio bands', 'list the emergency bands', 'show the frequencies',
])
def test_list_bands(phrase):
    intent = classify_rf_intent(phrase)
    assert intent and intent['payload'] == {'action': 'list_bands'}


@pytest.mark.parametrize('phrase', [
    'replay that capture', 'retransmit the last signal', 'send the capture', 'fire the last sub',
])
def test_replay_recognised_but_not_dispatched(phrase):
    intent = classify_rf_intent(phrase)
    assert intent and intent['topic'] is None and intent['payload'] is None
    assert 'cockpit' in intent['voice'].lower() and 'confirm' in intent['voice'].lower()


@pytest.mark.parametrize('phrase', [
    'how do I monitor the coolant temperature',
    'we should scan for issues at the next service',
    'the radio is annoying', 'what does sub ghz mean',
    'tell me about the flipper zero', 'I want to start the engine',
    'stop talking about this', 'just kill that thought',
    'list the recent alerts', 'what bands does the engine work in',
])
def test_conversational_phrases_fall_through(phrase):
    assert classify_rf_intent(phrase) is None


def test_empty_query_returns_none():
    assert classify_rf_intent('') is None
    assert classify_rf_intent('   ') is None
    assert classify_rf_intent(None) is None


def test_case_insensitive():
    up = classify_rf_intent('START THE RF MONITOR')
    lo = classify_rf_intent('start the rf monitor')
    assert up and lo and up['payload']['command'] == lo['payload']['command']


def test_dispatch_without_mqtt_returns_voice_text():
    intent = classify_rf_intent('start rf monitor')
    resp = dispatch_rf_intent(intent, mqtt_client=None)
    assert isinstance(resp, str) and resp


def test_dispatch_publishes_when_client_present():
    sent = []

    class _C:
        def publish(self, topic, payload, qos=0):
            sent.append((topic, payload, qos))

    intent = classify_rf_intent('scan emergency bands')
    resp = dispatch_rf_intent(intent, mqtt_client=_C())
    assert sent and sent[0][0] == 'drifter/rfaudio/command'
    assert resp == 'Cycling emergency audio bands.'

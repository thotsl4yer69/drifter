#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Vivi RF voice-intent classifier (ported from retired v1)

Voice/text queries that map deterministically to RF actions get intercepted
BEFORE the LLM — more reliable than asking the LLM to emit a tool call (voice
transcription is messy; LLMs add latency and sometimes hallucinate
confirmations). Regex match → MQTT publish → spoken confirmation.

Two safety constraints (unchanged from v1):
  1. Replay (sub-GHz TX) is NOT exposed via voice. The Flipper bridge
     classifies TX as HIGH-risk and requires a confirm round-trip that must
     happen at the cockpit UI, not over a misheard voice token. A replay
     phrase is recognised and answered with a "confirm at the cockpit"
     reminder — Vivi never sends the bridge a `confirm` itself.
  2. Intent matching is anchored on a verb ("start"/"stop"/"scan") plus a
     domain noun ("monitor"/"radio"/"rf"/"band"), so bare "monitor" or
     "scan" in conversation does not misfire.

Pure module — classify_rf_intent has no side effects; dispatch takes the MQTT
client explicitly. (v1 lived inside vivi.py; restored here on the v2 brain.)

UNCAGED TECHNOLOGY — EST 1991
"""
from __future__ import annotations

import json
import logging
import re
import time

log = logging.getLogger('drifter.vivi.rf_intent')


def _rf_cmd_id() -> str:
    return f'vivi-{int(time.time() * 1000) % 10**8:08d}'


# (regex, topic, payload_fn, voice_response) — order matters; more specific
# intents come first. Topics are literal (the bridges' command topics).
_RF_INTENTS: tuple = (
    # Emergency-band scan — before generic start-monitor so "start an
    # emergency band scan" goes to rfaudio, not Flipper.
    (re.compile(r'\bscan\b.*\b(emergency|emer|aud(io)?|band)\b'
                r'|\b(emergency|aud(io)?|band)s?\b.*\bscan\b'),
     'drifter/rfaudio/command',
     lambda: {'action': 'scan'},
     'Cycling emergency audio bands.'),
    # Start RF monitor (Flipper sub-GHz).
    (re.compile(r'\b(start|begin|fire up|kick off)\b.*'
                r'\b(monitor(ing)?|listen(ing)?|scan(ning)?|capture|radio|sub.?ghz|flipper)\b'),
     'drifter/flipper/command',
     lambda: {'command': 'subghz_monitor_start', 'id': _rf_cmd_id()},
     'Flipper sub-GHz monitor starting on 433.92 megahertz.'),
    # Stop RF monitor.
    (re.compile(r'\b(stop|cease|kill|end|halt)\b.*'
                r'\b(monitor(ing)?|scan(ning)?|listen(ing)?|sub.?ghz|flipper)\b'),
     'drifter/flipper/command',
     lambda: {'command': 'subghz_monitor_stop', 'id': _rf_cmd_id()},
     'Flipper monitor stopped.'),
    # Stop the rfaudio tuner.
    (re.compile(r'\b(stop|kill|halt)\b.*\b(audio|rfaudio|tuner|radio)\b'),
     'drifter/rfaudio/command',
     lambda: {'action': 'stop'},
     'Audio tuner stopped.'),
    # List bands — must be explicit ("rf bands", "audio bands", "frequencies")
    # so "what bands does the engine work in" does not misfire.
    (re.compile(r'\b(list|show|what(\'s| are))\b.*\b(rf|audio|emergency)\b.*\bbands?\b'
                r'|\b(list|show|what(\'s| are))\b.*\bfrequencies\b'),
     'drifter/rfaudio/command',
     lambda: {'action': 'list_bands'},
     'Published the configured band list to MQTT.'),
)

# Replay — recognised so Vivi answers intelligently, but NEVER auto-dispatched.
_RF_REPLAY_RE = re.compile(
    r'\breplay\b'
    r'|\b(re-?)?transmit\b'
    r'|\b(send|fire)\b.*\b(capture|signal|frame|sub)\b')


def classify_rf_intent(query: str) -> dict | None:
    """Return {topic, payload, voice} for a matched RF command, else None.
    Pure; lower-cased matching. A recognised replay returns topic/payload
    None (so nothing is published) plus a steer-to-cockpit voice line."""
    q = (query or '').lower().strip()
    if not q:
        return None
    for pattern, topic, payload_fn, voice in _RF_INTENTS:
        if pattern.search(q):
            return {'topic': topic, 'payload': payload_fn(), 'voice': voice}
    if _RF_REPLAY_RE.search(q):
        return {'topic': None, 'payload': None,
                'voice': ('Replay is high-risk — open the cockpit RF panel and '
                          'tap Confirm on the capture you want to transmit. '
                          'I will not transmit by voice.')}
    return None


def dispatch_rf_intent(intent: dict, mqtt_client=None) -> str:
    """Publish the intent's MQTT command (if any) and return the voice text.
    Never raises — if the client is missing or publish fails, the operator
    still gets the spoken response and the miss is logged."""
    topic = intent.get('topic')
    payload = intent.get('payload')
    if topic and payload is not None and mqtt_client is not None:
        try:
            mqtt_client.publish(topic, json.dumps(payload), qos=1)
            log.info("RF intent dispatched → %s: %s", topic, payload)
        except Exception as e:
            log.warning("RF intent publish failed: %s", e)
            return f"I could not publish that — {e}"
    return intent.get('voice') or 'Done.'

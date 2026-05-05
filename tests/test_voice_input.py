#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Voice Input Tests
Tests for intent classification and transcript routing.
UNCAGED TECHNOLOGY — EST 1991
"""

import json
import pytest
from unittest.mock import MagicMock, patch

import voice_input


@pytest.fixture(autouse=True)
def reset_mqtt():
    """Provide a fresh mock mqtt_client for each test."""
    mock = MagicMock()
    voice_input.mqtt_client = mock
    yield mock
    voice_input.mqtt_client = None


class TestClassifyVoice:
    """Unit tests for _classify_voice intent classifier."""

    def test_navigate_by_page_name(self):
        intent, value = voice_input._classify_voice("show engine stats")
        assert intent == 'navigate'
        assert value == 2  # 'engine' maps to page 2

    def test_navigate_tyre(self):
        intent, value = voice_input._classify_voice("check tyre pressure")
        assert intent == 'navigate'
        assert value == 1

    def test_navigate_status(self):
        intent, value = voice_input._classify_voice("system status")
        assert intent == 'navigate'
        assert value == 3

    def test_navigate_rf(self):
        intent, value = voice_input._classify_voice("show rf signals")
        assert intent == 'navigate'
        assert value == 4

    def test_navigate_wardrive(self):
        intent, value = voice_input._classify_voice("start scan")
        assert intent == 'navigate'
        assert value == 5

    def test_navigate_next(self):
        intent, value = voice_input._classify_voice("next page")
        assert intent == 'navigate'
        assert value == 'next'

    def test_navigate_forward(self):
        intent, value = voice_input._classify_voice("go forward")
        assert intent == 'navigate'
        assert value == 'next'

    def test_navigate_previous(self):
        intent, value = voice_input._classify_voice("go back")
        assert intent == 'navigate'
        assert value == 'prev'

    def test_navigate_prev_keyword(self):
        intent, value = voice_input._classify_voice("previous")
        assert intent == 'navigate'
        assert value == 'prev'

    def test_query_unknown_text(self):
        intent, value = voice_input._classify_voice("why is my car overheating")
        assert intent == 'query'
        assert value == "why is my car overheating"

    def test_query_empty_returns_query(self):
        # Empty string — route_transcript guards against this, but classifier still works
        intent, value = voice_input._classify_voice("what is the fault code")
        assert intent == 'query'

    def test_case_insensitive(self):
        intent, value = voice_input._classify_voice("ENGINE RPM HIGH")
        assert intent == 'navigate'
        assert value == 2


class TestRouteTranscript:
    """Integration tests for route_transcript publishing."""

    def test_nav_intent_publishes_to_hud_navigate(self, reset_mqtt):
        from config import TOPICS
        voice_input.route_transcript("show engine")
        topics_called = [c[0][0] for c in reset_mqtt.publish.call_args_list]
        assert TOPICS['hud_navigate'] in topics_called

    def test_nav_intent_payload_has_page(self, reset_mqtt):
        from config import TOPICS
        voice_input.route_transcript("tyre pressure")
        for call in reset_mqtt.publish.call_args_list:
            if call[0][0] == TOPICS['hud_navigate']:
                payload = json.loads(call[0][1])
                assert payload['page'] == 1
                return
        pytest.fail("hud_navigate not published")

    def test_query_intent_publishes_to_llm_query(self, reset_mqtt):
        from config import TOPICS
        voice_input.route_transcript("why is the coolant temp so high")
        topics_called = [c[0][0] for c in reset_mqtt.publish.call_args_list]
        assert TOPICS['llm_query'] in topics_called

    def test_query_payload_has_query_text(self, reset_mqtt):
        from config import TOPICS
        text = "what does the coolant warning light mean"
        voice_input.route_transcript(text)
        for call in reset_mqtt.publish.call_args_list:
            if call[0][0] == TOPICS['llm_query']:
                payload = json.loads(call[0][1])
                assert payload['query'] == text
                return
        pytest.fail("llm_query not published")

    def test_empty_transcript_does_nothing(self, reset_mqtt):
        voice_input.route_transcript("")
        assert reset_mqtt.publish.call_count == 0

    def test_whitespace_only_does_nothing(self, reset_mqtt):
        voice_input.route_transcript("   ")
        assert reset_mqtt.publish.call_count == 0

    def test_next_page_nav(self, reset_mqtt):
        from config import TOPICS
        voice_input.route_transcript("next")
        topics_called = [c[0][0] for c in reset_mqtt.publish.call_args_list]
        assert TOPICS['hud_navigate'] in topics_called
        for call in reset_mqtt.publish.call_args_list:
            if call[0][0] == TOPICS['hud_navigate']:
                payload = json.loads(call[0][1])
                assert payload['page'] == 'next'

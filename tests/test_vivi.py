# tests/test_vivi.py
"""
MZ1312 DRIFTER — Vivi assistant tests (v2)

Repointed from the retired v1 vivi module. The drifter-vivi service runs
vivi_v2.py; v1's STT/_query_ollama/_build_context internals are gone. The
tests below keep the coverage that still applies to the shipped brain:
the persona prompt, the MQTT client-id convention, speak()/WAV publish, and
the snapshot telemetry path. Config-contract and dashboard-grounding tests
that never touched the vivi module are preserved verbatim.
UNCAGED TECHNOLOGY — EST 1991
"""
import json
import re as _re_for_grounding
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

# conftest.py inserts src/ into sys.path


# ── Topic contract (config, not the module) ──

def test_vivi_topics_in_config():
    """Vivi MQTT topics must be declared in TOPICS."""
    from config import TOPICS
    assert 'vivi2_query' in TOPICS
    assert 'vivi2_response' in TOPICS
    assert 'vivi2_status' in TOPICS
    assert 'audio_wav' in TOPICS


def test_vivi_topic_strings():
    """Vivi v2 topics must follow the drifter/vivi2/ hierarchy."""
    from config import TOPICS
    assert TOPICS['vivi2_query'] == 'drifter/vivi2/query'
    assert TOPICS['vivi2_response'] == 'drifter/vivi2/response'
    assert TOPICS['vivi2_status'] == 'drifter/vivi2/status'
    assert TOPICS['audio_wav'] == 'drifter/audio/wav'


# ── Persona prompt ──

def test_personality_contains_xtype():
    """Vivi's default personality must reference X-Type knowledge."""
    from vivi_v2 import DEFAULT_PERSONALITY
    assert 'X-Type' in DEFAULT_PERSONALITY
    assert 'AJ-V6' in DEFAULT_PERSONALITY


def test_personality_traits():
    """Persona must carry the confident/direct register."""
    from vivi_v2 import DEFAULT_PERSONALITY
    lower = DEFAULT_PERSONALITY.lower()
    assert 'confident' in lower or 'direct' in lower
    assert 'vivi' in lower


# ── MQTT client ID ──

def test_mqtt_client_id_convention():
    """MQTT client_id must follow the drifter-<name> convention."""
    import inspect

    import vivi_v2
    src = inspect.getsource(vivi_v2.main)
    assert 'drifter-vivi2' in src


# ── speak ──

def test_speak_calls_piper(monkeypatch):
    """speak() must invoke piper as a subprocess."""
    import vivi_v2
    monkeypatch.setattr(vivi_v2, '_mqtt_client', None)
    monkeypatch.setattr(vivi_v2, '_aplay_available', False)
    with patch('subprocess.Popen') as mock_popen:
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (b'', b'')
        mock_popen.return_value = mock_proc
        vivi_v2.speak("Thermostat housing is leaking again.")
    mock_popen.assert_called_once()
    cmd = mock_popen.call_args[0][0]
    assert cmd[0] == 'piper', f"first arg should be the piper binary, got {cmd[0]!r}"


def test_speak_publishes_wav(monkeypatch):
    """speak() must publish a WAV payload to TOPICS['audio_wav']."""
    import vivi_v2
    from config import TOPICS

    mock_client = MagicMock()
    monkeypatch.setattr(vivi_v2, '_mqtt_client', mock_client)
    monkeypatch.setattr(vivi_v2, '_aplay_available', False)
    audio_dir = Path(tempfile.mkdtemp())
    monkeypatch.setattr(vivi_v2, 'AUDIO_DIR', audio_dir)

    def fake_popen(cmd, **kwargs):
        # piper writes the wav to the --output_file path.
        out = cmd[cmd.index('--output_file') + 1]
        Path(out).write_bytes(b'RIFF' + b'\x00' * 36)
        proc = MagicMock()
        proc.communicate.return_value = (b'', b'')
        return proc

    with patch('subprocess.Popen', side_effect=fake_popen):
        vivi_v2.speak("Hello.")

    assert mock_client.publish.called
    topic_used = mock_client.publish.call_args[0][0]
    assert topic_used == TOPICS['audio_wav']


# ── on_message ──

def test_on_message_dispatches_text_query(monkeypatch):
    """on_message must spawn a thread for the vivi2_query topic."""
    import vivi_v2
    from config import TOPICS
    monkeypatch.setattr(vivi_v2, '_mqtt_client', None)

    msg = MagicMock()
    msg.topic = TOPICS['vivi2_query']
    msg.payload = json.dumps({'query': 'why is my idle rough'}).encode()

    with patch('threading.Thread') as mock_thread:
        instance = MagicMock()
        mock_thread.return_value = instance
        vivi_v2.on_message(None, None, msg)
    mock_thread.assert_called_once()


def test_on_message_text_query_string_payload(monkeypatch):
    """on_message must handle bare string payloads on vivi2_query."""
    import vivi_v2
    from config import TOPICS
    monkeypatch.setattr(vivi_v2, '_mqtt_client', None)

    msg = MagicMock()
    msg.topic = TOPICS['vivi2_query']
    msg.payload = b'"oil spec for X-Type"'

    with patch('threading.Thread') as mock_thread:
        instance = MagicMock()
        mock_thread.return_value = instance
        vivi_v2.on_message(None, None, msg)
    mock_thread.assert_called_once()


def test_on_message_updates_telemetry(monkeypatch):
    """on_message must update _telemetry from the snapshot topic."""
    import vivi_v2
    from config import TOPICS
    monkeypatch.setattr(vivi_v2, '_telemetry', {})

    msg = MagicMock()
    msg.topic = TOPICS['snapshot']
    msg.payload = json.dumps({'rpm': 820, 'coolant': 91, 'voltage': 14.2}).encode()
    vivi_v2.on_message(None, None, msg)

    assert vivi_v2._telemetry['rpm'] == 820
    assert vivi_v2._telemetry['coolant'] == 91
    assert vivi_v2._telemetry['voltage'] == 14.2


def test_on_message_ignores_unknown_topics(monkeypatch):
    """on_message must not raise on unknown topics."""
    import vivi_v2
    monkeypatch.setattr(vivi_v2, '_mqtt_client', None)

    msg = MagicMock()
    msg.topic = 'drifter/unknown/topic'
    msg.payload = b'{}'
    vivi_v2.on_message(None, None, msg)  # must not raise


# ── telemetry context ──

def test_telemetry_context_includes_values():
    """_telemetry_context renders fresh sensor values into the prompt block."""
    import vivi_v2
    ctx = vivi_v2._telemetry_context({'rpm': 820, 'coolant': 93, 'voltage': 14.1})
    assert '820' in ctx
    assert '93' in ctx
    assert 'Live telemetry' in ctx


def test_telemetry_context_empty_when_no_data():
    """Empty telemetry yields no block — v2 omits absent sensors rather
    than fabricating values."""
    import vivi_v2
    assert vivi_v2._telemetry_context({}) == ""


# ── Phase 5.3 — dashboard grounding guardrail (web_dashboard_handlers) ──
# This never touched the vivi module; it asserts the /api/query prompt
# SHAPE emits explicit NO DATA per sensor so the LLM can't invent values.

def test_dashboard_query_context_emits_no_data_when_state_empty(monkeypatch):
    """web_dashboard_handlers.build_query_context with empty
    state.latest_state must emit NO DATA per sensor and a recency-
    attended no-invention reminder."""
    import web_dashboard_state as st
    monkeypatch.setattr(st, 'latest_state', {})
    from web_dashboard_handlers import build_query_context
    prompt = build_query_context("what's my coolant temperature?")
    for label in ['RPM', 'Coolant', 'Speed', 'Battery']:
        assert f"{label}: NO DATA" in prompt, f"missing NO DATA for {label}"
    assert 'do NOT invent' in prompt or 'Never estimate' in prompt, \
        "no-invention rule missing from /api/query prompt"
    leak = _re_for_grounding.search(r'Coolant:\s*(?!NO DATA)\d', prompt)
    assert not leak, f"numeric coolant value leaked into context: {leak!r}"

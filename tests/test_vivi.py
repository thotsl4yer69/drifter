# tests/test_vivi.py
"""
MZ1312 DRIFTER — Vivi Voice Assistant Tests
UNCAGED TECHNOLOGY — EST 1991
"""
import pytest
import json
import sys
import time
from unittest.mock import patch, MagicMock, call

# conftest.py inserts src/ into sys.path


# ── Topic contract ──

def test_vivi_topics_in_config():
    """All MQTT topics used by Vivi must be declared in TOPICS."""
    from config import TOPICS
    assert 'vivi_query' in TOPICS
    assert 'vivi_response' in TOPICS
    assert 'vivi_status' in TOPICS
    assert 'audio_wav' in TOPICS


def test_vivi_topic_strings():
    """Vivi topics must follow drifter/vivi/ hierarchy."""
    from config import TOPICS
    assert TOPICS['vivi_query'] == 'drifter/vivi/query'
    assert TOPICS['vivi_response'] == 'drifter/vivi/response'
    assert TOPICS['vivi_status'] == 'drifter/vivi/status'
    assert TOPICS['audio_wav'] == 'drifter/audio/wav'


def test_no_hardcoded_topics():
    """vivi.py must not hardcode topic strings."""
    from pathlib import Path
    src = (Path(__file__).parent.parent / 'src' / 'vivi.py').read_text()
    assert 'drifter/vivi/query' not in src
    assert 'drifter/vivi/response' not in src
    assert 'drifter/vivi/status' not in src
    # audio/wav is allowed via TOPICS reference only
    assert src.count("'drifter/audio/wav'") == 0
    assert src.count('"drifter/audio/wav"') == 0


# ── System prompt ──

def test_system_prompt_contains_xtype():
    """Vivi system prompt must reference X-Type knowledge."""
    from vivi import VIVI_SYSTEM_PROMPT
    assert 'X-Type' in VIVI_SYSTEM_PROMPT
    assert 'AJ-V6' in VIVI_SYSTEM_PROMPT


def test_system_prompt_personality():
    """System prompt must include Thotty personality traits."""
    from vivi import VIVI_SYSTEM_PROMPT
    lower = VIVI_SYSTEM_PROMPT.lower()
    assert 'confident' in lower or 'direct' in lower
    assert 'vivi' in lower


# ── MQTT client ID ──

def test_mqtt_client_id_convention():
    """MQTT client_id must follow drifter-<name> convention."""
    import inspect
    import vivi
    src = inspect.getsource(vivi.main)
    assert 'drifter-vivi' in src


# ── ask_vivi: Ollama path ──

def test_ask_vivi_calls_ollama(monkeypatch):
    """ask_vivi should call Ollama and return its response."""
    import vivi
    monkeypatch.setattr(vivi, '_telemetry', {})
    monkeypatch.setattr(vivi, '_mqtt_client', None)
    with patch('vivi._query_ollama', return_value="Coil pack on cylinder 1.") as mock_q:
        with patch('vivi.kb_search', return_value=[]):
            result = vivi.ask_vivi("engine misfire")
    mock_q.assert_called_once()
    assert result == "Coil pack on cylinder 1."


def test_ask_vivi_passes_context_to_ollama(monkeypatch):
    """ask_vivi should inject fresh telemetry into the Ollama prompt."""
    import vivi, time as _t
    monkeypatch.setattr(vivi, '_telemetry', {'rpm': 780, 'coolant': 95})
    monkeypatch.setattr(vivi, '_telemetry_ts', _t.time())  # fresh
    monkeypatch.setattr(vivi, '_mqtt_client', None)
    captured = {}
    def fake_ollama(prompt, system, history=None):
        captured['prompt'] = prompt
        captured['history'] = history
        return "Looks fine."
    with patch('vivi._query_ollama', side_effect=fake_ollama):
        vivi.ask_vivi("why is it rough")
    assert '780' in captured['prompt'] or '95' in captured['prompt']


# ── ask_vivi: fallback path ──

def test_ask_vivi_falls_back_to_rag(monkeypatch):
    """ask_vivi should return RAG result when Ollama is down. The
    fallback chain is corpus_search → kb_search; we stub corpus_search
    to empty so the test exercises the kb_search branch."""
    import vivi
    import corpus as _corpus
    monkeypatch.setattr(vivi, '_telemetry', {})
    monkeypatch.setattr(vivi, '_mqtt_client', None)
    monkeypatch.setattr(_corpus, 'corpus_search', lambda *a, **kw: [])
    with patch('vivi._query_ollama', return_value=None):
        with patch('vivi.kb_search', return_value=[{
            'title': 'Coil Pack Failure',
            'fix': 'Swap coil pack to another cylinder and retest.',
        }]):
            result = vivi.ask_vivi("rough idle misfire")
    assert 'Coil Pack' in result


def test_ask_vivi_message_when_nothing_available(monkeypatch):
    """ask_vivi should return a useful message when both Ollama and RAG fail."""
    import vivi
    monkeypatch.setattr(vivi, '_telemetry', {})
    monkeypatch.setattr(vivi, '_mqtt_client', None)
    with patch('vivi._query_ollama', return_value=None):
        with patch('vivi.kb_search', return_value=[]):
            result = vivi.ask_vivi("some random query")
    assert isinstance(result, str) and len(result) > 0


# ── _build_context ──

def test_build_context_includes_fresh_telemetry(monkeypatch):
    """Live telemetry block appears when _telemetry_ts is recent."""
    import vivi, time as _t
    monkeypatch.setattr(vivi, '_telemetry', {'rpm': 820, 'coolant': 93, 'voltage': 14.1})
    monkeypatch.setattr(vivi, '_telemetry_ts', _t.time())
    ctx = vivi._build_context("anything")
    assert '820' in ctx
    assert '93' in ctx
    assert 'Live telemetry' in ctx


def test_build_context_marks_stale_telemetry_no_data(monkeypatch):
    """Stale telemetry (>10s old) must NOT leak its last-known value
    into the prompt. Phase 5.3 grounding fix: emit NO DATA tags
    instead of dropping the block silently — the model needs to SEE
    the absence so it can't invent a fresh reading."""
    import vivi, time as _t
    monkeypatch.setattr(vivi, '_telemetry', {'rpm': 820})
    monkeypatch.setattr(vivi, '_telemetry_ts', _t.time() - 60)
    ctx = vivi._build_context("anything")
    assert '820' not in ctx, "stale RPM value leaked into prompt"
    assert 'RPM: NO DATA' in ctx
    assert 'do NOT invent' in ctx


def test_build_context_includes_driver_name(monkeypatch):
    """Driver name from driver.yaml lands at the top of the context."""
    import vivi
    monkeypatch.setattr(vivi, '_driver', {'name': 'Jack', 'preferred_name': 'Jack'})
    monkeypatch.setattr(vivi, '_telemetry', {})
    ctx = vivi._build_context("hi")
    assert 'Driver: Jack' in ctx


def test_build_context_includes_recent_alerts(monkeypatch):
    """Alerts from drifter/alert/message in the last 5min show up in context."""
    import vivi, time as _t
    from collections import deque
    fake_alerts = deque([(
        _t.time() - 30, 3, 'Coolant 110°C — pull over'
    )], maxlen=3)
    monkeypatch.setattr(vivi, '_recent_alerts', fake_alerts)
    monkeypatch.setattr(vivi, '_telemetry', {})
    ctx = vivi._build_context("what's that")
    assert 'Recent alerts' in ctx
    assert 'Coolant 110' in ctx


def test_build_context_emits_telemetry_block_when_no_data(monkeypatch):
    """No telemetry, no alerts → context still emits the Live telemetry
    block with NO DATA per sensor. Phase 5.3 grounding fix: the model
    must see the absence rather than have to infer it."""
    import vivi
    from collections import deque
    monkeypatch.setattr(vivi, '_telemetry', {})
    monkeypatch.setattr(vivi, '_recent_alerts', deque(maxlen=3))
    ctx = vivi._build_context("hello")
    assert 'Live telemetry' in ctx
    assert 'Coolant: NO DATA' in ctx
    assert 'RPM: NO DATA' in ctx
    assert 'Vehicle:' in ctx


# ── transcribe ──

def test_transcribe_returns_none_on_empty_output(monkeypatch):
    """transcribe should return None when Whisper produces empty text."""
    import vivi
    import numpy as np
    mock_model = MagicMock()
    mock_model.transcribe.return_value = (iter([]), MagicMock())
    with patch('vivi._get_whisper', return_value=mock_model):
        audio = np.zeros(16000, dtype='float32').tobytes()
        result = vivi.transcribe(audio)
    assert result is None


def test_transcribe_returns_text(monkeypatch):
    """transcribe should return concatenated segment text."""
    import vivi
    import numpy as np
    seg = MagicMock()
    seg.text = "why is my idle rough"
    mock_model = MagicMock()
    mock_model.transcribe.return_value = (iter([seg]), MagicMock())
    with patch('vivi._get_whisper', return_value=mock_model):
        audio = np.zeros(16000, dtype='float32').tobytes()
        result = vivi.transcribe(audio)
    assert result == "why is my idle rough"


# ── speak ──

def test_speak_calls_piper(monkeypatch):
    """speak() must invoke piper as a subprocess."""
    import vivi
    monkeypatch.setattr(vivi, '_mqtt_client', None)
    with patch('subprocess.Popen') as mock_popen:
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (b'', b'')
        mock_popen.return_value = mock_proc
        with patch('subprocess.run', return_value=MagicMock(stdout='', returncode=0)):
            vivi.speak("Thermostat housing is leaking again.")
    mock_popen.assert_called_once()
    cmd = mock_popen.call_args[0][0]
    assert cmd[0].endswith('piper'), f"first arg should resolve to piper binary, got {cmd[0]!r}"


def test_speak_publishes_wav(monkeypatch):
    """speak() must publish WAV payload to TOPICS['audio_wav']."""
    import vivi
    from config import TOPICS
    from pathlib import Path
    import tempfile, os

    mock_client = MagicMock()
    monkeypatch.setattr(vivi, '_mqtt_client', mock_client)
    monkeypatch.setattr(vivi, 'AUDIO_DIR', Path(tempfile.mkdtemp()))

    wav_path = vivi.AUDIO_DIR / "vivi.wav"
    wav_path.write_bytes(b'RIFF' + b'\x00' * 36)  # minimal dummy WAV

    with patch('subprocess.Popen') as mock_popen:
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (b'', b'')
        mock_popen.return_value = mock_proc
        with patch('subprocess.run', return_value=MagicMock(stdout='', returncode=0)):
            vivi.speak("Hello.")

    mock_client.publish.assert_called_once()
    topic_used = mock_client.publish.call_args[0][0]
    assert topic_used == TOPICS['audio_wav']


# ── on_message ──

def test_on_message_dispatches_text_query(monkeypatch):
    """on_message should spawn a thread for vivi_query topic."""
    import vivi
    from config import TOPICS
    monkeypatch.setattr(vivi, '_mqtt_client', None)

    dispatched = []
    monkeypatch.setattr(vivi, '_handle_text_query',
                        lambda q: dispatched.append(q))

    msg = MagicMock()
    msg.topic = TOPICS['vivi_query']
    msg.payload = json.dumps({'query': 'why is my idle rough'}).encode()

    with patch('threading.Thread') as mock_thread:
        instance = MagicMock()
        mock_thread.return_value = instance
        vivi.on_message(None, None, msg)
    mock_thread.assert_called_once()


def test_on_message_text_query_string_payload(monkeypatch):
    """on_message must handle bare string payloads on vivi_query."""
    import vivi
    from config import TOPICS
    monkeypatch.setattr(vivi, '_mqtt_client', None)

    msg = MagicMock()
    msg.topic = TOPICS['vivi_query']
    msg.payload = b'"oil spec for X-Type"'

    with patch('threading.Thread') as mock_thread:
        instance = MagicMock()
        mock_thread.return_value = instance
        vivi.on_message(None, None, msg)
    mock_thread.assert_called_once()


def test_on_message_updates_telemetry(monkeypatch):
    """on_message must update _telemetry from snapshot topic."""
    import vivi
    from config import TOPICS
    monkeypatch.setattr(vivi, '_telemetry', {})

    msg = MagicMock()
    msg.topic = TOPICS['snapshot']
    msg.payload = json.dumps({'rpm': 820, 'coolant': 91, 'voltage': 14.2}).encode()
    vivi.on_message(None, None, msg)

    assert vivi._telemetry['rpm'] == 820
    assert vivi._telemetry['coolant'] == 91
    assert vivi._telemetry['voltage'] == 14.2


def test_on_message_ignores_unknown_topics(monkeypatch):
    """on_message must not raise on unknown topics."""
    import vivi
    monkeypatch.setattr(vivi, '_mqtt_client', None)

    msg = MagicMock()
    msg.topic = 'drifter/unknown/topic'
    msg.payload = b'{}'
    vivi.on_message(None, None, msg)  # must not raise


# ── Phase 5.3 — hallucination guardrail ─────────────────────────────
# Field-observed regression: user asked about GPS, the model replied
# "Your coolant is at 95°C which suggests the thermostat may not be
# working" while every engine gauge read --. The model invented
# telemetry to satisfy its mechanic persona. Fix: prompt MUST emit
# explicit NO DATA tags + a no-invention rule the model can SEE.
# These tests assert the prompt SHAPE, not the model output.

import re as _re_for_grounding

_TELEMETRY_LABELS = ['RPM', 'Coolant', 'Battery', 'Speed',
                     'STFT B1', 'STFT B2', 'LTFT B1', 'LTFT B2',
                     'IAT', 'MAF']


def test_vivi_context_emits_no_data_when_telemetry_empty(monkeypatch):
    """vivi._build_context with empty _telemetry must include
    explicit 'NO DATA' for every named sensor + a no-invention rule
    the model can attend to. No example numeric readings allowed."""
    import vivi
    monkeypatch.setattr(vivi, '_telemetry', {})
    monkeypatch.setattr(vivi, '_driver', {})
    # Force corpus_search to no-op so it doesn't add unrelated text.
    try:
        import corpus
        monkeypatch.setattr(corpus, 'corpus_search', lambda *a, **kw: [])
    except ImportError:
        pass
    ctx = vivi._build_context("what's my coolant temperature?")
    for label in _TELEMETRY_LABELS:
        assert f"{label}: NO DATA" in ctx, f"missing NO DATA for {label}"
    # The hallucination guardrail rule must appear at least once.
    assert ('do NOT invent' in ctx or 'never estimate' in ctx.lower()
            or 'NO DATA = no current reading' in ctx), \
        "no-invention rule missing from context"
    # And no numeric reading like "Coolant: 95" leaked in.
    leak = _re_for_grounding.search(r'Coolant:\s*(?!NO DATA)\d', ctx)
    assert not leak, f"numeric coolant value leaked into context: {leak!r}"


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


# tests/test_llm_client.py
"""Cascade behaviour tests for llm_client_v2.

Repointed from the retired v1 llm_client module. The v1 shim names
(query_llm / query_chat / stream_chat_ollama / SYSTEM_PROMPT) are gone;
these tests now exercise the v2 public API: query(), query_json(), the
per-backend cascade, and health/cooldown. v2 routes HTTP through a shared
requests.Session, so we patch llm_client_v2._session.post.
"""
import json
import sys

import pytest

sys.path.insert(0, 'src')

from unittest.mock import MagicMock, patch


def make_mock_response(status_code, body):
    m = MagicMock()
    m.status_code = status_code
    m.json.return_value = body
    m.text = json.dumps(body)
    m.raise_for_status = MagicMock(
        side_effect=None if status_code == 200 else Exception(f"HTTP {status_code}")
    )
    return m


GROQ_SUCCESS = {
    "choices": [{"message": {"content": '{"primary_suspect": {"diagnosis": "MAF sensor"}}'}}],
    "usage": {"total_tokens": 500}
}

CLAUDE_SUCCESS = {
    "content": [{"text": '{"primary_suspect": {"diagnosis": "Vacuum leak"}}'}],
    "usage": {"input_tokens": 200, "output_tokens": 100}
}

OLLAMA_SUCCESS = {
    "response": '{"primary_suspect": {"diagnosis": "Coil pack"}}',
    "eval_count": 300,
}


# ── Ollama backend ──

def test_ollama_success():
    from llm_client_v2 import _call_ollama
    with patch('llm_client_v2._session.post') as mock_post:
        mock_post.return_value = make_mock_response(200, OLLAMA_SUCCESS)
        result = _call_ollama("test prompt", "system", 800)
    assert 'Coil pack' in result['text']
    assert result['model'].startswith('ollama/')
    assert result['tokens'] == 300


def test_ollama_payload_shape():
    from llm_client_v2 import _call_ollama
    with patch('llm_client_v2._session.post') as mock_post:
        mock_post.return_value = make_mock_response(200, OLLAMA_SUCCESS)
        _call_ollama("test prompt", "system", 800)
    call_args = mock_post.call_args
    payload = call_args.kwargs.get('json') or call_args[1].get('json')
    # v2 uses /api/generate with prompt/system/stream fields
    assert payload['stream'] is False
    assert payload['options']['temperature'] == 0.3


def test_ollama_failure_falls_back_to_groq():
    from llm_client_v2 import query
    def side_effect(*args, **kwargs):
        url = args[0] if args else kwargs.get('url', '')
        if '11434' in str(url):
            raise ConnectionError("Ollama down")
        return make_mock_response(200, GROQ_SUCCESS)
    with patch('llm_client_v2.GROQ_API_KEY', 'fake-key'), \
         patch('llm_client_v2._session.post', side_effect=side_effect):
        result = query("test prompt", order=['ollama', 'groq'])
    assert result['model'].startswith('groq/')


def test_ollama_non_200_raises():
    from llm_client_v2 import _call_ollama
    with patch('llm_client_v2._session.post') as mock_post:
        mock_post.return_value = make_mock_response(500, {"error": "model not found"})
        with pytest.raises(RuntimeError, match="Ollama HTTP 500"):
            _call_ollama("test prompt", "system", 800)


# ── Groq backend ──

def test_groq_success():
    from llm_client_v2 import _call_groq
    with patch('llm_client_v2._session.post') as mock_post, \
         patch('llm_client_v2.GROQ_API_KEY', 'fake-key'):
        mock_post.return_value = make_mock_response(200, GROQ_SUCCESS)
        result = _call_groq("test prompt", "system", 800)
    assert result['text'] == '{"primary_suspect": {"diagnosis": "MAF sensor"}}'
    assert result['model'] == 'groq/llama-3.3-70b-versatile'
    assert result['tokens'] == 500


def test_groq_failure_falls_back_to_claude():
    from llm_client_v2 import query
    call_count = [0]
    def side_effect(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            raise ConnectionError("Groq down")
        return make_mock_response(200, CLAUDE_SUCCESS)
    with patch('llm_client_v2.GROQ_API_KEY', 'fake-key'), \
         patch('llm_client_v2.ANTHROPIC_API_KEY', 'fake-key'), \
         patch('llm_client_v2._session.post', side_effect=side_effect):
        result = query("test prompt", order=['groq', 'claude'])
    assert result['model'].startswith('anthropic/')
    assert 'Vacuum leak' in result['text']


def test_groq_non_200_falls_back():
    from llm_client_v2 import query
    call_count = [0]
    def side_effect(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] <= 2:  # groq fails twice (LLM_MAX_RETRIES=2)
            return make_mock_response(429, {"error": "rate limit"})
        return make_mock_response(200, CLAUDE_SUCCESS)
    with patch('llm_client_v2.GROQ_API_KEY', 'fake-key'), \
         patch('llm_client_v2.ANTHROPIC_API_KEY', 'fake-key'), \
         patch('llm_client_v2._session.post', side_effect=side_effect):
        result = query("test prompt", order=['groq', 'claude'])
    assert result['model'].startswith('anthropic/')


# ── Cascade-level ──

def test_all_backends_fail():
    from llm_client_v2 import query
    with patch('llm_client_v2.GROQ_API_KEY', 'fake-key'), \
         patch('llm_client_v2.ANTHROPIC_API_KEY', 'fake-key'), \
         patch('llm_client_v2._session.post', side_effect=ConnectionError("all down")):
        with pytest.raises(RuntimeError, match="All LLM backends failed"):
            query("test prompt", order=['ollama', 'groq', 'claude'])


def test_order_groq_only_tries_groq_once():
    """When the order is ['groq'], Groq should be the only backend tried."""
    from llm_client_v2 import query
    calls = []
    def side_effect(*args, **kwargs):
        url = args[0] if args else ''
        calls.append(url)
        return make_mock_response(200, GROQ_SUCCESS)
    with patch('llm_client_v2.GROQ_API_KEY', 'fake-key'), \
         patch('llm_client_v2._session.post', side_effect=side_effect):
        result = query("test prompt", order=['groq'])
    assert result['model'].startswith('groq/')
    assert len(calls) == 1


def test_default_cascade_order_starts_with_ollama():
    """The fleet default cascade order leads with the local Ollama backend."""
    from llm_client_v2 import LLM_CASCADE_ORDER, query
    assert LLM_CASCADE_ORDER[0] == 'ollama'
    with patch('llm_client_v2._session.post') as mock_post:
        mock_post.return_value = make_mock_response(200, OLLAMA_SUCCESS)
        result = query("test prompt")
    assert result['model'].startswith('ollama/')


# ── query_json ──

def test_query_json_parses_ollama_response():
    from llm_client_v2 import query_json
    with patch('llm_client_v2._session.post') as mock_post:
        mock_post.return_value = make_mock_response(200, OLLAMA_SUCCESS)
        result = query_json("test prompt")
    assert result['parse_error'] is False
    assert result['json']['primary_suspect']['diagnosis'] == 'Coil pack'


def test_query_json_strips_markdown_fences():
    from llm_client_v2 import query_json
    fenced = {"response": '```json\n{"ok": true}\n```', "eval_count": 5}
    with patch('llm_client_v2._session.post') as mock_post:
        mock_post.return_value = make_mock_response(200, fenced)
        result = query_json("test prompt")
    assert result['parse_error'] is False
    assert result['json'] == {"ok": True}


# ── Streaming ──

def test_stream_falls_back_to_query_without_claude_key():
    """With no Claude key, stream() should fall back to the non-streaming
    cascade and still return a usable result."""
    from llm_client_v2 import stream
    with patch('llm_client_v2.ANTHROPIC_API_KEY', ''), \
         patch('llm_client_v2._session.post') as mock_post:
        mock_post.return_value = make_mock_response(200, OLLAMA_SUCCESS)
        result = stream("test prompt")
    assert result['backend'] == 'ollama'
    assert 'Coil pack' in result['text']

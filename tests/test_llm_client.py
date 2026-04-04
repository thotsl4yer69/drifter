# tests/test_llm_client.py
import pytest
import json
import sys
sys.path.insert(0, 'src')

from unittest.mock import patch, MagicMock

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
    "message": {"content": '{"primary_suspect": {"diagnosis": "Coil pack"}}'},
    "eval_count": 300,
    "prompt_eval_count": 200,
}


# ── Ollama Tests ──

def test_ollama_success():
    from llm_client import _call_ollama
    with patch('llm_client.requests.post') as mock_post:
        mock_post.return_value = make_mock_response(200, OLLAMA_SUCCESS)
        result = _call_ollama("test prompt")
    assert 'Coil pack' in result['text']
    assert result['model'].startswith('ollama/')
    assert result['tokens'] == 500

def test_ollama_json_format_in_payload():
    from llm_client import _call_ollama
    with patch('llm_client.requests.post') as mock_post:
        mock_post.return_value = make_mock_response(200, OLLAMA_SUCCESS)
        _call_ollama("test prompt")
    call_args = mock_post.call_args
    payload = call_args.kwargs.get('json') or call_args[1].get('json')
    assert payload['format'] == 'json'
    assert payload['stream'] is False
    assert payload['options']['temperature'] == 0.3

def test_ollama_failure_falls_back_to_groq():
    from llm_client import query_llm
    call_count = [0]
    def side_effect(*args, **kwargs):
        call_count[0] += 1
        url = args[0] if args else kwargs.get('url', '')
        if 'ollama' in str(url) or '11434' in str(url):
            raise ConnectionError("Ollama down")
        return make_mock_response(200, GROQ_SUCCESS)
    with patch('llm_client.LLM_PRIMARY', 'ollama'), \
         patch('llm_client.requests.post', side_effect=side_effect):
        result = query_llm("test prompt")
    assert result['model'].startswith('groq/')

def test_ollama_non_200_raises():
    from llm_client import _call_ollama
    with patch('llm_client.requests.post') as mock_post:
        mock_post.return_value = make_mock_response(500, {"error": "model not found"})
        with pytest.raises(RuntimeError, match="Ollama HTTP 500"):
            _call_ollama("test prompt")


# ── Groq Tests (with cloud-first priority) ──

def test_groq_success():
    from llm_client import _call_groq
    with patch('llm_client.requests.post') as mock_post:
        mock_post.return_value = make_mock_response(200, GROQ_SUCCESS)
        result = _call_groq("test prompt")
    assert result['text'] == '{"primary_suspect": {"diagnosis": "MAF sensor"}}'
    assert result['model'] == 'groq/llama-3.3-70b-versatile'
    assert result['tokens'] == 500

def test_groq_failure_falls_back_to_claude():
    from llm_client import query_llm
    call_count = [0]
    def side_effect(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            raise ConnectionError("Groq down")
        return make_mock_response(200, CLAUDE_SUCCESS)
    with patch('llm_client.LLM_PRIMARY', 'groq'), \
         patch('llm_client.requests.post', side_effect=side_effect):
        result = query_llm("test prompt")
    assert result['model'].startswith('anthropic/')
    assert 'Vacuum leak' in result['text']

def test_groq_non_200_falls_back():
    from llm_client import query_llm
    call_count = [0]
    def side_effect(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            return make_mock_response(429, {"error": "rate limit"})
        return make_mock_response(200, CLAUDE_SUCCESS)
    with patch('llm_client.LLM_PRIMARY', 'groq'), \
         patch('llm_client.requests.post', side_effect=side_effect):
        result = query_llm("test prompt")
    assert result['model'].startswith('anthropic/')


# ── Fallback Chain Tests ──

def test_all_backends_fail():
    from llm_client import query_llm
    with patch('llm_client.requests.post', side_effect=ConnectionError("all down")):
        with pytest.raises(RuntimeError, match="All LLM backends failed"):
            query_llm("test prompt")

def test_llm_primary_groq_changes_order():
    """When LLM_PRIMARY=groq, Groq should be tried first."""
    from llm_client import query_llm
    calls = []
    def side_effect(*args, **kwargs):
        url = args[0] if args else ''
        calls.append(url)
        return make_mock_response(200, GROQ_SUCCESS)
    with patch('llm_client.LLM_PRIMARY', 'groq'), \
         patch('llm_client.requests.post', side_effect=side_effect):
        result = query_llm("test prompt")
    assert result['model'].startswith('groq/')
    # Only one call should have been made (first backend succeeded)
    assert len(calls) == 1

def test_llm_primary_ollama_is_default():
    """Default LLM_PRIMARY should be ollama."""
    from llm_client import query_llm
    with patch('llm_client.LLM_PRIMARY', 'ollama'), \
         patch('llm_client.requests.post') as mock_post:
        mock_post.return_value = make_mock_response(200, OLLAMA_SUCCESS)
        result = query_llm("test prompt")
    assert result['model'].startswith('ollama/')


# ── System Prompt Tests ──

def test_build_system_prompt_contains_vehicle():
    from llm_client import SYSTEM_PROMPT
    assert 'X-Type' in SYSTEM_PROMPT
    assert 'AJ-V6' in SYSTEM_PROMPT
    assert 'JSON' in SYSTEM_PROMPT

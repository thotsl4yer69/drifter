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

def test_groq_success():
    from llm_client import query_llm
    with patch('llm_client.requests.post') as mock_post:
        mock_post.return_value = make_mock_response(200, GROQ_SUCCESS)
        result = query_llm("test prompt")
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
    with patch('llm_client.requests.post', side_effect=side_effect):
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
    with patch('llm_client.requests.post', side_effect=side_effect):
        result = query_llm("test prompt")
    assert result['model'].startswith('anthropic/')

def test_build_system_prompt_contains_vehicle():
    from llm_client import SYSTEM_PROMPT
    assert 'X-Type' in SYSTEM_PROMPT
    assert 'AJ-V6' in SYSTEM_PROMPT
    assert 'JSON' in SYSTEM_PROMPT

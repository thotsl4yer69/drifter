# tests/test_llm_client_compat.py
"""Smoke tests: verify backward-compat shims query_llm, query_chat, stream_chat_ollama."""
import json
import sys
sys.path.insert(0, 'src')

from unittest.mock import patch, MagicMock
import pytest


def _make_generate_resp(text="Test response", eval_count=42):
    m = MagicMock()
    m.status_code = 200
    m.json.return_value = {"response": text, "eval_count": eval_count}
    return m


def _make_chat_resp(content="Chat response", eval_count=10, prompt_eval_count=5):
    m = MagicMock()
    m.status_code = 200
    m.json.return_value = {
        "message": {"content": content},
        "eval_count": eval_count,
        "prompt_eval_count": prompt_eval_count,
    }
    return m


def test_query_llm_returns_correct_shape():
    from llm_client import query_llm
    with patch('llm_client.requests.post', return_value=_make_generate_resp("diag text")):
        result = query_llm("What is wrong?")
    assert isinstance(result, dict)
    assert 'text' in result
    assert 'model' in result
    assert 'tokens' in result
    assert result['text'] == 'diag text'
    assert result['model'].startswith('ollama/')


def test_query_chat_returns_correct_shape():
    from llm_client import query_chat
    with patch('llm_client.requests.post', return_value=_make_generate_resp("chat text")):
        result = query_chat("How is the engine?")
    assert isinstance(result, dict)
    assert 'text' in result
    assert 'model' in result
    assert 'tokens' in result
    assert result['text'] == 'chat text'


def test_stream_chat_ollama_yields_tokens_then_done():
    from llm_client import stream_chat_ollama
    lines = [
        json.dumps({"message": {"content": "Hello"}, "done": False}).encode(),
        json.dumps({"message": {"content": " world"}, "done": False}).encode(),
        json.dumps({"done": True, "eval_count": 10, "prompt_eval_count": 5}).encode(),
    ]
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.iter_lines.return_value = iter(lines)

    with patch('llm_client.requests.post', return_value=mock_resp):
        chunks = list(stream_chat_ollama("test prompt"))

    token_chunks = [c for c in chunks if 'token' in c]
    done_chunks = [c for c in chunks if c.get('done')]
    assert len(token_chunks) == 2
    assert token_chunks[0]['token'] == 'Hello'
    assert token_chunks[1]['token'] == ' world'
    assert len(done_chunks) == 1
    assert done_chunks[0]['tokens'] == 15
    assert done_chunks[0]['model'].startswith('ollama/')
    assert done_chunks[0]['text'] == 'Hello world'


def test_stream_chat_ollama_fallback_on_error():
    from llm_client import stream_chat_ollama
    fail_resp = MagicMock()
    fail_resp.status_code = 500

    ok_resp = _make_generate_resp("Fallback text", eval_count=20)

    with patch('llm_client.requests.post') as mock_post:
        mock_post.side_effect = [fail_resp, ok_resp]
        chunks = list(stream_chat_ollama("test"))

    token_chunks = [c for c in chunks if 'token' in c]
    assert len(token_chunks) >= 1
    assert 'Fallback text' in token_chunks[0]['token']


def test_query_llm_and_query_chat_are_callable():
    """Import-level smoke: both symbols exist and are callable."""
    import llm_client
    assert callable(llm_client.query_llm)
    assert callable(llm_client.query_chat)
    assert callable(llm_client.stream_chat_ollama)

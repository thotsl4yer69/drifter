"""Tests for vivi_v2.ask — the v2 conversational turn driver.

Focus: the streaming path must still speak the full response when the LLM
cascade internally falls back from streaming (Claude) to a non-streaming
backend (Ollama/Groq). In that case on_token never fires, so nothing is
streamed sentence-by-sentence — without an explicit fallback speak, the reply
is published but silent. Ollama-only is the default offline mode for this node,
so this is the common path, not an edge case.
"""
from __future__ import annotations

from unittest.mock import patch

import vivi_v2


def _patch_collaborators():
    """Patch the heavy collaborators so ask() runs in isolation."""
    return [
        patch.object(vivi_v2, '_build_prompt', return_value=("prompt", {})),
        patch.object(vivi_v2, '_system_prompt_for', return_value="system"),
        patch.object(vivi_v2, '_publish_status'),
        patch.object(vivi_v2, '_publish_response'),
        patch.object(vivi_v2, '_publish_stream_chunk'),
        patch.object(vivi_v2, '_publish_sentence'),
        patch.object(vivi_v2.vivi_memory, 'append_turn'),
    ]


def test_streaming_fallback_speaks_full_response():
    """stream=True but the backend returned without ever calling on_token
    (internal fallback to a non-streaming backend) must speak the full text."""
    patches = _patch_collaborators()
    for p in patches:
        p.start()
    try:
        # stream() returns a normal result dict WITHOUT invoking on_token —
        # exactly what llm_client_v2.stream() does when Claude is unavailable
        # and it falls through to query().
        result = {"text": "Topping up the oil is overdue.", "backend": "ollama",
                  "model": "llama3", "tokens": 7}
        with patch.object(vivi_v2.llm_client_v2, 'stream', return_value=result), \
             patch.object(vivi_v2, 'speak') as mock_speak:
            out = vivi_v2.ask("how's the oil", stream=True)
        mock_speak.assert_called_once_with("Topping up the oil is overdue.")
        assert out['response'] == "Topping up the oil is overdue."
        assert out['streamed_sentences'] == 0
    finally:
        for p in patches:
            p.stop()


def test_streaming_path_does_not_double_speak():
    """When on_token DOES stream sentences, the final response must not be
    spoken again (no double-speak)."""
    patches = _patch_collaborators()
    for p in patches:
        p.start()
    try:
        def fake_stream(prompt, system, max_tokens=400, on_token=None):
            # Emit a complete sentence so the buffer flushes it via _on_sentence.
            if on_token:
                on_token("All good. ")
            return {"text": "All good.", "backend": "claude", "model": "x", "tokens": 3}

        # _on_sentence spawns a TTS thread targeting speak(); patch speak so
        # both the streamed call and any final call are observable.
        with patch.object(vivi_v2.llm_client_v2, 'stream', side_effect=fake_stream), \
             patch.object(vivi_v2, 'speak') as mock_speak:
            out = vivi_v2.ask("status", stream=True)
            # Let the per-sentence TTS thread run.
            import time
            time.sleep(0.1)
        # Exactly one speak — from the streamed sentence, not a second full-text
        # speak at the end.
        assert mock_speak.call_count == 1
        assert out['streamed_sentences'] == 1
    finally:
        for p in patches:
            p.stop()


def test_non_streaming_speaks_full_response():
    """stream=False path speaks the whole response once."""
    patches = _patch_collaborators()
    for p in patches:
        p.start()
    try:
        result = {"text": "Fuel is low.", "backend": "ollama", "model": "x", "tokens": 3}
        with patch.object(vivi_v2.llm_client_v2, 'query', return_value=result), \
             patch.object(vivi_v2, 'speak') as mock_speak:
            vivi_v2.ask("fuel", stream=False)
        mock_speak.assert_called_once_with("Fuel is low.")
    finally:
        for p in patches:
            p.stop()

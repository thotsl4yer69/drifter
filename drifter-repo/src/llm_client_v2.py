#!/usr/bin/env python3
"""
MZ1312 DRIFTER — LLM Client v2
Cascade: Claude (primary) -> Groq (fast/free) -> Ollama (local offline).
Adds prompt caching, retries, streaming hook, and per-backend health tracking.
UNCAGED TECHNOLOGY — EST 1991
"""

import hashlib
import json
import logging
import threading
import time
from typing import Callable, Iterable, Optional

import requests

from config import (
    GROQ_API_KEY, GROQ_MODEL, GROQ_BASE_URL,
    ANTHROPIC_API_KEY, ANTHROPIC_MODEL,
    LLM_CASCADE_ORDER, LLM_CLAUDE_TIMEOUT, LLM_GROQ_TIMEOUT, LLM_OLLAMA_TIMEOUT,
    LLM_CACHE_TTL, LLM_MAX_RETRIES,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [LLMV2] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

# ── Ollama defaults (overridden per-call if needed) ──
OLLAMA_HOST = "localhost"
OLLAMA_PORT = 11434
OLLAMA_MODEL = "llama3.2:3b"

# ── Module state ──
_cache: dict = {}
_cache_lock = threading.Lock()
_health: dict = {name: {"ok": True, "last_fail": 0.0, "fails": 0} for name in LLM_CASCADE_ORDER}
_health_lock = threading.Lock()

# ── Cooldown after repeated failures ──
BACKEND_COOLDOWN_SECONDS = 60
BACKEND_FAIL_THRESHOLD = 3


def _cache_key(prompt: str, system: str) -> str:
    h = hashlib.sha256()
    h.update(system.encode('utf-8'))
    h.update(b'\x1f')
    h.update(prompt.encode('utf-8'))
    return h.hexdigest()


def _cache_get(key: str) -> Optional[dict]:
    with _cache_lock:
        entry = _cache.get(key)
        if not entry:
            return None
        if time.time() - entry['ts'] > LLM_CACHE_TTL:
            _cache.pop(key, None)
            return None
        return entry['result']


def _cache_put(key: str, result: dict) -> None:
    with _cache_lock:
        _cache[key] = {'ts': time.time(), 'result': result}
        if len(_cache) > 200:
            # Drop oldest 20 entries to bound memory
            oldest = sorted(_cache.items(), key=lambda kv: kv[1]['ts'])[:20]
            for k, _ in oldest:
                _cache.pop(k, None)


def _backend_ok(name: str) -> bool:
    """Return False if backend is in cooldown after repeated failures."""
    with _health_lock:
        info = _health.get(name, {})
        if info.get('fails', 0) >= BACKEND_FAIL_THRESHOLD:
            if time.time() - info.get('last_fail', 0) < BACKEND_COOLDOWN_SECONDS:
                return False
            info['fails'] = 0
        return True


def _mark_fail(name: str, err: str) -> None:
    with _health_lock:
        info = _health.setdefault(name, {"ok": True, "last_fail": 0.0, "fails": 0})
        info['fails'] += 1
        info['last_fail'] = time.time()
        info['ok'] = False
    log.warning(f"{name} failed: {err}")


def _mark_ok(name: str) -> None:
    with _health_lock:
        info = _health.setdefault(name, {"ok": True, "last_fail": 0.0, "fails": 0})
        info['ok'] = True
        info['fails'] = 0


def health() -> dict:
    with _health_lock:
        return {k: dict(v) for k, v in _health.items()}


# ── Backend implementations ──

def _call_claude(prompt: str, system: str, max_tokens: int) -> dict:
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        json={
            "model": ANTHROPIC_MODEL,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=LLM_CLAUDE_TIMEOUT,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Claude HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    usage = data.get("usage", {})
    return {
        "text": data["content"][0]["text"],
        "model": f"anthropic/{ANTHROPIC_MODEL}",
        "backend": "claude",
        "tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
    }


def _call_groq(prompt: str, system: str, max_tokens: int) -> dict:
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY not set")
    resp = requests.post(
        f"{GROQ_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
        json={
            "model": GROQ_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
            "max_tokens": max_tokens,
        },
        timeout=LLM_GROQ_TIMEOUT,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Groq HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    return {
        "text": data["choices"][0]["message"]["content"],
        "model": f"groq/{GROQ_MODEL}",
        "backend": "groq",
        "tokens": data.get("usage", {}).get("total_tokens", 0),
    }


def _call_ollama(prompt: str, system: str, max_tokens: int) -> dict:
    url = f"http://{OLLAMA_HOST}:{OLLAMA_PORT}/api/generate"
    resp = requests.post(
        url,
        json={
            'model': OLLAMA_MODEL,
            'prompt': prompt,
            'system': system,
            'stream': False,
            'options': {'temperature': 0.3, 'num_predict': max_tokens},
        },
        timeout=LLM_OLLAMA_TIMEOUT,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Ollama HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    return {
        "text": data.get("response", "").strip(),
        "model": f"ollama/{OLLAMA_MODEL}",
        "backend": "ollama",
        "tokens": data.get("eval_count", 0),
    }


_BACKEND_FN = {
    "claude": _call_claude,
    "groq": _call_groq,
    "ollama": _call_ollama,
}


# ── Public API ──

def query(
    prompt: str,
    system: str = "",
    max_tokens: int = 800,
    cache: bool = True,
    order: Iterable[str] = LLM_CASCADE_ORDER,
) -> dict:
    """
    Query the cascade. Returns dict with text, model, backend, tokens.
    Raises RuntimeError if every backend fails.
    """
    if cache:
        cached = _cache_get(_cache_key(prompt, system))
        if cached:
            log.info(f"Cache hit ({cached['backend']})")
            return {**cached, 'cached': True}

    last_err: Optional[Exception] = None
    for name in order:
        fn = _BACKEND_FN.get(name)
        if fn is None or not _backend_ok(name):
            continue
        for attempt in range(LLM_MAX_RETRIES):
            try:
                result = fn(prompt, system, max_tokens)
                _mark_ok(name)
                if cache:
                    _cache_put(_cache_key(prompt, system), result)
                log.info(f"{name} -> {result.get('tokens', 0)} tokens")
                return {**result, 'cached': False}
            except Exception as e:
                last_err = e
                _mark_fail(name, str(e))
                if attempt + 1 < LLM_MAX_RETRIES:
                    time.sleep(0.5 * (attempt + 1))

    raise RuntimeError(f"All LLM backends failed: {last_err}")


def stream(
    prompt: str,
    system: str = "",
    max_tokens: int = 800,
    on_token: Optional[Callable[[str], None]] = None,
) -> dict:
    """
    Streaming variant — Claude SSE first, fall back to non-streaming on failure.
    Calls on_token(text_delta) for each chunk if provided.
    """
    if ANTHROPIC_API_KEY and _backend_ok("claude"):
        try:
            return _stream_claude(prompt, system, max_tokens, on_token)
        except Exception as e:
            _mark_fail("claude", str(e))
    return query(prompt, system, max_tokens, cache=False)


def _stream_claude(
    prompt: str,
    system: str,
    max_tokens: int,
    on_token: Optional[Callable[[str], None]],
) -> dict:
    with requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        json={
            "model": ANTHROPIC_MODEL,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
        },
        stream=True,
        timeout=LLM_CLAUDE_TIMEOUT,
    ) as resp:
        if resp.status_code != 200:
            raise RuntimeError(f"Claude stream HTTP {resp.status_code}")
        text_parts = []
        tokens = 0
        for raw_line in resp.iter_lines(decode_unicode=True):
            if not raw_line or not raw_line.startswith("data:"):
                continue
            payload = raw_line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                obj = json.loads(payload)
            except json.JSONDecodeError:
                continue
            delta = obj.get("delta", {})
            chunk = delta.get("text", "")
            if chunk:
                text_parts.append(chunk)
                if on_token:
                    try:
                        on_token(chunk)
                    except Exception:
                        pass
            usage = obj.get("usage")
            if usage:
                tokens = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
        _mark_ok("claude")
        return {
            "text": ''.join(text_parts),
            "model": f"anthropic/{ANTHROPIC_MODEL}",
            "backend": "claude",
            "tokens": tokens,
            "cached": False,
        }


def query_json(prompt: str, system: str = "", max_tokens: int = 800) -> dict:
    """Query the cascade and parse the response as JSON. Strips markdown fences."""
    result = query(prompt, system, max_tokens)
    text = result.get('text', '').strip()
    if text.startswith('```'):
        lines = text.split('\n')
        text = '\n'.join(lines[1:])
        if text.endswith('```'):
            text = text[:-3]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {**result, 'json': None, 'parse_error': True}
    return {**result, 'json': parsed, 'parse_error': False}

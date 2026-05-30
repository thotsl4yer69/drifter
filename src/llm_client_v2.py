#!/usr/bin/env python3
"""
MZ1312 DRIFTER — LLM Client v2

Cascade: Claude (primary) -> Groq (fast/free) -> Ollama (local offline).

Features:
  - Per-(system, prompt, max_tokens) response cache with TTL + size cap.
  - Per-backend health tracking with cooldown after repeated failures.
  - Auth failures don't trigger the cooldown — they won't fix themselves
    on retry, but a key being unset is also not a "transient" condition.
  - Streaming variant for Claude with safe non-streaming fallback.
  - Robust JSON-from-LLM extraction (handles ```json fences and prose).
  - A shared requests.Session for connection pooling and consistent UA.

UNCAGED TECHNOLOGY — EST 1991
"""

import hashlib
import json
import logging
import random
import re
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

# ── HTTP session — reused across requests for connection pooling ──
_session = requests.Session()
_session.headers.update({"User-Agent": "drifter-llm-client/2"})

# ── Module state ──
_cache: dict = {}
_cache_lock = threading.Lock()
_CACHE_MAX = 200
_CACHE_TRIM = 20

_health: dict = {}
_health_lock = threading.Lock()


def _init_health() -> None:
    """Make sure every backend in LLM_CASCADE_ORDER has a health record."""
    with _health_lock:
        for name in LLM_CASCADE_ORDER:
            _health.setdefault(name, {"ok": True, "last_fail": 0.0, "fails": 0})


_init_health()

# ── Cooldown after repeated failures ──
BACKEND_COOLDOWN_SECONDS = 60
BACKEND_FAIL_THRESHOLD = 3
BACKOFF_BASE_S = 0.5
BACKOFF_JITTER_S = 0.4

# ── Sanity limits ──
MAX_PROMPT_CHARS = 80_000       # ~20k tokens, well below Claude's input limit


def _cache_key(prompt: str, system: str, max_tokens: int) -> str:
    """Hash system + prompt + max_tokens together so a wider request
    doesn't get served a previously-truncated response."""
    h = hashlib.sha256()
    h.update(system.encode('utf-8'))
    h.update(b'\x1f')
    h.update(prompt.encode('utf-8'))
    h.update(b'\x1f')
    h.update(str(max_tokens).encode('utf-8'))
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
        if len(_cache) > _CACHE_MAX:
            oldest = sorted(_cache.items(), key=lambda kv: kv[1]['ts'])[:_CACHE_TRIM]
            for k, _ in oldest:
                _cache.pop(k, None)


def _backend_ok(name: str) -> bool:
    """Return False if backend is in cooldown after repeated failures.

    A backend in cooldown self-releases once the cooldown window expires;
    the next call will get a clean attempt.
    """
    with _health_lock:
        info = _health.setdefault(name, {"ok": True, "last_fail": 0.0, "fails": 0})
        if info.get('fails', 0) >= BACKEND_FAIL_THRESHOLD:
            if time.time() - info.get('last_fail', 0) < BACKEND_COOLDOWN_SECONDS:
                return False
            info['fails'] = 0
        return True


def _mark_fail(name: str, err: str, counts_toward_cooldown: bool = True) -> None:
    """Record a failure. counts_toward_cooldown=False for auth errors etc.

    Auth failures (missing/invalid key) won't get better on retry but
    also aren't an excuse to stop trying every minute — keep ok=False so
    callers can introspect, but don't increment fails toward the cooldown.
    """
    with _health_lock:
        info = _health.setdefault(name, {"ok": True, "last_fail": 0.0, "fails": 0})
        if counts_toward_cooldown:
            info['fails'] = info.get('fails', 0) + 1
        info['last_fail'] = time.time()
        info['ok'] = False
    log.warning(f"{name} failed: {err}")


def _mark_ok(name: str) -> None:
    with _health_lock:
        info = _health.setdefault(name, {"ok": True, "last_fail": 0.0, "fails": 0})
        info['ok'] = True
        info['fails'] = 0


def health() -> dict:
    """Snapshot of per-backend health for diagnostics/dashboard."""
    with _health_lock:
        return {k: dict(v) for k, v in _health.items()}


def reset_cooldown(name: Optional[str] = None) -> None:
    """Force a backend (or all) out of cooldown — useful for manual recovery."""
    with _health_lock:
        if name is None:
            for info in _health.values():
                info['fails'] = 0
                info['ok'] = True
        elif name in _health:
            _health[name]['fails'] = 0
            _health[name]['ok'] = True


# ── Backend implementations ──

class _AuthError(RuntimeError):
    """Raised when a backend can't run because of a config issue (no key, etc.)."""


def _call_claude(prompt: str, system: str, max_tokens: int) -> dict:
    if not ANTHROPIC_API_KEY:
        raise _AuthError("ANTHROPIC_API_KEY not set")
    resp = _session.post(
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
    if resp.status_code in (401, 403):
        raise _AuthError(f"Claude auth failed HTTP {resp.status_code}")
    if resp.status_code != 200:
        raise RuntimeError(f"Claude HTTP {resp.status_code}: {resp.text[:200]}")
    try:
        data = resp.json()
        text = data["content"][0]["text"]
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"Claude bad response shape: {e}")
    usage = data.get("usage") or {}
    return {
        "text": text,
        "model": f"anthropic/{ANTHROPIC_MODEL}",
        "backend": "claude",
        "tokens": int(usage.get("input_tokens", 0) or 0)
                  + int(usage.get("output_tokens", 0) or 0),
    }


def _call_groq(prompt: str, system: str, max_tokens: int) -> dict:
    if not GROQ_API_KEY:
        raise _AuthError("GROQ_API_KEY not set")
    resp = _session.post(
        f"{GROQ_BASE_URL}/chat/completions",
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
        },
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
    if resp.status_code in (401, 403):
        raise _AuthError(f"Groq auth failed HTTP {resp.status_code}")
    if resp.status_code != 200:
        raise RuntimeError(f"Groq HTTP {resp.status_code}: {resp.text[:200]}")
    try:
        data = resp.json()
        text = data["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"Groq bad response shape: {e}")
    usage = data.get("usage") or {}
    return {
        "text": text,
        "model": f"groq/{GROQ_MODEL}",
        "backend": "groq",
        "tokens": int(usage.get("total_tokens", 0) or 0),
    }


def _call_ollama(prompt: str, system: str, max_tokens: int) -> dict:
    url = f"http://{OLLAMA_HOST}:{OLLAMA_PORT}/api/generate"
    try:
        resp = _session.post(
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
    except requests.exceptions.ConnectionError as e:
        # Distinguish "Ollama isn't installed/running" from a real failure
        raise RuntimeError(f"Ollama unreachable: {e}")
    if resp.status_code != 200:
        raise RuntimeError(f"Ollama HTTP {resp.status_code}: {resp.text[:200]}")
    try:
        data = resp.json()
    except ValueError as e:
        raise RuntimeError(f"Ollama bad response: {e}")
    return {
        "text": (data.get("response") or "").strip(),
        "model": f"ollama/{OLLAMA_MODEL}",
        "backend": "ollama",
        "tokens": int(data.get("eval_count", 0) or 0),
    }


_BACKEND_FN = {
    "claude": _call_claude,
    "groq": _call_groq,
    "ollama": _call_ollama,
}


def _backoff_sleep(attempt: int) -> None:
    """Exponential backoff with jitter — avoids thundering herd on retry."""
    delay = BACKOFF_BASE_S * (2 ** attempt) + random.uniform(0, BACKOFF_JITTER_S)
    time.sleep(delay)


# ── Public API ──

def query(
    prompt: str,
    system: str = "",
    max_tokens: int = 800,
    cache: bool = True,
    order: Iterable[str] = LLM_CASCADE_ORDER,
) -> dict:
    """Query the cascade. Returns dict with text, model, backend, tokens, cached.

    Raises RuntimeError if every backend fails (last error attached).
    Auth failures skip the affected backend but don't count toward cooldown.
    """
    if not isinstance(prompt, str):
        prompt = str(prompt)
    if len(prompt) > MAX_PROMPT_CHARS:
        log.warning(f"prompt too large ({len(prompt)} chars) — truncating")
        prompt = prompt[:MAX_PROMPT_CHARS]

    if cache:
        cached = _cache_get(_cache_key(prompt, system, max_tokens))
        if cached:
            log.info(f"Cache hit ({cached.get('backend', '?')})")
            return {**cached, 'cached': True}

    last_err: Optional[Exception] = None
    tried_any = False

    for name in order:
        fn = _BACKEND_FN.get(name)
        if fn is None:
            continue
        if not _backend_ok(name):
            log.info(f"{name} in cooldown — skipping")
            continue

        for attempt in range(LLM_MAX_RETRIES):
            tried_any = True
            try:
                result = fn(prompt, system, max_tokens)
                _mark_ok(name)
                if cache:
                    _cache_put(_cache_key(prompt, system, max_tokens), result)
                log.info(f"{name} -> {result.get('tokens', 0)} tokens")
                return {**result, 'cached': False}
            except _AuthError as e:
                # Auth doesn't get better on retry — skip remaining attempts.
                last_err = e
                _mark_fail(name, str(e), counts_toward_cooldown=False)
                break
            except Exception as e:
                last_err = e
                _mark_fail(name, str(e), counts_toward_cooldown=True)
                if attempt + 1 < LLM_MAX_RETRIES:
                    _backoff_sleep(attempt)

    if not tried_any:
        raise RuntimeError("No LLM backend available (all skipped or unconfigured)")
    raise RuntimeError(f"All LLM backends failed: {last_err}")


def stream(
    prompt: str,
    system: str = "",
    max_tokens: int = 800,
    on_token: Optional[Callable[[str], None]] = None,
) -> dict:
    """Streaming variant — Claude SSE first, fall back to non-streaming on failure.

    Calls on_token(text_delta) for each chunk if provided. Returns the same
    shape as query() so callers can treat both uniformly.
    """
    if ANTHROPIC_API_KEY and _backend_ok("claude"):
        try:
            return _stream_claude(prompt, system, max_tokens, on_token)
        except _AuthError as e:
            _mark_fail("claude", str(e), counts_toward_cooldown=False)
        except Exception as e:
            _mark_fail("claude", str(e), counts_toward_cooldown=True)
    return query(prompt, system, max_tokens, cache=False)


def _stream_claude(
    prompt: str,
    system: str,
    max_tokens: int,
    on_token: Optional[Callable[[str], None]],
) -> dict:
    with _session.post(
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
        if resp.status_code in (401, 403):
            raise _AuthError(f"Claude stream auth HTTP {resp.status_code}")
        if resp.status_code != 200:
            body = ""
            try:
                body = resp.text[:200]
            except Exception:
                pass
            raise RuntimeError(f"Claude stream HTTP {resp.status_code}: {body}")
        text_parts: list[str] = []
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
            delta = obj.get("delta") or {}
            chunk = delta.get("text", "")
            if chunk:
                text_parts.append(chunk)
                if on_token:
                    try:
                        on_token(chunk)
                    except Exception as e:
                        log.warning(f"on_token callback raised: {e}")
            usage = obj.get("usage")
            if usage:
                tokens = (int(usage.get("input_tokens", 0) or 0)
                          + int(usage.get("output_tokens", 0) or 0))
        _mark_ok("claude")
        return {
            "text": ''.join(text_parts),
            "model": f"anthropic/{ANTHROPIC_MODEL}",
            "backend": "claude",
            "tokens": tokens,
            "cached": False,
        }


# Match a fenced block whether tagged ```json or bare ```.
_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)```", re.DOTALL | re.IGNORECASE)
# Find first balanced-looking JSON object as a last resort.
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json_text(text: str) -> str:
    """Pull JSON out of an LLM response. Handles fences and prose around it."""
    if not text:
        return text
    text = text.strip()
    # Whole response is a fenced block?
    m = _FENCE_RE.search(text)
    if m:
        return m.group(1).strip()
    # Plain text with prose around an object — grab the outermost { ... }
    if not text.startswith("{"):
        m2 = _JSON_OBJECT_RE.search(text)
        if m2:
            return m2.group(0).strip()
    return text


def query_json(prompt: str, system: str = "", max_tokens: int = 800) -> dict:
    """Query the cascade and parse the response as JSON.

    Always returns the original result dict; adds 'json' (parsed dict or None)
    and 'parse_error' (bool). Never raises on parse failure — caller can
    inspect 'raw' (via result['text']) if needed.
    """
    result = query(prompt, system, max_tokens)
    text = result.get('text', '') or ''
    candidate = _extract_json_text(text)
    try:
        parsed = json.loads(candidate)
        if not isinstance(parsed, (dict, list)):
            return {**result, 'json': None, 'parse_error': True}
        return {**result, 'json': parsed, 'parse_error': False}
    except json.JSONDecodeError:
        return {**result, 'json': None, 'parse_error': True}

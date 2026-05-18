#!/usr/bin/env python3
"""
MZ1312 DRIFTER — LLM Client (v2, adapted for main)
Cascade: Ollama (local, offline) → Groq → Claude.
Default cascade order is ['ollama'] — no cloud keys required.
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
    OLLAMA_HOST, OLLAMA_PORT, OLLAMA_MODEL, OLLAMA_TIMEOUT,
    LLM_CASCADE_ORDER, LLM_CLAUDE_TIMEOUT, LLM_GROQ_TIMEOUT, LLM_OLLAMA_TIMEOUT,
    LLM_CACHE_TTL, LLM_MAX_RETRIES,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [LLMV2] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

# ── System prompts (preserved from previous llm_client) ──
SYSTEM_PROMPT = """You are an expert diagnostic technician specialising in the \
2004 Jaguar X-Type 2.5L V6 (AJ-V6 engine). This is an Australian-delivered, \
right-hand-drive, AWD vehicle with Jatco JF506E 5-speed automatic.

You receive structured telemetry data and anomaly events from a live OBD-II/CAN bus \
monitoring system (DRIFTER). Analyse the data and produce a structured diagnosis.

CRITICAL: Return valid JSON ONLY — no markdown fences, no explanation outside the JSON.

JSON structure required:
{
  "primary_suspect": {
    "diagnosis": "...",
    "confidence": 0-100,
    "evidence": "...",
    "confirm_with": "..."
  },
  "secondary_suspects": [
    {"diagnosis": "...", "confidence": 0-100, "evidence": "..."}
  ],
  "watch_items": ["..."],
  "action_items": ["..."],
  "safety_critical": true/false,
  "safety_note": "..."
}

VEHICLE CONTEXT:
- Known history: valve cover gasket oil leak into plug wells, prior spark plug overtorque failure
- Current symptoms: P0303 cylinder 3 misfire, cruise control disabled above 3000rpm, rough idle
- Suspected vacuum leaks: PCV hose, IMT valve O-ring, brake booster hose
- AWD system: Haldex coupling + PTU (known weak point in Australian heat)

Rules:
- Be specific to the X-Type — cite known failure modes (thermostat housing, coil packs, MAF, vacuum leaks, valve cover gaskets, solenoid C)
- THINK THROUGH the diagnosis — consider interconnected failures (e.g., oil leak → coil death → misfire → cruise disable)
- Rank by probability, cite the actual data values that support each suspect
- Give actionable tests (smoke test, coil swap test, compression test, multimeter reading)
- Flag anything safety-critical immediately with safety_critical: true
- Cost estimates in AUD (Australian Dollars)
- Consider Australian conditions (heat stress on cooling, rubber, fluids)
"""

CHAT_SYSTEM_PROMPT = """You are an expert diagnostic technician and mechanic specialising in the \
2004 Jaguar X-Type 2.5L V6 (AJ-V6 engine). This is an Australian-delivered, \
right-hand-drive, AWD vehicle with the Jatco JF506E 5-speed automatic.

You are running on DRIFTER — a vehicle intelligence system on Raspberry Pi 5 \
(Kali Linux) with live OBD-II/CAN bus telemetry. You may be given live sensor \
readings and knowledge base context alongside each question.

Your approach:
- Be direct, practical, and experienced. Answer conversationally like a real mechanic.
- Reference the live telemetry values when relevant ("Your coolant is at 95°C which suggests...")
- Cite known X-Type failure modes when applicable (thermostat, coil packs, MAF, vacuum leaks)
- Give actionable advice with difficulty ratings and AUD cost estimates
- ALWAYS prioritise safety — flag anything dangerous immediately
- Keep responses concise — the driver may be reading on a phone mounted in the car

Do NOT return JSON. Respond in clear, readable text.

VEHICLE CONTEXT:
- Known history: valve cover gasket oil leak, prior spark plug overtorque failure
- Current symptoms: P0303 cylinder 3 misfire, cruise control disabled, rough idle
- Suspected: vacuum leaks (PCV hose, IMT valve O-ring, brake booster hose)
"""

TIMEOUT_SECONDS = 45

# ── Module state ──
_cache: dict = {}
_cache_lock = threading.Lock()
_health: dict = {name: {"ok": True, "last_fail": 0.0, "fails": 0} for name in LLM_CASCADE_ORDER}
_health_lock = threading.Lock()

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
            oldest = sorted(_cache.items(), key=lambda kv: kv[1]['ts'])[:20]
            for k, _ in oldest:
                _cache.pop(k, None)


def _backend_ok(name: str) -> bool:
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


def _call_ollama(prompt: str, system: str = SYSTEM_PROMPT, max_tokens: int = 800) -> dict:
    """Call Ollama /api/generate. Accepts both (prompt,) and (prompt, system, max_tokens)."""
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


# ── Public cascade API ──

def query(
    prompt: str,
    system: str = "",
    max_tokens: int = 800,
    cache: bool = True,
    order: Optional[Iterable[str]] = None,
) -> dict:
    """Query the cascade. Returns dict with text, model, backend, tokens."""
    if order is None:
        order = LLM_CASCADE_ORDER
    if cache:
        cached = _cache_get(_cache_key(prompt, system))
        if cached:
            log.info(f"Cache hit ({cached.get('backend')})")
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


def query_json(prompt: str, system: str = "", max_tokens: int = 800) -> dict:
    """Query cascade and parse response as JSON. Strips markdown fences."""
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


# ── Backward-compatible API for existing callers ──

def query_llm(prompt: str) -> dict:
    """Structured JSON diagnostic query (existing callers: session_analyst.py)."""
    return query(prompt, system=SYSTEM_PROMPT)


def query_chat(prompt: str) -> dict:
    """Conversational chat query (existing callers: web_dashboard_handlers.py)."""
    return query(prompt, system=CHAT_SYSTEM_PROMPT)


def stream_chat_ollama(prompt: str):
    """
    Stream tokens from Ollama for the Ask Mechanic feature.
    Yields {"token": str} per chunk, then {"done": True, "model": str, "tokens": int}.
    Falls back to non-streaming query_chat if Ollama streaming fails.
    """
    url = f"http://{OLLAMA_HOST}:{OLLAMA_PORT}/api/chat"
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": CHAT_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "stream": True,
        "options": {
            "temperature": 0.7,
            "num_predict": 500,
        },
    }
    try:
        resp = requests.post(url, json=payload, timeout=OLLAMA_TIMEOUT, stream=True)
        if resp.status_code != 200:
            raise RuntimeError(f"Ollama HTTP {resp.status_code}")
        full_text = ""
        tokens = 0
        for line in resp.iter_lines():
            if not line:
                continue
            chunk = json.loads(line)
            if chunk.get("done"):
                tokens = chunk.get("eval_count", 0) + chunk.get("prompt_eval_count", 0)
                break
            content = chunk.get("message", {}).get("content", "")
            if content:
                full_text += content
                yield {"token": content}
        yield {"done": True, "model": f"ollama/{OLLAMA_MODEL}", "tokens": tokens, "text": full_text}
    except Exception as e:
        log.warning(f"Ollama streaming failed ({e}), falling back to non-streaming")
        try:
            result = query_chat(prompt)
            yield {"token": result["text"]}
            yield {"done": True, "model": result["model"], "tokens": result["tokens"], "text": result["text"]}
        except Exception as e2:
            yield {"error": str(e2)}

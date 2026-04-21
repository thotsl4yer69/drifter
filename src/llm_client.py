#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Model-Agnostic LLM Client
Primary: Ollama (local, offline)
Fallback: Groq (Llama 3.3 70B, free tier) → Claude (claude-sonnet-4-6)
UNCAGED TECHNOLOGY — EST 1991
"""

import json
import logging
import requests
from typing import Optional

from config import (
    GROQ_API_KEY, GROQ_MODEL, GROQ_BASE_URL,
    ANTHROPIC_API_KEY, ANTHROPIC_MODEL,
    OLLAMA_HOST, OLLAMA_PORT, OLLAMA_MODEL, OLLAMA_TIMEOUT,
    LLM_PRIMARY,
)

log = logging.getLogger(__name__)

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

TIMEOUT_SECONDS = 45

# Conversational system prompt — for the Ask Mechanic dashboard feature.
# This is separate from SYSTEM_PROMPT (above) which forces JSON output for reports.
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


def _call_ollama(prompt: str) -> dict:
    """Call local Ollama instance. Raises on any failure."""
    url = f"http://{OLLAMA_HOST}:{OLLAMA_PORT}/api/chat"
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0.3,
            "num_predict": 400,
        },
    }
    resp = requests.post(url, json=payload, timeout=OLLAMA_TIMEOUT)
    if resp.status_code != 200:
        raise RuntimeError(f"Ollama HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    text = data.get("message", {}).get("content", "")
    tokens = data.get("eval_count", 0) + data.get("prompt_eval_count", 0)
    return {
        "text": text,
        "model": f"ollama/{OLLAMA_MODEL}",
        "tokens": tokens,
    }


def _call_groq(prompt: str) -> dict:
    """Call Groq API. Raises on any failure."""
    resp = requests.post(
        f"{GROQ_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
        json={
            "model": GROQ_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
            "max_tokens": 1000,
        },
        timeout=TIMEOUT_SECONDS,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Groq HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"Unexpected Groq response structure: {e}") from e
    return {
        "text": text,
        "model": f"groq/{GROQ_MODEL}",
        "tokens": data.get("usage", {}).get("total_tokens", 0),
    }


def _call_claude(prompt: str) -> dict:
    """Call Anthropic Claude API. Raises on any failure."""
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        json={
            "model": ANTHROPIC_MODEL,
            "max_tokens": 1000,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=TIMEOUT_SECONDS,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Claude HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    usage = data.get("usage", {})
    try:
        text = data["content"][0]["text"]
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"Unexpected Claude response structure: {e}") from e
    return {
        "text": text,
        "model": f"anthropic/{ANTHROPIC_MODEL}",
        "tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
    }


def query_llm(prompt: str) -> dict:
    """
    Query the LLM for structured JSON diagnostic reports.
    Returns: {"text": str, "model": str, "tokens": int}
    Priority configurable via LLM_PRIMARY env var.
    """
    if LLM_PRIMARY == "ollama":
        chain = [("Ollama", _call_ollama), ("Groq", _call_groq), ("Claude", _call_claude)]
    else:
        chain = [("Groq", _call_groq), ("Claude", _call_claude), ("Ollama", _call_ollama)]

    last_error = None
    for name, fn in chain:
        try:
            result = fn(prompt)
            log.info(f"{name} response: {result['tokens']} tokens")
            return result
        except Exception as e:
            log.warning(f"{name} failed ({e}), trying next backend")
            last_error = e

    raise RuntimeError("All LLM backends failed") from last_error


# ═══════════════════════════════════════════════════════════════════
#  Conversational Chat — for Ask Mechanic dashboard feature
# ═══════════════════════════════════════════════════════════════════

def _chat_ollama(prompt: str) -> dict:
    """Ollama chat — no JSON format, conversational."""
    url = f"http://{OLLAMA_HOST}:{OLLAMA_PORT}/api/chat"
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": CHAT_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "options": {
            "temperature": 0.7,
            "num_predict": 200,
        },
    }
    resp = requests.post(url, json=payload, timeout=OLLAMA_TIMEOUT)
    if resp.status_code != 200:
        raise RuntimeError(f"Ollama HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    text = data.get("message", {}).get("content", "")
    tokens = data.get("eval_count", 0) + data.get("prompt_eval_count", 0)
    return {"text": text, "model": f"ollama/{OLLAMA_MODEL}", "tokens": tokens}


def _chat_groq(prompt: str) -> dict:
    """Groq chat — conversational."""
    resp = requests.post(
        f"{GROQ_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
        json={
            "model": GROQ_MODEL,
            "messages": [
                {"role": "system", "content": CHAT_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.7,
            "max_tokens": 500,
        },
        timeout=TIMEOUT_SECONDS,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Groq HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"Unexpected Groq chat response structure: {e}") from e
    return {
        "text": text,
        "model": f"groq/{GROQ_MODEL}",
        "tokens": data.get("usage", {}).get("total_tokens", 0),
    }


def _chat_claude(prompt: str) -> dict:
    """Claude chat — conversational."""
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        json={
            "model": ANTHROPIC_MODEL,
            "max_tokens": 500,
            "system": CHAT_SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=TIMEOUT_SECONDS,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Claude HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    usage = data.get("usage", {})
    try:
        text = data["content"][0]["text"]
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"Unexpected Claude chat response structure: {e}") from e
    return {
        "text": text,
        "model": f"anthropic/{ANTHROPIC_MODEL}",
        "tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
    }


def query_chat(prompt: str) -> dict:
    """
    Conversational LLM query for Ask Mechanic.
    Returns natural language (NOT JSON). Same cascading fallback.
    Returns: {"text": str, "model": str, "tokens": int}
    """
    if LLM_PRIMARY == "ollama":
        chain = [("Ollama", _chat_ollama), ("Groq", _chat_groq), ("Claude", _chat_claude)]
    else:
        chain = [("Groq", _chat_groq), ("Claude", _chat_claude), ("Ollama", _chat_ollama)]

    last_error = None
    for name, fn in chain:
        try:
            result = fn(prompt)
            log.info(f"Chat [{name}]: {result['tokens']} tokens")
            return result
        except Exception as e:
            log.warning(f"Chat {name} failed ({e}), trying next backend")
            last_error = e

    raise RuntimeError("All LLM backends failed") from last_error


# ═══════════════════════════════════════════════════════════════════
#  Streaming Chat — yields tokens as they arrive from Ollama
# ═══════════════════════════════════════════════════════════════════

def stream_chat_ollama(prompt: str):
    """
    Stream tokens from Ollama for the Ask Mechanic feature.
    Yields dicts: {"token": str} for each token, then {"done": True, "model": str, "tokens": int}.
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

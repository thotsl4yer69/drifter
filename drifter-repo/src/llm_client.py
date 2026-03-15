#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Model-Agnostic LLM Client
Primary: Groq (Llama 3.3 70B, free tier)
Fallback: Claude (claude-sonnet-4-6) on any failure
UNCAGED TECHNOLOGY — EST 1991
"""

import json
import logging
import requests
from typing import Optional

from config import (
    GROQ_API_KEY, GROQ_MODEL, GROQ_BASE_URL,
    ANTHROPIC_API_KEY, ANTHROPIC_MODEL,
)

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an expert diagnostic technician specialising in the \
2004 Jaguar X-Type 2.5L V6 (AJ-V6 engine).

You receive structured telemetry data and anomaly events from a live OBD-II \
monitoring system. Analyse the data and produce a structured diagnosis.

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

Rules:
- Be specific to the X-Type — cite known failure modes (thermostat housing, coil packs, MAF, vacuum leaks)
- Rank by probability, cite the actual data values that support each suspect
- Give actionable tests (smoke test, swap test, multimeter reading)
- Flag anything safety-critical immediately with safety_critical: true
- Cost estimates in GBP where relevant
"""

TIMEOUT_SECONDS = 45


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
    return {
        "text": data["choices"][0]["message"]["content"],
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
    return {
        "text": data["content"][0]["text"],
        "model": f"anthropic/{ANTHROPIC_MODEL}",
        "tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
    }


def query_llm(prompt: str) -> dict:
    """
    Query the LLM with automatic fallback.
    Returns: {"text": str, "model": str, "tokens": int}
    """
    try:
        result = _call_groq(prompt)
        log.info(f"Groq response: {result['tokens']} tokens")
        return result
    except Exception as e:
        log.warning(f"Groq failed ({e}), falling back to Claude")

    try:
        result = _call_claude(prompt)
        log.info(f"Claude response: {result['tokens']} tokens")
        return result
    except Exception as e:
        log.error(f"Claude also failed: {e}")
        raise RuntimeError("All LLM backends failed") from e

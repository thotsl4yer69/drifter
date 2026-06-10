"""Ask-Mechanic conversation history + LLM query-context assembly.

Extracted verbatim from web_dashboard_handlers.py (non-security HUD helper
group). The handler module re-imports these names so the public API at
web_dashboard_handlers.X is unchanged.

Stdlib + corpus only — no torch. corpus_search() is a no-op (returns []) in
the embed-free dashboard process (DRIFTER_CORPUS_NO_EMBED), so importing it
here does not pull sentence-transformers/torch into the memory-capped HUD.
"""
from __future__ import annotations

import json
import logging
import threading as _threading
import time
from collections import deque as _deque
from pathlib import Path

import web_dashboard_state as state
from corpus import corpus_search

log = logging.getLogger(__name__)

# Conversation history ring for "Ask Mechanic". Keep the last few turns and
# trim oldest turns while the joined char-count exceeds the char budget,
# which approximates the ~2000-token ceiling at ~4 chars/token.
MECHANIC_HISTORY_TURNS = 10
MECHANIC_HISTORY_CHAR_BUDGET = 8000

_mechanic_history: _deque = _deque(maxlen=MECHANIC_HISTORY_TURNS)
_mechanic_history_lock = _threading.Lock()


def _mechanic_history_append(role: str, content: str) -> None:
    """Append a turn to the ring buffer and trim to char budget."""
    if not isinstance(content, str) or not content.strip():
        return
    with _mechanic_history_lock:
        _mechanic_history.append({
            'ts': time.time(),
            'role': role,
            'content': content.strip(),
        })
        # Trim oldest turns while the budget is exceeded. Char-count
        # is the proxy for token-count (≈ 4 chars/token).
        total = sum(len(t.get('content') or '') for t in _mechanic_history)
        while total > MECHANIC_HISTORY_CHAR_BUDGET and len(_mechanic_history) > 1:
            dropped = _mechanic_history.popleft()
            total -= len(dropped.get('content') or '')


def _mechanic_history_reset() -> None:
    with _mechanic_history_lock:
        _mechanic_history.clear()


def _mechanic_history_snapshot() -> list:
    with _mechanic_history_lock:
        return list(_mechanic_history)


def _mechanic_history_block() -> str:
    """Render the ring as a CONVERSATION HISTORY context block."""
    turns = _mechanic_history_snapshot()
    if not turns:
        return ''
    lines = []
    for t in turns:
        role = (t.get('role') or '').upper()
        content = (t.get('content') or '').strip()
        if not content:
            continue
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


_TELEMETRY_LINES = [
    ('engine_rpm',      'RPM',      '{:.0f}'),
    ('engine_coolant',  'Coolant',  '{:.1f}°C'),
    ('vehicle_speed',   'Speed',    '{:.0f} km/h'),
    ('engine_stft1',    'STFT B1',  '{:+.1f}%'),
    ('engine_stft2',    'STFT B2',  '{:+.1f}%'),
    ('engine_ltft1',    'LTFT B1',  '{:+.1f}%'),
    ('engine_ltft2',    'LTFT B2',  '{:+.1f}%'),
    ('power_voltage',   'Battery',  '{:.1f}V'),
    ('engine_load',     'Load',     '{:.0f}%'),
    ('vehicle_throttle','Throttle', '{:.0f}%'),
    ('engine_iat',      'IAT',      '{:.0f}°C'),
    ('engine_maf',      'MAF',      '{:.1f} g/s'),
]


def _query_telemetry_keys():
    """Return [(state_key, label), ...] — single source of truth shared
    between build_query_context and the grounding validator."""
    return [(k, label) for k, label, _ in _TELEMETRY_LINES]


# Path to the drifter-feeds aggregator output. Single source so the
# helper below stays in lockstep with feeds.SUMMARY_PATH.
_FEEDS_SUMMARY_PATH = Path('/opt/drifter/state/feeds_summary.json')


def _format_feed_context() -> str | None:
    """Read drifter-feeds aggregator output and produce a compact live-
    context block: weather, EMV incidents, BOM warnings, interesting
    aircraft. Empty when the file is missing, stale (>10min), or all
    sub-sections are empty. Capped to ~2 KB to stay well under 500 tokens.

    Ported verbatim from the retired vivi v1 module so the dashboard query
    path and (formerly) the voice path render feeds identically. Self-
    contained — stdlib only — so the memory-capped HUD process doesn't have
    to import the heavy feeds aggregator just to read its output file."""
    try:
        s = json.loads(_FEEDS_SUMMARY_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    age = int(time.time() - float(s.get('ts', 0) or 0))
    if age > 600:
        return None
    o = s.get('origin') or {}
    src = o.get('source', '?')
    radius = s.get('radius_km', '?')
    parts = [f"[Live context — age {age}s, origin {src}, radius {radius}km]"]
    w = s.get('weather') or {}
    if any(w.get(k) is not None for k in ('temp_c', 'wind_kmh')):
        parts.append(
            f"Weather: {w.get('temp_c')}°C feels {w.get('feels_c')}°C, "
            f"wind {w.get('wind_kmh')} km/h gust {w.get('gust_kmh')}, "
            f"rain {w.get('rain_mm') or 0}mm"
        )
    inc = int(s.get('incidents_nearby') or 0)
    parts.append(f"EMV incidents nearby: {inc}")
    for it in (s.get('incidents_top') or [])[:3]:
        parts.append(
            f"  - {it.get('category1')} @ {it.get('location')} "
            f"({it.get('distance_km')}km, status {it.get('status')})"
        )
    wc = int(s.get('warnings_count') or 0)
    if wc:
        parts.append(f"BOM VIC warnings: {wc}")
        for it in (s.get('warnings_top') or [])[:2]:
            parts.append(f"  - {it.get('title')}")
    interesting = s.get('aircraft_interesting') or []
    if interesting:
        parts.append("Interesting aircraft nearby: " + ", ".join(
            f"{a.get('flight') or a.get('hex')}@{a.get('distance_km')}km"
            for a in interesting[:3]
        ))
    body = '\n'.join(parts)
    return body[:2000]


def build_query_context(query: str) -> str:
    """Assemble the prompt the LLM sees when you ask a question in the UI.

    Exposed at module scope so tests can reuse it without instantiating
    a handler.
    """
    def _v(key):
        d = state.latest_state.get(key, {})
        return d.get('value') if isinstance(d, dict) else None

    TELEMETRY_LINES = _TELEMETRY_LINES

    telem_lines = []
    for key, label, fmt in TELEMETRY_LINES:
        v = _v(key)
        if v is not None:
            telem_lines.append(f"{label}: {fmt.format(v)}")
        else:
            # Explicit NO DATA — the model must SEE the absence rather
            # than infer one. Closes the hallucination class where the
            # LLM invented values to satisfy its mechanic persona.
            telem_lines.append(f"{label}: NO DATA")

    dtc_data = state.latest_state.get('diag_dtc', {})
    if isinstance(dtc_data, dict):
        if dtc_data.get('stored'):
            telem_lines.append(f"Active DTCs: {', '.join(dtc_data['stored'])}")
        if dtc_data.get('pending'):
            telem_lines.append(f"Pending DTCs: {', '.join(dtc_data['pending'])}")

    alert_d = state.latest_state.get('alert_message', {})
    if isinstance(alert_d, dict):
        alert_msg = alert_d.get('message', '')
        if alert_msg and alert_msg != 'Systems nominal':
            telem_lines.append(f"Active alert: {alert_msg}")

    context_parts = []
    # Conversation history — the last few user/assistant turns from this
    # session so follow-up questions resolve correctly ("what about the
    # second one?") without re-stating context every turn.
    history_block = _mechanic_history_block()
    if history_block:
        context_parts.append(
            "CONVERSATION HISTORY (most recent turns; use to resolve "
            "pronouns and follow-ups, not as a source of telemetry):\n"
            + history_block
        )
    # Telemetry is always emitted with explicit NO DATA markers — the
    # model must see absent sensors rather than have to infer their
    # absence from a vague "car may be off" line.
    context_parts.append(
        "CURRENT VEHICLE STATE (NO DATA = no current reading; do NOT "
        "invent, estimate, or infer a value for any sensor marked "
        "NO DATA):\n" + "\n".join(telem_lines)
    )

    # Live public-data feeds — same source the cockpit reads. The formatter
    # lives in this module now (ported from the retired vivi v1 module) so
    # the format stays identical to what the cockpit renders. A None means
    # the feeds aggregator is offline / stale (>10 min) and we omit cleanly.
    try:
        feed_block = _format_feed_context()
        if feed_block:
            context_parts.append("LIVE EXTERIOR CONTEXT (use these numbers verbatim "
                                 "— do not invent or refer to coolant/engine):\n"
                                 + feed_block)
    except Exception as e:
        log.debug(f"feed-context build failed: {e}")

    # Corpus retrieval — top 3 chunks ranked by cosine similarity. In the
    # embed-free dashboard process this is a no-op (corpus_search returns []),
    # so the LLM prompt simply omits the RELEVANT KNOWLEDGE block rather than
    # loading sentence-transformers/torch and OOM-killing the memory-capped HUD.
    kb_lines = []
    for hit in corpus_search(query, k=3, min_similarity=0.4):
        topic = hit.get('topic') or hit.get('section') or 'reference'
        body = (hit.get('content') or '').strip().replace('\n', ' ')[:400]
        kb_lines.append(f"{topic}: {body}")
    if kb_lines:
        context_parts.append("RELEVANT KNOWLEDGE:\n" + "\n---\n".join(kb_lines))

    # Recency-attended reminder — qwen2.5 weights instructions later in
    # the prompt more strongly. The static-spec loophole was real:
    # 1.5b read "normal coolant range 85-100°C" from the corpus and
    # answered "Your coolant is at 95°C". The reminder now explicitly
    # forbids quoting a number for a NO DATA sensor even if the
    # knowledge base documents a normal range.
    context_parts.append(
        "REMINDER: If a sensor in the CURRENT VEHICLE STATE block above "
        "shows NO DATA, you MUST respond that you don't have a current "
        "reading for it. Do NOT state any specific number for that "
        "sensor — not from a normal range, not from a static spec, "
        "not from a knowledge-base reference. Never estimate, infer, "
        "or invent sensor values."
    )

    return query + ("\n\n---\n\n" + "\n\n".join(context_parts) if context_parts else "")

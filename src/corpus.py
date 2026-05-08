#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Corpus retrieval (RAG)

Walks /opt/drifter/corpus/, chunks markdown by ## section, embeds with
sentence-transformers (all-MiniLM-L6-v2), persists vectors to SQLite at
/opt/drifter/state/corpus.db.

Public API:
  - corpus_search(query, k=3, min_similarity=0.4) → ranked hits
  - rebuild(force=False)                          → re-embed changed files
  - stats()                                       → counts + last rebuild ts

CLI: invoked via `drifter corpus stats|rebuild` (see bin/drifter).
UNCAGED TECHNOLOGY — EST 1991
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import struct
import sys
import time
from pathlib import Path
from typing import Iterable, Optional

log = logging.getLogger(__name__)

CORPUS_DIR = Path("/opt/drifter/corpus")
STATE_DIR = Path("/opt/drifter/state")
DB_PATH = STATE_DIR / "corpus.db"
EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
EMBED_DIM = 384  # MiniLM L6 v2 output size

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?\n)---\s*\n", re.DOTALL)
_SECTION_RE = re.compile(r"^##\s+(.+?)$", re.MULTILINE)
_model = None  # lazy-loaded


# ── Schema ──────────────────────────────────────────────────────────

def _connect() -> sqlite3.Connection:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chunks (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            source_path  TEXT NOT NULL,
            source_mtime REAL NOT NULL,
            section      TEXT NOT NULL,
            content      TEXT NOT NULL,
            tags         TEXT NOT NULL,
            vehicle      TEXT,
            confidence   TEXT,
            topic        TEXT,
            embedding    BLOB NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source_path)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS meta (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


# ── Frontmatter + chunking ──────────────────────────────────────────

def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Strip YAML-ish frontmatter (key: value lines, lists in [a, b] form)."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    body = text[m.end():]
    fm: dict = {}
    for line in m.group(1).splitlines():
        if ':' not in line:
            continue
        k, _, v = line.partition(':')
        k, v = k.strip(), v.strip()
        if v.startswith('[') and v.endswith(']'):
            fm[k] = [t.strip() for t in v[1:-1].split(',') if t.strip()]
        else:
            fm[k] = v
    return fm, body


def _split_sections(body: str) -> Iterable[tuple[str, str]]:
    """Yield (section_title, section_text) pairs. The text BEFORE the first
    ## becomes a section titled 'overview'."""
    parts = _SECTION_RE.split(body)
    if len(parts) == 1:
        if parts[0].strip():
            yield ('overview', parts[0].strip())
        return
    head = parts[0].strip()
    if head:
        yield ('overview', head)
    # parts after re.split are [head, title1, body1, title2, body2, …]
    pairs = zip(parts[1::2], parts[2::2])
    for title, content in pairs:
        c = content.strip()
        if c:
            yield (title.strip(), c)


# ── Embeddings ──────────────────────────────────────────────────────

def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        log.info(f"Loading embedding model {EMBED_MODEL}…")
        _model = SentenceTransformer(EMBED_MODEL)
        log.info("Embedding model ready")
    return _model


def _embed(text: str) -> bytes:
    """Encode text to a packed float32 byte string for sqlite BLOB storage."""
    vec = _get_model().encode(text, normalize_embeddings=True)
    return struct.pack(f'{EMBED_DIM}f', *vec.tolist())


def _decode_embedding(blob: bytes) -> list[float]:
    return list(struct.unpack(f'{EMBED_DIM}f', blob))


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity (vectors already normalised → just dot product)."""
    return sum(x * y for x, y in zip(a, b))


# ── Rebuild ─────────────────────────────────────────────────────────

def rebuild(force: bool = False) -> dict:
    """Walk CORPUS_DIR, embed every (file, section), upsert into the DB.
    Files whose mtime hasn't changed since the last run are skipped unless
    force=True. Returns summary stats."""
    if not CORPUS_DIR.exists():
        return {'files': 0, 'chunks': 0, 'skipped': 0, 'error': 'no corpus dir'}

    conn = _connect()
    cur = conn.cursor()

    # Index existing source mtimes for incremental rebuilds.
    if force:
        cur.execute("DELETE FROM chunks")
        existing: dict[str, float] = {}
    else:
        cur.execute("SELECT source_path, MIN(source_mtime) FROM chunks GROUP BY source_path")
        existing = {row[0]: row[1] for row in cur.fetchall()}

    files = list(CORPUS_DIR.rglob("*.md"))
    embedded = 0
    skipped = 0

    for path in files:
        rel = str(path.relative_to(CORPUS_DIR))
        mtime = path.stat().st_mtime
        if rel in existing and existing[rel] >= mtime:
            skipped += 1
            continue

        text = path.read_text(encoding='utf-8')
        fm, body = _parse_frontmatter(text)
        topic = fm.get('topic', path.stem)
        tags = fm.get('tags', [])
        if isinstance(tags, str):
            tags = [tags]
        vehicle = fm.get('vehicle', '')
        confidence = fm.get('confidence', 'medium')

        cur.execute("DELETE FROM chunks WHERE source_path = ?", (rel,))
        for section, content in _split_sections(body):
            embedding = _embed(f"{topic}\n{section}\n{content}")
            cur.execute(
                """INSERT INTO chunks
                   (source_path, source_mtime, section, content, tags,
                    vehicle, confidence, topic, embedding)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (rel, mtime, section, content, ','.join(tags),
                 vehicle, confidence, topic, embedding),
            )
            embedded += 1
        conn.commit()

    cur.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                ('last_rebuild_ts', str(time.time())))
    conn.commit()
    conn.close()
    return {'files': len(files), 'chunks': embedded, 'skipped': skipped}


# ── Search ──────────────────────────────────────────────────────────

def corpus_search(query: str, k: int = 3,
                  min_similarity: float = 0.4) -> list[dict]:
    """Return up to k chunks ranked by cosine similarity. Empty corpus or
    no match above threshold → empty list. Score-sorted descending."""
    if not query or not query.strip():
        return []
    if not DB_PATH.exists():
        return []
    try:
        q_blob = _embed(query)
        q_vec = _decode_embedding(q_blob)
    except Exception as e:
        log.warning(f"corpus_search embed failed: {e}")
        return []

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT source_path, section, content, tags, vehicle, "
        "confidence, topic, embedding FROM chunks"
    )
    scored = []
    for src, sec, content, tags, vehicle, conf, topic, blob in cur.fetchall():
        score = _cosine(q_vec, _decode_embedding(blob))
        if score >= min_similarity:
            scored.append({
                'source': src,
                'section': sec,
                'content': content,
                'tags': tags.split(',') if tags else [],
                'vehicle': vehicle,
                'confidence': conf,
                'topic': topic,
                'score': float(score),
            })
    conn.close()
    scored.sort(key=lambda x: x['score'], reverse=True)
    return scored[:k]


def dtc_lookup(code: str) -> Optional[dict]:
    """Direct lookup by DTC code (e.g. 'P0171'). Falls through to semantic
    search if the code isn't in the dtc/ subdir."""
    code = code.strip().upper()
    if not code:
        return None
    candidate = CORPUS_DIR / "dtc" / f"{code}.md"
    if candidate.exists():
        text = candidate.read_text(encoding='utf-8')
        fm, body = _parse_frontmatter(text)
        return {
            'source': str(candidate.relative_to(CORPUS_DIR)),
            'section': 'dtc',
            'content': body.strip(),
            'topic': fm.get('topic', code),
            'tags': fm.get('tags', []),
            'vehicle': fm.get('vehicle', ''),
            'confidence': fm.get('confidence', 'medium'),
            'score': 1.0,
        }
    hits = corpus_search(code, k=1, min_similarity=0.3)
    return hits[0] if hits else None


def stats() -> dict:
    """Counts, dimensions, last rebuild. Safe to call before rebuild()."""
    if not DB_PATH.exists():
        return {'files': 0, 'chunks': 0, 'embedding_dim': EMBED_DIM,
                'last_rebuild_ts': 0, 'corpus_dir': str(CORPUS_DIR),
                'db_path': str(DB_PATH), 'db_bytes': 0}
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM chunks")
    chunks = cur.fetchone()[0]
    cur.execute("SELECT COUNT(DISTINCT source_path) FROM chunks")
    files = cur.fetchone()[0]
    cur.execute("SELECT value FROM meta WHERE key='last_rebuild_ts'")
    row = cur.fetchone()
    last = float(row[0]) if row else 0.0
    conn.close()
    return {
        'files':           files,
        'chunks':          chunks,
        'embedding_dim':   EMBED_DIM,
        'last_rebuild_ts': last,
        'corpus_dir':      str(CORPUS_DIR),
        'db_path':         str(DB_PATH),
        'db_bytes':        DB_PATH.stat().st_size if DB_PATH.exists() else 0,
    }


# ── CLI ─────────────────────────────────────────────────────────────

def _cli(argv: list[str]) -> int:
    if not argv or argv[0] in ('-h', '--help', 'help'):
        print(__doc__)
        return 0
    cmd = argv[0]
    if cmd == 'stats':
        s = stats()
        for k, v in s.items():
            print(f"{k:18s} {v}")
        return 0
    if cmd == 'rebuild':
        force = '--force' in argv
        result = rebuild(force=force)
        print(json.dumps(result, indent=2))
        return 0 if not result.get('error') else 1
    if cmd == 'search':
        if len(argv) < 2:
            print("usage: corpus search <query>", file=sys.stderr)
            return 1
        for hit in corpus_search(' '.join(argv[1:])):
            print(f"  {hit['score']:.3f}  {hit['source']}  §{hit['section']}")
            print(f"        {hit['content'][:120]}")
        return 0
    print(f"unknown command {cmd!r}", file=sys.stderr)
    return 1


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    sys.exit(_cli(sys.argv[1:]))

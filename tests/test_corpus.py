# tests/test_corpus.py
"""
MZ1312 DRIFTER — corpus retrieval tests
UNCAGED TECHNOLOGY — EST 1991
"""
from __future__ import annotations

import struct
from pathlib import Path

import pytest

import corpus as corpus_mod


# Real embeddings (sentence-transformers + torch) are slow to import in CI
# and not what this layer is testing. We stub _embed with a tiny fake that
# encodes a string into a 384-dim vector by hashing characters into buckets,
# normalised. Cosine on these vectors is consistent enough to verify the
# ranking + filtering logic without pulling in pytorch.

def _fake_embed(text: str) -> bytes:
    text = (text or "").lower()
    vec = [0.0] * corpus_mod.EMBED_DIM
    for i, ch in enumerate(text):
        vec[(ord(ch) + i * 7) % corpus_mod.EMBED_DIM] += 1.0
    norm = sum(v * v for v in vec) ** 0.5 or 1.0
    vec = [v / norm for v in vec]
    return struct.pack(f'{corpus_mod.EMBED_DIM}f', *vec)


@pytest.fixture(autouse=True)
def _patch_paths_and_embed(tmp_path, monkeypatch):
    """Redirect CORPUS_DIR + DB_PATH into tmp; stub embeddings."""
    corpus_dir = tmp_path / "corpus"
    state_dir = tmp_path / "state"
    db_path = state_dir / "corpus.db"
    monkeypatch.setattr(corpus_mod, 'CORPUS_DIR', corpus_dir)
    monkeypatch.setattr(corpus_mod, 'STATE_DIR', state_dir)
    monkeypatch.setattr(corpus_mod, 'DB_PATH', db_path)
    monkeypatch.setattr(corpus_mod, '_embed', _fake_embed)
    monkeypatch.setattr(corpus_mod, '_model', None)
    return corpus_dir


def _write_md(dir_: Path, rel: str, frontmatter: dict, body: str) -> None:
    path = dir_ / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = ['---']
    for k, v in frontmatter.items():
        if isinstance(v, list):
            fm.append(f'{k}: [{", ".join(v)}]')
        else:
            fm.append(f'{k}: {v}')
    fm.append('---')
    path.write_text('\n'.join(fm) + '\n\n' + body, encoding='utf-8')


# ── corpus_search() ─────────────────────────────────────────────────

def test_search_empty_corpus_returns_empty(_patch_paths_and_embed):
    # No files written, no rebuild ever called.
    assert corpus_mod.corpus_search("anything") == []


def test_search_no_match_below_threshold(_patch_paths_and_embed):
    _write_md(_patch_paths_and_embed, 'vehicle/x.md',
              {'topic': 'rare topic', 'tags': ['rare']},
              "# rare\n\nUnique text that won't match common queries.")
    corpus_mod.rebuild(force=True)
    # Query unlikely to hit min_similarity=0.99 with the fake embedder.
    assert corpus_mod.corpus_search("xx", min_similarity=0.99) == []


def test_search_returns_sorted_by_score(_patch_paths_and_embed):
    _write_md(_patch_paths_and_embed, 'dtc/P0171.md',
              {'topic': 'P0171', 'tags': ['dtc']},
              "# P0171\n\nSystem too lean Bank 1.")
    _write_md(_patch_paths_and_embed, 'vehicle/cooling.md',
              {'topic': 'Cooling', 'tags': ['coolant']},
              "# Cooling\n\nThermostat housing fails on X-Type.")
    corpus_mod.rebuild(force=True)
    hits = corpus_mod.corpus_search("P0171 lean", k=2, min_similarity=0.0)
    assert len(hits) >= 1
    scores = [h['score'] for h in hits]
    assert scores == sorted(scores, reverse=True)


def test_rebuild_emits_chunk_count(_patch_paths_and_embed):
    _write_md(_patch_paths_and_embed, 'vehicle/a.md',
              {'topic': 'a'}, "# a\n\nbody one\n\n## section\n\nbody two")
    result = corpus_mod.rebuild(force=True)
    assert result['files'] == 1
    assert result['chunks'] >= 2  # one before ##, one after


def test_rebuild_skips_unchanged_files(_patch_paths_and_embed, monkeypatch):
    _write_md(_patch_paths_and_embed, 'vehicle/a.md',
              {'topic': 'a'}, "# a\n\nbody")
    first = corpus_mod.rebuild(force=False)
    assert first['chunks'] >= 1
    second = corpus_mod.rebuild(force=False)
    assert second['skipped'] >= 1
    assert second['chunks'] == 0


def test_dtc_lookup_direct_hit(_patch_paths_and_embed):
    _write_md(_patch_paths_and_embed, 'dtc/P0171.md',
              {'topic': 'P0171', 'tags': ['dtc']},
              "# P0171\n\nSystem too lean Bank 1.")
    hit = corpus_mod.dtc_lookup('P0171')
    assert hit is not None
    assert 'P0171' in hit['content']
    assert hit['score'] == 1.0


def test_dtc_lookup_unknown_code(_patch_paths_and_embed):
    # Empty corpus, unknown code → None
    assert corpus_mod.dtc_lookup('P9999') is None


def test_stats_before_rebuild(_patch_paths_and_embed):
    s = corpus_mod.stats()
    assert s['chunks'] == 0
    assert s['embedding_dim'] == corpus_mod.EMBED_DIM


def test_stats_after_rebuild(_patch_paths_and_embed):
    _write_md(_patch_paths_and_embed, 'driving/x.md',
              {'topic': 'x'}, "# x\n\nbody")
    corpus_mod.rebuild(force=True)
    s = corpus_mod.stats()
    assert s['files'] == 1
    assert s['chunks'] >= 1
    assert s['last_rebuild_ts'] > 0


# ── frontmatter parser ─────────────────────────────────────────────

def test_parse_frontmatter_extracts_fields():
    text = "---\ntopic: thing\ntags: [a, b, c]\n---\n\n# body"
    fm, body = corpus_mod._parse_frontmatter(text)
    assert fm['topic'] == 'thing'
    assert fm['tags'] == ['a', 'b', 'c']
    assert body.strip() == '# body'


def test_parse_frontmatter_missing_returns_empty_fm():
    text = "no frontmatter here\n\n# body"
    fm, body = corpus_mod._parse_frontmatter(text)
    assert fm == {}
    assert body == text

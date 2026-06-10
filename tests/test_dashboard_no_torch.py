# tests/test_dashboard_no_torch.py
"""
MZ1312 DRIFTER — dashboard memory-safety guard.

The always-on web dashboard runs under systemd MemoryMax=512M
(services/drifter-dashboard.service). corpus.corpus_search() lazy-loads
sentence-transformers + torch (~1 GB resident). If the dashboard process ever
imports torch, a single corpus/DTC query OOM-kills the very thing that serves
/healthz, crash-looping the HUD.

These tests lock in the fix: importing the dashboard handler module and
exercising the DTC-lookup path must NEVER pull in torch / sentence_transformers
and must NEVER touch corpus's embedding model.

Note: these tests scope DRIFTER_CORPUS_NO_EMBED with monkeypatch (per-test,
auto-reverted) rather than mutating os.environ at module import — that keeps
the embed-using tests in test_corpus.py unaffected by collection order.

UNCAGED TECHNOLOGY — EST 1991
"""
from __future__ import annotations

import sys

import pytest

_ML_MODULES = ("torch", "sentence_transformers")


def _ml_loaded() -> list[str]:
    return [m for m in _ML_MODULES if m in sys.modules]


@pytest.fixture
def embed_free(monkeypatch):
    """Put corpus into embed-free mode the way the dashboard does, scoped to a
    single test so the global suite (test_corpus.py) is unaffected."""
    monkeypatch.setenv("DRIFTER_CORPUS_NO_EMBED", "1")
    return monkeypatch


def test_importing_dashboard_handlers_loads_no_torch():
    """Importing the HTTP handler module must not transitively import the ML
    stack. corpus.py keeps those imports strictly lazy (inside _get_model)."""
    assert _ml_loaded() == [], (
        f"ML modules already imported before the dashboard handler: {_ml_loaded()}"
    )
    import web_dashboard_handlers  # noqa: F401

    assert _ml_loaded() == [], (
        f"importing web_dashboard_handlers pulled in {_ml_loaded()} — "
        "the memory-capped dashboard must stay torch-free"
    )


def test_dashboard_handlers_imports_static_dtc_only():
    """The handler must reach corpus via the torch-free static lookup, never
    the semantic dtc_lookup (which can fall through to corpus_search)."""
    import web_dashboard_handlers as h

    assert hasattr(h, "dtc_lookup_static")
    assert not hasattr(h, "dtc_lookup"), (
        "web_dashboard_handlers must not import the semantic dtc_lookup"
    )


def test_embed_free_guard_reports_disabled(embed_free):
    import corpus

    assert corpus._embed_disabled() is True


def test_dtc_static_lookup_never_loads_model(monkeypatch, tmp_path):
    """The dashboard's DTC path uses dtc_lookup_static — a static-file read.
    Even with a corpus dir present it must not invoke the embedding model."""
    import corpus

    # Tripwire: any attempt to load the embedding model fails the test loudly.
    def _boom():
        raise AssertionError("embedding model loaded on the DTC lookup path")

    monkeypatch.setattr(corpus, "_get_model", _boom)

    corpus_dir = tmp_path / "corpus"
    (corpus_dir / "dtc").mkdir(parents=True)
    (corpus_dir / "dtc" / "P0171.md").write_text(
        "---\ntopic: P0171\ntags: [dtc]\n---\n\nSystem too lean Bank 1.",
        encoding="utf-8",
    )
    monkeypatch.setattr(corpus, "CORPUS_DIR", corpus_dir)

    hit = corpus.dtc_lookup_static("P0171")
    assert hit is not None
    assert "lean" in hit["content"].lower()
    assert hit["score"] == 1.0

    # A miss returns None with no semantic fallback (and thus no model load).
    assert corpus.dtc_lookup_static("P9999") is None

    assert _ml_loaded() == []


def test_corpus_search_is_noop_when_embed_disabled(embed_free, monkeypatch):
    """With the embed-free env set, corpus_search must short-circuit to []
    without ever touching the model — this is what neutralises the two residual
    corpus_search call sites still reachable from the dashboard."""
    import corpus

    assert corpus._embed_disabled() is True

    def _boom():
        raise AssertionError("embedding model loaded from corpus_search")

    monkeypatch.setattr(corpus, "_get_model", _boom)

    assert corpus.corpus_search("system too lean bank 1") == []
    assert _ml_loaded() == []


def test_full_dtc_lookup_stays_torch_free_when_embed_disabled(embed_free, monkeypatch, tmp_path):
    """The richer dtc_lookup() (used outside the dashboard) tries static first;
    its semantic fallback is itself gated by the embed-free guard, so even that
    entry point stays torch-free in an embed-free process."""
    import corpus

    def _boom():
        raise AssertionError("embedding model loaded from dtc_lookup fallback")

    monkeypatch.setattr(corpus, "_get_model", _boom)
    monkeypatch.setattr(corpus, "CORPUS_DIR", tmp_path / "empty_corpus")

    # No static file, no semantic fallback (embed-free) → None, no model.
    assert corpus.dtc_lookup("P9999") is None
    assert _ml_loaded() == []


@pytest.mark.parametrize("name", _ML_MODULES)
def test_ml_module_never_in_sys_modules_after_dtc_path(name, embed_free, monkeypatch, tmp_path):
    import corpus

    monkeypatch.setattr(corpus, "CORPUS_DIR", tmp_path / "no_corpus")
    corpus.dtc_lookup_static("P0420")
    corpus.corpus_search("misfire")
    assert name not in sys.modules

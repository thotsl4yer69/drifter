"""Shared pytest fixtures for the DRIFTER test suite."""
import pytest


@pytest.fixture(autouse=True)
def reset_llm_state():
    """Reset llm_client_v2 module-level health and cache state between tests.

    The health tracker and cache are module-level dicts that persist across
    test invocations in the same process. Without this fixture, a backend
    failure in one test can put that backend into cooldown and cause
    subsequent tests to skip it entirely, producing wrong results.

    (Repointed from the retired v1 llm_client module to llm_client_v2, which
    is the only LLM client the fleet ships now.)
    """
    try:
        import llm_client_v2
    except Exception:
        yield
        return
    llm_client_v2._health = {name: {"ok": True, "last_fail": 0.0, "fails": 0}
                             for name in ['ollama', 'groq', 'claude']}
    llm_client_v2._cache = {}
    yield

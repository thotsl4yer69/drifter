"""Lock the local-LLM model strategy so it can't drift back into a RAM risk.

There is exactly ONE small model for the whole fleet (config.OLLAMA_MODEL).
Every local LLM consumer resolves to it: session_analyst, session_reporter and
ai_diagnostics via src/llm_client.py, and Vivi via config/vivi.yaml. install.sh
must therefore pull EXACTLY that tag by default — no unused-model downloads
(the old bug pulled qwen2.5:7b + :3b, ~6.6GB, neither of which the running
config used) and no missing one (a pull that omits the configured tag means a
cold first turn on the live Pi).

A single larger model (qwen2.5:7b) is allowed ONLY behind the explicit
`--with-7b` opt-in flag, because the 8GB Pi can't hold it warm alongside Vivi.

These tests fail loudly the moment install.sh and the config disagree again.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, 'src')

import config

REPO = Path(__file__).resolve().parent.parent
INSTALL_SH = (REPO / 'install.sh').read_text()
KEEPWARM = (REPO / 'services' / 'drifter-llm-keepwarm.service').read_text()

# Tag allowed only behind the explicit --with-7b opt-in (never a default pull).
OPT_IN_TAG = 'qwen2.5:7b'


def _ollama_pull_tags() -> list[str]:
    """Every literal/variable model tag install.sh feeds to `ollama pull`."""
    tags: list[str] = []
    # Expand the OLLAMA_DEFAULT_MODEL="..." assignment so we resolve the var.
    var_m = re.search(r'OLLAMA_DEFAULT_MODEL="([^"]+)"', INSTALL_SH)
    default_var = var_m.group(1) if var_m else None
    for m in re.finditer(r'ollama pull\s+"?\$?\{?([A-Za-z0-9_.:]+)\}?"?', INSTALL_SH):
        token = m.group(1)
        if token == 'OLLAMA_DEFAULT_MODEL':
            assert default_var, "OLLAMA_DEFAULT_MODEL referenced but never assigned"
            tags.append(default_var)
        else:
            tags.append(token)
    return tags


def _default_pull_tags() -> set[str]:
    """Tags pulled unconditionally (i.e. NOT gated behind the --with-7b block)."""
    # The opt-in pull lives inside an `if [[ "${WITH_7B}" == "1" ]]; then ... fi`
    # block; strip that block out, then collect the remaining pulls.
    without_optin = re.sub(
        r'if \[\[ "\$\{WITH_7B\}" == "1" \]\]; then.*?\n    fi',
        '',
        INSTALL_SH,
        flags=re.DOTALL,
    )
    tags: set[str] = set()
    var_m = re.search(r'OLLAMA_DEFAULT_MODEL="([^"]+)"', without_optin)
    default_var = var_m.group(1) if var_m else None
    for m in re.finditer(r'ollama pull\s+"?\$?\{?([A-Za-z0-9_.:]+)\}?"?', without_optin):
        token = m.group(1)
        tags.add(default_var if token == 'OLLAMA_DEFAULT_MODEL' else token)
    return tags


def test_install_default_pull_is_exactly_the_configured_model():
    """No unused downloads, no missing one: default pull == {config.OLLAMA_MODEL}."""
    assert _default_pull_tags() == {config.OLLAMA_MODEL}, (
        f"install.sh default pull {_default_pull_tags()} must equal exactly "
        f"the configured tag {{{config.OLLAMA_MODEL!r}}}"
    )


def test_no_unconfigured_default_pull():
    """The legacy 3b/7b unconditional pulls must be gone."""
    defaults = _default_pull_tags()
    assert OPT_IN_TAG not in defaults, "qwen2.5:7b must be opt-in (--with-7b), not a default pull"
    assert 'qwen2.5:3b' not in defaults, "qwen2.5:3b is unused by the running config — drop it"


def test_7b_is_gated_behind_with_7b_flag():
    """If install.sh pulls 7b at all, it must be inside the --with-7b block."""
    if OPT_IN_TAG in _ollama_pull_tags():
        assert OPT_IN_TAG not in _default_pull_tags(), (
            "qwen2.5:7b is pulled but not gated behind --with-7b"
        )
        assert '--with-7b' in INSTALL_SH, "--with-7b flag must be documented/parsed"


def test_keepwarm_pings_the_configured_model():
    """drifter-llm-keepwarm must warm the same tag everything else uses."""
    assert config.OLLAMA_MODEL in KEEPWARM, (
        f"keepwarm unit must ping {config.OLLAMA_MODEL!r} (config.OLLAMA_MODEL)"
    )


def test_vivi_yaml_matches_configured_model():
    """Vivi's own model selector must not diverge from the fleet default."""
    vivi_yaml = (REPO / 'config' / 'vivi.yaml').read_text()
    m = re.search(r'^ollama_model:\s*(\S+)', vivi_yaml, re.MULTILINE)
    assert m, "config/vivi.yaml must set ollama_model"
    assert m.group(1) == config.OLLAMA_MODEL, (
        f"vivi.yaml ollama_model {m.group(1)!r} must match config.OLLAMA_MODEL "
        f"{config.OLLAMA_MODEL!r}"
    )


def test_install_caps_resident_models_to_one():
    """install.sh must write the single-model residency guard for the ollama daemon."""
    assert 'OLLAMA_MAX_LOADED_MODELS=1' in INSTALL_SH, (
        "install.sh must set OLLAMA_MAX_LOADED_MODELS=1 on the ollama daemon "
        "to prevent analyst+vivi co-residency OOM"
    )
    assert config.OLLAMA_MAX_LOADED_MODELS == 1

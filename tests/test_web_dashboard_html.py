# tests/test_web_dashboard_html.py
"""
MZ1312 DRIFTER — drift guards for the dashboard HTML/CSS/JS file.

The single web_dashboard_html.py module ships SIX places where the
list of themes appears, all of which have to stay in sync. Phase 5.1
(commit 8a6c74b) caught this class of bug after the boot validator
allowlist had silently fallen behind the actual registry by 5 themes.
This test makes that drift impossible to ship again.

UNCAGED TECHNOLOGY — EST 1991
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

DASHBOARD_PATH = Path(__file__).resolve().parent.parent / 'src' / 'web_dashboard_html.py'


def _src() -> str:
    return DASHBOARD_PATH.read_text(encoding='utf-8')


def _registry() -> set[str]:
    """Canonical theme registry: every `:root[data-theme="X"]` CSS
    block. This is the source of truth — every other allowlist must
    equal this set."""
    src = _src()
    return set(re.findall(r':root\[data-theme="(\w+)"\]', src))


def _quoted_names(s: str) -> set[str]:
    """Pull single-quoted bareword names out of a JS-array body."""
    return set(re.findall(r"'(\w+)'", s))


def _array_bodies(name_pattern: str) -> list[str]:
    """Find every `<keyword> <name>=[...]` body in the file. The
    keyword is var/let/const; <name> is the regex passed in."""
    src = _src()
    rx = rf"(?:const|let|var)\s+{name_pattern}\s*=\s*\[([^\]]+)\]"
    return re.findall(rx, src)


def _object_keys(name_pattern: str) -> list[set[str]]:
    """Find every `<keyword> <name>={...}` body and extract bare
    object-literal keys (the `key:` half of `key:'value'`)."""
    src = _src()
    rx = rf"(?:const|let|var)\s+{name_pattern}\s*=\s*\{{([^}}]+)\}}"
    out = []
    for body in re.findall(rx, src):
        keys = set(re.findall(r"\b(\w+)\s*:", body))
        out.append(keys)
    return out


# ── 1. Registry parses — guards the regex itself, not just the data ──

def test_theme_registry_has_all_eight_themes():
    reg = _registry()
    expected = {'uncaged', 'ghost', 'drift', 'amber',
                'nightrun', 'daylight', 'woobs', 'deckrun'}
    assert reg == expected, (
        f"theme registry diverged from the documented 8-theme set. "
        f"got {sorted(reg)}, expected {sorted(expected)}. "
        f"if you added a theme, add it here too AND make sure all "
        f"five JS allowlists got updated (see test_*_matches_registry)."
    )


# ── 2. Every JS allowlist must equal the registry ─────────────────

def test_boot_validator_allowlists_match_registry():
    """Two boot scripts (DASHBOARD_HTML + SETTINGS_HTML) carry
    `var ok=[...]` allowlists that gate localStorage + ?theme=X
    URL param. If either drifts, themes silently reset to uncaged."""
    reg = _registry()
    bodies = _array_bodies(r'ok')
    assert len(bodies) == 2, (
        f"expected exactly 2 boot validator allowlists "
        f"(DASHBOARD_HTML + SETTINGS_HTML); found {len(bodies)}. "
        f"if you added or removed a boot script, update this test."
    )
    for i, body in enumerate(bodies):
        names = _quoted_names(body)
        diff = reg.symmetric_difference(names)
        assert not diff, (
            f"boot validator allowlist #{i+1} drifted from registry. "
            f"missing from allowlist: {sorted(reg - names)}. "
            f"in allowlist but not in registry: {sorted(names - reg)}. "
            f"file: src/web_dashboard_html.py"
        )


def test_theme_cycle_array_matches_registry():
    """`const themes=[...]` is the cycle order for the ⏻ button.
    Drift means the cycle skips themes."""
    reg = _registry()
    bodies = _array_bodies(r'themes')
    assert bodies, "no `const themes=[...]` cycle list found"
    for i, body in enumerate(bodies):
        names = _quoted_names(body)
        diff = reg.symmetric_difference(names)
        assert not diff, (
            f"theme cycle list #{i+1} drifted from registry. "
            f"missing: {sorted(reg - names)}, "
            f"extra: {sorted(names - reg)}."
        )


def test_settings_theme_options_match_registry():
    """`const THEME_OPTIONS=[...]` powers the settings page picker
    + applyTheme validation. Drift means the picker can't reach a
    theme."""
    reg = _registry()
    bodies = _array_bodies(r'THEME_OPTIONS')
    assert bodies, "no `const THEME_OPTIONS=[...]` list found"
    for body in bodies:
        names = _quoted_names(body)
        diff = reg.symmetric_difference(names)
        assert not diff, (
            f"THEME_OPTIONS drifted from registry. "
            f"missing: {sorted(reg - names)}, "
            f"extra: {sorted(names - reg)}."
        )


def test_glyph_map_keys_match_registry():
    """The cycle-button glyph (☾ ☀ ❦ etc) must exist for every
    theme — otherwise switching to a missing-glyph theme falls
    back to ⏻ which is wrong but not catastrophic. Catch it anyway."""
    reg = _registry()
    glyph_sets = _object_keys(r'glyphs')
    assert glyph_sets, "no `const glyphs={...}` map found"
    for keys in glyph_sets:
        diff = reg.symmetric_difference(keys)
        assert not diff, (
            f"glyph map drifted from registry. "
            f"missing: {sorted(reg - keys)}, "
            f"extra: {sorted(keys - reg)}."
        )


def test_meta_theme_color_map_matches_registry():
    """The settings-page applyTheme() updates <meta name=theme-color>
    via a hardcoded {theme: '#hex'} map. If a theme is missing the
    address bar / PWA chrome stays the previous theme's colour."""
    reg = _registry()
    src = _src()
    # The colors map lives only inside applyTheme() — there are
    # other `const colors=` declarations (alert-level → CSS-var
    # maps) that we must NOT match. Anchor on the function name.
    m = re.search(
        r'function applyTheme\([^)]*\)\s*\{[^}]*?const\s+colors\s*=\s*\{([^}]+)\}',
        src, re.DOTALL,
    )
    assert m, "applyTheme() colors map not found — has the function moved?"
    keys = set(re.findall(r"\b(\w+)\s*:", m.group(1)))
    diff = reg.symmetric_difference(keys)
    assert not diff, (
        f"applyTheme() meta-theme-color map drifted from registry. "
        f"missing: {sorted(reg - keys)}, "
        f"extra: {sorted(keys - reg)}."
    )


# ── 3. Negative-control: deleting one entry from any allowlist
# should make AT LEAST ONE of the above tests fail with a useful
# message. We run it as a self-test that exercises the drift logic
# without modifying the actual file. ──

def test_drift_detector_negative_control(tmp_path):
    """Synthetic file with one theme removed from the boot validator
    but kept in the registry must trip the boot validator drift
    test's logic. Validates the regex + comparison, not the data."""
    fake = """
    :root[data-theme="uncaged"] { color:red }
    :root[data-theme="ghost"]   { color:blue }
    var ok=['uncaged'];  // deliberately missing 'ghost'
    """
    reg = set(re.findall(r':root\[data-theme="(\w+)"\]', fake))
    body_match = re.search(r"var\s+ok\s*=\s*\[([^\]]+)\]", fake)
    assert body_match, "test fixture parse failed"
    names = set(re.findall(r"'(\w+)'", body_match.group(1)))
    assert reg.symmetric_difference(names) == {'ghost'}, (
        "drift detector regex/logic regressed — should have flagged "
        "the deliberately missing theme"
    )

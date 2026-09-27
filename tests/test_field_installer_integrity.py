#!/usr/bin/env python3
"""Keep the narrow field installer aligned with the merged acceptance source."""
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_field_installer_pins_the_current_source_blob():
    source = (ROOT / "src/field_acceptance.py").read_bytes()
    actual = hashlib.sha1(b"blob " + str(len(source)).encode() + b"\0" + source).hexdigest()
    installer = (ROOT / "scripts/install-field-hardening.sh").read_text()
    match = re.search(r'^PATCHED="([0-9a-f]{40})"$', installer, re.MULTILINE)
    assert match is not None
    assert match.group(1) == actual, "Update the reviewed installer hash when acceptance source changes."


def test_field_installer_keeps_both_reviewed_upgrade_paths():
    installer = (ROOT / "scripts/install-field-hardening.sh").read_text()
    assert 'BASE="9b5b1e3112e6e5c5a164586b148a2f49d58141a3"' in installer
    assert 'PREVIOUS_PATCHED="3009cfa9c7d26cfcef137f51bdc6c4af4c55f6b9"' in installer
    assert '"$CURRENT" == "$BASE" || "$CURRENT" == "$PREVIOUS_PATCHED"' in installer
    assert 'if [[ "$CURRENT" == "$PATCHED" ]]' in installer
    assert "Refusing a mixed-version update" in installer

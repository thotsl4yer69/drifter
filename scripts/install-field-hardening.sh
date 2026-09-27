#!/usr/bin/env bash
# Additive field-gate patch. Does not update the OS, restart services or alter OBD settings.
set -euo pipefail
ROOT="${DRIFTER_INSTALL_DIR:-/opt/drifter}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
SOURCE="$REPO/src/field_acceptance.py"
TARGET="$ROOT/field_acceptance.py"
PY="$ROOT/venv/bin/python3"
MODE="${1:---check}"
BASE="9b5b1e3112e6e5c5a164586b148a2f49d58141a3"
PREVIOUS_PATCHED="3009cfa9c7d26cfcef137f51bdc6c4af4c55f6b9"
PATCHED="797f2822152afda43f72aa5293337f28e625b111"
fail() { printf 'HOLD: %s\n' "$*" >&2; exit 2; }
[[ $# -le 1 && ( "$MODE" == --check || "$MODE" == --apply ) ]] || fail 'Usage: install-field-hardening.sh [--check|--apply]'
[[ -f "$SOURCE" && -f "$TARGET" && -x "$PY" ]] || fail 'A complete existing DRIFTER install is required; nothing changed.'
command -v pgrep >/dev/null || fail 'pgrep is required to check that no acceptance run is active.'
if pgrep -f '(^|/)field_acceptance[.]py([[:space:]]|$)' >/dev/null; then
    fail 'Finish the active acceptance command before installing.'
fi
blob() {
    "$PY" - "$1" <<'PY'
import hashlib
import sys
from pathlib import Path
b = Path(sys.argv[1]).read_bytes()
print(hashlib.sha1(b"blob " + str(len(b)).encode() + b"\0" + b).hexdigest())
PY
}
[[ "$(blob "$SOURCE")" == "$PATCHED" ]] || fail 'Patch differs from the reviewed source; nothing changed.'
CURRENT="$(blob "$TARGET")"
if [[ "$CURRENT" == "$PATCHED" ]]; then
    echo 'Field hardening is already installed. Existing evidence was not reset.'
    exit 0
fi
[[ "$CURRENT" == "$BASE" || "$CURRENT" == "$PREVIOUS_PATCHED" ]] || fail 'Installed field gate is a different revision. Refusing a mixed-version update; nothing changed.'
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$ROOT" "$PY" - "$SOURCE" <<'PY'
import ast
import sys
from pathlib import Path
import paho.mqtt.client as mqtt
from config import TOPICS, make_mqtt_client
assert hasattr(mqtt, "CallbackAPIVersion"), "paho-mqtt 2.x is required"
assert callable(make_mqtt_client)
assert all(key in TOPICS for key in ("rpm", "coolant", "speed", "voltage", "obd_status"))
ast.parse(Path(sys.argv[1]).read_text(encoding="utf-8"))
PY
if [[ "$MODE" == --check ]]; then
    echo 'Patch baseline, Python syntax and shared imports verified. No files changed.'
    echo 'Apply while parked: sudo bash scripts/install-field-hardening.sh --apply'
    exit 0
fi
[[ "$EUID" -eq 0 ]] || fail 'Use sudo for --apply.'
[[ -z "${DRIFTER_ACCEPTANCE_STATE:-}" ]] || fail 'Custom acceptance-state path requires a separately reviewed migration.'
if [[ -f "$ROOT/.env" ]] && grep -Eq '^[[:space:]]*(export[[:space:]]+)?DRIFTER_ACCEPTANCE_STATE=' "$ROOT/.env"; then
    fail 'Custom acceptance-state path in .env requires a separately reviewed migration.'
fi
umask 077
mkdir -p "$ROOT/backups"
BACKUP="$(mktemp -d "$ROOT/backups/field-hardening.XXXXXXXX")"
cp -a "$TARGET" "$BACKUP/field_acceptance.py"
STATE="$ROOT/data/field_acceptance.json"
[[ ! -f "$STATE" ]] || cp -a "$STATE" "$BACKUP/field_acceptance.json"
TMP="$(mktemp "$ROOT/.field-acceptance.XXXXXXXX")"
INSTALLED=0
cleanup() {
    local rc=$?
    rm -f "$TMP"
    if [[ "$rc" -ne 0 && "$INSTALLED" -eq 1 ]]; then
        cp -a "$BACKUP/field_acceptance.py" "$TARGET"
        [[ ! -f "$BACKUP/field_acceptance.json" || -f "$STATE" ]] || cp -a "$BACKUP/field_acceptance.json" "$STATE"
        echo 'Installation failed; original gate restored.' >&2
    fi
}
trap cleanup EXIT
install -m 0644 "$SOURCE" "$TMP"
chown --reference="$TARGET" "$TMP"
mv -f "$TMP" "$TARGET"
INSTALLED=1
# Preserve old evidence, but start a fresh campaign for the changed gate.
[[ ! -f "$STATE" ]] || mv "$STATE" "$BACKUP/prior-campaign.json"
printf 'Installed field acceptance hardening. Backup: %s\n' "$BACKUP"
echo 'No services were restarted. Configuration, dashboard, phone app and drive logs are unchanged.'
echo 'Previous acceptance evidence is archived. New hardware validation is required.'
printf 'Code rollback: sudo cp -a %q %q\n' "$BACKUP/field_acceptance.py" "$TARGET"

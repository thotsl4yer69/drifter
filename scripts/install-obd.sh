#!/usr/bin/env bash
# MZ1312 DRIFTER — complete diagnostics runtime update for an existing Pi.
# UNCAGED TECHNOLOGY — EST 1991
set -euo pipefail
DRIFTER_DIR=${DRIFTER_INSTALL_ROOT:-/opt/drifter}
UNIT_DIR=${DRIFTER_SYSTEMD_DIR:-/etc/systemd/system}
BIN_DIR=${DRIFTER_BIN_DIR:-/usr/local/bin}
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PY="$DRIFTER_DIR/venv/bin/python3"
[[ $EUID -eq 0 ]] || { echo 'Run: sudo ./scripts/install-obd.sh' >&2; exit 2; }
[[ -x "$PY" ]] || { echo 'Existing DRIFTER runtime missing. Run sudo ./install.sh first.' >&2; exit 2; }
command -v systemctl >/dev/null

# Prepare and validate before interrupting the running services.
"$PY" -m pip install --quiet 'paho-mqtt>=2,<3' pyserial pyyaml python-can psutil requests
STAGE="$(mktemp -d "$DRIFTER_DIR/.obd-stage.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT
cp -a "$REPO_DIR/src/." "$STAGE/"
"$PY" -m compileall -q "$STAGE"
PYTHONPATH="$STAGE" "$PY" -c 'import config, obd_bridge, elm_protocol, can_bridge, alert_engine, safety_engine, logger, vehicle_id, vehicle_check'

BACKUP="$DRIFTER_DIR/backups/vehicle-update-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$BACKUP/runtime" "$BACKUP/units" "$BACKUP/bin"
if [[ -f "$BIN_DIR/drifter" ]]; then cp -a "$BIN_DIR/drifter" "$BACKUP/bin/drifter"; fi
# Back up every replaced source file, including config's companion modules.
# A partial config.py-only copy was capable of breaking the whole runtime.
"$PY" - "$STAGE" "$DRIFTER_DIR" "$BACKUP" <<'PY'
import json, pathlib, shutil, sys
stage, dest, backup = map(pathlib.Path, sys.argv[1:])
manifest = []
for p in stage.rglob('*'):
    if not p.is_file() or '__pycache__' in p.parts:
        continue
    rel = p.relative_to(stage)
    old = dest / rel
    manifest.append({'path': str(rel), 'existed': old.exists()})
    if old.is_file():
        target = backup / 'runtime' / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(old, target)
(backup / 'manifest.json').write_text(json.dumps(manifest, indent=2))
PY

UNITS=(drifter-canbridge drifter-obdbridge drifter-batcher drifter-alerts drifter-safety drifter-logger drifter-vehicleid drifter-trip drifter-thresholds drifter-dashboard)
ACTIVE=()
ENABLED=()
for svc in "${UNITS[@]}"; do
    if systemctl is-active --quiet "$svc"; then ACTIVE+=("$svc"); fi
    if systemctl is-enabled --quiet "$svc"; then ENABLED+=("$svc"); fi
    if [[ -f "$UNIT_DIR/$svc.service" ]]; then
        cp -a "$UNIT_DIR/$svc.service" "$BACKUP/units/"
    fi
done
printf '%s\n' "${ACTIVE[@]}" > "$BACKUP/active-services.txt"
# Pause the watchdog while the planned maintenance stops its monitored units.
WATCHDOG_WAS_ACTIVE=0
if systemctl is-active --quiet drifter-watchdog; then
    WATCHDOG_WAS_ACTIVE=1
fi
cleanup() {
    local rc=$?
    trap - EXIT
    if [[ $rc -ne 0 ]]; then
        echo "Update failed; restoring previous runtime from $BACKUP" >&2
        systemctl stop "${UNITS[@]}" || true
        "$PY" - "$DRIFTER_DIR" "$BACKUP" <<'PY'
import json, pathlib, shutil, sys
dest, backup = map(pathlib.Path, sys.argv[1:])
for row in json.loads((backup / 'manifest.json').read_text()):
    target = dest / row['path']
    if row['existed']:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup / 'runtime' / row['path'], target)
    else:
        target.unlink(missing_ok=True)
# Never load bytecode belonging to the failed deployment after rollback.
for p in dest.glob('__pycache__/*.pyc'):
    p.unlink()
PY
        for svc in "${UNITS[@]}"; do
            if [[ -f "$BACKUP/units/$svc.service" ]]; then
                cp -a "$BACKUP/units/$svc.service" "$UNIT_DIR/"
            else
                rm -f "$UNIT_DIR/$svc.service"
            fi
        done
        if [[ -f "$BACKUP/bin/drifter" ]]; then
            cp -a "$BACKUP/bin/drifter" "$BIN_DIR/drifter"
        else
            rm -f "$BIN_DIR/drifter"
        fi
        for svc in drifter-obdbridge drifter-safety; do
            if [[ ! " ${ENABLED[*]} " == *" $svc "* ]]; then systemctl disable "$svc" || true; fi
        done
        systemctl daemon-reload || true
        if ((${#ACTIVE[@]})); then systemctl restart "${ACTIVE[@]}" || true; fi
    fi
    if [[ $WATCHDOG_WAS_ACTIVE -eq 1 ]]; then systemctl start drifter-watchdog || true; fi
    rm -rf "$STAGE"
    exit "$rc"
}
trap cleanup EXIT
if [[ $WATCHDOG_WAS_ACTIVE -eq 1 ]]; then systemctl stop drifter-watchdog; fi
if ((${#ACTIVE[@]})); then systemctl stop "${ACTIVE[@]}"; fi
cp -a "$STAGE/." "$DRIFTER_DIR/"
for svc in "${UNITS[@]}"; do
    install -m 0644 "$REPO_DIR/services/$svc.service" "$UNIT_DIR/$svc.service"
done
install -m 0755 "$REPO_DIR/bin/drifter" "$BIN_DIR/drifter"
if [[ ! -f "$DRIFTER_DIR/obd.yaml" ]]; then cp "$REPO_DIR/config/obd.yaml" "$DRIFTER_DIR/"; fi
if [[ ! -f "$DRIFTER_DIR/.env" ]]; then install -m 0600 /dev/null "$DRIFTER_DIR/.env"; fi
systemctl daemon-reload
systemctl enable drifter-obdbridge drifter-safety
if ((${#ACTIVE[@]})); then systemctl restart "${ACTIVE[@]}"; fi
systemctl restart drifter-obdbridge drifter-safety
# Catch an immediate import/start failure; hardware-pending is a healthy
# running process and does not require an ECU to be connected at install time.
sleep 2
systemctl is-active --quiet drifter-obdbridge drifter-safety
printf 'Diagnostics runtime updated. Backup: %s\n' "$BACKUP"
echo 'Next: configure one reader in /opt/drifter/.env; restart drifter-canbridge and drifter-obdbridge; run drifter vehicle-check --seconds 60.'
echo 'The touchscreen update is separate: sudo ./scripts/deploy-cockpit-v4.sh'

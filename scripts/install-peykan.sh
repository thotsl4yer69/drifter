#!/usr/bin/env bash
# MZ1312 DRIFTER — opt-in Peykan 0.2.1 integration.
# Must be invoked from the DRIFTER repository; does not change the normal
# ELM327/SocketCAN telemetry selector or the monitored DRIFTER service list.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST=/opt/drifter
MODE=""
INTERFACE=""
DBC=""
PREFLIGHT=0
usage() {
    echo "Usage: sudo bash scripts/install-peykan.sh --demo [--check]"
    echo "   or: sudo bash scripts/install-peykan.sh --socketcan can0 --dbc /absolute/vehicle.dbc [--check]"
    exit 2
}
while [ "$#" -gt 0 ]; do
    case "$1" in
        --demo) MODE=demo; shift ;;
        --socketcan) [ "$#" -ge 2 ] || usage; MODE=socketcan; INTERFACE="$2"; shift 2 ;;
        --dbc) [ "$#" -ge 2 ] || usage; DBC="$2"; shift 2 ;;
        --check) PREFLIGHT=1; shift ;;
        *) usage ;;
    esac
done
[ "$(id -u)" -eq 0 ] || { echo "Requires sudo"; exit 1; }
[ -d "$DEST/venv" ] && [ -f "$DEST/config.py" ] || {
    echo "Install DRIFTER first using scripts/oneshot.sh"; exit 1;
}
if [ "$MODE" = socketcan ]; then
    [[ "$INTERFACE" =~ ^[a-zA-Z][a-zA-Z0-9_]*$ ]] || usage
    [ -f "$DBC" ] || { echo "A real vehicle-specific .dbc is required"; exit 1; }
    [[ "$DBC" = /* ]] || usage
    command -v ip >/dev/null && ip link show "$INTERFACE" >/dev/null || {
        echo "CAN interface not present: $INTERFACE"; exit 1;
    }
elif [ "$MODE" != demo ] || [ -n "$DBC" ]; then
    usage
fi

id drifter >/dev/null 2>&1 || { echo "DRIFTER service user missing"; exit 1; }
[ -f "$ROOT/src/peykan_server.py" ] && [ -f "$ROOT/src/peykan_bridge.py" ] && [ -f "$ROOT/scripts/peykan-verify.py" ] || {
    echo "Incomplete DRIFTER checkout; Peykan deployment files are missing"; exit 1;
}
command -v systemctl >/dev/null || { echo "systemd is required"; exit 1; }
command -v python3 >/dev/null || { echo "Python 3 is required"; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || {
    echo "Peykan requires Python >=3.10"; exit 1;
}
if [ "$PREFLIGHT" -eq 1 ]; then
    echo "PASS Peykan preflight: $MODE, existing DRIFTER, service user, Python, systemd"
    exit 0
fi
# A redeploy must not leave the prior Python process running old code,
# or continue serving the previous CAN interface while its files change.
systemctl stop drifter-peykan-bridge.service drifter-peykan-mcp.service 2>/dev/null || true
install -d -o drifter -g drifter -m 0750 "$DEST/peykan-logs"
python3 -m venv "$DEST/peykan-venv"
"$DEST/peykan-venv/bin/pip" install --disable-pip-version-check "peykan==0.2.1" "mcp==2.3.0" "paho-mqtt==2.1.0"
install -m 0755 "$ROOT/src/peykan_bridge.py" "$DEST/peykan_bridge.py"
install -m 0644 "$ROOT/src/peykan_context.py" "$DEST/peykan_context.py"
install -m 0755 "$ROOT/src/peykan_server.py" "$DEST/peykan_server.py"
install -m 0755 "$ROOT/scripts/peykan-verify.py" "$DEST/peykan_verify.py"
# The launcher and bridge import the DRIFTER's pure Python config/topics files.
for f in config.py _config_topics.py _config_models.py _config_dtc.py api_keys.py; do
    if [ -f "$ROOT/src/$f" ]; then install -m 0644 "$ROOT/src/$f" "$DEST/$f"; fi
done
if [ "$MODE" = socketcan ]; then
    install -o drifter -g drifter -m 0640 "$DBC" "$DEST/peykan-logs/vehicle.dbc"
fi
CONF="$(mktemp)"
trap 'rm -f "$CONF"' EXIT
{
    echo "DRIFTER_PEYKAN_MODE=$MODE"
    echo "DRIFTER_PEYKAN_MCP_URL=http://127.0.0.1:6278/mcp"
    echo "PEYKAN_CAN_INTERFACE=$([ "$MODE" = demo ] && echo virtual || echo socketcan)"
    echo "PEYKAN_CAN_CHANNEL=$([ "$MODE" = demo ] && echo bus0 || echo "$INTERFACE")"
    echo "PEYKAN_MCP_HOST=127.0.0.1"
    echo "PEYKAN_MCP_TRANSPORT=streamable-http"
    echo "PEYKAN_ALLOW_TRANSMIT=false"
    echo "PEYKAN_ALLOW_WRITE_SERVICES=false"
    echo "PEYKAN_SIMULATOR_ON_HARDWARE=false"
    echo "PEYKAN_LOG_DIR=$DEST/peykan-logs"
    if [ "$MODE" = socketcan ]; then
        echo "PEYKAN_DBC_PATH=$DEST/peykan-logs/vehicle.dbc"
    fi
} > "$CONF"
install -o root -g drifter -m 0640 "$CONF" "$DEST/peykan.env"
install -m 0644 "$ROOT/services/drifter-peykan-mcp.service" /etc/systemd/system/
install -m 0644 "$ROOT/services/drifter-peykan-bridge.service" /etc/systemd/system/
systemctl daemon-reload
# Enable without relying on enable --now: that doesn't restart existing units
# after files and environment have been replaced.
systemctl enable drifter-peykan-mcp.service drifter-peykan-bridge.service
systemctl restart drifter-peykan-mcp.service
systemctl restart drifter-peykan-bridge.service
if ! systemctl is-active --quiet drifter-peykan-mcp.service ||    ! systemctl is-active --quiet drifter-peykan-bridge.service ||    ! "$DEST/peykan-venv/bin/python3" "$DEST/peykan_verify.py" --mode "$MODE"; then
    echo "FAIL Peykan deployment acceptance; inspect service logs below" >&2
    journalctl --no-pager -u drifter-peykan-mcp -u drifter-peykan-bridge -n 45 >&2 || true
    exit 1
fi
echo "PASS Peykan $MODE installed and verified; read-only, loopback-only."
systemctl --no-pager --full status drifter-peykan-mcp.service drifter-peykan-bridge.service || true

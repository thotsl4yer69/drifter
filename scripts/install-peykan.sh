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
usage() {
    echo "Usage: sudo bash scripts/install-peykan.sh --demo"
    echo "   or: sudo bash scripts/install-peykan.sh --socketcan can0 --dbc /absolute/vehicle.dbc"
    exit 2
}
while [ "$#" -gt 0 ]; do
    case "$1" in
        --demo) MODE=demo; shift ;;
        --socketcan) [ "$#" -ge 2 ] || usage; MODE=socketcan; INTERFACE="$2"; shift 2 ;;
        --dbc) [ "$#" -ge 2 ] || usage; DBC="$2"; shift 2 ;;
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
install -d -o drifter -g drifter -m 0750 "$DEST/peykan-logs"
python3 -m venv "$DEST/peykan-venv"
"$DEST/peykan-venv/bin/pip" install --disable-pip-version-check "peykan==0.2.1" "paho-mqtt>=2,<3"
install -m 0755 "$ROOT/src/peykan_bridge.py" "$DEST/peykan_bridge.py"
install -m 0644 "$ROOT/src/peykan_context.py" "$DEST/peykan_context.py"
install -m 0755 "$ROOT/src/peykan_server.py" "$DEST/peykan_server.py"
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
systemctl enable --now drifter-peykan-mcp.service drifter-peykan-bridge.service
echo "Peykan installed in $MODE mode. Read-only, loopback only."
systemctl --no-pager --full status drifter-peykan-mcp.service drifter-peykan-bridge.service || true

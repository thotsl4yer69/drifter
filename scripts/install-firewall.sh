#!/bin/bash
# ============================================
# MZ1312 DRIFTER — Opt-in host firewall
# Restricts the node's service ports to loopback + the wlan0 hotspot.
# SSH and all other ports are untouched.
# UNCAGED TECHNOLOGY — EST 1991
# ============================================
# Usage: sudo ./scripts/install-firewall.sh            # install + enable at boot
#        sudo ./scripts/install-firewall.sh --dry-run  # syntax check only
#        sudo ./scripts/install-firewall.sh --remove   # remove the table
# ============================================
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RULES_SRC="${REPO_DIR}/config/drifter-nftables.conf"
RULES_DST="/etc/nftables.d/drifter.nft"
UNIT="/etc/systemd/system/drifter-firewall.service"

if [ "${EUID}" -ne 0 ]; then echo "Run as root: sudo $0" >&2; exit 1; fi
command -v nft >/dev/null 2>&1 || { echo "nft not found: apt install nftables" >&2; exit 1; }

case "${1:-}" in
  --dry-run)
    nft -c -f "${RULES_SRC}" && echo "ruleset OK (not applied)"
    exit 0 ;;
  --remove)
    nft delete table inet drifter 2>/dev/null || true
    systemctl disable --now drifter-firewall.service 2>/dev/null || true
    rm -f "${UNIT}" "${RULES_DST}"
    systemctl daemon-reload
    echo "drifter firewall removed"
    exit 0 ;;
  "") ;;
  *) echo "unknown option: $1" >&2; exit 2 ;;
esac

nft -c -f "${RULES_SRC}"
install -D -m 0644 "${RULES_SRC}" "${RULES_DST}"
cat > "${UNIT}" <<UNITEOF
[Unit]
Description=DRIFTER host firewall (service ports hotspot-only)
After=network-pre.target
Before=network.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/sbin/nft -f ${RULES_DST}
ExecStop=/usr/sbin/nft delete table inet drifter

[Install]
WantedBy=multi-user.target
UNITEOF
systemctl daemon-reload
systemctl enable --now drifter-firewall.service
nft list table inet drifter
echo "drifter firewall active. Verify the phone still reaches http://10.42.0.1:8080/healthz"

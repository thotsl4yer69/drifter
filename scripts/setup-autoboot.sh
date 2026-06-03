#!/bin/bash
# ============================================
# MZ1312 DRIFTER — Auto-Boot / Auto-Login Setup
# UNCAGED TECHNOLOGY — EST 1991
# ============================================
# Fixes the "drops to a CLI login prompt" problem so the node comes up
# unattended in the car:
#   1. tty1 auto-login for the operator user (no password prompt)
#   2. drifter-boot-manager runs at boot (LCD splash + spine wait)
#   3. on an interactive tty1 login, print `drifter status` as a quick menu
#
# Usage:  sudo ./scripts/setup-autoboot.sh [user]
#   user  account to auto-login (default: $SUDO_USER, else 'kali')
# ============================================
set -eo pipefail

CYAN='\033[0;36m'; RED='\033[0;31m'; GREEN='\033[0;32m'; AMBER='\033[0;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}  ✓ $1${NC}"; }
warn() { echo -e "${AMBER}  ! $1${NC}"; }
fail() { echo -e "${RED}  ✗ $1${NC}"; exit 1; }
step() { echo -e "\n${AMBER}» $1${NC}"; }

[ "$EUID" -ne 0 ] && fail "Run as root: sudo ./scripts/setup-autoboot.sh"

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOGIN_USER="${1:-${SUDO_USER:-kali}}"
getent passwd "$LOGIN_USER" >/dev/null 2>&1 || fail "User '$LOGIN_USER' does not exist"
USER_HOME="$(getent passwd "$LOGIN_USER" | cut -d: -f6)"

echo -e "${CYAN}DRIFTER auto-boot setup — user=${LOGIN_USER}${NC}"

# ── 0. Wi-Fi boot-hang fix (brcmfmac power-save loop) ──
# This is the ROOT CAUSE of "hangs before login": brcmfmac gets stuck cycling
# Wi-Fi power management. Clear it FIRST — autologin is pointless if the kernel
# never reaches getty. Delegated to the standalone fix script so it can also be
# run on its own against an already-deployed Pi.
step "Applying Wi-Fi boot-hang fix"
WIFI_FIX="${REPO_DIR}/scripts/fix-wifi-boot.sh"
if [ -x "$WIFI_FIX" ] || [ -f "$WIFI_FIX" ]; then
    bash "$WIFI_FIX" && ok "Wi-Fi boot-hang fix applied" || warn "Wi-Fi fix script returned non-zero"
else
    warn "fix-wifi-boot.sh not found — applying inline fallback"
    echo 'options brcmfmac roamoff=1 feature_disable=0x82000' > /etc/modprobe.d/brcmfmac.conf
    systemctl disable NetworkManager-wait-online.service 2>/dev/null || true
fi

# ── 1. Boot to multi-user (CLI), not graphical ──
step "Setting default boot target to multi-user (headless CLI)"
systemctl set-default multi-user.target 2>/dev/null && ok "default target = multi-user.target" \
    || warn "could not set default target"

# ── 2. tty1 auto-login ──
step "Configuring tty1 auto-login for ${LOGIN_USER}"
OVR_DIR="/etc/systemd/system/getty@tty1.service.d"
mkdir -p "$OVR_DIR"
cat > "${OVR_DIR}/autologin.conf" <<EOF
# MZ1312 DRIFTER — auto-login the operator on tty1 so the car node comes up
# unattended. ExecStart is cleared first (override, not append).
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin ${LOGIN_USER} --noclear %I \$TERM
EOF
ok "getty@tty1 autologin drop-in installed"

# ── 3. boot manager at boot ──
step "Enabling drifter-boot-manager (LCD splash + spine wait)"
if [ -f /etc/systemd/system/drifter-boot-manager.service ]; then
    systemctl enable drifter-boot-manager 2>/dev/null && ok "drifter-boot-manager enabled" \
        || warn "could not enable drifter-boot-manager"
elif [ -f "${REPO_DIR}/services/drifter-boot-manager.service" ]; then
    cp "${REPO_DIR}/services/drifter-boot-manager.service" /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable drifter-boot-manager 2>/dev/null && ok "drifter-boot-manager installed + enabled" \
        || warn "could not enable drifter-boot-manager"
else
    warn "drifter-boot-manager.service not found — run setup-lcd.sh or install.sh first"
fi

# ── 4. interactive tty1 login → quick status menu ──
step "Adding 'drifter status' to ${LOGIN_USER}'s interactive tty1 login"
BASHRC="${USER_HOME}/.bashrc"
MARK_BEGIN="# >>> MZ1312 DRIFTER login menu >>>"
MARK_END="# <<< MZ1312 DRIFTER login menu <<<"
if [ -f "$BASHRC" ] && grep -qF "$MARK_BEGIN" "$BASHRC"; then
    ok ".bashrc menu already present"
else
    touch "$BASHRC"
    cat >> "$BASHRC" <<EOF

${MARK_BEGIN}
# On a real tty1 login (not SSH/X), show the node status as a quick menu.
if [ "\$(tty)" = "/dev/tty1" ] && command -v drifter >/dev/null 2>&1; then
    echo
    echo "  MZ1312 DRIFTER — \$(hostname)"
    drifter status 2>/dev/null || true
    echo
    echo "  Commands: drifter diagnose | drifter logs <svc> | drifter restart <svc>"
    echo
fi
${MARK_END}
EOF
    chown "$LOGIN_USER":"$LOGIN_USER" "$BASHRC" 2>/dev/null || true
    ok "login menu appended to $BASHRC"
fi

systemctl daemon-reload

echo ""
echo -e "${GREEN}════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  AUTO-BOOT SETUP COMPLETE${NC}"
echo -e "${GREEN}════════════════════════════════════════════════${NC}"
echo -e "  ${LOGIN_USER} will auto-login on tty1 and see ${CYAN}drifter status${NC}."
echo -e "  The LCD (if wired) shows the boot splash then the live dashboard."
echo ""
echo -e "  ${CYAN}sudo reboot${NC} to verify."
echo ""

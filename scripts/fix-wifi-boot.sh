#!/bin/bash
# ============================================
# MZ1312 DRIFTER — Wi-Fi Boot-Hang Fix
# UNCAGED TECHNOLOGY — EST 1991
# ============================================
# Fixes the Pi 5 boot hang where brcmfmac gets stuck cycling Wi-Fi power
# management and never reaches the login prompt:
#
#   brcmfmac: brcmf_cfg80211_set_power_mgmt: power save enabled
#   brcmfmac: brcmf_cfg80211_set_power_mgmt: power save disabled   (loops…)
#
# Root-cause remedies, all idempotent:
#   1. brcmfmac module options: roamoff=1 + feature_disable mask
#   2. Disable NetworkManager-wait-online (don't block boot on the network)
#   3. wifi-powersave-off.service — kill power_save on every boot
#   4. net.ifnames=0 on the kernel cmdline (stable wlan0 the AP profile binds to)
#   5. ensure `iw` is installed (the power-save service + auto_connect need it)
#
# Safe to run standalone on an already-deployed Pi:  sudo ./scripts/fix-wifi-boot.sh
# Then: sudo reboot
# ============================================
set -eo pipefail

CYAN='\033[0;36m'; RED='\033[0;31m'; GREEN='\033[0;32m'; AMBER='\033[0;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}  ✓ $1${NC}"; }
warn() { echo -e "${AMBER}  ! $1${NC}"; }
fail() { echo -e "${RED}  ✗ $1${NC}"; exit 1; }
step() { echo -e "\n${AMBER}» $1${NC}"; }

[ "$EUID" -ne 0 ] && fail "Run as root: sudo ./scripts/fix-wifi-boot.sh"

WIFI_IFACE="${WIFI_IFACE:-wlan0}"
echo -e "${CYAN}DRIFTER Wi-Fi boot-hang fix — iface=${WIFI_IFACE}${NC}"

# ── 0. ensure iw is present ──
if ! command -v iw >/dev/null 2>&1; then
    step "Installing iw"
    apt-get install -y -qq iw 2>/dev/null && ok "iw installed" || \
        warn "could not install iw — power-save service will be a no-op until present"
fi

# ── 1. brcmfmac module options ──
step "Pinning brcmfmac module options (roamoff + feature_disable)"
echo 'options brcmfmac roamoff=1 feature_disable=0x82000' > /etc/modprobe.d/brcmfmac.conf
ok "/etc/modprobe.d/brcmfmac.conf written"

# ── 2. don't block boot waiting for the network ──
step "Disabling NetworkManager-wait-online"
systemctl disable NetworkManager-wait-online.service 2>/dev/null && \
    ok "NetworkManager-wait-online disabled" || \
    warn "NetworkManager-wait-online not present (ok)"
# systemd-networkd-wait-online too, if that variant is enabled.
systemctl disable systemd-networkd-wait-online.service 2>/dev/null || true

# ── 3. disable power save on every boot ──
step "Installing wifi-powersave-off.service"
cat > /etc/systemd/system/wifi-powersave-off.service <<SVCEOF
[Unit]
Description=MZ1312 DRIFTER — Disable Wi-Fi Power Save (brcmfmac boot-hang fix)
After=network-pre.target
Before=network.target
Wants=network-pre.target

[Service]
Type=oneshot
RemainAfterExit=yes
# Wait briefly for the interface to enumerate, then disable power save.
# Tolerant: never fails the unit even if iw / the iface is missing.
ExecStart=/bin/sh -c 'for i in \$(seq 1 10); do [ -d /sys/class/net/${WIFI_IFACE} ] && break; sleep 1; done; /sbin/iw dev ${WIFI_IFACE} set power_save off || iw dev ${WIFI_IFACE} set power_save off || true'

[Install]
WantedBy=multi-user.target
SVCEOF
systemctl daemon-reload
systemctl enable wifi-powersave-off.service 2>/dev/null && \
    ok "wifi-powersave-off.service enabled" || warn "could not enable wifi-powersave-off.service"
# Apply now too (best-effort) so this boot benefits without a reboot.
iw dev "${WIFI_IFACE}" set power_save off 2>/dev/null && ok "power_save off applied now" || \
    warn "could not apply power_save off this boot (will take effect after reboot)"

# ── 4. stable interface naming on the kernel cmdline ──
step "Adding net.ifnames=0 to the kernel cmdline"
CMDLINE="/boot/firmware/cmdline.txt"
[ -f "$CMDLINE" ] || CMDLINE="/boot/cmdline.txt"
if [ -f "$CMDLINE" ]; then
    if grep -qw "net.ifnames=0" "$CMDLINE"; then
        ok "net.ifnames=0 already present"
    else
        # cmdline.txt MUST stay a single line — append to the existing line.
        sed -i 's/[[:space:]]*$//' "$CMDLINE"          # strip trailing space
        sed -i '1 s/$/ net.ifnames=0/' "$CMDLINE"
        ok "net.ifnames=0 appended to $CMDLINE"
    fi
else
    warn "No cmdline.txt found (looked in /boot/firmware and /boot) — add net.ifnames=0 by hand"
fi

echo ""
echo -e "${GREEN}════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  WI-FI BOOT-HANG FIX APPLIED${NC}"
echo -e "${GREEN}════════════════════════════════════════════════${NC}"
echo -e "  brcmfmac options + power-save service + net.ifnames=0 in place."
echo -e "  ${CYAN}sudo reboot${NC} to clear the boot hang."
echo ""

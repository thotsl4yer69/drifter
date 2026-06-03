#!/bin/bash
# ============================================
# MZ1312 DRIFTER — 3.5" SPI LCD Setup
# UNCAGED TECHNOLOGY — EST 1991
# ============================================
# Wires up a 3.5" SPI TFT (Waveshare 3.5A / piscreen-compatible) as the
# in-car triage display for lcd_dashboard.py. Enables SPI + the panel
# overlay, installs render deps into the drifter venv, fixes fb/GPIO
# permissions, and enables the LCD systemd units.
#
# Usage:  sudo ./scripts/setup-lcd.sh [overlay]
#   overlay  dtoverlay name for your panel (default: piscreen)
#            common: piscreen | waveshare35a | tft35a | flexfb
#
# After running:  sudo reboot
# ============================================
set -eo pipefail

CYAN='\033[0;36m'; RED='\033[0;31m'; GREEN='\033[0;32m'; AMBER='\033[0;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}  ✓ $1${NC}"; }
warn() { echo -e "${AMBER}  ! $1${NC}"; }
fail() { echo -e "${RED}  ✗ $1${NC}"; exit 1; }
step() { echo -e "\n${AMBER}» $1${NC}"; }

[ "$EUID" -ne 0 ] && fail "Run as root: sudo ./scripts/setup-lcd.sh"

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DRIFTER_DIR="/opt/drifter"
OVERLAY="${1:-piscreen}"
LCD_ROTATE="${LCD_ROTATE:-90}"   # most 3.5" panels want 90° landscape
LCD_SPEED="${LCD_SPEED:-16000000}"
LCD_FPS="${LCD_FPS:-30}"

echo -e "${CYAN}DRIFTER LCD setup — overlay=${OVERLAY} rotate=${LCD_ROTATE}${NC}"

# ── 1. boot config: SPI + panel overlay ──
step "Enabling SPI + ${OVERLAY} overlay in boot config"
BOOT_CFG="/boot/firmware/config.txt"
[ -f "$BOOT_CFG" ] || BOOT_CFG="/boot/config.txt"
[ -f "$BOOT_CFG" ] || fail "No boot config.txt found (looked in /boot/firmware and /boot)"

MARK_BEGIN="# >>> MZ1312 DRIFTER LCD >>>"
MARK_END="# <<< MZ1312 DRIFTER LCD <<<"
if grep -qF "$MARK_BEGIN" "$BOOT_CFG"; then
    warn "LCD block already present in $BOOT_CFG — leaving it (edit by hand to change)"
else
    {
        echo ""
        echo "$MARK_BEGIN"
        echo "dtparam=spi=on"
        echo "dtoverlay=${OVERLAY}:rotate=${LCD_ROTATE},speed=${LCD_SPEED},fps=${LCD_FPS}"
        echo "$MARK_END"
    } >> "$BOOT_CFG"
    ok "Appended SPI + ${OVERLAY} overlay to $BOOT_CFG"
fi

# ── 2. render + GPIO deps ──
step "Installing system packages (PIL/numpy/fonts/gpio)"
apt-get install -y -qq \
    python3-pil \
    python3-numpy \
    python3-rpi.gpio \
    fonts-dejavu-core 2>/dev/null && ok "apt deps installed" || \
    warn "apt deps partially failed — venv install below is the source of truth"

# Deps must live in the drifter venv (the units run venv/bin/python3).
if [ -x "${DRIFTER_DIR}/venv/bin/pip" ]; then
    step "Installing render deps into the drifter venv"
    "${DRIFTER_DIR}/venv/bin/pip" install --quiet Pillow numpy RPi.GPIO psutil 2>/dev/null \
        && ok "venv: Pillow numpy RPi.GPIO psutil" \
        || warn "venv pip install failed — run it by hand in ${DRIFTER_DIR}/venv"
else
    warn "drifter venv missing — run install.sh first"
fi

# Vendored font fallback (LCD_FONT_CANDIDATES looks here too).
DEJAVU="/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
if [ -f "$DEJAVU" ]; then
    mkdir -p "${DRIFTER_DIR}/fonts"
    cp "$DEJAVU" "${DRIFTER_DIR}/fonts/" 2>/dev/null && ok "Font cached in ${DRIFTER_DIR}/fonts" || true
fi

# ── 3. framebuffer + GPIO permissions ──
step "Configuring framebuffer + GPIO permissions"
# udev: SPI framebuffer (fb1) readable/writable by the video group.
cat > /etc/udev/rules.d/99-drifter-lcd.rules <<'EOF'
# MZ1312 DRIFTER — SPI LCD framebuffer + GPIO access for the dashboard
SUBSYSTEM=="graphics", KERNEL=="fb1", GROUP="video", MODE="0660"
SUBSYSTEM=="gpio", GROUP="gpio", MODE="0660"
EOF
udevadm control --reload-rules 2>/dev/null || true
ok "udev rule installed (99-drifter-lcd.rules)"
# Group memberships so a non-root run would also work.
for grp in video gpio spi; do
    if getent group "$grp" >/dev/null 2>&1; then
        getent passwd drifter >/dev/null 2>&1 && usermod -aG "$grp" drifter 2>/dev/null || true
    fi
done
ok "drifter user added to video/gpio/spi groups (where present)"

# ── 4. systemd units ──
step "Installing LCD systemd units"
for unit in drifter-lcd drifter-autoconnect drifter-boot-manager; do
    if [ -f "${REPO_DIR}/services/${unit}.service" ]; then
        cp "${REPO_DIR}/services/${unit}.service" /etc/systemd/system/
        ok "copied ${unit}.service"
    else
        warn "${unit}.service not found in repo"
    fi
done
systemctl daemon-reload
for unit in drifter-lcd drifter-autoconnect drifter-boot-manager; do
    systemctl enable "$unit" 2>/dev/null && ok "enabled $unit" || warn "could not enable $unit"
done

echo ""
echo -e "${GREEN}════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  LCD SETUP COMPLETE${NC}"
echo -e "${GREEN}════════════════════════════════════════════════${NC}"
echo -e "  Panel overlay : ${CYAN}${OVERLAY}${NC} (rotate=${LCD_ROTATE})"
echo -e "  If the image is sideways, re-run with a different rotate:"
echo -e "    ${CYAN}sudo LCD_ROTATE=270 ./scripts/setup-lcd.sh ${OVERLAY}${NC}"
echo -e "  If /dev/fb1 never appears, try another overlay:"
echo -e "    ${CYAN}sudo ./scripts/setup-lcd.sh waveshare35a${NC}"
echo ""
echo -e "  Set your phone hotspot for auto-connect in ${CYAN}${DRIFTER_DIR}/.env${NC}:"
echo -e "    ${CYAN}PHONE_HOTSPOT_SSID=YourPhone${NC}"
echo -e "    ${CYAN}PHONE_HOTSPOT_PSK=yourpassword${NC}"
echo ""
echo -e "  Then: ${CYAN}sudo reboot${NC}"
echo ""

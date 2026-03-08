#!/bin/bash
# ============================================
# MZ1312 DRIFTER — Master Installer
# UNCAGED TECHNOLOGY — EST 1991
# ============================================
# Usage: sudo ./install.sh
# ============================================

set -e

CYAN='\033[0;36m'
RED='\033[0;31m'
GREEN='\033[0;32m'
AMBER='\033[0;33m'
NC='\033[0m'

DRIFTER_DIR="/opt/drifter"
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

banner() {
    echo -e "${CYAN}"
    echo "  ██████  ██████  ██ ███████ ████████ ███████ ██████  "
    echo "  ██   ██ ██   ██ ██ ██         ██    ██      ██   ██ "
    echo "  ██   ██ ██████  ██ █████      ██    █████   ██████  "
    echo "  ██   ██ ██   ██ ██ ██         ██    ██      ██   ██ "
    echo "  ██████  ██   ██ ██ ██         ██    ███████ ██   ██ "
    echo ""
    echo "  MZ1312 UNCAGED TECHNOLOGY — Vehicle Intelligence Module"
    echo -e "${NC}"
}

step() { echo -e "\n${AMBER}[$1/$TOTAL] $2${NC}"; }
ok()   { echo -e "${GREEN}  ✓ $1${NC}"; }
warn() { echo -e "${AMBER}  ! $1${NC}"; }
fail() { echo -e "${RED}  ✗ $1${NC}"; exit 1; }

banner

# ── Preflight ──
if [ "$EUID" -ne 0 ]; then fail "Run as root: sudo ./install.sh"; fi

TOTAL=10

# ── 1. System Update ──
step 1 "Updating system packages"
apt-get update -qq 2>/dev/null
apt-get upgrade -y -qq 2>/dev/null
ok "System updated"

# ── 2. Core Dependencies ──
step 2 "Installing core dependencies"
apt-get install -y -qq \
    python3-pip \
    python3-venv \
    can-utils \
    mosquitto-clients \
    network-manager \
    alsa-utils \
    git \
    curl \
    jq \
    slcand 2>/dev/null
ok "Core packages installed"

# ── 3. NanoMQ MQTT Broker ──
step 3 "Installing NanoMQ MQTT broker"
if command -v nanomq &>/dev/null; then
    ok "NanoMQ already installed"
else
    # Try the official install script
    if curl -s https://assets.emqx.com/images/install-nanomq-deb.sh | bash 2>/dev/null; then
        apt-get install -y -qq nanomq 2>/dev/null
        ok "NanoMQ installed from EMQX repo"
    else
        # Fallback: use mosquitto
        warn "NanoMQ repo unavailable, installing Mosquitto as fallback"
        apt-get install -y -qq mosquitto 2>/dev/null
        systemctl enable mosquitto
        ok "Mosquitto installed as MQTT broker"
    fi
fi

# ── 4. TTS Engine ──
step 4 "Installing TTS engine"
if command -v piper &>/dev/null; then
    ok "Piper TTS already installed"
else
    apt-get install -y -qq piper 2>/dev/null && ok "Piper TTS installed" || {
        # Fallback
        apt-get install -y -qq espeak-ng 2>/dev/null
        warn "Piper unavailable, using espeak-ng fallback"
    }
fi

# ── 5. Python Environment ──
step 5 "Setting up Python environment"
mkdir -p ${DRIFTER_DIR}
python3 -m venv ${DRIFTER_DIR}/venv
source ${DRIFTER_DIR}/venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet \
    python-can \
    paho-mqtt \
    psutil
ok "Python venv ready at ${DRIFTER_DIR}/venv"

# ── 6. Deploy Application ──
step 6 "Deploying DRIFTER application"

# Source files
SRC_FILES="can_bridge.py alert_engine.py logger.py voice_alerts.py home_sync.py status.py config.py calibrate.py watchdog.py realdash_bridge.py"
for f in $SRC_FILES; do
    cp "${REPO_DIR}/src/${f}" "${DRIFTER_DIR}/"
    chmod +x "${DRIFTER_DIR}/${f}"
done
ok "Python services deployed to ${DRIFTER_DIR}"

# RealDash config
mkdir -p ${DRIFTER_DIR}/realdash
cp ${REPO_DIR}/realdash/drifter_channels.xml ${DRIFTER_DIR}/realdash/
ok "RealDash channel map deployed"

# Log & session directories
mkdir -p ${DRIFTER_DIR}/logs/sessions
ok "Log directories created"

# ── 7. CAN Interface Setup ──
step 7 "Configuring CAN interface"

cp ${REPO_DIR}/config/setup-can.sh /usr/local/bin/drifter-setup-can
chmod +x /usr/local/bin/drifter-setup-can
cp ${REPO_DIR}/config/80-can.rules /etc/udev/rules.d/
udevadm control --reload-rules 2>/dev/null || true
ok "CAN auto-detection configured"

# Check if boot config needs updating
BOOT_CFG="/boot/firmware/config.txt"
if [ -f "$BOOT_CFG" ]; then
    if ! grep -q "dtparam=spi=on" "$BOOT_CFG"; then
        echo "" >> "$BOOT_CFG"
        cat "${REPO_DIR}/config/boot-config.txt" >> "$BOOT_CFG"
        ok "Boot config updated (SPI + CAN overlay added)"
    else
        ok "Boot config already has SPI enabled"
    fi
else
    warn "Boot config not found at $BOOT_CFG — add entries manually (see config/boot-config.txt)"
fi

# ── 8. Wi-Fi Hotspot ──
step 8 "Configuring Wi-Fi hotspot"

# Remove existing if present
nmcli con show "MZ1312_DRIFTER" &>/dev/null && nmcli con delete "MZ1312_DRIFTER" &>/dev/null

nmcli con add type wifi \
    ifname wlan0 \
    con-name "MZ1312_DRIFTER" \
    autoconnect no \
    ssid "MZ1312_DRIFTER" \
    -- \
    802-11-wireless.mode ap \
    802-11-wireless.band bg \
    802-11-wireless.channel 6 \
    ipv4.method shared \
    ipv4.addresses 10.42.0.1/24 \
    wifi-sec.key-mgmt wpa-psk \
    wifi-sec.psk "uncaged1312" 2>/dev/null

ok "Hotspot: MZ1312_DRIFTER / uncaged1312 / 10.42.0.1"

# ── 9. systemd Services ──
step 9 "Installing systemd services"

# NanoMQ config
if [ -d /etc/nanomq ]; then
    cp ${REPO_DIR}/config/nanomq.conf /etc/nanomq/nanomq.conf
elif command -v nanomq &>/dev/null; then
    cp ${REPO_DIR}/config/nanomq.conf /etc/nanomq.conf
fi

# Deploy all service files
for svc in ${REPO_DIR}/services/*.service; do
    cp "$svc" /etc/systemd/system/
done

systemctl daemon-reload

# Enable all services
SERVICES="drifter-canbridge drifter-alerts drifter-logger drifter-voice drifter-hotspot drifter-homesync drifter-watchdog drifter-realdash"
if command -v nanomq &>/dev/null; then
    systemctl enable nanomq 2>/dev/null || true
else
    # Mosquitto is already enabled
    true
fi

for svc in $SERVICES; do
    systemctl enable "$svc" 2>/dev/null
    ok "Enabled: $svc"
done

# ── 10. Initial Calibration Hint ──
step 10 "Post-install calibration"
echo -e "  After first warm-up drive, run calibration to learn baselines:"
echo -e "  ${CYAN}sudo /opt/drifter/venv/bin/python3 /opt/drifter/calibrate.py --auto${NC}"
ok "Calibration tool ready"

# ── Done ──
echo ""
echo -e "${GREEN}════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  DRIFTER INSTALLED SUCCESSFULLY${NC}"
echo -e "${GREEN}════════════════════════════════════════════════${NC}"
echo ""
echo -e "  ${CYAN}Reboot now:${NC} sudo reboot"
echo ""
echo -e "  After reboot:"
echo -e "  1. Connect phone to Wi-Fi: ${CYAN}MZ1312_DRIFTER${NC}"
echo -e "     Password: ${CYAN}uncaged1312${NC}"
echo -e "  2. Open RealDash → TCP CAN → ${CYAN}10.42.0.1:35000${NC}"
echo -e "     (or MQTT → ${CYAN}10.42.0.1:1883${NC})"
echo -e "  3. Plug phone into Pioneer via USB for Android Auto"
echo -e "  4. Screw OBD-II pigtail into USB2CANFD terminals"
echo -e "  5. After first warm-up: ${CYAN}sudo /opt/drifter/venv/bin/python3 /opt/drifter/calibrate.py --auto${NC}"
echo ""
echo -e "  Check status: ${CYAN}python3 ${DRIFTER_DIR}/status.py${NC}"
echo -e "  Service logs: ${CYAN}journalctl -u drifter-alerts -f${NC}"
echo ""
echo -e "  ${RED}1312${NC} — LOCAL PROCESSING — ZERO CLOUD — TOTAL SOVEREIGNTY"
echo ""

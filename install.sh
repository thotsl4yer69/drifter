#!/bin/bash
# ============================================
# MZ1312 DRIFTER — Master Installer
# UNCAGED TECHNOLOGY — EST 1991
# ============================================
# Usage: sudo ./install.sh
# ============================================

set -eo pipefail

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

TOTAL=12

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
    rsync \
    librtlsdr-dev \
    rtl-sdr 2>/dev/null
ok "Core packages installed"

# Install rtl_433 (433 MHz signal decoder)
if command -v rtl_433 &>/dev/null; then
    ok "rtl_433 already installed"
else
    apt-get install -y -qq rtl-433 2>/dev/null && ok "rtl_433 installed from repo" || {
        # Build from source if not in package repos
        if [ -d /tmp/rtl_433 ]; then rm -rf /tmp/rtl_433; fi
        git clone --quiet --depth 1 https://github.com/merbanan/rtl_433.git /tmp/rtl_433 2>/dev/null
        if [ -d /tmp/rtl_433 ]; then
            apt-get install -y -qq cmake build-essential libusb-1.0-0-dev 2>/dev/null
            mkdir -p /tmp/rtl_433/build && cd /tmp/rtl_433/build
            cmake -DCMAKE_INSTALL_PREFIX=/usr/local .. -Wno-dev 2>/dev/null
            make -j$(nproc) 2>/dev/null && make install 2>/dev/null
            cd ${REPO_DIR}
            rm -rf /tmp/rtl_433
            ok "rtl_433 built from source"
        else
            warn "Could not install rtl_433 — RF features will be unavailable"
        fi
    }
fi

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

# Download Piper voice model — Jenny (female British) matches Vivi's persona.
# en_GB-alan-medium ships pre-existing on some installs; we keep it untouched
# so the legacy voice_alerts service still resolves it, but Vivi's PIPER_MODEL
# in src/config.py points at jenny_dioco.
PIPER_MODEL_DIR="${DRIFTER_DIR}/piper-models"
PIPER_MODEL_NAME="en_GB-jenny_dioco-medium"
PIPER_MODEL_FILE="${PIPER_MODEL_DIR}/${PIPER_MODEL_NAME}.onnx"
PIPER_JSON_FILE="${PIPER_MODEL_DIR}/${PIPER_MODEL_NAME}.onnx.json"

if [ -f "$PIPER_MODEL_FILE" ]; then
    ok "Piper voice model already present (${PIPER_MODEL_NAME})"
else
    mkdir -p "$PIPER_MODEL_DIR"
    PIPER_BASE_URL="https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/jenny_dioco/medium"
    curl -sL "${PIPER_BASE_URL}/${PIPER_MODEL_NAME}.onnx" -o "$PIPER_MODEL_FILE" 2>/dev/null && \
    curl -sL "${PIPER_BASE_URL}/${PIPER_MODEL_NAME}.onnx.json" -o "$PIPER_JSON_FILE" 2>/dev/null && \
    ok "Piper voice model downloaded (${PIPER_MODEL_NAME})" || \
    warn "Could not download Piper model — voice will use espeak-ng fallback"
fi

# ── 5. LLM Engine (Ollama) ──
step 5 "Installing Ollama LLM engine"
if command -v ollama &>/dev/null; then
    ok "Ollama already installed"
else
    curl -fsSL https://ollama.com/install.sh | sh 2>/dev/null && ok "Ollama installed" || \
        warn "Ollama installation failed — LLM mechanic will be unavailable"
fi

# Pull LLM models
# qwen2.5:7b — session_analyst (offline, no turn budget, smarter)
# qwen2.5:3b — Vivi conversational turns on Pi 5 (~25-40s end-to-end)
if command -v ollama &>/dev/null; then
    step 5 "Pulling LLM models (qwen2.5:7b for analyst, qwen2.5:3b for Vivi)"
    ollama pull qwen2.5:7b 2>/dev/null && ok "qwen2.5:7b ready (analyst)" || \
        warn "Could not pull qwen2.5:7b — run 'ollama pull qwen2.5:7b' manually"
    ollama pull qwen2.5:3b 2>/dev/null && ok "qwen2.5:3b ready (Vivi)" || \
        warn "Could not pull qwen2.5:3b — run 'ollama pull qwen2.5:3b' manually"
fi

# ── 5b. Voice Input (STT + Wake Word) ──
step 5 "Installing voice input system dependencies"
apt-get install -y -qq portaudio19-dev 2>/dev/null || warn "portaudio19-dev not found"
# Python deps (vosk, pyaudio, openwakeword) installed below after venv creation

# Download Vosk model
VOSK_MODEL_DIR="${DRIFTER_DIR}/vosk-models"
VOSK_MODEL_NAME="vosk-model-small-en-us-0.15"
if [ -d "${VOSK_MODEL_DIR}/${VOSK_MODEL_NAME}" ]; then
    ok "Vosk model already present"
else
    mkdir -p "$VOSK_MODEL_DIR"
    VOSK_URL="https://alphacephei.com/vosk/models/${VOSK_MODEL_NAME}.zip"
    curl -sL "$VOSK_URL" -o "/tmp/${VOSK_MODEL_NAME}.zip" 2>/dev/null && \
    unzip -qo "/tmp/${VOSK_MODEL_NAME}.zip" -d "$VOSK_MODEL_DIR" 2>/dev/null && \
    rm -f "/tmp/${VOSK_MODEL_NAME}.zip" && \
    ok "Vosk STT model downloaded" || \
    warn "Could not download Vosk model — voice input STT unavailable"
fi

# ── 5c. Unprivileged service user ──
# Most drifter-* services don't need root — only the ones that tweak the
# network stack (canbridge / hotspot / watchdog). Create a system user
# `drifter` so the pure-software services can drop privileges.
step 5 "Creating unprivileged 'drifter' service user"
if ! getent passwd drifter >/dev/null 2>&1; then
    useradd --system --home "${DRIFTER_DIR}" --shell /usr/sbin/nologin \
            --user-group drifter 2>/dev/null && ok "'drifter' user created" || \
            warn "Could not create 'drifter' user"
else
    ok "'drifter' user already exists"
fi
# Group memberships for hardware access: audio (ALSA), dialout (USB-serial
# CAN adapters), plugdev (USB hotplug incl. RTL-SDR), video (framebuffer).
for grp in audio dialout plugdev video; do
    if getent group "$grp" >/dev/null 2>&1; then
        usermod -aG "$grp" drifter 2>/dev/null || true
    fi
done

# ── 6. Python Environment ──
step 6 "Setting up Python environment"
mkdir -p ${DRIFTER_DIR}
python3 -m venv ${DRIFTER_DIR}/venv
source ${DRIFTER_DIR}/venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet \
    python-can \
    "paho-mqtt>=2.0" \
    psutil \
    websockets \
    requests \
    numpy
# Voice input Python deps (must be in venv)
pip install --quiet vosk pyaudio openwakeword pyyaml 2>/dev/null && ok "Voice input Python deps installed" || \
    warn "Voice input deps failed — run 'pip install vosk pyaudio openwakeword pyyaml' in venv"
# Vivi STT/TTS deps
pip install --quiet faster-whisper piper-tts sounddevice 2>/dev/null && ok "Vivi STT/TTS deps installed" || \
    warn "Vivi deps failed — run 'pip install faster-whisper piper-tts sounddevice' in venv"
# Corpus retrieval (sentence-transformers + torch — large download, only if missing)
"${DRIFTER_DIR}/venv/bin/python3" -c "import sentence_transformers" 2>/dev/null \
    && ok "sentence-transformers already installed" \
    || (pip install --quiet sentence-transformers \
        && ok "sentence-transformers installed (corpus retrieval)") \
    || warn "sentence-transformers install failed — corpus search disabled"
# Passive BLE scanner deps
"${DRIFTER_DIR}/venv/bin/python3" -c "import bleak" 2>/dev/null \
    && ok "bleak already installed" \
    || (pip install --quiet "bleak>=0.21.0" \
        && ok "bleak installed (passive BLE scanner)") \
    || warn "bleak install failed — drifter-bleconv disabled"
ok "Python venv ready at ${DRIFTER_DIR}/venv"

# ── 7. Deploy Application ──
step 7 "Deploying DRIFTER application"

# Source files
SRC_FILES="can_bridge.py alert_engine.py logger.py voice_alerts.py home_sync.py status.py config.py calibrate.py watchdog.py realdash_bridge.py rf_monitor.py wardrive.py web_dashboard.py web_dashboard_state.py web_dashboard_handlers.py web_dashboard_html.py web_dashboard_audio.py web_dashboard_hardware.py mechanic.py anomaly_monitor.py session_analyst.py db.py llm_client.py voice_input.py field_ops_kb.py diagnose.py vivi.py flipper_bridge.py mode.py opsec_dashboard.py corpus.py ble_passive.py"
for f in $SRC_FILES; do
    if [ -f "${REPO_DIR}/src/${f}" ]; then
        cp "${REPO_DIR}/src/${f}" "${DRIFTER_DIR}/"
        chmod +x "${DRIFTER_DIR}/${f}"
    fi
done
ok "Python services deployed to ${DRIFTER_DIR}"

# Fleet-contract operator CLI: /usr/local/bin/drifter → /opt/drifter/diagnose.py
if [ -f "${REPO_DIR}/bin/drifter" ]; then
    install -m 0755 "${REPO_DIR}/bin/drifter" /usr/local/bin/drifter
    ok "drifter CLI installed (/usr/local/bin/drifter)"
fi

# Data files
if [ -f "${REPO_DIR}/src/knowledge_base.json" ]; then
    cp "${REPO_DIR}/src/knowledge_base.json" "${DRIFTER_DIR}/"
    ok "Knowledge base deployed"
fi

# Mechanic knowledge base (JSON data files loaded by mechanic.py at runtime)
if [ -d "${REPO_DIR}/src/data/mechanic" ]; then
    mkdir -p "${DRIFTER_DIR}/data/mechanic"
    cp "${REPO_DIR}"/src/data/mechanic/*.json "${DRIFTER_DIR}/data/mechanic/"
    ok "Mechanic knowledge base deployed ($(ls "${REPO_DIR}/src/data/mechanic" | wc -l) files)"
fi

# (Old kiosk-mode SPI HUD removed — drifter-fbmirror mirrors fb0→fb1
# directly in C, no HTML required, no Firefox-on-the-Pi layer needed.)

# Framebuffer mirror (SPI LCD support)
if [ -f "${REPO_DIR}/src/fbmirror.c" ]; then
    gcc -O2 -o "${DRIFTER_DIR}/fbmirror" "${REPO_DIR}/src/fbmirror.c" 2>/dev/null && \
    ok "fbmirror compiled for SPI LCD" || \
    warn "fbmirror compilation failed — SPI LCD mirroring unavailable"
fi

# RealDash config
mkdir -p "${DRIFTER_DIR}/realdash"
cp "${REPO_DIR}/realdash/drifter_channels.xml" "${DRIFTER_DIR}/realdash/"
ok "RealDash channel map deployed"

# Vivi config (don't overwrite if already customised)
if [ ! -f "${DRIFTER_DIR}/vivi.yaml" ]; then
    cp "${REPO_DIR}/config/vivi.yaml" "${DRIFTER_DIR}/"
    ok "vivi.yaml deployed"
else
    ok "vivi.yaml already present — not overwriting"
fi

# Driver profile (Vivi reads name every turn)
if [ ! -f "${DRIFTER_DIR}/driver.yaml" ]; then
    cp "${REPO_DIR}/config/driver.yaml" "${DRIFTER_DIR}/"
    ok "driver.yaml deployed"
fi

# BLE target registry (drifter-bleconv)
if [ ! -f "${DRIFTER_DIR}/ble_targets.yaml" ]; then
    cp "${REPO_DIR}/config/ble_targets.yaml" "${DRIFTER_DIR}/"
    ok "ble_targets.yaml deployed"
else
    ok "ble_targets.yaml already present — not overwriting"
fi

# polkit grant for drifter user → BlueZ (BLE passive scan needs D-Bus access)
POLKIT_SRC="${REPO_DIR}/services/51-drifter-bluetooth.rules"
POLKIT_DST="/etc/polkit-1/rules.d/51-drifter-bluetooth.rules"
if [ -f "$POLKIT_SRC" ]; then
    install -m 0644 -o root -g root "$POLKIT_SRC" "$POLKIT_DST"
    ok "BlueZ polkit rule installed"
fi

# Log & session + state directories
mkdir -p ${DRIFTER_DIR}/logs/sessions ${DRIFTER_DIR}/state
chown -R drifter:drifter ${DRIFTER_DIR}/state ${DRIFTER_DIR}/logs 2>/dev/null || true
ok "Log/state directories created"

# Analyst data directories and API key placeholder
mkdir -p ${DRIFTER_DIR}/data ${DRIFTER_DIR}/reports
touch ${DRIFTER_DIR}/.env
ok "Analyst data directories created"

# Hand everything under DRIFTER_DIR to the drifter user. The services that
# still run as root can write to root-owned paths fine; the services that
# drop to `drifter` need this ownership to write logs / settings / the
# SQLite DB. Keep the venv and data dir group-writable so re-installs don't
# fight with mode 600 files from the previous run.
if getent passwd drifter >/dev/null 2>&1; then
    chown -R drifter:drifter "${DRIFTER_DIR}"
    # .env may hold API keys — lock it down to the service user.
    chmod 640 "${DRIFTER_DIR}/.env" 2>/dev/null || true
    ok "Ownership of ${DRIFTER_DIR} assigned to drifter:drifter"
fi

# ── 8. CAN Interface Setup ──
step 8 "Configuring CAN interface"

cp "${REPO_DIR}/config/setup-can.sh" /usr/local/bin/drifter-setup-can
chmod +x /usr/local/bin/drifter-setup-can
cp "${REPO_DIR}/config/80-can.rules" /etc/udev/rules.d/
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

# ── 9. Wi-Fi Hotspot ──
step 9 "Configuring Wi-Fi hotspot"

# Remove existing if present
nmcli con show "MZ1312_DRIFTER" &>/dev/null && nmcli con delete "MZ1312_DRIFTER" &>/dev/null

nmcli con add type wifi \
    ifname wlan0 \
    con-name "MZ1312_DRIFTER" \
    autoconnect yes \
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

# ── 10. systemd Services ──
step 10 "Installing systemd services"

# NanoMQ config
if [ -d /etc/nanomq ]; then
    cp "${REPO_DIR}/config/nanomq.conf" /etc/nanomq/nanomq.conf
elif command -v nanomq &>/dev/null; then
    cp "${REPO_DIR}/config/nanomq.conf" /etc/nanomq.conf
fi

# Deploy all service files
for svc in ${REPO_DIR}/services/*.service; do
    cp "$svc" /etc/systemd/system/
done

# Sudoers drop-ins — narrow NOPASSWD entries for the dashboards.
# visudo -cf validates each file before activating; a bad sudoers file would
# break system-wide sudo, so refuse to ship it (set -e propagates the failure).
for sudoers_src in "${REPO_DIR}"/services/drifter-*.sudoers; do
    [ -f "$sudoers_src" ] || continue
    name="$(basename "$sudoers_src" .sudoers)"
    sudoers_dst="/etc/sudoers.d/${name}"
    install -m 0440 -o root -g root "$sudoers_src" "$sudoers_dst"
    visudo -cf "$sudoers_dst" >/dev/null
done

systemctl daemon-reload

# Enable all services
# Older deploys shipped drifter-llm.service (superseded by drifter-analyst);
# tear it down if a unit file is left over from before that cleanup.
systemctl disable --now drifter-llm 2>/dev/null || true
rm -f /etc/systemd/system/drifter-llm.service

SERVICES="drifter-canbridge drifter-alerts drifter-dashboard drifter-logger drifter-voice drifter-vivi drifter-hotspot drifter-homesync drifter-watchdog drifter-realdash drifter-rf drifter-wardrive drifter-fbmirror drifter-anomaly drifter-analyst drifter-voicein drifter-flipper drifter-opsec"
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

# ── 11. RTL-SDR Blacklist ──
step 11 "Configuring RTL-SDR"

# Blacklist the DVB-T kernel driver so rtl-sdr can use the device
if [ ! -f /etc/modprobe.d/blacklist-rtlsdr.conf ]; then
    cat > /etc/modprobe.d/blacklist-rtlsdr.conf << 'EOF'
# MZ1312 DRIFTER — Blacklist DVB-T drivers so RTL-SDR can access the device
blacklist dvb_usb_rtl28xxu
blacklist rtl2832
blacklist rtl2830
EOF
    ok "DVB-T kernel driver blacklisted for RTL-SDR"
else
    ok "RTL-SDR blacklist already configured"
fi

# ── 12. Initial Calibration Hint ──
step 12 "Post-install calibration"
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

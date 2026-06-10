#!/bin/bash
# ============================================
# MZ1312 DRIFTER — Master Installer
# UNCAGED TECHNOLOGY — EST 1991
# ============================================
# Usage: sudo ./install.sh
# ============================================

set -eo pipefail

# ── Argument parsing ──
# --skip-apt: skip step 1 (apt update + apt upgrade). Useful for repeated
# deploys where the system is already up to date and you just want to push
# new source/services. Step 2+ still run (apt install of specific packages
# is idempotent — dpkg no-ops if already installed).
SKIP_APT=0
WITH_7B=0
for arg in "$@"; do
    case "$arg" in
        --skip-apt) SKIP_APT=1 ;;
        --with-7b) WITH_7B=1 ;;
        -h|--help)
            echo "Usage: sudo ./install.sh [--skip-apt] [--with-7b]"
            echo "  --skip-apt: skip step 1 system update/upgrade"
            echo "  --with-7b:  also pull qwen2.5:7b (opt-in; ~4.7GB). The Pi 5"
            echo "              can't hold 7b warm alongside Vivi — only pull it"
            echo "              if you'll run the analyst standalone/offline."
            exit 0
            ;;
        *)
            echo "unknown arg: $arg" >&2
            exit 2
            ;;
    esac
done

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
if [[ "${SKIP_APT:-0}" != "1" ]]; then
    apt-get update -qq 2>/dev/null
    apt-get upgrade -y -qq 2>/dev/null
    ok "System updated"
else
    ok "Skipped (--skip-apt)"
fi

# ── 2. Core Dependencies ──
step 2 "Installing core dependencies"
if [[ "${SKIP_APT:-0}" != "1" ]]; then
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
        rtl-sdr \
        gpsd \
        gpsd-clients 2>/dev/null
    ok "Core packages installed"
else
    ok "Skipped (--skip-apt) — assumed present"
fi

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
elif command -v mosquitto &>/dev/null; then
    ok "Mosquitto already installed (MQTT broker)"
    systemctl enable mosquitto 2>/dev/null || true
elif [[ "${SKIP_APT:-0}" != "1" ]]; then
    # Try the official install script
    if curl -s https://assets.emqx.com/images/install-nanomq-deb.sh | bash 2>/dev/null; then
        apt-get install -y -qq nanomq 2>/dev/null
        ok "NanoMQ installed from EMQX repo"
    else
        warn "NanoMQ repo unavailable, installing Mosquitto as fallback"
        apt-get install -y -qq mosquitto 2>/dev/null
        systemctl enable mosquitto
        ok "Mosquitto installed as MQTT broker"
    fi
else
    warn "No MQTT broker found — skipped install (--skip-apt)"
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

# Pull LLM model(s).
# ONE small model for the whole fleet — qwen2.5:1.5b (~1.0GB). This is the
# config.py OLLAMA_MODEL default AND the vivi.yaml ollama_model: every LLM
# consumer (vivi conversational turns, session_analyst, session_reporter,
# ai_diagnostics — all via src/llm_client.py) resolves to this tag. The Pi 5
# is CPU-only; 1.5b warms in ~10-40s. Larger tags (3b/7b) stalled 60-120s per
# turn on this hardware and never fit warm alongside a second model, so we do
# NOT pull them by default. `--with-7b` opts into qwen2.5:7b for operators who
# run the analyst standalone/offline (no turn budget) and accept it can't be
# co-resident with Vivi (OLLAMA_MAX_LOADED_MODELS=1 evicts one for the other).
#
# IMPORTANT: keep the default-pull tag(s) below in lock-step with
# config.py OLLAMA_MODEL — tests/test_llm_model_strategy.py fails the build if
# install.sh pulls a model the config doesn't reference, or omits one it does.
OLLAMA_DEFAULT_MODEL="qwen2.5:1.5b"
if command -v ollama &>/dev/null; then
    step 5 "Pulling LLM model (${OLLAMA_DEFAULT_MODEL} — fleet-wide default)"
    ollama pull "${OLLAMA_DEFAULT_MODEL}" 2>/dev/null \
        && ok "${OLLAMA_DEFAULT_MODEL} ready" \
        || warn "Could not pull ${OLLAMA_DEFAULT_MODEL} — run 'ollama pull ${OLLAMA_DEFAULT_MODEL}' manually"
    if [[ "${WITH_7B}" == "1" ]]; then
        step 5 "Pulling qwen2.5:7b (opt-in via --with-7b; analyst standalone)"
        ollama pull qwen2.5:7b 2>/dev/null && ok "qwen2.5:7b ready (analyst, opt-in)" || \
            warn "Could not pull qwen2.5:7b — run 'ollama pull qwen2.5:7b' manually"
    fi
fi

# Cap Ollama at ONE resident model so analyst and vivi can never co-pin two
# models and OOM the 8GB Pi. Set on the ollama DAEMON (not drifter's .env —
# ollama.service runs as its own user and won't read /opt/drifter/.env) via a
# systemd drop-in. KEEP_ALIVE matches config.OLLAMA_KEEP_ALIVE so the warm
# model survives between turns but a second request for a different tag evicts
# the first instead of holding both.
if command -v ollama &>/dev/null && systemctl list-unit-files ollama.service &>/dev/null; then
    step 5 "Configuring Ollama single-model residency (OLLAMA_MAX_LOADED_MODELS=1)"
    mkdir -p /etc/systemd/system/ollama.service.d
    cat > /etc/systemd/system/ollama.service.d/drifter-residency.conf <<'OLLAMA_CONF'
# Managed by DRIFTER install.sh — Pi 5 8GB single-model residency guard.
# Holds at most one model in RAM so session_analyst and Vivi can't both pin a
# model concurrently (would OOM). KEEP_ALIVE keeps the active model warm.
[Service]
Environment="OLLAMA_MAX_LOADED_MODELS=1"
Environment="OLLAMA_KEEP_ALIVE=30m"
OLLAMA_CONF
    systemctl daemon-reload 2>/dev/null || true
    systemctl restart ollama 2>/dev/null || true
    ok "Ollama capped to 1 resident model (keep_alive 30m)"
fi

# ── 5b. Voice Input (STT + Wake Word) ──
step 5 "Installing voice input system dependencies"
if dpkg -s portaudio19-dev 2>/dev/null | grep -q 'Status: install ok installed'; then
    ok "portaudio19-dev already installed"
elif [[ "${SKIP_APT:-0}" != "1" ]]; then
    apt-get install -y -qq portaudio19-dev 2>/dev/null || warn "portaudio19-dev not found"
else
    warn "portaudio19-dev missing — skipped install (--skip-apt)"
fi
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
    numpy \
    pyserial \
    pyyaml \
    Pillow
# Pillow: lcd_dashboard.py / boot_manager.py render the in-car 3.5" SPI LCD with
#   PIL + numpy straight to the framebuffer. numpy is already above; Pillow is a
#   prebuilt arm64 wheel so it's safe in the core block.
# pyserial: flipper_bridge.py imports `serial` at module load (drifter-flipper
#   is enabled) and marauder_transport/realdash also need it — without it the
#   service crash-loops on ModuleNotFoundError. pyyaml: config.py imports `yaml`
#   and EVERY service imports config, so it must be in the core block (not only
#   the best-effort voice-deps line below, which may warn-and-skip on failure).
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
# Public-feeds producer (drifter-feeds, in SERVICES). aiohttp drives the async
# HTTP polls; lxml parses the emergency-marker GeoJSON/CAP. feeds GUARDS both
# imports and degrades to the file-based ADS-B path (its fly-catcher producer
# role) without them, so this is best-effort — but installing them lets the
# service run fully (weather/emergency feeds), not ADS-B-only.
pip install --quiet aiohttp lxml 2>/dev/null && ok "feeds deps installed (aiohttp, lxml)" || \
    warn "feeds deps failed — drifter-feeds runs ADS-B-only (run 'pip install aiohttp lxml' in venv)"
# In-car SPI LCD GPIO buttons (drifter-lcd). RPi.GPIO only builds on a Pi, so
# keep it best-effort — the LCD dashboard auto-cycles/MQTT-controls without it.
pip install --quiet RPi.GPIO 2>/dev/null && ok "RPi.GPIO installed (LCD buttons)" || \
    warn "RPi.GPIO install failed — LCD buttons disabled (run scripts/setup-lcd.sh on the Pi)"
ok "Python venv ready at ${DRIFTER_DIR}/venv"

# ── 7. Deploy Application ──
step 7 "Deploying DRIFTER application"

# Source files — deploy the ENTIRE src/ Python tree, not a hand-maintained
# manifest. A stale manifest is how drifter-vivi (vivi_v2.py), drifter-marauder
# (marauder_bridge.py + the marauder_features/ package) and drifter-boot-reason
# (boot_reason.py) shipped enabled-but-undeployed and crash-looped. Copying all
# of src/*.py is safe: only the services we `systemctl enable` ever run, so the
# extra modules just sit idle. Subpackages (marauder_features/) come along too.
# This also auto-deploys the hid_* modules (drifter-hid) — no manifest to update.
cp "${REPO_DIR}"/src/*.py "${DRIFTER_DIR}/"
chmod +x "${DRIFTER_DIR}"/*.py 2>/dev/null || true
# Dashboard HTML assets served from disk (e.g. web_dashboard's vivi_avatar.html,
# which looks beside the module / under /opt/drifter). Without these the route
# 404s "not deployed". nullglob so it's a no-op if none exist.
shopt -s nullglob
for html in "${REPO_DIR}"/src/*.html; do cp "$html" "${DRIFTER_DIR}/"; done
shopt -u nullglob
# Local subpackages imported by services (e.g. marauder_bridge -> marauder_features).
for pkg in marauder_features; do
    if [ -d "${REPO_DIR}/src/${pkg}" ]; then
        rm -rf "${DRIFTER_DIR:?}/${pkg}"
        cp -r "${REPO_DIR}/src/${pkg}" "${DRIFTER_DIR}/${pkg}"
    fi
done
ok "Python services deployed to ${DRIFTER_DIR} ($(ls "${REPO_DIR}"/src/*.py | wc -l) modules + subpackages)"

# Fleet-contract operator CLI: /usr/local/bin/drifter → /opt/drifter/diagnose.py
if [ -f "${REPO_DIR}/bin/drifter" ]; then
    install -m 0755 "${REPO_DIR}/bin/drifter" /usr/local/bin/drifter
    ok "drifter CLI installed (/usr/local/bin/drifter)"
fi

# Phase 4.7 BLE forensic export CLI
if [ -f "${REPO_DIR}/scripts/drifter-ble-export" ]; then
    install -m 0755 "${REPO_DIR}/scripts/drifter-ble-export" \
            /usr/local/bin/drifter-ble-export
    ok "drifter-ble-export installed (/usr/local/bin/drifter-ble-export)"
fi

# Phase 4.8 closeout — soak diagnostic report
if [ -f "${REPO_DIR}/scripts/drifter-ble-soak-report" ]; then
    install -m 0755 "${REPO_DIR}/scripts/drifter-ble-soak-report" \
            /usr/local/bin/drifter-ble-soak-report
    ok "drifter-ble-soak-report installed (/usr/local/bin/drifter-ble-soak-report)"
fi

# Vendored Leaflet for the /map/ble route — phones tethered to the
# MZ1312_DRIFTER hotspot can't reliably reach unpkg.
if [ -d "${REPO_DIR}/static/leaflet" ]; then
    mkdir -p "${DRIFTER_DIR}/static/leaflet"
    cp "${REPO_DIR}"/static/leaflet/* "${DRIFTER_DIR}/static/leaflet/"
    ok "Leaflet $(ls "${REPO_DIR}/static/leaflet" | wc -l) asset(s) deployed"
fi

# Brand / PWA icons (favicon, apple-touch-icon, web manifest) — served by the
# dashboard so the phone-tethered cockpit installs to the home screen branded.
if [ -d "${REPO_DIR}/static/icons" ]; then
    mkdir -p "${DRIFTER_DIR}/static/icons"
    cp "${REPO_DIR}"/static/icons/* "${DRIFTER_DIR}/static/icons/"
    ok "Brand/PWA icon $(ls "${REPO_DIR}/static/icons" | wc -l) asset(s) deployed"
fi

# Cockpit front door — web_dashboard_handlers.py serves the cockpit page from
# /opt/drifter/ui/cockpit-preview.html. Without this the dashboard root returns
# "503 cockpit not deployed" even though the service is healthy.
if [ -f "${REPO_DIR}/ui/cockpit-preview.html" ]; then
    mkdir -p "${DRIFTER_DIR}/ui"
    cp "${REPO_DIR}/ui/cockpit-preview.html" "${DRIFTER_DIR}/ui/"
    ok "Cockpit page deployed (ui/cockpit-preview.html)"
fi

# Avatar + media assets — web_dashboard_handlers._serve_avatar_asset serves
# /assets/* from /opt/drifter/assets. Without this the Vivi 3D viewer
# (/avatar) loads but the .glb model 404s, so she never renders.
if [ -d "${REPO_DIR}/assets" ]; then
    mkdir -p "${DRIFTER_DIR}/assets"
    cp "${REPO_DIR}"/assets/* "${DRIFTER_DIR}/assets/" 2>/dev/null || true
    ok "Assets deployed ($(ls "${REPO_DIR}/assets" | wc -l) file(s), incl. vivi_avatar.glb)"
fi

# Cockpit desktop launcher — DRIFTER-Cockpit.desktop execs this.
if [ -f "${REPO_DIR}/tools/launch-cockpit.sh" ]; then
    install -D -m 0755 "${REPO_DIR}/tools/launch-cockpit.sh" \
            "${DRIFTER_DIR}/bin/launch-cockpit.sh"
    ok "Cockpit launcher deployed (bin/launch-cockpit.sh)"
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

# Kismet site config — overrides configdir to /var/lib/drifter-kismet
# so the daemon survives ProtectHome=true in drifter-kismet.service.
KISMET_SITE_SRC="${REPO_DIR}/config/kismet_site.conf"
KISMET_SITE_DST="/etc/kismet/kismet_site.conf"
if [ -f "$KISMET_SITE_SRC" ] && [ -d /etc/kismet ]; then
    install -m 0644 -o root -g root "$KISMET_SITE_SRC" "$KISMET_SITE_DST"
    ok "Kismet site config installed"
fi

# Marauder config tree — operator allowlist + portal templates + beacon lists
mkdir -p /opt/drifter/etc/marauder/portals \
         /opt/drifter/etc/marauder/beacon_lists
chown -R drifter:drifter /opt/drifter/etc/marauder
ok "Marauder config tree at /opt/drifter/etc/marauder/"

# Seed audit_targets.yaml ONLY if it doesn't already exist — never
# overwrite operator scope.
if [ ! -f /opt/drifter/etc/audit_targets.yaml ]; then
    mkdir -p /opt/drifter/etc
    install -m 0640 -o drifter -g drifter \
        "${REPO_DIR}/config/audit_targets.sample.yaml" \
        /opt/drifter/etc/audit_targets.yaml
    ok "audit_targets.yaml seeded (EMPTY — populate before HIGH-risk commands work)"
else
    ok "audit_targets.yaml already present — not overwriting"
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

# zram compressed-swap OOM backstop (no disk swap on a car-mounted SD Pi).
# drifter-zram.service is shipped by the services/*.service glob below;
# enable it separately (it's infra, not a config.SERVICES unit).
cp "${REPO_DIR}/config/setup-zram.sh" /usr/local/bin/drifter-zram
chmod +x /usr/local/bin/drifter-zram
ok "zram OOM backstop installed (drifter-zram)"

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

# Preserve existing hotspot profile + rotated PSK (operator may have changed it).
# Only create from defaults if it doesn't exist yet.
if nmcli con show "MZ1312_DRIFTER" &>/dev/null; then
    ok "Hotspot MZ1312_DRIFTER already configured — preserving PSK"
else
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
    ok "Hotspot: MZ1312_DRIFTER (PSK via nmcli --show-secrets) / 10.42.0.1"
fi

# ── 10. systemd Services ──
step 10 "Installing systemd services"

# NanoMQ config
if [ -d /etc/nanomq ]; then
    cp "${REPO_DIR}/config/nanomq.conf" /etc/nanomq/nanomq.conf
elif command -v nanomq &>/dev/null; then
    cp "${REPO_DIR}/config/nanomq.conf" /etc/nanomq.conf
fi

# Deploy all service + timer files
for svc in ${REPO_DIR}/services/*.service; do
    cp "$svc" /etc/systemd/system/
done
# Timer units must be copied too (otherwise enable below fails with
# "Unit not found"). nullglob so the glob expands to nothing if no .timer
# files exist, instead of looping over the literal '*.timer' string.
shopt -s nullglob
for tmr in ${REPO_DIR}/services/*.timer; do
    cp "$tmr" /etc/systemd/system/
done
shopt -u nullglob

# Sudoers drop-ins — narrow NOPASSWD entries for the dashboards.
# Ships drifter-mode.sudoers (mode-switch) AND drifter-service.sudoers
# (arsenal service start/stop/restart for the foot-mode toolkit, BE-4).
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

# zram OOM backstop — infra unit (not a config.SERVICES member), enabled on its
# own so it doesn't perturb the SERVICES invariant the deploy tests enforce.
systemctl enable --now drifter-zram 2>/dev/null || true

# Enable all services
# Older deploys shipped drifter-llm.service (superseded by drifter-analyst);
# tear it down if a unit file is left over from before that cleanup.
systemctl disable --now drifter-llm 2>/dev/null || true
rm -f /etc/systemd/system/drifter-llm.service

# Pre-2026-05 deploys wrote the persona to /opt/drifter/state/mode; the
# canonical path is /opt/drifter/mode.state (config.py MODE_STATE_PATH).
# Drop the stale file so it can't drift out of sync with mode.state.
rm -f "${DRIFTER_DIR}/state/mode"

# The first 38 entries are config.py SERVICES verbatim (the set /healthz
# monitors); the trailing three are boot/oneshot aux units that run but aren't
# health-checked. tests/test_deploy_service_lists.py enforces that this list is
# a superset of config.SERVICES so the deploy always enables what /healthz
# expects.
SERVICES="drifter-alerts drifter-analyst drifter-anomaly drifter-autoconnect drifter-batcher drifter-bleconv drifter-can-discovery drifter-canbridge drifter-dashboard drifter-fbmirror drifter-flipper drifter-fly-catcher drifter-feeds drifter-ghost drifter-ghost-voice drifter-gps drifter-hid drifter-homesync drifter-hotspot drifter-kismet drifter-kismet-bridge drifter-lcd drifter-location drifter-logger drifter-marauder drifter-opsec drifter-realdash drifter-reporter drifter-rf drifter-rfaudio drifter-thresholds drifter-trip drifter-vivi drifter-voice drifter-voicein drifter-wardrive drifter-watchdog drifter-weather drifter-wifi-audit drifter-boot-manager drifter-boot-reason drifter-db-checkpoint"
# NOTE: drifter-fbmirror (fb0→fb1 mirror) and drifter-lcd (standalone fb1 menu)
# both drive the SPI LCD — they are mutually exclusive. The deploy enables both
# here; pick ONE on the Pi: `systemctl disable --now drifter-fbmirror` to use
# the lcd_dashboard menu UI, or disable drifter-lcd to keep the mirror.
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

# Enable any drifter-*.timer units we shipped. Timer enables go via
# timers.target, so they need separate `systemctl enable` calls.
shopt -s nullglob
for tmr_file in ${REPO_DIR}/services/drifter-*.timer; do
    tmr_name="$(basename "$tmr_file")"
    systemctl enable "$tmr_name" 2>/dev/null
    ok "Enabled: $tmr_name"
done
shopt -u nullglob

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

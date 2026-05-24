#!/bin/bash
# ============================================
# MZ1312 DRIFTER — Vivi Voice Assistant Installer
# Installs faster-whisper STT, sounddevice, pyyaml
# and deploys the vivi service.
# UNCAGED TECHNOLOGY — EST 1991
# ============================================
# Usage: sudo ./scripts/install-vivi.sh
# ============================================

set -e

CYAN='\033[0;36m'
RED='\033[0;31m'
GREEN='\033[0;32m'
AMBER='\033[0;33m'
NC='\033[0m'

DRIFTER_DIR="/opt/drifter"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

ok()   { echo -e "${GREEN}  ✓ $1${NC}"; }
warn() { echo -e "${AMBER}  ! $1${NC}"; }
fail() { echo -e "${RED}  ✗ $1${NC}"; exit 1; }
step() { echo -e "\n${AMBER}[$1] $2${NC}"; }

echo -e "${CYAN}  DRIFTER — Vivi Voice Assistant Installer${NC}"
echo -e "${CYAN}  MZ1312 UNCAGED TECHNOLOGY — EST 1991${NC}\n"

if [ "$EUID" -ne 0 ]; then fail "Run as root: sudo ./scripts/install-vivi.sh"; fi

# ── 1. System audio deps ──
step 1 "Installing audio system dependencies"
apt-get install -y -qq \
    libportaudio2 \
    portaudio19-dev \
    python3-dev 2>/dev/null
ok "Audio system packages installed"

# ── 2. Python deps in venv ──
step 2 "Installing Python packages (faster-whisper, sounddevice, pyyaml)"
source "${DRIFTER_DIR}/venv/bin/activate"
pip install --quiet \
    "faster-whisper>=1.0.0" \
    sounddevice \
    pyyaml
ok "Python packages installed"

# ── 3. Deploy vivi.py ──
step 3 "Deploying vivi.py"
cp "${REPO_DIR}/src/vivi.py" "${DRIFTER_DIR}/"
chmod +x "${DRIFTER_DIR}/vivi.py"
ok "vivi.py deployed to ${DRIFTER_DIR}"

# ── 4. Deploy config ──
step 4 "Deploying vivi.yaml config"
if [ -f "${DRIFTER_DIR}/vivi.yaml" ]; then
    warn "vivi.yaml already exists — not overwriting (edit manually)"
else
    cp "${REPO_DIR}/config/vivi.yaml" "${DRIFTER_DIR}/"
    ok "vivi.yaml deployed"
fi

# ── 5. systemd service ──
step 5 "Installing drifter-vivi systemd service"
cp "${REPO_DIR}/services/drifter-vivi.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable drifter-vivi
ok "drifter-vivi service installed and enabled"

# ── 6. Verify Ollama model ──
step 6 "Checking Ollama llama3.2:3b model"
if command -v ollama &>/dev/null; then
    ollama list 2>/dev/null | grep -q "llama3.2:3b" && \
        ok "llama3.2:3b already present" || {
        warn "Pulling llama3.2:3b (this takes a few minutes)..."
        ollama pull llama3.2:3b 2>/dev/null && ok "llama3.2:3b ready" || \
            warn "Pull failed — run 'ollama pull llama3.2:3b' manually"
    }
else
    warn "Ollama not found — install with: curl -fsSL https://ollama.com/install.sh | sh"
fi

echo ""
echo -e "${GREEN}  Vivi installed.${NC}"
echo -e "  Start now:   ${CYAN}sudo systemctl start drifter-vivi${NC}"
echo -e "  Logs:        ${CYAN}journalctl -u drifter-vivi -f${NC}"
echo -e "  Config:      ${CYAN}${DRIFTER_DIR}/vivi.yaml${NC}"
echo ""
echo -e "  Default mode is PTT — press Enter to talk."
echo -e "  Change to 'wake_word' or 'always_on' in vivi.yaml for hands-free."
echo ""

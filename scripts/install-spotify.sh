#!/bin/bash
# ============================================
# MZ1312 DRIFTER — Spotify Bridge Installer
# UNCAGED TECHNOLOGY — EST 1991
# ============================================
# Usage: sudo ./scripts/install-spotify.sh
# ============================================

set -e

CYAN='\033[0;36m'; RED='\033[0;31m'; GREEN='\033[0;32m'; AMBER='\033[0;33m'; NC='\033[0m'
DRIFTER_DIR="/opt/drifter"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ok()   { echo -e "${GREEN}  ✓ $1${NC}"; }
warn() { echo -e "${AMBER}  ! $1${NC}"; }
fail() { echo -e "${RED}  ✗ $1${NC}"; exit 1; }
step() { echo -e "\n${AMBER}[$1] $2${NC}"; }

echo -e "${CYAN}  DRIFTER — Spotify Bridge Installer${NC}\n"
if [ "$EUID" -ne 0 ]; then fail "Run as root: sudo ./scripts/install-spotify.sh"; fi

step 1 "Installing spotipy + pyyaml"
source "${DRIFTER_DIR}/venv/bin/activate"
pip install --quiet spotipy pyyaml
ok "Python deps installed"

step 2 "Deploying spotify_bridge.py"
cp "${REPO_DIR}/src/spotify_bridge.py" "${DRIFTER_DIR}/"
chmod +x "${DRIFTER_DIR}/spotify_bridge.py"
ok "spotify_bridge.py deployed"

step 3 "Deploying spotify.yaml"
if [ -f "${DRIFTER_DIR}/spotify.yaml" ]; then
    warn "spotify.yaml already exists — not overwriting"
else
    cp "${REPO_DIR}/config/spotify.yaml" "${DRIFTER_DIR}/"
    ok "spotify.yaml deployed — edit ${DRIFTER_DIR}/spotify.yaml with your Spotify Dev creds"
fi

step 4 "(Optional) raspotify for Spotify Connect device"
if command -v raspotify &>/dev/null; then
    ok "raspotify already installed"
else
    warn "raspotify not installed. Install with:"
    echo -e "    ${CYAN}curl -sL https://dtcooper.github.io/raspotify/install.sh | sh${NC}"
fi

step 5 "Installing drifter-spotify systemd service"
cp "${REPO_DIR}/services/drifter-spotify.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable drifter-spotify
ok "drifter-spotify enabled"

echo ""
echo -e "${GREEN}  Spotify bridge installed.${NC}"
echo -e "  Edit creds:  ${CYAN}sudoedit ${DRIFTER_DIR}/spotify.yaml${NC}"
echo -e "  Start:       ${CYAN}sudo systemctl start drifter-spotify${NC}"
echo -e "  Logs:        ${CYAN}journalctl -u drifter-spotify -f${NC}"

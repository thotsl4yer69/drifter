#!/bin/bash
# ============================================
# MZ1312 DRIFTER — Vivi Discord installer
# UNCAGED TECHNOLOGY — EST 1991
# ============================================
set -e

CYAN='\033[0;36m'; GREEN='\033[0;32m'; AMBER='\033[0;33m'; RED='\033[0;31m'; NC='\033[0m'
DRIFTER_DIR="/opt/drifter"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

step() { echo -e "\n${AMBER}» $1${NC}"; }
ok()   { echo -e "${GREEN}  ✓ $1${NC}"; }
fail() { echo -e "${RED}  ✗ $1${NC}"; exit 1; }

if [ "$EUID" -ne 0 ]; then fail "Run as root: sudo $0"; fi

step "Installing discord.py"
${DRIFTER_DIR}/venv/bin/pip install --quiet 'discord.py>=2.3'
ok "discord.py installed"

step "Deploying vivi_discord.py"
cp "${REPO_DIR}/src/vivi_discord.py" "${DRIFTER_DIR}/"
chmod +x "${DRIFTER_DIR}/vivi_discord.py"
ok "vivi_discord.py deployed"

step "Deploying discord config"
if [ ! -f "${DRIFTER_DIR}/discord.yaml" ]; then
    cp "${REPO_DIR}/config/discord.yaml" "${DRIFTER_DIR}/"
    ok "discord.yaml deployed"
    echo -e "  ${AMBER}!${NC} Edit ${CYAN}/opt/drifter/discord.yaml${NC} and set bot_token + channel_ids"
else
    ok "discord.yaml already present — not overwriting"
fi

# Ensure .env exists for systemd to source DISCORD_BOT_TOKEN
touch "${DRIFTER_DIR}/.env"
chmod 600 "${DRIFTER_DIR}/.env"
if ! grep -q '^DISCORD_BOT_TOKEN=' "${DRIFTER_DIR}/.env"; then
    echo '# DISCORD_BOT_TOKEN=xxxxxx' >> "${DRIFTER_DIR}/.env"
fi

step "Installing systemd unit"
cp "${REPO_DIR}/services/drifter-discord.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable drifter-discord 2>/dev/null
ok "drifter-discord enabled"

# Don't auto-start — user must add bot token first
echo -e "\n${CYAN}Next:${NC}"
echo "  1. Create a bot at https://discord.com/developers"
echo "  2. Edit /opt/drifter/discord.yaml (bot_token + channel IDs) OR"
echo "     uncomment DISCORD_BOT_TOKEN= in /opt/drifter/.env"
echo "  3. systemctl start drifter-discord"
echo "  4. journalctl -u drifter-discord -f"

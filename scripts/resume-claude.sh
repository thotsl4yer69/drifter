#!/usr/bin/env bash
# resume-claude.sh — One-click resume for the DRIFTER Claude Code session.
#
# Opens a terminal in ~/drifter and runs `claude --resume <session>` so
# the conversation continues with full context. If the explicit session
# id is no longer cached locally, falls back to --continue (most-recent).
#
# UNCAGED TECHNOLOGY — EST 1991

SESSION_ID='887380f0-3abc-4fc2-9eac-2ebc6443934b'
PROJECT_DIR="${HOME}/drifter"

# Discover an available terminal emulator. Kali defaults to qterminal;
# alacritty and x-terminal-emulator are kept as alternates.
pick_term() {
  for t in qterminal alacritty x-terminal-emulator xterm; do
    if command -v "$t" >/dev/null 2>&1; then echo "$t"; return; fi
  done
  echo ""
}
TERM_BIN="$(pick_term)"
if [ -z "$TERM_BIN" ]; then
  notify-send "DRIFTER" "No terminal emulator found" 2>/dev/null
  exit 1
fi

# The shell-side command the terminal will run. Tries explicit resume
# first; if claude exits non-zero (session expired, etc.) falls through
# to --continue. Final exec keeps the terminal alive on completion so
# the operator can see any errors before it closes.
INNER='cd '"$PROJECT_DIR"' && \
       echo "DRIFTER · Resuming Claude Code session '"$SESSION_ID"'" && \
       (claude --resume '"$SESSION_ID"' || claude --continue) ; \
       echo "" ; echo "[session ended — press Enter to close]" ; read'

case "$TERM_BIN" in
  qterminal)            exec "$TERM_BIN" -e bash -lc "$INNER" ;;
  alacritty)            exec "$TERM_BIN" --working-directory "$PROJECT_DIR" -e bash -lc "$INNER" ;;
  x-terminal-emulator)  exec "$TERM_BIN" -e bash -lc "$INNER" ;;
  *)                    exec "$TERM_BIN" -e bash -lc "$INNER" ;;
esac

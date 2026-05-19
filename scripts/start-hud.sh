#!/usr/bin/env bash
set -u

URL="${DRIFTER_HUD_URL:-http://127.0.0.1:8080/}"
LOG="${HOME}/.local/share/drifter-hud.log"
mkdir -p "$(dirname "$LOG")"

exec >>"$LOG" 2>&1
echo "[$(date -Is)] start-hud begin url=$URL display=${DISPLAY:-unset}"

export DISPLAY="${DISPLAY:-:0.0}"
export XAUTHORITY="${XAUTHORITY:-/home/kali/.Xauthority}"

# Wait up to 90s for dashboard to answer
for i in $(seq 1 90); do
    if curl -fsS -o /dev/null --max-time 2 "${URL%/}/healthz"; then
        echo "[$(date -Is)] dashboard healthy after ${i}s"
        break
    fi
    sleep 1
done

# Wait for X server
for i in $(seq 1 30); do
    if xset -q >/dev/null 2>&1; then break; fi
    sleep 1
done

# Disable screen blanking on the in-car LCD
xset s off       2>/dev/null || true
xset -dpms       2>/dev/null || true
xset s noblank   2>/dev/null || true
command -v unclutter >/dev/null 2>&1 && (unclutter -idle 1 -root &)

# Reuse running browser if present (match only browser processes, not shells)
if pgrep -f '^/usr/.*chromium .*drifter-hud-chromium' >/dev/null; then
    echo "[$(date -Is)] chromium kiosk already running"
    exit 0
fi
if pgrep -f '^/usr/.*firefox.* --profile .*drifter-hud' >/dev/null; then
    echo "[$(date -Is)] firefox kiosk already running"
    exit 0
fi

if command -v chromium >/dev/null 2>&1; then
    PROFILE="${HOME}/.cache/drifter-hud-chromium"
    mkdir -p "$PROFILE"
    exec chromium \
        --user-data-dir="$PROFILE" \
        --kiosk \
        --noerrdialogs \
        --disable-infobars \
        --disable-session-crashed-bubble \
        --disable-translate \
        --no-first-run \
        --check-for-update-interval=31536000 \
        --overscroll-history-navigation=0 \
        --app="$URL"
fi

if command -v firefox-esr >/dev/null 2>&1 || command -v firefox >/dev/null 2>&1; then
    BIN="$(command -v firefox-esr || command -v firefox)"
    PROFILE="${HOME}/.mozilla/firefox/drifter-hud"
    mkdir -p "$PROFILE"
    exec "$BIN" --kiosk --profile "$PROFILE" --no-remote "$URL"
fi

echo "[$(date -Is)] ERROR: no browser found (chromium, firefox, firefox-esr)"
exit 1

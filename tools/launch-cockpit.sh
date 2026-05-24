#!/bin/bash
# MZ1312 DRIFTER cockpit launcher
# UNCAGED TECHNOLOGY — EST 1991
#
# Brings the cockpit forward in chromium kiosk mode on the local Pi
# display. If chromium is already showing the cockpit, just focuses
# the window. Otherwise launches fresh.

set -u
URL="http://127.0.0.1:8080/"
export DISPLAY="${DISPLAY:-:0.0}"
LOG="/tmp/drifter-cockpit-launcher.log"

# Wait briefly for the dashboard to answer — handy when launched right
# after boot before drifter-dashboard.service is fully up.
for i in 1 2 3 4 5 6 7 8 9 10; do
    if curl -fsS --max-time 2 -o /dev/null "$URL"; then break; fi
    sleep 1
done

# If an existing cockpit kiosk is running, focus it (wmctrl handles X11).
if pgrep -af "chromium.*${URL}" >/dev/null 2>&1; then
    if command -v wmctrl >/dev/null 2>&1; then
        wmctrl -a "DRIFTER" 2>/dev/null && exit 0
    fi
    # No wmctrl — exit quietly; the existing process is already on screen.
    exit 0
fi

exec chromium \
    --kiosk \
    --no-first-run \
    --noerrdialogs \
    --disable-translate \
    --disable-features=TranslateUI \
    --disable-session-crashed-bubble \
    --autoplay-policy=no-user-gesture-required \
    --user-data-dir=/tmp/drifter-cockpit-chromium \
    "$URL" >>"$LOG" 2>&1

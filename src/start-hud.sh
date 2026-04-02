#!/bin/bash
# MZ1312 DRIFTER — HUD Launcher (auto-scales to any display)
# Waits for the dashboard service, then opens Firefox in kiosk mode.

export DISPLAY=:0
URL="http://localhost:8080/screen"
MAX_WAIT=30

# Wait for dashboard HTTP to respond
for i in $(seq 1 $MAX_WAIT); do
    if curl -s -o /dev/null -w "%{http_code}" "$URL" 2>/dev/null | grep -q "200"; then
        break
    fi
    sleep 1
done

# Kill any existing Firefox instances
pkill -f firefox-esr 2>/dev/null
sleep 2

# Disable screen blanking / DPMS
xset s off 2>/dev/null
xset -dpms 2>/dev/null
xset s noblank 2>/dev/null

# Hide cursor after 3 seconds of inactivity
unclutter -idle 3 -root 2>/dev/null &

# Launch Firefox in kiosk mode (full screen, no UI chrome)
exec firefox-esr --kiosk "$URL" 2>/dev/null

#!/bin/bash
# MZ1312 DRIFTER — HUD Launcher (auto-scales to any display)
# Waits for the dashboard service, then opens Firefox in kiosk mode.

export DISPLAY=:0
URL="http://localhost:8080/screen"
MAX_WAIT=30
PROFILE_DIR="$HOME/.drifter-firefox"

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

# ── Firefox profile with GPU acceleration disabled ───────────────
# This Pi 5 has no /dev/dri/ DRM node, so Firefox's SWGL compositor
# crashes with "RenderCompositorSWGL failed mapping default framebuffer".
# Force the legacy basic compositor + software-only Mesa.
mkdir -p "$PROFILE_DIR"
cat > "$PROFILE_DIR/user.js" <<'EOF'
// Disable hardware acceleration & WebRender — no DRM device on this Pi
user_pref("gfx.webrender.all", false);
user_pref("gfx.webrender.enabled", false);
user_pref("layers.acceleration.disabled", true);
user_pref("gfx.canvas.accelerated", false);
user_pref("media.hardware-video-decoding.enabled", false);
user_pref("dom.ipc.processCount", 1);
// Kiosk niceties
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("browser.startup.homepage_override.mstone", "ignore");
user_pref("toolkit.telemetry.enabled", false);
user_pref("datareporting.healthreport.uploadEnabled", false);
user_pref("app.update.auto", false);
user_pref("app.update.enabled", false);
user_pref("browser.tabs.warnOnClose", false);
user_pref("browser.sessionstore.resume_from_crash", false);
EOF

# GPU/Mesa env vars that further reinforce the prefs
export MOZ_WEBRENDER=0
export MOZ_ACCELERATED=0
export MOZ_DISABLE_GFX_SANITY=1
export LIBGL_ALWAYS_SOFTWARE=1

# Launch Firefox in kiosk mode (full screen, no UI chrome) with custom profile
exec firefox-esr --profile "$PROFILE_DIR" --no-remote --kiosk "$URL" 2>/dev/null

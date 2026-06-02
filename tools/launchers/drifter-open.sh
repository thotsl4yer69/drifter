#!/bin/bash
# Open a DRIFTER web surface in a normal Chromium window (not kiosk), after
# waiting briefly for the dashboard to answer. Used by the OPSEC and Vivi
# desktop launchers. Falls back to xdg-open if Chromium is absent.
#   drifter-open.sh opsec   -> http://localhost:8090
#   drifter-open.sh vivi    -> http://localhost:8080/avatar
target="${1:-cockpit}"
case "$target" in
  opsec) URL="http://localhost:8090" ;;
  vivi)  URL="http://localhost:8080/avatar" ;;
  *)     URL="http://localhost:8080/" ;;
esac

# Wait up to ~10s for the endpoint (handy right after boot).
for _ in $(seq 1 10); do
  curl -fsS --max-time 2 -o /dev/null "$URL" && break
  sleep 1
done

# This Pi has no hardware GL (/dev/dri absent — vc4-kms-v3d disabled), so
# Mesa falls back to llvmpipe which Chromium can't use for WebGL. Force
# Chromium's bundled SwiftShader software GL so the Vivi 3D viewer (three.js)
# gets a working WebGL context. Harmless for the non-WebGL opsec view.
GL_FLAGS=(--ignore-gpu-blocklist --enable-unsafe-swiftshader
          --use-gl=angle --use-angle=swiftshader)
if command -v chromium >/dev/null 2>&1; then
  exec chromium --new-window --no-first-run --noerrdialogs "${GL_FLAGS[@]}" \
    --user-data-dir=/tmp/drifter-chromium-views "$URL" >/dev/null 2>&1
elif command -v chromium-browser >/dev/null 2>&1; then
  exec chromium-browser --new-window "${GL_FLAGS[@]}" "$URL" >/dev/null 2>&1
else
  exec xdg-open "$URL"
fi

#!/bin/bash
# Generate branded DRIFTER launcher icons (stencil/CRT amber-on-dark) as SVGs.
# One consistent tile per launcher: dark glass square, accent border, a short
# label, MZ1312 / DRIFTER wordmarks. librsvg (XFCE/Tumbler) renders these fine.
set -eu
OUT="$(cd "$(dirname "$0")/../../assets/icons" 2>/dev/null && pwd || true)"
[ -z "$OUT" ] && OUT="$(dirname "$0")/../../assets/icons" && mkdir -p "$OUT"

# key|label|accent(hex)
ICONS="
cockpit|HUD|#ffae42
vivi|VIVI|#5eead4
opsec|OPS|#ff5151
diagnose|DIAG|#ffae42
status|STAT|#7dd3fc
health|HLTH|#5eead4
logs|LOG|#9aa3b1
mqtt|MQTT|#7dd3fc
restart|RST|#ff5151
"

emit() {
  local key="$1" label="$2" accent="$3"
  cat > "$OUT/drifter-$key.svg" <<SVG
<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#11161f"/>
      <stop offset="1" stop-color="#07090d"/>
    </linearGradient>
  </defs>
  <rect x="8" y="8" width="240" height="240" rx="34" fill="url(#bg)" stroke="${accent}" stroke-width="3" stroke-opacity="0.85"/>
  <rect x="22" y="22" width="212" height="212" rx="24" fill="none" stroke="${accent}" stroke-width="1" stroke-opacity="0.22"/>
  <text x="128" y="58" font-family="monospace" font-size="20" letter-spacing="3"
        text-anchor="middle" fill="${accent}" fill-opacity="0.75">MZ1312</text>
  <text x="128" y="150" font-family="sans-serif" font-weight="700" font-size="62"
        letter-spacing="1" text-anchor="middle" fill="${accent}">${label}</text>
  <text x="128" y="206" font-family="monospace" font-size="22" letter-spacing="6"
        text-anchor="middle" fill="#e8eaed" fill-opacity="0.9">DRIFTER</text>
</svg>
SVG
}

while IFS='|' read -r key label accent; do
  [ -z "$key" ] && continue
  emit "$key" "$label" "$accent"
  echo "  icon: drifter-$key.svg ($label)"
done <<< "$ICONS"
echo "icons written to $OUT"

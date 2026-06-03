#!/bin/bash
# Install the DRIFTER desktop launchers + icons for the invoking user.
#  - icons + wrapper scripts -> /opt/drifter/{icons,bin}   (needs sudo)
#  - .desktop entries        -> ~/.local/share/applications (app menu)
#                            -> ~/Desktop                   (desktop icons)
# Re-runnable. Run as the desktop user (e.g. kali), NOT as root, so the
# Desktop files land in the right home and are owned correctly.
set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
LDIR="$HERE/launchers"
DRIFTER_DIR="/opt/drifter"
APPS="$HOME/.local/share/applications"
DESKTOP="${XDG_DESKTOP_DIR:-$HOME/Desktop}"
ICONS_SRC="$REPO/assets/icons"
ICONS_DST="$DRIFTER_DIR/icons"
BIN_DST="$DRIFTER_DIR/bin"

if [ "$(id -u)" = 0 ]; then
  echo "Run as your desktop user (e.g. kali), not root — it installs to \$HOME." >&2
  exit 2
fi

echo "▶ generating icons"
bash "$LDIR/gen-icons.sh"

echo "▶ installing icons + wrappers to $DRIFTER_DIR (sudo)"
sudo mkdir -p "$ICONS_DST" "$BIN_DST"
sudo cp "$ICONS_SRC"/drifter-*.svg "$ICONS_DST/"
sudo cp "$LDIR/drifter-term.sh" "$LDIR/drifter-open.sh" "$BIN_DST/"
sudo chmod 0755 "$BIN_DST/drifter-term.sh" "$BIN_DST/drifter-open.sh"
# launch-cockpit.sh is deployed by install.sh; ensure it's present for the icon.
[ -f "$BIN_DST/launch-cockpit.sh" ] || sudo cp "$REPO/tools/launch-cockpit.sh" "$BIN_DST/" 2>/dev/null || true

mkdir -p "$APPS" "$DESKTOP"

# key | Name | Comment | Terminal(true/false) | Exec
LAUNCHERS="
cockpit|DRIFTER Cockpit|Vehicle HUD — hero gauges, RF intel, Vivi (kiosk)|false|${BIN_DST}/launch-cockpit.sh
opsec|DRIFTER OPSEC Console|Foot-mode Kali console on :8090 (terminal, tools, killswitch)|false|${BIN_DST}/drifter-open.sh opsec
diagnose|DRIFTER Diagnose|Full fleet-contract hardware + service probe|true|${BIN_DST}/drifter-term.sh diagnose
status|DRIFTER Service Status|One line per drifter service|true|${BIN_DST}/drifter-term.sh status
health|DRIFTER Health|Probe /healthz and pretty-print|true|${BIN_DST}/drifter-term.sh health
logs|DRIFTER Logs|Follow the dashboard service journal|true|${BIN_DST}/drifter-term.sh logs
mqtt|DRIFTER MQTT Monitor|Live drifter/# telemetry firehose|true|${BIN_DST}/drifter-term.sh mqtt
restart|DRIFTER Restart Services|Restart ALL drifter services (asks to confirm)|true|${BIN_DST}/drifter-term.sh restart
"

write_desktop() {
  local key="$1" name="$2" comment="$3" term="$4" exec="$5" dest="$6"
  cat > "$dest" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=$name
Comment=$comment
Exec=$exec
Icon=$ICONS_DST/drifter-$key.svg
Terminal=$term
StartupNotify=true
Categories=Utility;
Keywords=drifter;mz1312;$key;
EOF
  chmod +x "$dest"
  # XFCE: mark trusted so it launches without the "untrusted" prompt.
  gio set "$dest" metadata::trusted true 2>/dev/null || true
}

echo "▶ writing launchers"
while IFS='|' read -r key name comment term exec; do
  [ -z "$key" ] && continue
  write_desktop "$key" "$name" "$comment" "$term" "$exec" "$APPS/drifter-$key.desktop"
  write_desktop "$key" "$name" "$comment" "$term" "$exec" "$DESKTOP/drifter-$key.desktop"
  echo "  ✓ $name"
done <<< "$LAUNCHERS"

update-desktop-database "$APPS" 2>/dev/null || true
echo "✔ DRIFTER launchers installed to the app menu and $DESKTOP"

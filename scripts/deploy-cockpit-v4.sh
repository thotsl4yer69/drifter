#!/usr/bin/env bash
# Build and deploy the React/Vite cockpit used by drifter-dashboard.
# New builds are staged under /opt/drifter/ui/releases and activated through
# an atomic symlink swap at /opt/drifter/ui/v4.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SRC_DIR="${REPO_DIR}/cockpit-v4"
UI_ROOT="/opt/drifter/ui"
RELEASES_DIR="${UI_ROOT}/releases"
DST_DIR="${UI_ROOT}/v4"

if [ "$EUID" -ne 0 ]; then
    echo "Run with sudo: sudo $0" >&2
    exit 2
fi

if [ ! -f "${SRC_DIR}/package.json" ] || [ ! -f "${SRC_DIR}/package-lock.json" ]; then
    echo "cockpit-v4 source is missing" >&2
    exit 2
fi

if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
    echo "[cockpit] node/npm missing — installing"
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nodejs npm
fi

echo "[cockpit] node $(node --version) · npm $(npm --version)"
cd "$SRC_DIR"
npm ci --no-audit --no-fund
npm run build

[ -f "${SRC_DIR}/dist/index.html" ] || {
    echo "[cockpit] build completed without dist/index.html" >&2
    exit 3
}

mkdir -p "$RELEASES_DIR"
REV="$(git -C "$REPO_DIR" rev-parse --short=12 HEAD 2>/dev/null || echo unknown)"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RELEASE_NAME="v4-${STAMP}-${REV}"
STAGE_DIR="${RELEASES_DIR}/.${RELEASE_NAME}.tmp"
RELEASE_DIR="${RELEASES_DIR}/${RELEASE_NAME}"
NEXT_LINK="${UI_ROOT}/.v4-next.$$"
LEGACY_BACKUP=""

cleanup() {
    rm -rf "$STAGE_DIR" 2>/dev/null || true
    rm -f "$NEXT_LINK" 2>/dev/null || true
}
trap cleanup EXIT

rm -rf "$STAGE_DIR"
mkdir -p "$STAGE_DIR"
rsync -a --delete "${SRC_DIR}/dist/" "$STAGE_DIR/"

if getent passwd drifter >/dev/null 2>&1; then
    chown -R drifter:drifter "$STAGE_DIR"
fi
find "$STAGE_DIR" -type d -exec chmod 0755 {} +
find "$STAGE_DIR" -type f -exec chmod 0644 {} +

[ -f "${STAGE_DIR}/index.html" ] || {
    echo "[cockpit] staged release missing index.html" >&2
    exit 3
}

mv "$STAGE_DIR" "$RELEASE_DIR"
ln -s "releases/${RELEASE_NAME}" "$NEXT_LINK"

# One-time migration from the historical live directory to release+symlink.
# If activation fails, restore that directory exactly.
if [ -e "$DST_DIR" ] && [ ! -L "$DST_DIR" ]; then
    LEGACY_BACKUP="${RELEASES_DIR}/legacy-v4-${STAMP}"
    mv "$DST_DIR" "$LEGACY_BACKUP"
fi

if ! mv -Tf "$NEXT_LINK" "$DST_DIR"; then
    rm -f "$NEXT_LINK"
    if [ -n "$LEGACY_BACKUP" ] && [ ! -e "$DST_DIR" ]; then
        mv "$LEGACY_BACKUP" "$DST_DIR"
    fi
    echo "[cockpit] activation failed; previous cockpit restored" >&2
    exit 4
fi

trap - EXIT
FILES="$(find -L "$DST_DIR" -type f | wc -l)"
echo "[cockpit] activated ${RELEASE_NAME} (${FILES} files) at $DST_DIR"

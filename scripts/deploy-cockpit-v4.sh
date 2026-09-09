#!/usr/bin/env bash
# Build and deploy the React/Vite cockpit used by drifter-dashboard.
# Installs to /opt/drifter/ui/v4, which web_dashboard_handlers.py serves
# preferentially over the legacy ui/cockpit-preview.html fallback.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SRC_DIR="${REPO_DIR}/cockpit-v4"
DST_DIR="/opt/drifter/ui/v4"

if [ "$EUID" -ne 0 ]; then
    echo "Run with sudo: sudo $0" >&2
    exit 2
fi

if [ ! -f "${SRC_DIR}/package.json" ] || [ ! -f "${SRC_DIR}/package-lock.json" ]; then
    echo "cockpit-v4 source is missing" >&2
    exit 2
fi

# First deploy may predate the cockpit-v4 toolchain. Do not require a full
# apt upgrade just to refresh the UI; install node/npm only when absent.
if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
    echo "[cockpit] node/npm missing — installing"
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nodejs npm
fi

echo "[cockpit] node $(node --version) · npm $(npm --version)"
cd "$SRC_DIR"

# npm ci is deterministic against package-lock.json and replaces stale
# node_modules from earlier builds. Vite output is fully offline-capable.
npm ci --no-audit --no-fund
npm run build

[ -f "${SRC_DIR}/dist/index.html" ] || {
    echo "[cockpit] build completed without dist/index.html" >&2
    exit 3
}

mkdir -p "$DST_DIR"
rsync -a --delete "${SRC_DIR}/dist/" "$DST_DIR/"

# Dashboard normally runs as the unprivileged drifter user. Static assets only
# need read access, but keep ownership consistent with /opt/drifter.
if getent passwd drifter >/dev/null 2>&1; then
    chown -R drifter:drifter "$DST_DIR"
fi
find "$DST_DIR" -type d -exec chmod 0755 {} +
find "$DST_DIR" -type f -exec chmod 0644 {} +

echo "[cockpit] deployed $(find "$DST_DIR" -type f | wc -l) files to $DST_DIR"

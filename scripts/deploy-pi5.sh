#!/usr/bin/env bash
# ============================================================
# MZ1312 DRIFTER — Interactive Pi 5 Deploy
# UNCAGED TECHNOLOGY — EST 1991
# ============================================================
# Operator-driven 9-phase deploy for the Raspberry Pi 5 node. Unlike
# oneshot.sh (unattended, fleet-orchestrated), this walks the operator
# through each phase with a confirm prompt and a clear PASS/FAIL line.
#
#   phase 1 — preflight        repo + root + platform sanity
#   phase 2 — system deps       apt + venv (delegated to install.sh)
#   phase 3 — deploy code       /opt/drifter sync (install.sh already ran)
#   phase 4 — CAN bring-up      slcan/native CAN interface
#   phase 5 — diagnose          drifter diagnose pre-flight
#   phase 6 — enable services   systemctl enable + start (SERVICES)
#   phase 7 — smoke             post-deploy-check.sh
#   phase 8 — health probe      curl /healthz
#   phase 9 — summary           git rev + service tally
#
# Usage:
#   sudo ./scripts/deploy-pi5.sh            # interactive
#   sudo ./scripts/deploy-pi5.sh --yes      # assume-yes (non-interactive)
#   sudo ./scripts/deploy-pi5.sh --skip-apt # skip phase 2
# ============================================================

set -eo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DRIFTER_DIR="/opt/drifter"
DASHBOARD_HOST="127.0.0.1"
DASHBOARD_PORT="8080"

ASSUME_YES=0
SKIP_APT=0
for arg in "$@"; do
    case "$arg" in
        --yes|-y)   ASSUME_YES=1 ;;
        --skip-apt) SKIP_APT=1 ;;
        -h|--help)  sed -n '2,33p' "$0"; exit 0 ;;
        *) echo "unknown arg: $arg" >&2; exit 2 ;;
    esac
done

if [ -t 1 ]; then
    CYAN='\033[0;36m'; RED='\033[0;31m'; GREEN='\033[0;32m'; AMBER='\033[0;33m'; NC='\033[0m'
else
    CYAN=''; RED=''; GREEN=''; AMBER=''; NC=''
fi

phase()  { printf "\n${CYAN}━━ PHASE %s/9 — %s ━━${NC}\n" "$1" "$2"; }
ok()     { printf "  ${GREEN}✓${NC} %s\n" "$1"; }
warn()   { printf "  ${AMBER}!${NC} %s\n" "$1"; }
fail()   { printf "  ${RED}✗ %s${NC}\n" "$1"; exit 1; }

confirm() {
    # confirm "message" — returns 0 to proceed, 1 to skip the phase
    [ "$ASSUME_YES" -eq 1 ] && return 0
    printf "  ${AMBER}? %s [Y/n] ${NC}" "$1"
    read -r reply </dev/tty || reply="y"
    case "$reply" in
        n|N|no|NO) return 1 ;;
        *) return 0 ;;
    esac
}

echo -e "${CYAN}  DRIFTER — Interactive Pi 5 Deploy${NC}"

# ── PHASE 1 — preflight ──────────────────────────────────────
phase 1 "preflight"
[ "$EUID" -ne 0 ] && fail "run with sudo: sudo $0 $*"
[ -d "$REPO_DIR/src" ] || fail "$REPO_DIR is not the drifter repo (no src/)"
[ -f "$REPO_DIR/install.sh" ] || fail "install.sh missing"
MODEL="$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo unknown)"
ok "repo: $REPO_DIR"
ok "model: $MODEL"
cd "$REPO_DIR"

# ── PHASE 2 — system deps ────────────────────────────────────
phase 2 "system deps (apt + venv)"
if [ "$SKIP_APT" -eq 1 ]; then
    warn "--skip-apt: trusting existing venv at $DRIFTER_DIR/venv"
elif confirm "run install.sh (apt + venv + deploy)?"; then
    bash "$REPO_DIR/install.sh" || fail "install.sh exited non-zero"
    ok "install.sh complete"
else
    warn "skipped install.sh"
fi
[ -x "$DRIFTER_DIR/venv/bin/python3" ] || fail "venv missing at $DRIFTER_DIR/venv"
ok "venv present"

# ── PHASE 3 — deploy code ────────────────────────────────────
phase 3 "deploy code sync"
# install.sh already copies SRC_FILES; this phase re-syncs the new RDK X5 /
# ghost modules in case install.sh was skipped.
for f in hardware.py can_native.py ghost_protocol.py; do
    if [ -f "${REPO_DIR}/src/${f}" ]; then
        cp "${REPO_DIR}/src/${f}" "${DRIFTER_DIR}/" && chmod +x "${DRIFTER_DIR}/${f}"
        ok "synced ${f}"
    fi
done

# ── PHASE 4 — CAN bring-up ───────────────────────────────────
phase 4 "CAN interface bring-up"
if ip link show can0 >/dev/null 2>&1; then
    if confirm "configure native can0 via setup-can-fd.sh?"; then
        bash "$REPO_DIR/scripts/setup-can-fd.sh" can0 "${CAN_BITRATE:-500000}" || warn "CAN FD setup failed"
    fi
else
    warn "can0 not present — relying on can_bridge.py slcan auto-detect (USB2CANFD)"
fi

# ── PHASE 5 — diagnose ───────────────────────────────────────
phase 5 "diagnose pre-flight"
if [ -x /usr/local/bin/drifter ]; then
    /usr/local/bin/drifter diagnose || warn "diagnose reported warnings (services may not be up yet)"
else
    warn "drifter CLI not installed — skipping diagnose"
fi

# ── PHASE 6 — enable services ────────────────────────────────
phase 6 "enable + start services"
SERVICES=(
    drifter-canbridge drifter-alerts drifter-logger drifter-anomaly
    drifter-analyst drifter-voice drifter-vivi drifter-hotspot drifter-homesync
    drifter-watchdog drifter-realdash drifter-rf drifter-wardrive
    drifter-dashboard drifter-fbmirror drifter-voicein drifter-flipper
    drifter-opsec
)
if confirm "enable + restart ${#SERVICES[@]} services now?"; then
    systemctl daemon-reload
    for svc in "${SERVICES[@]}"; do
        systemctl enable "$svc" >/dev/null 2>&1 || warn "enable $svc failed"
        systemctl restart "$svc" 2>/dev/null && ok "$svc" || warn "$svc did not start (hardware pending?)"
    done
else
    warn "skipped service enable"
fi

# ── PHASE 7 — smoke ──────────────────────────────────────────
phase 7 "post-deploy smoke"
if [ -x "$REPO_DIR/scripts/post-deploy-check.sh" ]; then
    bash "$REPO_DIR/scripts/post-deploy-check.sh" || warn "post-deploy-check reported issues"
else
    warn "post-deploy-check.sh not found"
fi

# ── PHASE 8 — health probe ───────────────────────────────────
phase 8 "health probe /healthz"
HEALTHZ_OK=0
for i in $(seq 1 30); do
    if body="$(curl -fsS -m 2 "http://${DASHBOARD_HOST}:${DASHBOARD_PORT}/healthz" 2>/dev/null)"; then
        echo "  $body"
        HEALTHZ_OK=1
        break
    fi
    sleep 1
done
[ "$HEALTHZ_OK" -eq 1 ] && ok "/healthz reachable" || warn "/healthz not reachable within 30s"

# ── PHASE 9 — summary ────────────────────────────────────────
phase 9 "summary"
REV="$(git -C "$REPO_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)"
BRANCH="$(git -C "$REPO_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
ACTIVE="$(systemctl list-units 'drifter-*' --state=active --no-legend 2>/dev/null | wc -l)"
ok "git ${BRANCH}@${REV}"
ok "${ACTIVE} drifter-* units active"
if [ "$HEALTHZ_OK" -eq 1 ]; then
    printf "${GREEN}DEPLOY: ok${NC}\n"
    exit 0
fi
printf "${AMBER}DEPLOY: partial — review warnings above${NC}\n"
exit 0

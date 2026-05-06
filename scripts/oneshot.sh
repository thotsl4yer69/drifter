#!/usr/bin/env bash
# ============================================================
# MZ1312 DRIFTER — Fleet-Contract One-Shot Deploy
# UNCAGED TECHNOLOGY — EST 1991
# ============================================================
# Wraps install.sh with the stage gates the fleet DEPLOY_CONTRACT
# expects:
#
#   stage 10 — apt + venv      (idempotent system bring-up)
#   stage 20 — diagnose         (drifter diagnose pre-flight)
#   stage 30 — smoke            (post-deploy verification)
#   stage 40 — enable services  (systemctl enable + start)
#   final    — curl /healthz    (contract health probe)
#
# Each stage emits "STAGE <n> START / OK / FAIL" lines so the
# fleet `mesh deploy drifter` orchestrator can grep progress.
#
# Exit codes:
#   0   — all stages green, /healthz returned 200
#   10  — stage 10 (apt/venv) failed
#   20  — stage 20 (diagnose) failed (only fatal in --strict mode)
#   30  — stage 30 (smoke) failed
#   40  — stage 40 (enable) failed
#   50  — /healthz returned non-200
#
# Usage:
#   sudo ./scripts/oneshot.sh              # full deploy
#   sudo ./scripts/oneshot.sh --skip-apt   # skip stage 10 (re-run)
#   sudo ./scripts/oneshot.sh --strict     # fail on any diagnose warning
# ============================================================

set -eo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DRIFTER_DIR="/opt/drifter"
DASHBOARD_HOST="127.0.0.1"
DASHBOARD_PORT="8080"

SKIP_APT=0
STRICT=0
for arg in "$@"; do
    case "$arg" in
        --skip-apt) SKIP_APT=1 ;;
        --strict)   STRICT=1 ;;
        -h|--help)
            sed -n '2,30p' "$0"; exit 0 ;;
        *)
            echo "unknown arg: $arg" >&2; exit 2 ;;
    esac
done

# ── Logging helpers ──────────────────────────────────────────
if [ -t 1 ]; then
    CYAN='\033[0;36m'; RED='\033[0;31m'; GREEN='\033[0;32m'
    AMBER='\033[0;33m'; NC='\033[0m'
else
    CYAN=''; RED=''; GREEN=''; AMBER=''; NC=''
fi

stage_start() { printf "${CYAN}STAGE %s START — %s${NC}\n" "$1" "$2"; }
stage_ok()    { printf "${GREEN}STAGE %s OK${NC}\n\n" "$1"; }
stage_fail()  { printf "${RED}STAGE %s FAIL — %s${NC}\n" "$1" "$2"; exit "$1"; }
note()        { printf "  ${AMBER}!${NC} %s\n" "$1"; }
ok()          { printf "  ${GREEN}✓${NC} %s\n" "$1"; }

# ── Preflight ────────────────────────────────────────────────
if [ "$EUID" -ne 0 ]; then
    echo "Run with sudo: sudo $0 $*" >&2
    exit 2
fi
if [ ! -d "$REPO_DIR/src" ] || [ ! -f "$REPO_DIR/install.sh" ]; then
    echo "$REPO_DIR doesn't look like the drifter repo" >&2
    exit 2
fi

cd "$REPO_DIR"

# ════════════════════════════════════════════════════════════════
# STAGE 10 — apt + venv
# ════════════════════════════════════════════════════════════════
stage_start 10 "apt + venv (delegated to install.sh)"
if [ "$SKIP_APT" -eq 1 ]; then
    note "--skip-apt: trusting existing /opt/drifter install"
else
    if ! bash "$REPO_DIR/install.sh"; then
        stage_fail 10 "install.sh exited non-zero"
    fi
fi
# Sanity-check the install.sh outputs the rest of the contract relies on.
[ -x "$DRIFTER_DIR/venv/bin/python3" ] || stage_fail 10 "venv missing at $DRIFTER_DIR/venv"
[ -d "$DRIFTER_DIR" ]                  || stage_fail 10 "$DRIFTER_DIR missing"
ok "venv at $DRIFTER_DIR/venv"
stage_ok 10

# Belt-and-braces: install.sh already deploys diagnose.py and the
# /usr/local/bin/drifter wrapper, but we re-install here in case
# someone bypassed install.sh with --skip-apt + a stale install dir.
if [ ! -f "$DRIFTER_DIR/diagnose.py" ] || [ ! -x /usr/local/bin/drifter ]; then
    install -m 0755 "$REPO_DIR/src/diagnose.py"  "$DRIFTER_DIR/diagnose.py"
    install -m 0755 "$REPO_DIR/bin/drifter"      /usr/local/bin/drifter
    ok "drifter CLI re-installed (was missing)"
fi

# ════════════════════════════════════════════════════════════════
# STAGE 20 — diagnose (pre-flight)
# ════════════════════════════════════════════════════════════════
stage_start 20 "drifter diagnose"
DIAG_OUT="$(mktemp)"
trap 'rm -f "$DIAG_OUT"' EXIT
DIAG_RC=0
/usr/local/bin/drifter diagnose --json > "$DIAG_OUT" 2>&1 || DIAG_RC=$?
# Show pretty version too — operators read this on screen.
/usr/local/bin/drifter diagnose || true
if [ "$DIAG_RC" -ne 0 ]; then
    if [ "$STRICT" -eq 1 ]; then
        stage_fail 20 "diagnose reported failures (rc=$DIAG_RC, --strict)"
    fi
    note "diagnose returned rc=$DIAG_RC — continuing (services may not be running yet)"
fi
stage_ok 20

# ════════════════════════════════════════════════════════════════
# STAGE 30 — smoke (post-deploy verification)
# ════════════════════════════════════════════════════════════════
stage_start 30 "post-deploy smoke"
if [ -x "$REPO_DIR/scripts/post-deploy-check.sh" ]; then
    if ! bash "$REPO_DIR/scripts/post-deploy-check.sh"; then
        stage_fail 30 "post-deploy-check.sh failed"
    fi
else
    note "post-deploy-check.sh not found — skipping (was install.sh modified?)"
fi
stage_ok 30

# ════════════════════════════════════════════════════════════════
# STAGE 40 — enable services
# ════════════════════════════════════════════════════════════════
stage_start 40 "systemctl enable + start"
SERVICES=(
    drifter-canbridge drifter-alerts drifter-logger drifter-anomaly
    drifter-analyst drifter-voice drifter-vivi drifter-hotspot drifter-homesync
    drifter-watchdog drifter-realdash drifter-rf drifter-wardrive
    drifter-dashboard drifter-fbmirror drifter-voicein drifter-flipper
)
systemctl daemon-reload
for svc in "${SERVICES[@]}"; do
    if ! systemctl enable "$svc" >/dev/null 2>&1; then
        stage_fail 40 "systemctl enable $svc failed"
    fi
    # Use restart so re-runs of oneshot.sh pick up new code without
    # leaving a stale process around.
    if ! systemctl restart "$svc"; then
        stage_fail 40 "systemctl restart $svc failed"
    fi
    ok "$svc"
done
stage_ok 40

# ════════════════════════════════════════════════════════════════
# Final — curl /healthz
# ════════════════════════════════════════════════════════════════
stage_start FINAL "curl http://${DASHBOARD_HOST}:${DASHBOARD_PORT}/healthz"
# Dashboard takes a moment to bind 8080 after restart. Poll for ~30s.
HEALTHZ_OK=0
HEALTHZ_BODY=""
for i in $(seq 1 30); do
    if curl_out="$(curl -fsS -m 2 "http://${DASHBOARD_HOST}:${DASHBOARD_PORT}/healthz" 2>/dev/null)"; then
        HEALTHZ_BODY="$curl_out"
        HEALTHZ_OK=1
        break
    fi
    sleep 1
done
if [ "$HEALTHZ_OK" -ne 1 ]; then
    stage_fail 50 "/healthz did not return 200 within 30s"
fi
echo "$HEALTHZ_BODY"
stage_ok FINAL

printf "${GREEN}DEPLOY: ok${NC}\n"
exit 0

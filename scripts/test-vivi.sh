#!/bin/bash
# ============================================
# MZ1312 DRIFTER — Vivi Voice Assistant Test Script
# UNCAGED TECHNOLOGY — EST 1991
# ============================================
# Usage: ./scripts/test-vivi.sh [--mqtt]
# ============================================

set -e

CYAN='\033[0;36m'
RED='\033[0;31m'
GREEN='\033[0;32m'
AMBER='\033[0;33m'
NC='\033[0m'

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DRIFTER_DIR="/opt/drifter"
VENV_PYTHON="${DRIFTER_DIR}/venv/bin/python3"
TEST_MQTT=false

ok()   { echo -e "${GREEN}  ✓ $1${NC}"; }
warn() { echo -e "${AMBER}  ! $1${NC}"; }
fail() { echo -e "${RED}  ✗ $1${NC}"; exit 1; }
step() { echo -e "\n${AMBER}[TEST] $1${NC}"; }

[ "$1" = "--mqtt" ] && TEST_MQTT=true

echo -e "${CYAN}  DRIFTER — Vivi Test Suite${NC}"
echo -e "${CYAN}  MZ1312 UNCAGED TECHNOLOGY — EST 1991${NC}\n"

# ── 1. Syntax check ──
step "Python syntax check"
python3 -m py_compile "${REPO_DIR}/src/vivi.py" && ok "vivi.py syntax OK" || \
    fail "vivi.py syntax error"

# ── 2. Unit tests ──
step "Running pytest"
if command -v pytest &>/dev/null; then
    pytest "${REPO_DIR}/tests/test_vivi.py" -v
    ok "Unit tests passed"
elif [ -f "${VENV_PYTHON}" ]; then
    "${VENV_PYTHON}" -m pytest "${REPO_DIR}/tests/test_vivi.py" -v
    ok "Unit tests passed"
else
    warn "pytest not found — skipping unit tests"
fi

# ── 3. Ollama check ──
step "Checking Ollama availability"
if command -v ollama &>/dev/null; then
    if curl -s --max-time 3 "http://localhost:11434/api/tags" &>/dev/null; then
        ok "Ollama is running"
        if ollama list 2>/dev/null | grep -q "llama3.2:3b"; then
            ok "llama3.2:3b model present"
        else
            warn "llama3.2:3b not found — run: ollama pull llama3.2:3b"
        fi
    else
        warn "Ollama not running — start with: ollama serve"
    fi
else
    warn "Ollama not installed"
fi

# ── 4. Piper TTS check ──
step "Checking Piper TTS"
if command -v piper &>/dev/null; then
    ok "piper binary found"
else
    warn "piper not found — TTS will be unavailable"
fi

# ── 5. Microphone check ──
step "Checking audio input"
if command -v arecord &>/dev/null; then
    if arecord -l 2>/dev/null | grep -q "card"; then
        ok "Microphone detected"
    else
        warn "No microphone found — voice input unavailable"
    fi
else
    warn "arecord not found — cannot check microphone"
fi

# ── 6. MQTT publish/subscribe test ──
if $TEST_MQTT; then
    step "MQTT round-trip test (drifter/vivi/query → drifter/vivi/response)"
    if command -v mosquitto_pub &>/dev/null && command -v mosquitto_sub &>/dev/null; then
        TEST_PAYLOAD='{"query": "what oil does the X-Type use"}'
        # Subscribe in background
        mosquitto_sub -h localhost -t 'drifter/vivi/response' -C 1 -W 15 &
        SUB_PID=$!
        sleep 1
        mosquitto_pub -h localhost -t 'drifter/vivi/query' -m "$TEST_PAYLOAD"
        wait $SUB_PID && ok "MQTT round-trip OK" || warn "MQTT test timed out (is drifter-vivi running?)"
    else
        warn "mosquitto_pub/sub not found — skipping MQTT test"
    fi
fi

echo ""
echo -e "${GREEN}  Vivi test suite complete.${NC}"
echo -e "  Run with --mqtt flag to test live MQTT round-trip."
echo ""

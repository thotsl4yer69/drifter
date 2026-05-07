#!/bin/bash
# ============================================
# MZ1312 DRIFTER — Post-Deploy Verification
# UNCAGED TECHNOLOGY — EST 1991
# ============================================
# Usage: sudo ./scripts/post-deploy-check.sh
# Run after deploy.ps1 + reboot to verify everything is healthy.
# ============================================

# Strict mode. Not `-e` on purpose — this script *expects* individual
# checks to fail and continues; it tallies PASS/FAIL at the end.
set -o pipefail

CYAN='\033[0;36m'
RED='\033[0;31m'
GREEN='\033[0;32m'
AMBER='\033[0;33m'
NC='\033[0m'

PASS=0
FAIL=0
WARN=0

ok()   { echo -e "${GREEN}  ✓ $1${NC}"; ((PASS++)); }
fail() { echo -e "${RED}  ✗ $1${NC}"; ((FAIL++)); }
warn() { echo -e "${AMBER}  ! $1${NC}"; ((WARN++)); }

echo -e "${CYAN}"
echo "  DRIFTER POST-DEPLOY CHECK"
echo "  MZ1312 UNCAGED TECHNOLOGY"
echo -e "${NC}"

# ── 1. Python venv ──
echo -e "\n${AMBER}[1/8] Python Environment${NC}"
if [ -f /opt/drifter/venv/bin/python3 ]; then
    ok "Python venv exists at /opt/drifter/venv"
    PYVER=$(/opt/drifter/venv/bin/python3 --version 2>&1)
    ok "Python version: $PYVER"
else
    fail "Python venv NOT found at /opt/drifter/venv"
fi

# Check critical Python deps
for pkg in can paho.mqtt psutil websockets requests numpy; do
    if /opt/drifter/venv/bin/python3 -c "import $pkg" 2>/dev/null; then
        ok "Python package: $pkg"
    else
        fail "Missing Python package: $pkg"
    fi
done

# ── 2. Source files deployed ──
echo -e "\n${AMBER}[2/8] Deployed Files${NC}"
SRC_FILES="can_bridge.py alert_engine.py logger.py voice_alerts.py home_sync.py status.py config.py calibrate.py watchdog.py realdash_bridge.py rf_monitor.py wardrive.py web_dashboard.py mechanic.py llm_mechanic.py anomaly_monitor.py session_analyst.py db.py llm_client.py voice_input.py tool_executor.py field_ops_kb.py diagnose.py vivi.py flipper_bridge.py"
MISSING=0
for f in $SRC_FILES; do
    if [ ! -f "/opt/drifter/$f" ]; then
        fail "Missing: /opt/drifter/$f"
        ((MISSING++))
    fi
done
if [ $MISSING -eq 0 ]; then
    ok "All 21 Python modules deployed"
fi

if [ -x /usr/local/bin/drifter ]; then
    ok "drifter CLI installed at /usr/local/bin/drifter"
else
    fail "drifter CLI missing at /usr/local/bin/drifter (run install.sh)"
fi

if [ -f /opt/drifter/knowledge_base.json ]; then
    ok "Knowledge base deployed"
else
    fail "Missing: knowledge_base.json"
fi

if [ -f /opt/drifter/screen_dash.html ]; then
    ok "Screen HUD deployed"
else
    warn "Missing: screen_dash.html (optional)"
fi

# ── 3. MQTT broker ──
echo -e "\n${AMBER}[3/8] MQTT Broker${NC}"
if systemctl is-active --quiet nanomq 2>/dev/null; then
    ok "NanoMQ is running"
elif systemctl is-active --quiet mosquitto 2>/dev/null; then
    ok "Mosquitto is running (fallback broker)"
else
    fail "No MQTT broker running (neither nanomq nor mosquitto)"
fi

# Test MQTT connectivity
if command -v mosquitto_pub &>/dev/null; then
    if mosquitto_pub -h localhost -t "drifter/test/ping" -m "check" -q 0 2>/dev/null; then
        ok "MQTT publish test succeeded"
    else
        fail "MQTT publish test failed — broker may not be accepting connections"
    fi
else
    warn "mosquitto_pub not found — cannot test MQTT connectivity"
fi

# ── 4. systemd services ──
echo -e "\n${AMBER}[4/8] systemd Services${NC}"
SERVICES="drifter-canbridge drifter-alerts drifter-dashboard drifter-logger drifter-voice drifter-vivi drifter-hotspot drifter-homesync drifter-watchdog drifter-realdash drifter-rf drifter-wardrive drifter-fbmirror drifter-anomaly drifter-analyst drifter-voicein drifter-flipper drifter-opsec"
for svc in $SERVICES; do
    if systemctl is-enabled --quiet "$svc" 2>/dev/null; then
        if systemctl is-active --quiet "$svc" 2>/dev/null; then
            ok "$svc: enabled + running"
        else
            STATUS=$(systemctl is-active "$svc" 2>/dev/null)
            warn "$svc: enabled but $STATUS"
        fi
    else
        fail "$svc: not enabled"
    fi
done

# Confirm drifter-llm is disabled (superseded)
if systemctl is-enabled --quiet drifter-llm 2>/dev/null; then
    warn "drifter-llm is still enabled (should be disabled — superseded by drifter-analyst)"
else
    ok "drifter-llm correctly disabled"
fi

# ── 5. CAN interface ──
echo -e "\n${AMBER}[5/8] CAN Interface${NC}"
if [ -f /etc/udev/rules.d/80-can.rules ]; then
    ok "CAN udev rules installed"
else
    fail "CAN udev rules missing at /etc/udev/rules.d/80-can.rules"
fi

if ip link show can0 &>/dev/null; then
    STATE=$(ip -brief link show can0 | awk '{print $2}')
    ok "can0 interface present (state: $STATE)"
elif ip link show slcan0 &>/dev/null; then
    ok "slcan0 interface present (USB serial CAN)"
else
    warn "No CAN interface detected — plug in the USB2CANFD adapter"
fi

# ── 6. Wi-Fi hotspot ──
echo -e "\n${AMBER}[6/8] Wi-Fi Hotspot${NC}"
if nmcli con show "MZ1312_DRIFTER" &>/dev/null; then
    ok "Hotspot profile MZ1312_DRIFTER exists"
    if nmcli con show --active | grep -q "MZ1312_DRIFTER"; then
        ok "Hotspot is active"
        IP=$(ip -4 addr show wlan0 2>/dev/null | grep -oP '(?<=inet\s)\d+(\.\d+){3}')
        if [ -n "$IP" ]; then
            ok "Hotspot IP: $IP"
        fi
    else
        warn "Hotspot profile exists but is not active"
    fi
else
    fail "Hotspot profile MZ1312_DRIFTER not found"
fi

# ── 7. Optional components ──
echo -e "\n${AMBER}[7/8] Optional Components${NC}"

# TTS
if command -v piper &>/dev/null; then
    ok "Piper TTS installed"
    if [ -f /opt/drifter/piper-models/en_GB-alan-medium.onnx ]; then
        ok "Piper voice model present"
    else
        warn "Piper voice model missing — voice alerts will use espeak-ng fallback"
    fi
elif command -v espeak-ng &>/dev/null; then
    ok "espeak-ng fallback TTS available"
else
    warn "No TTS engine found — voice alerts disabled"
fi

# Ollama
if command -v ollama &>/dev/null; then
    ok "Ollama installed"
    if ollama list 2>/dev/null | grep -q "qwen"; then
        ok "LLM model available"
    else
        warn "No LLM model pulled — run: ollama pull qwen2.5:7b"
    fi
else
    warn "Ollama not installed — LLM mechanic unavailable (offline AI disabled)"
fi

# RTL-SDR
if command -v rtl_433 &>/dev/null; then
    ok "rtl_433 installed (RF/TPMS decoding)"
else
    warn "rtl_433 not found — TPMS and RF features unavailable"
fi

# Vosk
if [ -d /opt/drifter/vosk-models/vosk-model-small-en-us-0.15 ]; then
    ok "Vosk STT model present"
else
    warn "Vosk model missing — voice input unavailable"
fi

# ── 8. Quick smoke test ──
echo -e "\n${AMBER}[8/8] Smoke Test${NC}"

# Syntax-check config.py (the single source of truth)
if /opt/drifter/venv/bin/python3 -m py_compile /opt/drifter/config.py 2>/dev/null; then
    ok "config.py compiles cleanly"
else
    fail "config.py has syntax errors"
fi

# Check web dashboard port
if command -v ss &>/dev/null; then
    if ss -tlnp 2>/dev/null | grep -q ':8080'; then
        ok "Web dashboard listening on port 8080"
    else
        warn "Port 8080 not listening — dashboard may still be starting"
    fi
fi

# Check RealDash port
if command -v ss &>/dev/null; then
    if ss -tlnp 2>/dev/null | grep -q ':35000'; then
        ok "RealDash bridge listening on port 35000"
    else
        warn "Port 35000 not listening — RealDash bridge may still be starting"
    fi
fi

# ── Summary ──
echo ""
echo -e "${CYAN}════════════════════════════════════════════════${NC}"
echo -e "  RESULTS: ${GREEN}${PASS} passed${NC}  ${AMBER}${WARN} warnings${NC}  ${RED}${FAIL} failed${NC}"
echo -e "${CYAN}════════════════════════════════════════════════${NC}"
echo ""

if [ $FAIL -gt 0 ]; then
    echo -e "  ${RED}Some checks failed — review above and fix before driving.${NC}"
    echo ""
    exit 1
elif [ $WARN -gt 0 ]; then
    echo -e "  ${AMBER}Warnings are OK — optional features may be unavailable.${NC}"
    echo -e "  ${GREEN}Core diagnostics should work fine.${NC}"
    echo ""
    exit 0
else
    echo -e "  ${GREEN}ALL CHECKS PASSED — DRIFTER is ready to roll.${NC}"
    echo ""
    exit 0
fi

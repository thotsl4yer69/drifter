#!/bin/bash
# ============================================
# MZ1312 DRIFTER — Test Bench
# Injects simulated telemetry via MQTT to test alert engine,
# logger, voice alerts, and RealDash bridge without a live CAN bus.
#
# Usage: ./scripts/test-bench.sh [scenario]
#   Scenarios: idle | vacuum | overheat | alternator | coldstart | thermostat | dtc | all
#
# Requires: mosquitto_pub (apt install mosquitto-clients)
# UNCAGED TECHNOLOGY — EST 1991
# ============================================

set -euo pipefail

CYAN='\033[0;36m'
GREEN='\033[0;32m'
AMBER='\033[0;33m'
RED='\033[0;31m'
NC='\033[0m'

MQTT_HOST="${MQTT_HOST:-localhost}"
MQTT_PORT="${MQTT_PORT:-1883}"

pub() {
    local topic="$1"
    local payload="$2"
    mosquitto_pub -h "$MQTT_HOST" -p "$MQTT_PORT" -t "$topic" -m "$payload" 2>/dev/null
}

pub_value() {
    local topic="$1"
    local value="$2"
    local unit="${3:-}"
    pub "$topic" "{\"value\": $value, \"unit\": \"$unit\", \"ts\": $(date +%s.%N)}"
}

banner() {
    echo -e "${CYAN}"
    echo "  ████████ ███████ ███████ ████████ "
    echo "     ██    ██      ██         ██    "
    echo "     ██    █████   ███████    ██    "
    echo "     ██    ██           ██    ██    "
    echo "     ██    ███████ ███████    ██    "
    echo ""
    echo "  DRIFTER TEST BENCH — MZ1312"
    echo -e "${NC}"
}

# ── Scenario 1: Normal Idle ──
scenario_idle() {
    echo -e "\n${GREEN}[SCENARIO] Normal Warm Idle — all values nominal${NC}"
    echo "  Duration: 15 seconds"
    echo "  Expected: Alert = OK, no warnings"
    echo ""

    for i in $(seq 1 30); do
        # Normal idle: 720-760 RPM, 90°C coolant, 14.2V, STFT ±2%
        rpm=$(( 720 + RANDOM % 40 ))
        coolant=92
        voltage="14.2"
        stft1=$(echo "scale=1; (($RANDOM % 40) - 20) / 10" | bc)
        stft2=$(echo "scale=1; (($RANDOM % 40) - 20) / 10" | bc)
        speed=0
        throttle="2.5"
        load="22.0"
        iat=35

        pub_value "drifter/engine/rpm" "$rpm" "rpm"
        pub_value "drifter/engine/coolant" "$coolant" "C"
        pub_value "drifter/power/voltage" "$voltage" "V"
        pub_value "drifter/engine/stft1" "$stft1" "%"
        pub_value "drifter/engine/stft2" "$stft2" "%"
        pub_value "drifter/vehicle/speed" "$speed" "km/h"
        pub_value "drifter/engine/throttle" "$throttle" "%"
        pub_value "drifter/engine/load" "$load" "%"
        pub_value "drifter/engine/iat" "$iat" "C"
        pub_value "drifter/engine/ltft1" "2.5" "%"
        pub_value "drifter/engine/ltft2" "-1.8" "%"

        # Snapshot
        pub "drifter/snapshot" "{\"rpm\": $rpm, \"coolant\": $coolant, \"voltage\": $voltage, \"stft1\": $stft1, \"stft2\": $stft2, \"speed\": $speed, \"throttle\": $throttle, \"load\": $load, \"iat\": $iat, \"ts\": $(date +%s.%N)}"

        echo -ne "\r  Tick $i/30 — RPM: $rpm, Coolant: ${coolant}°C, Voltage: ${voltage}V"
        sleep 0.5
    done
    echo -e "\n${GREEN}  ✓ Normal idle scenario complete${NC}"
}

# ── Scenario 2: Vacuum Leak ──
scenario_vacuum() {
    echo -e "\n${AMBER}[SCENARIO] Vacuum Leak — Bank 1 lean at idle${NC}"
    echo "  Duration: 15 seconds"
    echo "  Expected: AMBER alert — vacuum leak Bank 1"
    echo ""

    for i in $(seq 1 30); do
        rpm=$(( 680 + RANDOM % 60 ))
        coolant=91
        voltage="14.1"
        # Bank 1 lean: STFT1 high (+15 to +20%), Bank 2 normal
        stft1=$(echo "scale=1; 15 + ($RANDOM % 50) / 10" | bc)
        stft2=$(echo "scale=1; (($RANDOM % 40) - 20) / 10" | bc)
        speed=0
        throttle="2.8"
        load="25.0"

        pub_value "drifter/engine/rpm" "$rpm" "rpm"
        pub_value "drifter/engine/coolant" "$coolant" "C"
        pub_value "drifter/power/voltage" "$voltage" "V"
        pub_value "drifter/engine/stft1" "$stft1" "%"
        pub_value "drifter/engine/stft2" "$stft2" "%"
        pub_value "drifter/vehicle/speed" "$speed" "km/h"
        pub_value "drifter/engine/throttle" "$throttle" "%"
        pub_value "drifter/engine/load" "$load" "%"

        pub "drifter/snapshot" "{\"rpm\": $rpm, \"coolant\": $coolant, \"voltage\": $voltage, \"stft1\": $stft1, \"stft2\": $stft2, \"speed\": $speed, \"ts\": $(date +%s.%N)}"

        echo -ne "\r  Tick $i/30 — RPM: $rpm, STFT1: +${stft1}%, STFT2: ${stft2}%"
        sleep 0.5
    done
    echo -e "\n${AMBER}  ✓ Vacuum leak scenario complete — check alert engine output${NC}"
}

# ── Scenario 3: Coolant Overheat ──
scenario_overheat() {
    echo -e "\n${RED}[SCENARIO] Coolant Overheat — progressive temperature rise${NC}"
    echo "  Duration: 20 seconds"
    echo "  Expected: AMBER at 104°C, RED at 108°C"
    echo ""

    coolant=95
    for i in $(seq 1 40); do
        rpm=$(( 2500 + RANDOM % 200 ))
        # Ramp coolant: starts at 95, +0.5 per tick → hits 104 at tick 18, 108 at tick 26
        coolant=$(echo "scale=1; 95 + $i * 0.5" | bc)
        voltage="14.0"
        stft1="1.5"
        stft2="-0.8"
        speed=$(( 60 + RANDOM % 10 ))
        throttle="35.0"
        load="45.0"

        pub_value "drifter/engine/rpm" "$rpm" "rpm"
        pub_value "drifter/engine/coolant" "$coolant" "C"
        pub_value "drifter/power/voltage" "$voltage" "V"
        pub_value "drifter/engine/stft1" "$stft1" "%"
        pub_value "drifter/engine/stft2" "$stft2" "%"
        pub_value "drifter/vehicle/speed" "$speed" "km/h"
        pub_value "drifter/engine/throttle" "$throttle" "%"
        pub_value "drifter/engine/load" "$load" "%"

        pub "drifter/snapshot" "{\"rpm\": $rpm, \"coolant\": $coolant, \"voltage\": $voltage, \"speed\": $speed, \"ts\": $(date +%s.%N)}"

        level="OK"
        colour="$GREEN"
        ct_int=$(echo "$coolant" | cut -d. -f1)
        if [ "$ct_int" -ge 108 ]; then level="RED"; colour="$RED";
        elif [ "$ct_int" -ge 104 ]; then level="AMBER"; colour="$AMBER"; fi

        echo -ne "\r  Tick $i/40 — Coolant: ${coolant}°C [${colour}${level}${NC}]    "
        sleep 0.5
    done
    echo -e "\n${RED}  ✓ Overheat scenario complete — check alert escalation${NC}"
}

# ── Scenario 4: Alternator Failure ──
scenario_alternator() {
    echo -e "\n${RED}[SCENARIO] Alternator Failure — voltage drops under load${NC}"
    echo "  Duration: 15 seconds"
    echo "  Expected: AMBER at <13.2V, RED at <12.0V"
    echo ""

    voltage="14.3"
    for i in $(seq 1 30); do
        rpm=$(( 2000 + RANDOM % 300 ))
        coolant=91
        # Voltage drops: 14.3 → 13.0 → 11.5 over 30 ticks
        voltage=$(echo "scale=2; 14.3 - $i * 0.1" | bc)
        stft1="0.5"
        stft2="-1.2"
        speed=$(( 40 + RANDOM % 10 ))
        throttle="25.0"
        load="40.0"

        pub_value "drifter/engine/rpm" "$rpm" "rpm"
        pub_value "drifter/engine/coolant" "$coolant" "C"
        pub_value "drifter/power/voltage" "$voltage" "V"
        pub_value "drifter/engine/stft1" "$stft1" "%"
        pub_value "drifter/engine/stft2" "$stft2" "%"
        pub_value "drifter/vehicle/speed" "$speed" "km/h"
        pub_value "drifter/engine/throttle" "$throttle" "%"
        pub_value "drifter/engine/load" "$load" "%"

        pub "drifter/snapshot" "{\"rpm\": $rpm, \"coolant\": $coolant, \"voltage\": $voltage, \"speed\": $speed, \"ts\": $(date +%s.%N)}"

        level="OK"
        colour="$GREEN"
        v_int=$(echo "$voltage" | cut -d. -f1)
        v_dec=$(echo "$voltage < 12.0" | bc -l)
        v_warn=$(echo "$voltage < 13.2" | bc -l)
        if [ "$v_dec" -eq 1 ]; then level="RED"; colour="$RED";
        elif [ "$v_warn" -eq 1 ]; then level="AMBER"; colour="$AMBER"; fi

        echo -ne "\r  Tick $i/30 — Voltage: ${voltage}V at ${rpm} RPM [${colour}${level}${NC}]    "
        sleep 0.5
    done
    echo -e "\n${RED}  ✓ Alternator failure scenario complete — check voice alerts${NC}"
}

# ── Scenario 5: Cold Start (X-Type) ──
scenario_coldstart() {
    echo -e "\n${CYAN}[SCENARIO] X-Type Cold Start — low coolant, fast idle${NC}"
    echo "  Duration: 15 seconds"
    echo "  Expected: INFO — cold start monitoring, fast idle normal"
    echo ""

    for i in $(seq 1 30); do
        # Cold engine: coolant 5-25°C, RPM 1100-1300 (fast idle)
        coolant=$(( 5 + i / 2 ))
        rpm=$(( 1200 + RANDOM % 100 ))
        voltage="13.9"
        stft1=$(echo "scale=1; 8 + ($RANDOM % 40) / 10" | bc)
        stft2=$(echo "scale=1; 6 + ($RANDOM % 40) / 10" | bc)
        speed=0
        throttle="4.0"
        load="28.0"

        pub_value "drifter/engine/rpm" "$rpm" "rpm"
        pub_value "drifter/engine/coolant" "$coolant" "C"
        pub_value "drifter/power/voltage" "$voltage" "V"
        pub_value "drifter/engine/stft1" "$stft1" "%"
        pub_value "drifter/engine/stft2" "$stft2" "%"
        pub_value "drifter/vehicle/speed" "$speed" "km/h"
        pub_value "drifter/engine/throttle" "$throttle" "%"
        pub_value "drifter/engine/load" "$load" "%"

        pub "drifter/snapshot" "{\"rpm\": $rpm, \"coolant\": $coolant, \"voltage\": $voltage, \"stft1\": $stft1, \"stft2\": $stft2, \"speed\": $speed, \"ts\": $(date +%s.%N)}"

        echo -ne "\r  Tick $i/30 — RPM: $rpm, Coolant: ${coolant}°C (cold start)"
        sleep 0.5
    done
    echo -e "\n${CYAN}  ✓ Cold start scenario complete — check INFO messages${NC}"
}

# ── Scenario 6: Thermostat Failure (X-Type) ──
scenario_thermostat() {
    echo -e "\n${AMBER}[SCENARIO] X-Type Thermostat — coolant oscillation (failing housing)${NC}"
    echo "  Duration: 20 seconds"
    echo "  Expected: AMBER — thermostat cycling detected"
    echo ""

    for i in $(seq 1 40); do
        rpm=$(( 2200 + RANDOM % 200 ))
        # Oscillate coolant ±6°C around 88°C (thermostat open temp)
        osc=$(echo "scale=1; s($i * 0.4) * 6" | bc -l)
        coolant=$(echo "scale=1; 88 + $osc" | bc)
        voltage="14.1"
        stft1="1.5"
        stft2="-0.5"
        speed=$(( 50 + RANDOM % 10 ))
        throttle="30.0"
        load="40.0"

        pub_value "drifter/engine/rpm" "$rpm" "rpm"
        pub_value "drifter/engine/coolant" "$coolant" "C"
        pub_value "drifter/power/voltage" "$voltage" "V"
        pub_value "drifter/engine/stft1" "$stft1" "%"
        pub_value "drifter/engine/stft2" "$stft2" "%"
        pub_value "drifter/vehicle/speed" "$speed" "km/h"
        pub_value "drifter/engine/throttle" "$throttle" "%"
        pub_value "drifter/engine/load" "$load" "%"

        pub "drifter/snapshot" "{\"rpm\": $rpm, \"coolant\": $coolant, \"voltage\": $voltage, \"speed\": $speed, \"ts\": $(date +%s.%N)}"

        echo -ne "\r  Tick $i/40 — Coolant: ${coolant}°C at ${rpm} RPM (oscillating)"
        sleep 0.5
    done
    echo -e "\n${AMBER}  ✓ Thermostat scenario complete — check for cycling alert${NC}"
}

# ── Scenario 7: DTC Injection (X-Type) ──
scenario_dtc() {
    echo -e "\n${AMBER}[SCENARIO] X-Type DTC — injecting P0301 cylinder 1 misfire${NC}"
    echo "  Duration: 5 seconds"
    echo "  Expected: AMBER — active DTC with X-Type diagnosis"
    echo ""

    # Inject a DTC message directly (simulating what can_bridge.py publishes)
    pub "drifter/diag/dtc" "{\"stored\": [\"P0301\", \"P0420\"], \"pending\": [\"P0171\"], \"count\": 3, \"ts\": $(date +%s.%N)}"
    echo "  Injected: stored=[P0301, P0420] pending=[P0171]"

    # Send some normal engine data so alert engine has context
    for i in $(seq 1 10); do
        rpm=$(( 780 + RANDOM % 50 ))
        pub_value "drifter/engine/rpm" "$rpm" "rpm"
        pub_value "drifter/engine/coolant" "91" "C"
        pub_value "drifter/power/voltage" "14.1" "V"
        pub_value "drifter/engine/stft1" "2.0" "%"
        pub_value "drifter/engine/stft2" "-1.0" "%"

        echo -ne "\r  Tick $i/10 — RPM: $rpm (DTC active)"
        sleep 0.5
    done

    # Clear DTCs
    pub "drifter/diag/dtc" "{\"stored\": [], \"pending\": [], \"count\": 0, \"ts\": $(date +%s.%N)}"
    echo -e "\n  DTCs cleared"
    echo -e "${AMBER}  ✓ DTC scenario complete — check X-Type DTC lookup output${NC}"
}

# ── Main ──
banner

if ! command -v mosquitto_pub &>/dev/null; then
    echo -e "${RED}ERROR: mosquitto_pub not found. Install: sudo apt install mosquitto-clients${NC}"
    exit 1
fi

# Check MQTT broker
if ! mosquitto_pub -h "$MQTT_HOST" -p "$MQTT_PORT" -t "drifter/test/ping" -m '{"test":true}' 2>/dev/null; then
    echo -e "${RED}ERROR: Cannot connect to MQTT broker at ${MQTT_HOST}:${MQTT_PORT}${NC}"
    echo -e "  Start NanoMQ: ${CYAN}sudo systemctl start nanomq${NC}"
    exit 1
fi

echo -e "${GREEN}  Connected to MQTT broker at ${MQTT_HOST}:${MQTT_PORT}${NC}"

SCENARIO="${1:-all}"

case "$SCENARIO" in
    idle)
        scenario_idle
        ;;
    vacuum)
        scenario_vacuum
        ;;
    overheat)
        scenario_overheat
        ;;
    alternator)
        scenario_alternator
        ;;
    coldstart)
        scenario_coldstart
        ;;
    thermostat)
        scenario_thermostat
        ;;
    dtc)
        scenario_dtc
        ;;
    all)
        scenario_idle
        echo ""
        sleep 2
        scenario_vacuum
        echo ""
        sleep 2
        scenario_overheat
        echo ""
        sleep 2
        scenario_alternator
        echo ""
        sleep 2
        scenario_coldstart
        echo ""
        sleep 2
        scenario_thermostat
        echo ""
        sleep 2
        scenario_dtc
        ;;
    *)
        echo -e "${AMBER}Usage: $0 [idle|vacuum|overheat|alternator|coldstart|thermostat|dtc|all]${NC}"
        exit 1
        ;;
esac

echo -e "\n${CYAN}════════════════════════════════════════════════${NC}"
echo -e "${CYAN}  TEST BENCH COMPLETE${NC}"
echo -e "${CYAN}════════════════════════════════════════════════${NC}"
echo -e "  Check results:"
echo -e "    Alerts:  ${CYAN}mosquitto_sub -h $MQTT_HOST -t 'drifter/alert/#' -v${NC}"
echo -e "    Logs:    ${CYAN}journalctl -u drifter-alerts -n 50${NC}"
echo -e "    Voice:   ${CYAN}journalctl -u drifter-voice -n 20${NC}"
echo -e "    Status:  ${CYAN}python3 /opt/drifter/status.py${NC}"
echo ""

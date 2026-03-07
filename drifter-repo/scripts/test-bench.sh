#!/bin/bash
# ============================================
# MZ1312 DRIFTER — Bench Test
# Simulates a Jaguar X-Type with diagnostic issues
# Run this WITHOUT a car to test the full pipeline
# ============================================

set -e

CYAN='\033[0;36m'
RED='\033[0;31m'
GREEN='\033[0;32m'
AMBER='\033[0;33m'
NC='\033[0m'

echo -e "${CYAN}"
echo "  DRIFTER Bench Test — Virtual Jaguar"
echo "  =====================================${NC}"
echo ""

# Check root
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Run as root: sudo ./scripts/test-bench.sh${NC}"
    exit 1
fi

# ── Step 1: Create virtual CAN interface ──
echo -e "${AMBER}[1/4] Creating virtual CAN interface (vcan0)...${NC}"
modprobe vcan 2>/dev/null || true
ip link add dev vcan0 type vcan 2>/dev/null || true
ip link set up vcan0
echo -e "${GREEN}  ✓ vcan0 is up${NC}"

# ── Step 2: Check MQTT broker ──
echo -e "${AMBER}[2/4] Checking MQTT broker...${NC}"
if systemctl is-active nanomq &>/dev/null; then
    echo -e "${GREEN}  ✓ NanoMQ running${NC}"
elif systemctl is-active mosquitto &>/dev/null; then
    echo -e "${GREEN}  ✓ Mosquitto running${NC}"
else
    echo -e "${RED}  ✗ No MQTT broker running. Start with: sudo systemctl start nanomq${NC}"
    exit 1
fi

# ── Step 3: Subscribe to alerts in background ──
echo -e "${AMBER}[3/4] Subscribing to alert feed...${NC}"
mosquitto_sub -h localhost -t "drifter/alert/#" -v &
SUB_PID=$!
echo -e "${GREEN}  ✓ Listening on drifter/alert/#${NC}"

# ── Step 4: Simulate OBD-II responses ──
echo -e "${AMBER}[4/4] Simulating Jaguar X-Type telemetry...${NC}"
echo ""
echo -e "${CYAN}  Scenario 1: Normal operation (5 seconds)${NC}"

# Simulate normal: RPM=800, Coolant=95°C, STFT1=2%, STFT2=1%
for i in $(seq 1 5); do
    # OBD response for RPM (PID 0x0C): 800 RPM = 3200 raw → 0x0C80
    cansend vcan0 7E8#0441 0C0C800000
    # Coolant (PID 0x05): 95°C = 135 raw (95+40) → 0x87
    cansend vcan0 7E8#034105870000 2>/dev/null || \
    mosquitto_pub -h localhost -t "drifter/engine/rpm" -m '{"value":800,"unit":"rpm","ts":'$(date +%s)'}'
    mosquitto_pub -h localhost -t "drifter/engine/coolant" -m '{"value":95,"unit":"C","ts":'$(date +%s)'}'
    mosquitto_pub -h localhost -t "drifter/engine/stft1" -m '{"value":2.0,"unit":"%","ts":'$(date +%s)'}'
    mosquitto_pub -h localhost -t "drifter/engine/stft2" -m '{"value":1.0,"unit":"%","ts":'$(date +%s)'}'
    mosquitto_pub -h localhost -t "drifter/power/voltage" -m '{"value":14.2,"unit":"V","ts":'$(date +%s)'}'
    mosquitto_pub -h localhost -t "drifter/vehicle/speed" -m '{"value":0,"unit":"km/h","ts":'$(date +%s)'}'
    sleep 1
done

echo ""
echo -e "${AMBER}  Scenario 2: Vacuum leak developing on Bank 1 (10 seconds)${NC}"

for i in $(seq 1 10); do
    # STFT1 climbing from 5% to 18%
    STFT1=$(echo "5 + $i * 1.3" | bc)
    mosquitto_pub -h localhost -t "drifter/engine/rpm" -m '{"value":780,"unit":"rpm","ts":'$(date +%s)'}'
    mosquitto_pub -h localhost -t "drifter/engine/coolant" -m '{"value":96,"unit":"C","ts":'$(date +%s)'}'
    mosquitto_pub -h localhost -t "drifter/engine/stft1" -m "{\"value\":${STFT1},\"unit\":\"%\",\"ts\":$(date +%s)}"
    mosquitto_pub -h localhost -t "drifter/engine/stft2" -m '{"value":1.5,"unit":"%","ts":'$(date +%s)'}'
    mosquitto_pub -h localhost -t "drifter/power/voltage" -m '{"value":14.1,"unit":"V","ts":'$(date +%s)'}'
    mosquitto_pub -h localhost -t "drifter/vehicle/speed" -m '{"value":0,"unit":"km/h","ts":'$(date +%s)'}'
    sleep 1
done

echo ""
echo -e "${RED}  Scenario 3: Coolant overheating (10 seconds)${NC}"

for i in $(seq 1 10); do
    COOLANT=$(echo "100 + $i * 1.2" | bc)
    mosquitto_pub -h localhost -t "drifter/engine/rpm" -m '{"value":2500,"unit":"rpm","ts":'$(date +%s)'}'
    mosquitto_pub -h localhost -t "drifter/engine/coolant" -m "{\"value\":${COOLANT},\"unit\":\"C\",\"ts\":$(date +%s)}"
    mosquitto_pub -h localhost -t "drifter/engine/stft1" -m '{"value":3.0,"unit":"%","ts":'$(date +%s)'}'
    mosquitto_pub -h localhost -t "drifter/engine/stft2" -m '{"value":2.5,"unit":"%","ts":'$(date +%s)'}'
    mosquitto_pub -h localhost -t "drifter/power/voltage" -m '{"value":14.3,"unit":"V","ts":'$(date +%s)'}'
    mosquitto_pub -h localhost -t "drifter/vehicle/speed" -m '{"value":80,"unit":"km/h","ts":'$(date +%s)'}'
    sleep 1
done

echo ""
echo -e "${AMBER}  Scenario 4: Alternator failing (5 seconds)${NC}"

for i in $(seq 1 5); do
    VOLTS=$(echo "13.0 - $i * 0.3" | bc)
    mosquitto_pub -h localhost -t "drifter/engine/rpm" -m '{"value":2000,"unit":"rpm","ts":'$(date +%s)'}'
    mosquitto_pub -h localhost -t "drifter/engine/coolant" -m '{"value":98,"unit":"C","ts":'$(date +%s)'}'
    mosquitto_pub -h localhost -t "drifter/engine/stft1" -m '{"value":1.0,"unit":"%","ts":'$(date +%s)'}'
    mosquitto_pub -h localhost -t "drifter/engine/stft2" -m '{"value":0.5,"unit":"%","ts":'$(date +%s)'}'
    mosquitto_pub -h localhost -t "drifter/power/voltage" -m "{\"value\":${VOLTS},\"unit\":\"V\",\"ts\":$(date +%s)}"
    mosquitto_pub -h localhost -t "drifter/vehicle/speed" -m '{"value":60,"unit":"km/h","ts":'$(date +%s)'}'
    sleep 1
done

echo ""
echo -e "${GREEN}════════════════════════════════════════${NC}"
echo -e "${GREEN}  Bench test complete.${NC}"
echo -e "${GREEN}════════════════════════════════════════${NC}"
echo ""
echo "  If drifter-alerts is running, you should have seen:"
echo -e "  - ${GREEN}Normal operation (no alerts)${NC}"
echo -e "  - ${AMBER}Vacuum leak Bank 1 alert${NC}"
echo -e "  - ${RED}Coolant critical alert${NC}"
echo -e "  - ${AMBER}Alternator undercharging alert${NC}"
echo ""
echo "  Check voice output on 3.5mm jack if drifter-voice is running."
echo ""

# Cleanup
kill $SUB_PID 2>/dev/null || true

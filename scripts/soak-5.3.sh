#!/usr/bin/env bash
# soak-5.3.sh — Phase 5.3 soak runner.
#
# Stages a 2-hour (configurable) bench run that snapshots service
# health every 30s, fires a synthetic gps/fix every 5min, and prints
# a per-service restart count + healthz pass/fail summary at the end.
#
# Don't run this from the cockpit you're driving — it logs verbosely.
# Tail-watch via `tail -F /var/log/drifter/soak-*.log` from a second
# session if you want live progress.
#
# UNCAGED TECHNOLOGY — EST 1991

set -uo pipefail

DURATION_SEC=${1:-7200}              # default 2h
SNAPSHOT_INTERVAL=30
SYNTHETIC_GPS_INTERVAL=300

LOG_DIR="/var/log/drifter"
sudo mkdir -p "$LOG_DIR" 2>/dev/null
LOG="$LOG_DIR/soak-$(date +%Y%m%d-%H%M).log"
sudo touch "$LOG" 2>/dev/null
sudo chown "$(whoami):$(whoami)" "$LOG" 2>/dev/null || true

# Canonical service list — keep in sync with src/config.py SERVICES.
SERVICES=(
  drifter-canbridge drifter-alerts drifter-dashboard drifter-logger
  drifter-voice drifter-vivi drifter-hotspot drifter-homesync
  drifter-watchdog drifter-realdash drifter-rf drifter-wardrive
  drifter-fbmirror drifter-anomaly drifter-analyst drifter-voicein
  drifter-flipper drifter-opsec drifter-bleconv drifter-gps
)

# Synthetic GPS waypoints — three points in central Melbourne so the
# map follow path can be observed pinging back and forth without
# leaking the operator's actual coordinates into the soak log.
WAYPOINTS=(
  '{"lat":-37.8136,"lng":144.9631,"src":"soak-fed-sq"}'
  '{"lat":-37.8226,"lng":144.9691,"src":"soak-arts-precinct"}'
  '{"lat":-37.8004,"lng":144.9699,"src":"soak-carlton"}'
)

ts()        { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
log()       { echo "[$(ts)] $*" | tee -a "$LOG"; }
log_raw()   { echo "$*" >> "$LOG"; }

START_TS=$(date +%s)
END_TS=$((START_TS + DURATION_SEC))

# Per-service restart counter — keyed by service. Bump when
# ActiveEnterTimestampMonotonic rises between snapshots.
declare -A RESTART_COUNT
declare -A LAST_AET   # last seen ActiveEnterTimestampMonotonic
declare -A FINAL_STATE
for s in "${SERVICES[@]}"; do
  RESTART_COUNT[$s]=0
  LAST_AET[$s]=0
  FINAL_STATE[$s]="?"
done
HEALTHZ_PASS=0
HEALTHZ_FAIL=0
PEAK_TEMP=0
PEAK_MEM_USED=0

snapshot() {
  local n=$1
  log "── snapshot $n ──"
  # Per-service is-active + restart-count tracking
  for s in "${SERVICES[@]}"; do
    local state aet
    state=$(systemctl is-active "$s" 2>&1 | head -1)
    aet=$(systemctl show -p ActiveEnterTimestampMonotonic --value "$s" 2>/dev/null || echo 0)
    FINAL_STATE[$s]="$state"
    if [[ "${LAST_AET[$s]}" != "0" && "$aet" -gt "${LAST_AET[$s]}" ]]; then
      RESTART_COUNT[$s]=$(( RESTART_COUNT[$s] + 1 ))
      log "  restart detected: $s (count=${RESTART_COUNT[$s]})"
    fi
    LAST_AET[$s]=$aet
    log_raw "  $s = $state  (aet=$aet)"
  done

  # System resources
  local mem cputemp
  mem=$(free -m | awk '/^Mem:/ {printf "used=%dMB free=%dMB total=%dMB", $3, $4, $2}')
  cputemp=$(vcgencmd measure_temp 2>/dev/null | sed 's/temp=//;s/.C$//')
  log_raw "  mem: $mem"
  log_raw "  cpu_temp: ${cputemp:-N/A}"
  # Track peaks
  local mem_used; mem_used=$(free -m | awk '/^Mem:/ {print $3}')
  if [[ -n "$mem_used" && "$mem_used" -gt "$PEAK_MEM_USED" ]]; then PEAK_MEM_USED=$mem_used; fi
  if [[ -n "$cputemp" ]]; then
    local t_int; t_int=$(printf '%.0f' "$cputemp" 2>/dev/null || echo 0)
    if [[ "$t_int" -gt "$PEAK_TEMP" ]]; then PEAK_TEMP=$t_int; fi
  fi

  # /healthz one-line
  local hz
  hz=$(curl -fsS --max-time 3 http://127.0.0.1:8080/healthz 2>/dev/null)
  if [[ -n "$hz" ]]; then
    log_raw "  healthz: $(echo "$hz" | jq -c '{status,services_failed,mqtt_connected,telemetry_fresh}' 2>/dev/null || echo "$hz" | head -c 200)"
    HEALTHZ_PASS=$(( HEALTHZ_PASS + 1 ))
  else
    log_raw "  healthz: UNREACHABLE"
    HEALTHZ_FAIL=$(( HEALTHZ_FAIL + 1 ))
  fi
}

publish_synthetic() {
  local idx=$(( $1 % ${#WAYPOINTS[@]} ))
  local payload="${WAYPOINTS[$idx]}"
  payload="${payload%\}}, \"ts\":$(date +%s)}"
  if mosquitto_pub -h localhost -t drifter/gps/fix -m "$payload" 2>/dev/null; then
    log "synthetic gps publish #$1 → ${WAYPOINTS[$idx]:0:40}…"
  else
    log "synthetic gps publish #$1 FAILED — broker unreachable?"
  fi
}

trap 'log "interrupted"; print_summary; exit 130' INT TERM

print_summary() {
  log ""
  log "═══════════ SOAK SUMMARY ═══════════"
  local elapsed=$(( $(date +%s) - START_TS ))
  log "elapsed: ${elapsed}s of ${DURATION_SEC}s"
  log "healthz: pass=$HEALTHZ_PASS fail=$HEALTHZ_FAIL"
  log "peak cpu temp: ${PEAK_TEMP}°C"
  log "peak memory used: ${PEAK_MEM_USED}MB"
  log ""
  printf '%-22s %-12s %s\n' "SERVICE" "FINAL_STATE" "RESTARTS" | tee -a "$LOG"
  printf '%-22s %-12s %s\n' "----------------------" "-----------" "--------" | tee -a "$LOG"
  for s in "${SERVICES[@]}"; do
    printf '%-22s %-12s %d\n' "$s" "${FINAL_STATE[$s]}" "${RESTART_COUNT[$s]}" | tee -a "$LOG"
  done
  log ""
  log "log written to: $LOG"
}

log "soak start — duration=${DURATION_SEC}s, snapshot=${SNAPSHOT_INTERVAL}s, gps=${SYNTHETIC_GPS_INTERVAL}s"
log "log file: $LOG"

snap_n=0
gps_n=0
last_gps=$START_TS
NOW=$START_TS
while (( NOW < END_TS )); do
  snap_n=$(( snap_n + 1 ))
  snapshot "$snap_n"
  if (( NOW - last_gps >= SYNTHETIC_GPS_INTERVAL )); then
    gps_n=$(( gps_n + 1 ))
    publish_synthetic "$gps_n"
    last_gps=$NOW
  fi
  sleep "$SNAPSHOT_INTERVAL"
  NOW=$(date +%s)
done

print_summary
log "soak complete"

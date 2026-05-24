#!/usr/bin/env bash
# MZ1312 DRIFTER — Marauder bench tests.
# Modes:
#   probe              — runs autodetect via /api/marauder/probe + reads status
#   passive            — runs scan_ap for 30s, prints event count from MQTT
#   deauth_detect      — runs detector for 60s, prints any deauths seen
#   allowlist_refuse   — sends deauth_attack to a BSSID NOT in allowlist,
#                        asserts the bridge refuses
#
# Phase 4 portal_dryrun lands when the EvilPortal feature is implemented.

set -euo pipefail

MODE="${1:-probe}"
OPSEC_BASE="http://127.0.0.1:8090"
MQTT_HOST="127.0.0.1"
MQTT_PORT="1883"

die() { echo "FAIL: $*" >&2; exit 1; }
ok()  { echo "OK:   $*"; }

require() {
    command -v "$1" >/dev/null || die "missing dependency: $1"
}

require curl
require mosquitto_sub
require jq

case "$MODE" in
    probe)
        echo "→ POST /api/marauder/probe"
        curl -fsS -X POST "$OPSEC_BASE/api/marauder/probe" | jq .
        sleep 1
        echo "→ GET /api/marauder/status"
        status=$(curl -fsS "$OPSEC_BASE/api/marauder/status")
        echo "$status" | jq .
        state=$(echo "$status" | jq -r .state)
        case "$state" in
            idle)         ok "transport found — service idle";;
            no_hardware)  ok "no hardware present — service correctly in no_hardware state";;
            unknown)      ok "drifter-marauder not yet reporting (service may not be running)";;
            *)            die "unexpected state: $state";;
        esac
        ;;
    passive)
        echo "→ POST /cmd scan_ap duration_s=30 (running in background, listening for events)"
        op_id=$(curl -fsS -X POST "$OPSEC_BASE/api/marauder/cmd" \
            -H 'Content-Type: application/json' \
            -d '{"command":"scan_ap","args":{"duration_s":30}}' | jq -r .op_id)
        ok "op_id=$op_id"

        echo "→ Listening for drifter/marauder/scan/ap for 35s …"
        count=$(timeout 35s mosquitto_sub -h "$MQTT_HOST" -p "$MQTT_PORT" \
                -t 'drifter/marauder/scan/ap' -v 2>/dev/null | wc -l || true)
        if [ "$count" -eq 0 ]; then
            echo "WARN: no scan events received (no hardware, no APs in range, or service idle)"
        else
            ok "received $count scan/ap events"
        fi
        ;;
    deauth_detect)
        echo "→ POST /cmd deauth_detect (no confirm, no allowlist — LOW risk per §5.2)"
        curl -fsS -X POST "$OPSEC_BASE/api/marauder/cmd" \
            -H 'Content-Type: application/json' \
            -d '{"command":"deauth_detect","args":{"duration_s":60}}' | jq .
        echo "→ Listening for drifter/marauder/event {type:deauth_seen} for 65s …"
        timeout 65s mosquitto_sub -h "$MQTT_HOST" -p "$MQTT_PORT" \
            -t 'drifter/marauder/event' -v 2>/dev/null | grep deauth_seen || \
            echo "(no deauths observed — environment may be quiet, this is fine)"
        ;;
    allowlist_refuse)
        echo "→ Test: deauth_attack to BSSID NOT in allowlist must be refused"
        # 1) First call: should get confirm_token
        r1=$(curl -fsS -X POST "$OPSEC_BASE/api/marauder/cmd" \
            -H 'Content-Type: application/json' \
            -d '{"command":"deauth_attack","args":{"bssid":"de:ad:be:ef:00:00","ssid":"NOT_IN_ALLOWLIST"}}')
        op_id_1=$(echo "$r1" | jq -r .op_id)

        # Subscribe briefly to the event topic to get the token
        event_line=$(timeout 3s mosquitto_sub -h "$MQTT_HOST" -p "$MQTT_PORT" \
                     -t 'drifter/marauder/event' -C 2 -W 3 2>/dev/null \
                     | grep "$op_id_1" || true)
        token=$(echo "$event_line" | jq -r .confirm_token 2>/dev/null || echo "")
        [ -z "$token" ] && die "did not receive a confirm_token for op_id=$op_id_1"
        ok "got confirm_token (length=${#token})"

        # 2) Second call with token must be refused due to empty allowlist
        r2=$(curl -fsS -X POST "$OPSEC_BASE/api/marauder/cmd" \
            -H 'Content-Type: application/json' \
            -d "{\"command\":\"deauth_attack\",\"args\":{\"bssid\":\"de:ad:be:ef:00:00\",\"ssid\":\"NOT_IN_ALLOWLIST\"},\"confirm_token\":\"$token\"}")
        op_id_2=$(echo "$r2" | jq -r .op_id)

        event_line_2=$(timeout 3s mosquitto_sub -h "$MQTT_HOST" -p "$MQTT_PORT" \
                       -t 'drifter/marauder/event' -C 2 -W 3 2>/dev/null \
                       | grep "$op_id_2" || true)
        echo "$event_line_2" | grep -q '"ok":false' || die "expected refusal, got: $event_line_2"
        echo "$event_line_2" | grep -qi 'allowlist' || die "refusal reason missing 'allowlist': $event_line_2"
        ok "allowlist refusal correctly fired"
        ;;
    *)
        die "unknown mode: $MODE (want probe|passive|deauth_detect|allowlist_refuse)"
        ;;
esac

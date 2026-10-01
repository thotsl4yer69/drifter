#!/usr/bin/env bash
set -euo pipefail

fail(){ echo "VISION: FAIL — $*" >&2; exit 1; }
ok(){ echo "VISION: OK — $*"; }

command -v hailortcli >/dev/null || fail "hailortcli missing"
hailortcli fw-control identify >/dev/null || fail "Hailo NPU/firmware not detected"
ok "Hailo NPU detected"

command -v rpicam-hello >/dev/null || fail "rpicam-hello missing"
if rpicam-hello --list-cameras 2>&1 | grep -qiE 'available cameras|imx|camera'; then
    ok "camera enumerated"
else
    fail "no camera enumerated"
fi

command -v drifter >/dev/null || fail "drifter CLI missing"
MODE_JSON="$(drifter mode status --json 2>/dev/null || true)"
MODE="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("mode",""))' <<<"$MODE_JSON" 2>/dev/null || true)"
case "$MODE" in
    drive|recon) ok "operator mode permits vision: $MODE" ;;
    *) fail "vision service is mode-controlled; switch to DRIVE or RECON before acceptance (current: ${MODE:-unknown})" ;;
esac

# Exercise the actual DRIFTER service rather than opening the camera a second
# time with a parallel rpicam process. The retained vision status is published
# only after the model backend and camera path initialise.
systemctl restart drifter-vision || fail "could not restart drifter-vision"
systemctl is-active --quiet drifter-vision || fail "drifter-vision inactive"

command -v mosquitto_sub >/dev/null || fail "mosquitto_sub missing"
STATUS=""
for _ in $(seq 1 30); do
    CANDIDATE="$(timeout 2 mosquitto_sub -h 127.0.0.1 -t drifter/vision/status -C 1 -W 1 2>/dev/null || true)"
    if python3 - "$CANDIDATE" <<'PY' >/dev/null 2>&1
import json, sys
try:
    d = json.loads(sys.argv[1])
except Exception:
    raise SystemExit(1)
raise SystemExit(0 if d.get("state") == "online" and d.get("backend") == "hailo" and d.get("camera") == "online" else 1)
PY
    then
        STATUS="$CANDIDATE"
        break
    fi
    sleep 0.5
done
[ -n "$STATUS" ] || fail "actual Hailo + camera pipeline did not report online within 15s"

python3 - "$STATUS" <<'PY'
import json, sys
d = json.loads(sys.argv[1])
print(f"VISION: OK — actual DRIFTER pipeline online (backend=hailo camera={d.get('camera_id','unknown')})")
PY

# RECON adds recorder/indexer/ALPR on top of the same camera owner.
if [ "$MODE" = "recon" ]; then
    for svc in drifter-alpr drifter-recon-index; do
        systemctl is-active --quiet "$svc" || fail "$svc inactive in RECON"
    done
    ok "RECON evidence services active"
fi

echo "VISION: PASS"

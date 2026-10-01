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
STATUS="$(timeout 12 mosquitto_sub -h 127.0.0.1 -t drifter/vision/status -C 1 -W 10 2>/dev/null || true)"
[ -n "$STATUS" ] || fail "no retained drifter/vision/status received"

python3 - "$STATUS" <<'PY' || exit 1
import json, sys
try:
    d = json.loads(sys.argv[1])
except Exception as exc:
    print(f"VISION: FAIL — invalid status JSON: {exc}", file=sys.stderr)
    raise SystemExit(1)

if d.get("state") != "online":
    print(f"VISION: FAIL — service state is {d.get('state')!r}", file=sys.stderr)
    raise SystemExit(1)
if d.get("backend") != "hailo":
    print(f"VISION: FAIL — backend is {d.get('backend')!r}, expected 'hailo'", file=sys.stderr)
    raise SystemExit(1)
if d.get("camera") != "online":
    print(f"VISION: FAIL — camera is {d.get('camera')!r}", file=sys.stderr)
    raise SystemExit(1)

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

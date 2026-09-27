#!/usr/bin/env bash
set -euo pipefail

PP="/usr/share/rpi-camera-assets/hailo_yolov8_inference.json"
fail(){ echo "VISION: FAIL — $*" >&2; exit 1; }
ok(){ echo "VISION: OK — $*"; }

command -v hailortcli >/dev/null || fail "hailortcli missing"
hailortcli fw-control identify >/dev/null || fail "Hailo NPU/firmware not detected"
ok "Hailo NPU detected"

command -v rpicam-hello >/dev/null || fail "rpicam-hello missing"
[ -f "$PP" ] || fail "YOLOv8 Hailo post-process profile missing: $PP"
ok "YOLOv8 Hailo pipeline installed"

# Camera enumeration is side-effect free and catches ribbon/device failures.
if rpicam-hello --list-cameras 2>&1 | grep -qiE 'available cameras|imx|camera'; then
    ok "camera enumerated"
else
    fail "no camera enumerated"
fi

# Short headless inference smoke. A clean exit proves camera + Hailo pipeline
# can be constructed together; live detection quality is a separate road test.
timeout 12 rpicam-hello -n -t 5000 --post-process-file "$PP" >/tmp/drifter-hailo-smoke.log 2>&1     || fail "YOLOv8 Hailo smoke failed (see /tmp/drifter-hailo-smoke.log)"
ok "5s Hailo YOLOv8 camera smoke passed"

systemctl is-active --quiet drifter-perception || fail "drifter-perception inactive"
ok "perception fusion service active"
echo "VISION: PASS"

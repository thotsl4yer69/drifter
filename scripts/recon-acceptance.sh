#!/usr/bin/env bash
set -euo pipefail

REQUIRE_EVENT=0
if [ "${1:-}" = "--require-event" ]; then REQUIRE_EVENT=1; fi
fail(){ echo "RECON: FAIL — $*" >&2; exit 1; }
ok(){ echo "RECON: OK — $*"; }

command -v drifter >/dev/null || fail "drifter CLI missing"
MODE_JSON="$(drifter mode status --json 2>/dev/null || true)"
MODE="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("mode",""))' <<<"$MODE_JSON" 2>/dev/null || true)"
[ "$MODE" = "recon" ] || fail "switch to RECON first (current: ${MODE:-unknown})"
ok "RECON mode active"

for svc in drifter-vision drifter-alpr drifter-recon-index drifter-dashboard drifter-logger drifter-gps; do
    systemctl is-active --quiet "$svc" || fail "$svc inactive"
done
ok "RECON service set active"

command -v mosquitto_sub >/dev/null || fail "mosquitto_sub missing"
VISION="$(timeout 8 mosquitto_sub -h 127.0.0.1 -t drifter/vision/status -C 1 -W 6 2>/dev/null || true)"
RECON="$(timeout 8 mosquitto_sub -h 127.0.0.1 -t drifter/recon/status -C 1 -W 6 2>/dev/null || true)"
REC="$(timeout 8 mosquitto_sub -h 127.0.0.1 -t drifter/vision/dashcam/status -C 1 -W 6 2>/dev/null || true)"
[ -n "$VISION" ] || fail "no vision status"
[ -n "$RECON" ] || fail "no recon status"
[ -n "$REC" ] || fail "no recorder status"

python3 - "$VISION" "$RECON" "$REC" "$REQUIRE_EVENT" <<'PY'
import hashlib, json, pathlib, sys

vision, recon, rec = map(json.loads, sys.argv[1:4])
require_event = sys.argv[4] == '1'
if vision.get('state') != 'online': raise SystemExit(f"RECON: FAIL — vision={vision}")
if vision.get('backend') != 'hailo': raise SystemExit(f"RECON: FAIL — backend={vision.get('backend')!r}")
if vision.get('camera') != 'online': raise SystemExit(f"RECON: FAIL — camera={vision.get('camera')!r}")
if recon.get('state') != 'online': raise SystemExit(f"RECON: FAIL — indexer={recon.get('state')!r}")
if rec.get('state') not in {'recording','monitoring'}: raise SystemExit(f"RECON: FAIL — recorder={rec.get('state')!r}")

path = pathlib.Path(recon.get('ledger_path') or '')
root = pathlib.Path('/opt/drifter/recon/sessions').resolve()
try: path.resolve().relative_to(root)
except Exception: raise SystemExit(f"RECON: FAIL — invalid ledger path {path}")
if not path.is_file(): raise SystemExit(f"RECON: FAIL — ledger missing {path}")

prev = '0' * 64
count = 0
saw_vision = False
for raw in path.read_text(encoding='utf-8').splitlines():
    if not raw.strip(): continue
    row = json.loads(raw)
    digest = row.pop('hash', None)
    if row.get('prev_hash') != prev:
        raise SystemExit(f"RECON: FAIL — chain break at seq {row.get('seq')}")
    encoded = json.dumps(row, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')
    expected = hashlib.sha256(encoded).hexdigest()
    if digest != expected:
        raise SystemExit(f"RECON: FAIL — hash mismatch at seq {row.get('seq')}")
    prev = digest
    count += 1
    if row.get('kind') == 'vision':
        saw_vision = True

if not count: raise SystemExit('RECON: FAIL — empty ledger')
if recon.get('chain_head') != prev: raise SystemExit('RECON: FAIL — retained chain head does not match ledger')
if require_event and not saw_vision: raise SystemExit('RECON: FAIL — no indexed vision evidence yet; put a detectable object in view and retry')
print(f"RECON: OK — Hailo camera + recorder + ledger chain ({count} records, head {prev[:12]})")
PY

if command -v vcgencmd >/dev/null 2>&1; then
    THROTTLED="$(vcgencmd get_throttled 2>/dev/null || true)"
    echo "RECON: INFO — ${THROTTLED:-throttle state unavailable}"
fi

echo "RECON: PASS"

# DRIFTER Touch Control

The V4 cockpit is an operator control surface, not a read-only HUD.

## Entry points

- Cockpit: `http://127.0.0.1:8080/` on the Pi touchscreen.
- Expanded FOOT / OPSEC console: `http://127.0.0.1:8090/`.
- The kiosk launcher (`tools/launch-cockpit.sh`) opens the cockpit root on localhost, which is also inside the control API's local-peer ACL.

The dashboard prefers `/opt/drifter/ui/v4/index.html`. The legacy single-file cockpit is only a fallback when the V4 build is missing.

## Touch controls

The ARSENAL / FOOT surface now uses real dashboard APIs and displays backend state. It includes:

- DIAG, DRIVE and FOOT mode switches through `POST /api/mode/<mode>`.
- Expand / close full-screen touch-console mode.
- Start / restart / stop for the fail-closed arsenal service allowlist.
- Flipper bridge quick actions that are already accepted by the backend allowlist.
- Rubber Ducky / HID payload selection with the existing ARM -> service-confirmed arm id -> CONFIRM flow.
- A FULL OPSEC button to move into the separate port-8090 console.
- Five-second live state refresh plus explicit REFRESH.

The cockpit does not fabricate success. Controls stay locked when the mode, service, or hardware preconditions are not present.

## Arsenal service control

The touch surface controls only units already authorized by the backend and sudoers policy. Current surfaced units are:

- `drifter-flipper`
- `drifter-marauder`
- `drifter-wardrive`
- `drifter-kismet`
- `drifter-kismet-bridge`
- `drifter-wifi-audit`
- `drifter-rfaudio`
- `drifter-fly-catcher`

The server remains authoritative. Arbitrary systemd unit names are not accepted by the dashboard route.

## HID / Rubber Ducky

The UI reads:

- `GET /api/hid/payloads`
- `GET /api/hid/status`

and uses:

- `POST /api/hid/command` with `hid_arm`
- `POST /api/hid/command` with `hid_confirm`
- `POST /api/hid/command` with `hid_cancel`

A payload is not treated as armed until `drifter-hid` publishes an arm id that the UI reads back from `/api/hid/status`.

## Flipper bridge

The quick-action row intentionally uses only commands that already exist in the dashboard's Flipper allowlist. The buttons remain disabled until FOOT/BOTH mode is active and the Flipper service is live.

## Deployment to the Pi

From a terminal on the Pi, or via SSH from a LAN machine:

```bash
cd /home/kali/drifter
git fetch origin
git checkout main
git pull --ff-only
sudo ./scripts/oneshot.sh --skip-apt
```

`--skip-apt` now skips only the expensive system package upgrade. It still deploys current source, rebuilds the V4 cockpit, copies the build into `/opt/drifter/ui/v4`, restarts services, settles back into the persisted persona, and runs the final control-surface checks.

The final deploy stage must get HTTP 200 from:

- `/healthz`
- `/`
- `/api/mode`
- `/api/arsenal`
- `/api/hid/status`
- `/api/flipper/status`

A missing V4 `index.html` or a broken control dependency makes the deploy fail instead of silently falling back and claiming success.

## Verify locally

```bash
test -f /opt/drifter/ui/v4/index.html && echo 'cockpit-v4 deployed'
drifter status
drifter mode status
curl -fsS http://127.0.0.1:8080/healthz | python3 -m json.tool
curl -fsS http://127.0.0.1:8080/api/arsenal | python3 -m json.tool
```

Then launch/focus the touchscreen kiosk with:

```bash
/opt/drifter/bin/launch-cockpit.sh
```

## Network control boundary

The current high-impact dashboard routes deliberately trust localhost and the DRIFTER rescue-hotspot subnet. The physical Pi touchscreen uses localhost and therefore works without weakening that boundary. A browser reaching the cockpit through an ordinary home-LAN address may be able to render the UI while control POSTs are refused by the local-peer ACL. Do not broaden that ACL without adding an authenticated control mechanism.

## Tempest

`Tempest` is **not mapped to a DRIFTER service or repository component yet**. No exact Tempest component was found in the DRIFTER repo or the known project context, so no command or service has been guessed or attached to that label. Add it only after its actual package/repository/service identity is known.

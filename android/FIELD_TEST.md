# Drifter Diagnostics — in-car deploy & field-test checklist

This is the copy-paste runbook for getting the Android app onto a phone and
shaking it down against the live node in the Jaguar. It assumes the Pi is
already deployed (`sudo ./scripts/oneshot.sh` → `DEPLOY: ok`) and the
`MZ1312_DRIFTER` hotspot is up.

---

## 1. Get the APK onto the phone

No Android Studio required — CI builds it on every push.

1. Open the repo's **Actions** tab → the latest **build debug apk** run on
   branch `claude/android-drifter-diagnostics-oi2js8` (or `main` once merged).
2. Wait for the green check, then scroll to **Artifacts** and download
   **`drifter-diagnostics-debug-apk`**. Unzip it — inside is `app-debug.apk`.
3. Sideload it:
   - **Easiest:** email/Drive/USB the `.apk` to the phone and tap it. Allow
     "install from this source" when prompted.
   - **adb:** `adb install -r app-debug.apk` from a machine with the phone in
     USB-debug mode.

> The debug APK is unsigned for Play but fine for sideloading. It installs
> alongside nothing else — package id `com.mz1312.drifter`.

### Maps tab (optional)

The Map tab needs a Google Maps key, which is **never** committed. Without one
the app runs fine and the Map tab shows a "no key in this build" banner. To
enable maps you must build with your own key (see `android/README.md` →
*Google Maps key*) and restrict it to `com.mz1312.drifter` + the debug signing
SHA-1. Every other tab works without it.

---

## 2. First-run setup (in the driveway is fine)

1. **Tether the phone to `MZ1312_DRIFTER`** Wi-Fi (WPA2 — PSK is in
   NetworkManager on the Pi, `nmcli --show-secrets connection show MZ1312_DRIFTER`).
   The phone gets a `10.42.0.x` lease; the Pi is the gateway `10.42.0.1`.
2. Launch **Drifter Diagnostics**. It defaults to host `10.42.0.1:8080`.
   - If the host is blank/wrong: **Settings → Detect on this Wi-Fi** fills it
     from the hotspot gateway.
3. Grant **notifications** when asked (needed for background health alerts).
4. *(Recommended)* **Settings → AI assistant brain** → paste a Claude API key.
   Stored encrypted in the Android Keystore. With a key, the assistant works
   **even when the Pi is unreachable** — exactly when you need it most. Without
   one it falls back to the Pi's on-board LLM (only reachable while the Pi is).
5. *(Recommended)* **Settings → Background alerts** → toggle on. The node gets
   watched ~every 15 min even with the app closed; you get a notification the
   moment it degrades or drops.

---

## 3. Smoke test — what "good" looks like

Work top to bottom; each should pass before you trust the next.

| # | Action | Expected |
|---|---|---|
| 1 | App-bar **pip** (top of every tab) | Green/connected once on the hotspot |
| 2 | **Overview** tab | `status: ok` or `ok-hw-pending`; mode shown (`diag`/`drive`/`foot`); service counts |
| 3 | **Run Connection Doctor** (Overview) | 8080 PASS; 8081/8082 PASS or WARN; 1883 **closed = correct** (loopback-only since the 2026-05-18 hardening — not a fault) |
| 4 | **Services** tab | Units grouped by domain; failed (503) vs hw-pending distinguished. Tap one → recent `journalctl` tail loads (`/api/logs/<unit>`) |
| 5 | **Telemetry** tab (engine running) | Gauges move — RPM/coolant/speed/battery; sparklines fill. Needs OBD-II dongle + `drifter-canbridge` up |
| 6 | **Map** tab (if key built in) | Fix pulse + drive path once `drifter-gps` has a sky view; otherwise "no GPS fix yet" banner |
| 7 | **Assistant** tab | Ask "why can't I reach the dashboard?" — it should pull `/healthz` + logs on its own and answer with evidence |
| 8 | **Restart a service** (Services) | Succeeds on the hotspot; node's own 403 (off-subnet) / 409 (wrong mode) surfaced verbatim if it refuses |

---

## 4. The connection-trouble path (the whole point of this app)

If the cockpit at `10.42.0.1:8080` won't load in a browser, the app is still
useful — that's the design:

1. **Connection Doctor** runs from the phone even when the node is totally
   unreachable. It tells you *which* port is dead and a plain-English next step.
2. **Assistant** with a Claude key keeps reasoning while the Pi is down — ask it
   what to check. It can't run Pi-side tools without the Pi, but it knows the
   architecture and walks you through cable/power/hotspot triage.
3. **Detect on this Wi-Fi** re-derives the host if the Pi came up on a different
   lease.

Common real-world cause, fastest check first:
- **Phone not actually on `MZ1312_DRIFTER`** (auto-rejoined home/carrier) → re-tether.
- **Pi still booting / dongle re-enumerating** → `ok-hw-pending` is normal for a minute.
- **Pi wedged** → Doctor shows everything closed; power-cycle the node.

---

## 5. Reporting back

If something's off, the two highest-signal artifacts to capture:
- The **Connection Doctor** verdict (screenshot).
- The failing service's **log tail** from the Services tab (long-press →
  share, or screenshot).

Both are exactly what the assistant needs to diagnose, and what closes the loop
on a fix.

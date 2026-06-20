# Drifter Diagnostics — Android companion app

A field-diagnostics phone app for the **DRIFTER** Raspberry Pi telemetry node
(MZ1312 UNCAGED TECHNOLOGY). The Pi runs **headless** in the Jaguar, so when
something goes wrong there is no screen to look at — this app is the operator's
window into the node from the tethered phone.

It is **not** a re-skin of the web dashboard. The cockpit at `10.42.0.1:8080`
shows you telemetry *once you can reach it*. This app's job is the other half:
**figure out why you can't reach it, and help you fix it** — plus give honest
visibility into the services, the Kali-backed arsenal, and the live bus while
the node is running with no display attached.

## What it does that the dashboard can't

| Feature | Why it matters headless |
|---|---|
| **Connection Doctor** | Probes every port the Pi should expose (8080/8081/8082/8443/35000/1883) **from the phone**, with latency + a plain-English verdict and fix steps. Runs even when the node is totally unreachable — the one diagnostic the web UI can never give you. |
| **Service triage** | Parses `/healthz`, groups all units by domain, and distinguishes *failed* (real fault, HTTP 503) from *hardware-pending* (dongle not plugged in — expected on the bench). Per-service remediation baked in from `CLAUDE.md`/`config.py`. |
| **Remote control** | Switch persona (`diag`/`drive`/`foot`/`both`) and start/stop/restart arsenal units over the gated `/api` surface — and it surfaces the node's own refusals (403 off-subnet, 409 in drive mode) instead of failing silently. |
| **Arsenal / Carsenal** | Read-only status fan-out for the Kali-backed red-team + CAN-offense tooling (CAN discovery & captures, Flipper, Marauder, Kismet, HID, Ghost), with the foot-mode gate enforced. |
| **Live telemetry** | Opens the `ws://host:8081` fan-out — confirming data actually flows end-to-end is itself a diagnostic, separate from "is the port open". |
| **Knows the gotchas** | E.g. MQTT 1883 is loopback-only since the 2026-05-18 hardening, so "closed from the phone" is reported as **correct**, not a fault — a naive port scan would cry wolf. |

## Architecture

```
data/
  model/        Healthz, ModeInfo, results, TelemetryEvent, ApiResult
  net/          DrifterApi (OkHttp), ConnectionDoctor (TCP probes), TelemetrySocket (WS)
  store/        SettingsStore (DataStore: host/ports/poll)
  Knowledge.kt  per-service role + remediation + hw-pending/Kali tags
  DrifterRepository.kt
ui/
  DrifterViewModel.kt   one AndroidViewModel: polling, health, doctor, arsenal, telemetry
  overview/  doctor/  services/  arsenal/  telemetry/  settings/
  common/    Loadable, reusable Compose components
  theme/     MZ1312 amber-on-black Material 3 theme
```

- **Kotlin + Jetpack Compose (Material 3)**, single-Activity, bottom-nav.
- **OkHttp** for HTTP + WebSocket, **kotlinx.serialization** for JSON,
  **DataStore** for settings. No Hilt — a tiny hand-rolled `AppContainer`.
- Talks plain **HTTP** to the dashboard (all `/api` is server-gated to
  `127.0.0.1` + `10.42.0.0/24`, so the phone must be on the `MZ1312_DRIFTER`
  hotspot). `/healthz` is not gated and works from any reachable network.

## Build

Requires the **Android SDK** (API 35) — open the `android/` folder in Android
Studio (Ladybug or newer) and Run, or from the CLI:

```bash
cd android
./gradlew assembleDebug          # APK at app/build/outputs/apk/debug/
./gradlew installDebug           # install to a connected phone
```

The Gradle wrapper (8.9) is committed. Toolchain: AGP 8.7, Kotlin 2.1,
compileSdk 35, minSdk 26 (Android 8.0+).

> This module was scaffolded in an environment without the Android SDK, so it
> ships unbuilt. First build downloads the SDK/deps via Gradle as usual.

## Using it

1. Tether the phone to the **MZ1312_DRIFTER** Wi-Fi.
2. Launch the app — it defaults to `10.42.0.1:8080`. Change the host in
   **Settings** if the Pi is on another LAN.
3. **Overview** shows node health at a glance; if it's unreachable, tap
   **Run Connection Doctor** for the port-by-port verdict.

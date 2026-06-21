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
| **AI Assistant** | A free-form troubleshooting chat — *not* a fixed checklist. It gathers a live snapshot (health + port probes + **real `journalctl` logs** of failing services) and an LLM reasons over it, so it can help with faults nobody pre-wrote a fix for. Backed by **Claude** (works even when the Pi is down) with the **Pi's on-board LLM** as fallback. |
| **Service triage** | Parses `/healthz`, groups all units by domain, and distinguishes *failed* (real fault, HTTP 503) from *hardware-pending* (dongle not plugged in — expected on the bench). Per-service remediation baked in from `CLAUDE.md`/`config.py`. |
| **Remote control** | Switch persona (`diag`/`drive`/`foot`/`both`) and start/stop/restart arsenal units over the gated `/api` surface — and it surfaces the node's own refusals (403 off-subnet, 409 in drive mode) instead of failing silently. |
| **Arsenal / Carsenal** | Read-only status fan-out for the Kali-backed red-team + CAN-offense tooling (CAN discovery & captures, Flipper, Marauder, Kismet, HID, Ghost), with the foot-mode gate enforced. |
| **Live telemetry** | Opens the `ws://host:8081` fan-out — confirming data actually flows end-to-end is itself a diagnostic, separate from "is the port open". |
| **Knows the gotchas** | E.g. MQTT 1883 is loopback-only since the 2026-05-18 hardening, so "closed from the phone" is reported as **correct**, not a fault — a naive port scan would cry wolf. |

## Architecture

```
data/
  model/        Healthz, ModeInfo, results, TelemetryEvent, LogsResponse, Chat, ApiResult
  net/          DrifterApi (OkHttp), ConnectionDoctor (TCP probes), TelemetrySocket (WS),
                AssistantClient (Anthropic Messages API over OkHttp)
  store/        SettingsStore (DataStore: host/ports/poll + Claude key/model)
  Knowledge.kt        per-service role + remediation + hw-pending/Kali tags
  AssistantEngine.kt  system prompt (embedded architecture) + live-snapshot builder
  DrifterRepository.kt
ui/
  DrifterViewModel.kt   one AndroidViewModel: polling, health, doctor, arsenal, telemetry, chat
  overview/  doctor/  assistant/  services/  arsenal/  telemetry/  settings/
  common/    Loadable, reusable Compose components
  theme/     MZ1312 amber-on-black Material 3 theme
```

### The AI Assistant brain

The assistant turns the app from a fixed lookup table into something that can
diagnose *anything*, because it reasons over live evidence instead of a
hard-coded list:

- **Evidence**: each turn the app gathers `/healthz`, the Connection Doctor port
  probes, and the last journal lines of any genuinely-failed service via a new
  read-only **`GET /api/logs/<unit>`** endpoint on the Pi (added in
  `src/web_dashboard_handlers.py`; same `10.42.0.0/24` gate as the rest of
  `/api`, allowlisted to known units, `journalctl` invoked with a literal arg
  vector — never a shell).
- **Brain**: with a Claude API key set (Settings), the cloud model answers and
  keeps working even when the Pi is unreachable; on any cloud failure — or with
  no key — it falls back to the Pi's own on-board LLM (`POST /api/query`).
- **Client**: `AssistantClient` calls `POST https://api.anthropic.com/v1/messages`
  directly over OkHttp (the official Anthropic *Java* SDK targets the server JVM
  and is a poor fit on Android). Default model `claude-opus-4-8`, adaptive
  thinking, no sampling params, with `stop_reason: "refusal"` handled. The key
  is stored on-device in DataStore only.

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
4. **(Optional) Settings → AI assistant brain**: paste a Claude API key to
   enable the cloud brain (recommended — it works even when the Pi is down).
   Without a key the **Assistant** tab still works via the Pi's on-board LLM,
   but only while the Pi itself is reachable.

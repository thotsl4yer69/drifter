# Drifter Diagnostics — Android companion app

A field-diagnostics phone app for the **DRIFTER** Raspberry Pi telemetry node
(MZ1312 UNCAGED TECHNOLOGY). The Pi runs **headless** in the Jaguar, so when
something goes wrong there is no screen to look at — this app is the operator's
window into the node from the tethered phone.

It is **not** a re-skin of the web dashboard. The cockpit at `10.42.0.1:8080`
shows you telemetry *once you can reach it*. This app is the other half:
**figure out why you can't reach it, fix it, and have the node watch itself** —
proactive, agentic, and instrument-grade.

## What it does that the dashboard can't

| Feature | Why it matters headless |
|---|---|
| **Proactive alerts** | A background `WorkManager` job checks the node ~every 15 min even when the app is closed, and pushes an Android notification the moment it goes **offline / degrades / recovers** — staying silent on harmless "dongle not plugged in" states. Turns "open to check" into "it tells you." |
| **Agentic AI assistant** | A free-form troubleshooting chat that **investigates on its own**: it runs the Anthropic tool-use loop with read-only tools (`get_logs`, `get_healthz`, `get_telemetry`) and pulls the exact evidence it needs — not just a pre-bundled snapshot. Backed by **Claude** (works even when the Pi is down) with the **Pi's on-board LLM** as fallback. One-tap **"Diagnose with AI"** from any problem. |
| **Connection Doctor** | Probes every port the Pi should expose (8080/8081/8082/8443/35000/1883) **from the phone**, with latency + a plain-English verdict and fix steps. Runs even when the node is totally unreachable — the one diagnostic the web UI can never give you. |
| **Cockpit telemetry** | The live `ws://host:8081` fan-out rendered as a real instrument cluster: Canvas arc **gauges** (RPM, coolant, speed, battery, fuel, load, throttle, intake) with green→amber→red threshold bands and a rolling **sparkline** trend under each. |
| **Service triage + logs** | Parses `/healthz`, groups units by domain, distinguishes *failed* (HTTP 503) from *hardware-pending* (expected on the bench), and shows each service's recent `journalctl` tail on demand. Restart / mode-switch over the gated `/api`, surfacing the node's own refusals (403 off-subnet, 409 in drive mode). |
| **Arsenal / Carsenal** | Read-only status fan-out for the Kali-backed red-team + CAN-offense tooling (CAN discovery & captures, Flipper, Marauder, Kismet, HID, Ghost), foot-mode gate enforced. |
| **Trust + onboarding** | Claude key stored **encrypted** (Android Keystore), one-tap **"Detect node on this Wi-Fi"** (the hotspot gateway is the Pi), and a live **link pip** in the app bar on every tab. |
| **Knows the gotchas** | E.g. MQTT 1883 is loopback-only since the 2026-05-18 hardening, so "closed from the phone" is reported as **correct**, not a fault — a naive port scan would cry wolf. |

## Architecture

```
data/
  model/        Healthz, ModeInfo, LogsResponse, PiQueryResponse, Chat, TelemetryEvent, ApiResult
  net/          DrifterApi (OkHttp), ConnectionDoctor (TCP probes), TelemetrySocket (WS),
                AssistantClient (Anthropic Messages API + tool-use loop), NetworkInspector
  alerts/       HealthWatchWorker (CoroutineWorker), HealthWatch (scheduler),
                AlertNotifier (channel + notification), AlertState (transition dedupe)
  store/        SettingsStore (DataStore), SecureStore (Keystore-encrypted API key)
  Knowledge.kt        per-service role + remediation + hw-pending/Kali tags
  AssistantEngine.kt  system prompt (embedded architecture) + snapshot + tool defs
  DrifterRepository.kt  + the agentic tool executor
ui/
  DrifterViewModel.kt   one AndroidViewModel: polling, health, doctor, services, logs,
                        arsenal, telemetry (+ history), chat
  overview/ doctor/ assistant/ services/ arsenal/
  telemetry/  Gauge.kt (Canvas arc gauge + sparkline) + screen
  settings/
  common/    Loadable, reusable "glass" Compose components
  theme/     MZ1312 "graphite glass" design system (Color/Type/Theme)
```

### The agentic AI assistant

The assistant diagnoses *anything* because it reasons over live evidence and can
**fetch more on demand** — not a hard-coded checklist:

- **Tools**: it runs the Anthropic tool-use loop (`AssistantClient`, capped at 6
  steps). On `stop_reason: "tool_use"` it echoes the assistant turn verbatim
  (preserving adaptive-thinking blocks), runs the requested read-only tool, and
  returns the result — `get_logs(service)` (any unit, via the Pi's
  **`GET /api/logs/<unit>`** endpoint), `get_healthz`, `get_telemetry`.
- **Seed snapshot**: each conversation starts with `/healthz`, the Connection
  Doctor probes, and the logs of any already-failed service.
- **Brain**: with a Claude API key (Settings) the cloud model answers and keeps
  working even when the Pi is unreachable; on any cloud failure — or with no key
  — it falls back to the Pi's own on-board LLM (`POST /api/query`).
- **Client**: `AssistantClient` calls `POST https://api.anthropic.com/v1/messages`
  directly over OkHttp (the official Anthropic *Java* SDK targets the server JVM
  and is a poor fit on Android). Default model `claude-opus-4-8`, adaptive
  thinking, no sampling params, `stop_reason: "refusal"` handled. The key is
  stored **encrypted** in the Android Keystore (`SecureStore`), not plaintext.

### Pi-side additions

The app drives the existing dashboard contract plus one additive, gated,
tested endpoint: **`GET /api/logs/<unit>`** (read-only `journalctl` tail; same
`10.42.0.0/24` ACL; allowlisted to monitored units; literal arg vector — never a
shell). See `src/web_dashboard_handlers.py` and `tests/test_web_dashboard_handlers.py`.

### Stack

- **Kotlin + Jetpack Compose (Material 3)**, single-Activity, bottom-nav,
  dark-first instrument theme.
- **OkHttp** (HTTP + WebSocket + Anthropic API), **kotlinx.serialization**,
  **DataStore** + a direct **Android Keystore** AES-256-GCM helper for the
  encrypted key (no maintenance-mode `security-crypto`), **WorkManager**
  (background watch). No Hilt — a tiny hand-rolled `AppContainer`.
- Talks plain **HTTP** to the dashboard (all `/api` is server-gated to
  `127.0.0.1` + `10.42.0.0/24`, so the phone must be on the `MZ1312_DRIFTER`
  hotspot). `/healthz` is not gated and works from any reachable network.

## Build

CI builds a debug APK on every push (`.github/workflows/android-build.yml`) and
uploads it as the **`drifter-diagnostics-debug-apk`** artifact on the run — grab
it from the Actions tab, no Android Studio needed.

To build locally (requires the **Android SDK**, API 35):

```bash
cd android
./gradlew assembleDebug          # APK at app/build/outputs/apk/debug/
./gradlew installDebug           # install to a connected phone
```

Gradle wrapper 8.9 is committed. Toolchain: AGP 8.7, Kotlin 2.1, compileSdk 35,
minSdk 26 (Android 8.0+). CI also runs `testDebugUnitTest` as a gate.

### Google Maps key (for the Map tab)

The key is injected from a Gradle property into the manifest — **never
committed**. Without it the app still builds and runs; only the Map tab can't
load tiles (it shows a banner saying so). To enable maps, supply your key one of:

```properties
# ~/.gradle/gradle.properties  (recommended for local builds)
MAPS_API_KEY=AIza...your-key...
```

```bash
./gradlew assembleDebug -PMAPS_API_KEY=AIza...   # one-off
# or, e.g. in CI:  export ORG_GRADLE_PROJECT_MAPS_API_KEY=AIza...
```

Mint it in Google Cloud (Maps SDK for Android) and **restrict it** to the
`com.mz1312.drifter` package + your signing cert's SHA-1 so a leaked copy is
useless.

## Using it

1. Tether the phone to the **MZ1312_DRIFTER** Wi-Fi.
2. Launch the app — it defaults to `10.42.0.1:8080`. Or open **Settings** and tap
   **Detect on this Wi-Fi** to fill the host from the hotspot gateway.
3. The **app-bar pip** shows the link state on every tab; **Overview** has the
   detail. If it's unreachable, tap **Ask the assistant what's wrong** or **Run
   Connection Doctor**.
4. **Settings → AI assistant brain**: paste a Claude API key (stored encrypted)
   for the cloud brain — recommended, it works even when the Pi is down. Without
   a key the assistant uses the Pi's on-board LLM (reachable only while the Pi
   is).
5. **Settings → Background alerts**: toggle on to have the node watched in the
   background and be notified when it degrades or drops.

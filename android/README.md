# DRIFTER Diagnostics — Android Companion

**Native Android diagnostics and control surface for the DRIFTER Raspberry Pi vehicle node.**

> **Status: Supporting software prototype.** This app is part of the wider [DRIFTER](../README.md) hardware-integrated prototype. It helps inspect node health, telemetry and logs from a tethered phone; it is not represented as an instrument-certified diagnostic tool or a universal vehicle-repair system.

## Why it exists

The DRIFTER Pi normally runs headless. The Android companion provides a local operator interface when a browser dashboard alone is not enough — especially when the phone needs to determine whether the node is reachable before the normal dashboard can be used.

The app focuses on:

- node health and reachability;
- connection diagnostics;
- live telemetry presentation;
- service status and bounded administrative actions;
- read-only log retrieval;
- optional AI-assisted troubleshooting based on the evidence the app can retrieve.

## Architecture

```text
Android phone
    │
    ├── HTTP health/API requests
    ├── WebSocket telemetry
    └── optional cloud AI request
            │
            ▼
    DRIFTER Raspberry Pi
      health / logs / telemetry
            │
            ▼
      bounded operator UI
```

Repository layout:

```text
data/
  model/       API and telemetry models
  net/         HTTP/WebSocket clients and connection diagnostics
  alerts/      WorkManager health checks and notifications
  store/       DataStore + Android Keystore-backed secret storage
  Knowledge.kt
  AssistantEngine.kt
  DrifterRepository.kt

ui/
  overview/
  doctor/
  assistant/
  services/
  telemetry/
  settings/
  common/
  theme/
```

## Main capabilities

### Connection Doctor

Probes the expected DRIFTER interfaces from the phone and reports which paths are reachable. A failed probe is evidence about connectivity, not proof of a specific hardware or software fault.

### Telemetry UI

Consumes the node's live telemetry WebSocket and renders driver-facing values and trends. The visual thresholds are operational UI aids; they should be reconciled with the active vehicle profile and deterministic rule engine rather than treated as manufacturer diagnostic specifications.

### Service health and logs

Reads `/healthz` and the bounded log API exposed by the Pi. Hardware-pending services can be distinguished from actual failures where the node reports that state.

Administrative actions are subject to the Pi-side access controls and operating-mode gates. The Android application should surface server refusals rather than attempting to bypass them.

### Background health checks

A WorkManager job can periodically check node health and notify on meaningful state transitions. Android scheduling is best-effort; the operating system may defer background work based on power and app state.

### Optional AI-assisted troubleshooting

The application can give an AI assistant a bounded set of **read-only evidence tools**, such as health, telemetry and allowed log retrieval. The assistant may request additional evidence through those tools before responding.

This is a troubleshooting aid, not an authority. Model output can be wrong, incomplete or based on stale data. Vehicle safety and repair decisions should be verified against the actual vehicle, service information and deterministic evidence.

If a cloud AI provider is configured, its API key is stored using Android Keystore-backed application storage rather than committed to this repository. If the cloud path is unavailable, supported builds can fall back to the Pi-side local assistant path where configured.

Provider model identifiers are configuration details and may change independently of this repository; do not treat an old README model name as a permanent compatibility guarantee.

## Security and trust boundaries

- real API keys must never be committed;
- the Google Maps key, when used, is injected at build time;
- cloud-AI credentials are entered by the user and stored through Android Keystore-backed storage;
- sensitive node operations remain gated on the Pi/server side;
- read-only diagnostics should remain read-only even if the assistant requests a different action;
- network reachability is not authentication by itself.

The repository uses examples such as `AIza...` only as placeholders. Supply real keys through local Gradle properties or CI secrets and restrict them to the intended API/application/signing identity.

Example local configuration:

```properties
MAPS_API_KEY=your-restricted-android-maps-key
```

Do not paste live keys into documentation, source or command history that will be committed.

## Stack

- Kotlin + Jetpack Compose / Material 3;
- OkHttp for HTTP/WebSocket integration;
- kotlinx.serialization;
- DataStore;
- Android Keystore-backed encrypted application secret storage;
- WorkManager;
- hand-rolled application container rather than a mandatory DI framework.

## Build

From the `android` directory:

```bash
./gradlew assembleDebug testDebugUnitTest
```

The debug APK is produced under the normal Gradle output path in `app/build/outputs/apk/debug/`.

The repository CI also builds/tests the Android project. Treat a green compile/unit-test run as software evidence, not proof that every phone/Pi/vehicle/network combination has been field validated.

## Typical local use

1. Connect the phone to the DRIFTER network used by the test node.
2. Open the app and confirm the node address in Settings.
3. Check the link/health state.
4. Use Connection Doctor if the normal dashboard path is unavailable.
5. Inspect health, logs and telemetry before taking any administrative action.
6. Configure the optional AI assistant only if you want that external/local model path enabled.

See [`FIELD_TEST.md`](FIELD_TEST.md) for the repository's field-test workflow.

## Evidence boundary

This application demonstrates **native Android + edge-node integration**, not certified automotive diagnostics. It cannot guarantee that a Pi is healthy simply because a port responds, that a vehicle fault is correctly diagnosed by an AI model, or that a background Android task will run at an exact interval.

The Pi's deterministic diagnostics, actual OBD data and physical vehicle remain the sources that need verification.

## Development provenance

The DRIFTER Android companion is part of an authored MAZLABZ project developed with AI coding agents as part of the normal engineering workflow. AI assistance supports implementation, research, refactoring and testing; architecture, integration, access boundaries, field testing and acceptance remain the project owner's responsibility.

## Portfolio value

The app demonstrates the ability to build a **native mobile operator interface around a real edge system**: Android networking, WebSockets, background work, secure local secret handling, service diagnostics and live telemetry UI all connected to a Raspberry Pi deployment.

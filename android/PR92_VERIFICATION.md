# PR #92 compatibility verification

Verified locally on 15 October 2026 against PR head `f8028eedb0f23984f11045454125ca6e264bb3c8` plus this commit's changes.

## Architecture and compatibility

The existing APK (`com.mz1312.drifter`, launcher label **Drifter Diagnostics**) still opens `MainActivity` and its original DRIFTER navigation. Overview opens a non-exported `JaguarMedicActivity` in the same task. The back arrow and system Back finish Medic; the DRIFTER activity, settings and original routes remain intact. Speech-recognition visibility and the notification entry point are preserved. Medic retains its existing preferences, repair log, incident storage, ELM327 transports, workshop API and offline reference.

Medic now cancels in-flight connections/scans on exit, rejects late callbacks and saves partial incident captures. ELM commands are serialized so a live poll cannot consume a DTC response. Echo removal no longer corrupts P0301; invalid responses are not reported as an all-clear. The polling suspend-call compile defect is fixed. DRIFTER also observes telemetry settings correctly, renders readable card text and avoids constructing Google Maps without a key.

## Local gates

Environment: Gradle 8.9 (the wrapper's version), Temurin JDK 17.0.20.1, Android SDK 35, build tools 34.0.0; Robolectric API 34 native graphics.

Equivalent project command:

```sh
cd android
./gradlew testDebugUnitTest assembleDebug lintDebug --no-daemon
```

| Gate | Result |
|---|---|
| Kotlin/Java compilation and debug APK assembly | PASS |
| DRIFTER launcher, all eight destinations, settings updates and recreation | 4 tests PASS |
| Medic tabs, adapter controls, recreation and both exit controls | 4 tests PASS |
| Diagnostic engine | 3 tests PASS |
| Scripted TCP ELM327 measurements, concurrent scan, P0301/P1313/P1646, invalid/no-data responses | 3 tests PASS |
| Workshop API/fallback, persistence, invalid host and partial incident from a real local WebSocket | 4 tests PASS |
| Android lint | 0 errors; 46 advisory warnings |
| Render inspection | DRIFTER Overview, Medic CAR and LIVE at 411 × 891 dp inspected |
| Packaged APK identity | Original package, label and MainActivity launcher confirmed with aapt |

18 tests, 0 failures, 0 errors. Final combined Gradle run: **BUILD SUCCESSFUL**, 55 actionable tasks (53 executed, 2 from cache).

Local debug APK SHA-256: `561099b9f98ce20e633d5621292818c61c53ab8abb4015fbd232d96b0bbb0db0`. This identifies the locally built APK, not a distributed or device-validated release. CI uploads the regression reports/render captures and blocks on test, assembly and lint errors.

## Remaining gates — keep draft and unmerged

GitHub Actions run [38206702310](https://github.com/thotsl4yer69/drifter/actions/runs/38206702310) did not start. Its check annotation states: “The job was not started because your account is locked due to a billing issue.” A passing local build is not a passing GitHub CI result. Resolve that account restriction and obtain a successful Android run on the updated PR before considering merge.

No physical phone, Bluetooth ELM327, Jaguar ECU, deployed DRIFTER node, real workshop server or live Google Maps tiles was exercised. TCP/HTTP/WebSocket integration tests use local scripted servers; UI tests run under Robolectric. Full 90-second-before/45-second-after capture timing and on-car operation remain field verification, not claims established by these tests.

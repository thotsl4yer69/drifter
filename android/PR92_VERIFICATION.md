# PR #92 compatibility verification

Verified locally on 15 October 2026 against `20d684d9b1fb1b4ec0592ae6d8c2e8ba42774168` plus this follow-up commit's changes.

## Architecture and compatibility

The existing APK (`com.mz1312.drifter`, launcher label **Drifter Diagnostics**) still opens `MainActivity` and all eight original DRIFTER destinations. Overview opens a non-exported `JaguarMedicActivity` in the same task. The back arrow and system Back finish Medic. Existing DRIFTER settings, notification entry point and speech-recognition visibility remain intact. Medic retains its preferences, repair log, incident storage, Bluetooth/Wi-Fi ELM327, workshop API and offline reference.

## Follow-up fixes

- Keep separate ECU DTC replies separate, interpret CAN's DTC-count byte, and validate numbered CAN frames. Truncated, ambiguous, malformed and NO DATA replies cannot become an all-clear or invented fault codes. Poll/scan command serialization is retained.
- Finish captures using a 45-second timer even if readings stop. Bound history to 90 seconds using elapsed time, reject empty captures, save partial captures on disconnect, clear stale readings, and write incident files atomically. Reject non-finite incoming telemetry.
- Prevent delayed workshop searches/diagnoses from replacing a newer query. Offline results remain available on failure.
- Validate Wi-Fi ports instead of silently substituting the default.
- Correct certificate resource metadata, remove redundant/unused resources, move adaptive icons into the minimum-SDK resource directory, use mirrored icons and remove deprecated system-bar setters.
- Update AndroidX Test to 1.7.0. CI also assembles the unsigned release variant and retains lint reports.

## Verified local gates

Environment: Gradle 8.9, Android Gradle plugin 8.7.3, Temurin JDK 17.0.20.1, compile/target SDK 35, minimum SDK 26, build tools 34.0.0; Robolectric API 34 native graphics.

Final verification command (machine-specific SDK/proxy locations were configured outside git):

```sh
cd android
./gradlew clean testDebugUnitTest assembleDebug assembleRelease lintDebug --no-daemon --no-build-cache --max-workers=1
```

| Gate | Result |
|---|---|
| DRIFTER launcher, all original destinations, settings updates and recreation | 4 tests PASS |
| Medic tabs, adapter controls, invalid port, recreation and both exit controls | 5 tests PASS |
| Diagnostic engine | 3 tests PASS |
| Real local TCP ELM327 polling/concurrent scan, K-line and numbered CAN replies | 4 tests PASS |
| DTC parser: separate ECUs, CAN count, frame assembly, malformed/truncated responses | 4 tests PASS |
| Workshop fallback/races, persistence, connection loss, timer/partial capture, input validation | 10 tests PASS |
| Capture windows: 90 seconds before / 45 after, stale history, clock changes and session boundaries | 4 tests PASS |
| Kotlin/Java compilation, debug APK and unsigned release APK assembly | PASS |
| Release runtime-profile compilation and release vital lint | PASS |
| Debug lint | 0 errors; 1 warning: InsecureBaseConfiguration |
| Native-render inspection | DRIFTER Overview, Medic CAR and LIVE at 411 × 891 dp inspected |
| Packaged identity and debug signature | Original package/label/MainActivity confirmed; APK v2 signature verified |

**34 tests, 0 failures, 0 errors. BUILD SUCCESSFUL in 4m 41s; 104 actionable tasks (103 executed, 1 up-to-date).**

An initial incremental compile reported duplicate functions despite single source definitions. An incremental release runtime-profile task also failed once with “Failed requirement”; its isolated rerun passed. The final fresh build above passed with no cached task outputs. No test, compilation or profile task was disabled. Native-library stripping reports that two dependency libraries are packaged unchanged; this is not a compilation failure.

APK SHA-256 values:

- Debug: `231020ae8706f58447f892bea22719bf7aa8d944d87cbcdff2152c13a8ac00e0`
- Unsigned release: `faacee3f38775d9a5299f93533a2097d418c38fd5e952a2f4671254f87c4738c`

The supplied debug APK is for device testing. The unsigned release build is compilation/packaging evidence, not a signed production release.

## Warnings and dependency scope

The final lint report has one visible warning: app-wide cleartext networking. Configurable LAN HTTP/WebSocket endpoints require this for the existing companion. Removing it would break those connections; it has not been suppressed. Use these endpoints on a trusted vehicle network.

Certificate spell-check false positives and redundant resources are corrected. AndroidX/Compose production versions remain pinned to the verified SDK 35 / AGP 8.7 toolchain. Online dependency-update notices can vary with metadata availability; their absence from this final report does not mean every dependency is current. Current Compose requires a coordinated SDK/Gradle migration ([Android release notes](https://android-developers.googleblog.com/2026/08/jetpack-compose-august-2026-release.html)). Protocol interpretation follows the [ELM327 datasheet](https://www.elmelectronics.com/wp-content/uploads/2017/01/ELM327DS.pdf), including its distinction between K-line and CAN DTC formats.

## Remaining gates — keep draft and unmerged

GitHub Android run [38664071581](https://github.com/thotsl4yer69/drifter/actions/runs/38664071581) did not start. Its check annotation states: “The job was not started because your account is locked due to a billing issue.” Resolve that account restriction and obtain a successful Android run on the updated PR before considering merge. Local verification does not establish a passing hosted CI run.

No physical phone, Bluetooth ELM327, Jaguar ECU, deployed DRIFTER node, real workshop server or live Google Maps tiles was exercised. TCP/HTTP/WebSocket tests use local scripted servers; UI tests use Robolectric. Capture-window boundaries and timer completion are now verified with controlled time, not a physical road test or Android background/power-management validation. Keep the PR draft and unmerged pending those external acceptance checks.

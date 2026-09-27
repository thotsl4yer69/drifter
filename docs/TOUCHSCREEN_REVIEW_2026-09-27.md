# DRIFTER touchscreen continuation — 27 September 2026

Status: implemented source changes on PR #86; NOT physical or release sign-off.
Base reviewed: `bb741a571058a2d9d688ec0575526b67de7de3d7`.

## Implemented

The existing cockpit-v4 shell now uses a dedicated LEDGER DRIVE panel and accessible seven-workspace navigation. Screens at least 700px wide and at most 600px high place navigation below the instruments. DRIVE, MAP, DIAG, RF, FOOT, VIVI and SYSTEM remain available; RF/FOOT mode gates are preserved. Primary navigation, appearance, DATA and diagnostic-summary touch targets are at least 58 CSS pixels in the component checks. These are not physical millimetre measurements.

DAY / NIGHT / AMBER changes the existing display theme, not hardware backlight brightness. DATA is a native modal with explicit Tab wrapping, Escape/close dismissal, scrollable content and focus restoration. Trip/Vivi detail remains in DATA when the compact DRIVE panel hides its supplementary rows. Persisted surface selection is validated and storage exceptions are caught.

DRIVE speed is correctly labelled OBD and does not require a GPS fix. Speed and RPM expire after 3 seconds without a new PID receipt; coolant and voltage after 10 seconds. Each has independent receipt evidence. Missing, stale or disconnected readings display a dash, not an invented zero. A real zero is preserved. Reconnect requires new PID receipts. REST cache and aggregate snapshots cannot refresh these timestamps. This measures receipt freshness, NOT acquisition time, vehicle accuracy or sensor correctness. Other pre-existing workspaces still use their original adapter values.

Perception never claims ROAD CLEAR. Detection/FCW/event displays expire after 2.5 seconds, reject old/invalid supplied source timestamps, and disappear on link loss. Online dashcam status is READY, not proof of RECORDING. An online vision service does not prove a camera or Hailo inference is operational.

## Important backend finding — still OPEN

In the reviewed `src/vision_engine.py`, both Hailo inference and the ONNX decoding path return empty lists. The service can publish online despite these placeholder paths. This UI change does not implement those paths. HAILO remains UNVERIFIED; empty detections or service online status cannot become a clear-road claim. End-to-end camera/inference integration and physical acceptance are still required. The perception panel is experimental context, not collision protection.

## Verification actually performed

- 54/54 dependency-free Node tests passed: pure display decisions and the actual adapter executed with mocked WebSocket/REST I/O.
- 49/49 local Chromium component checks passed: five viewport configurations (800x480, 800x600, 1024x600, 1280x720, 1280x800), primary target geometry, navigation callbacks, themes, modal focus/dismissal/scrolling, link loss, missing and stale PIDs, hazard expiry and reduced motion. No JavaScript exceptions in that exercise.
- Modified JSX parses/transpiles successfully with the locally installed TypeScript compiler. This is NOT a production Vite build or full type check.

Browser scope: actual new touchscreen.jsx components and display-state.js in an isolated synthetic-data harness, using locally available React 16.0.0 / ReactDOM 16.0.1 and a LEDGER CSS-token/legacy-rule fixture. Navigation callbacks were exercised; the seven complete production workspaces and the real LgRight drawer were NOT end-to-end tested. Production requires React 18 and its bundled fonts. The old-rule fixture was loaded after the new CSS to test specificity. Screenshots are component previews, not a Pi/live-vehicle capture.

## Re-run / remaining gates

From the repository root: `node --test cockpit-v4/tests/*.test.mjs` and `pytest tests/test_cockpit_touch_design.py -q`.

Before merge/deployment sign-off: run the real `cockpit-v4` dependency install and Vite build; inspect all workspaces using the actual production fonts/React version; test Pi browser startup, touch calibration and readable targets on the physical screen; perform adapter disconnect/reconnect and display recovery; complete the established 10 cold boots and 30-minute telemetry soak. Separately implement and verify the Hailo/camera inference path. No acceptance record was marked passed by this software-only review.

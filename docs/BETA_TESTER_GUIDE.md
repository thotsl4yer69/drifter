# DRIFTER VIM — Founding Beta Tester Guide

DRIFTER VIM is currently a **hardware-integrated prototype / active hardening build**. The founding beta exists to turn architectural compatibility into measured compatibility across real vehicle/adapter combinations.

## Who this is for

The beta is for testers comfortable with Raspberry Pi/Linux hardware who can run a controlled vehicle test and return evidence. It is not yet a plug-and-forget retail product.

## Minimum test kit

- Raspberry Pi 5 recommended;
- storage suitable for Linux + drive logs;
- supported display path or browser UI;
- one OBD transport at a time for first validation:
  - compatible ELM327 Bluetooth/Wi-Fi/serial path, or
  - SocketCAN-class adapter where appropriate;
- stable vehicle power for the Pi;
- access to a vehicle you own or are authorised to test.

## First validation sequence

1. Install current DRIFTER build using the repository deployment flow.
2. Start from a clean vehicle-link configuration.
3. Cold boot the node from the intended vehicle power path.
4. Use **FIELD OPS → VEHICLE → AUTO DETECT**.
5. Confirm the UI distinguishes:
   - adapter unreachable;
   - adapter connected / ECU waiting;
   - ECU online;
   - live PID flow.
6. Confirm live RPM, coolant, speed and voltage where supported.
7. Run a 30-minute stationary/road telemetry session.
8. Unplug/replug the OBD adapter and record recovery behaviour.
9. Trigger **CAPTURE INCIDENT NOW** and confirm an incident bundle is produced.
10. If the display blanks while Linux remains alive, use the display recovery action and record the result.

## Evidence to return

For each run capture:

- vehicle year/make/model/powertrain;
- exact OBD adapter;
- Pi/display hardware;
- DRIFTER commit SHA;
- negotiated protocol where known;
- cold boots passed / attempted;
- 30-minute telemetry result;
- adapter recovery result;
- display recovery result;
- incident-capture result;
- unsupported PIDs / degraded states;
- redacted `drifter field-dump` for failures.

Do **not** post VINs, API keys, Wi-Fi passwords, hotspot credentials or other secrets.

## Acceptance levels

### Observed
The vehicle/adapter combination has connected and returned at least one valid standard PID.

### Functional
Touchscreen onboarding, ECU proof and normal live telemetry work without shell intervention.

### Validated
The combination has passed the repeatability gate: cold boots, sustained telemetry, adapter recovery and incident capture.

Only **Validated** combinations should be promoted as tested compatibility.

## Report

Open the **DRIFTER VIM founding beta vehicle test** issue form and attach the evidence above. Keep one issue per unique vehicle + adapter combination.

# DRIFTER — in-car readiness candidate, 27 September 2026

## Verdict and scope

Prepared for controlled, parked validation after installation checks. NOT vehicle-signed-off, not a complete system certification, and not yet installed on the physical Pi by this review.

Baseline reviewed: `4383c3a92b71fb72518cdabf9f15e478c963c8df` (main). Work branch: `hardening/in-car-readiness-20260927`. The original field-acceptance source was reconstructed locally and its Git blob verified exactly against `9b5b1e3112e6e5c5a164586b148a2f49d58141a3`. The patched source blob is `3009cfa9c7d26cfcef137f51bdc6c4af4c55f6b9`.

This change targets the reliability of the field-acceptance gate. It does not claim to fix the physical root cause of failed cold starts, a white LCD, or a vehicle/ELM communication fault. Issue #67 remains open. The entire runtime, UI and Android application were not end-to-end tested in this review.

## Changes implemented

- `status --no-live` can show stored evidence but can never return live sign-off. Output distinguishes `stored_gates_ready`, `live_checked` and `signoff_ready`.
- The cold-boot gate counts consecutive recorded passing boots, not lifetime successes. A recorded failure cannot be erased by retrying within that same boot; the first failure is retained. A boot that never reaches Linux still requires an operator failure record and investigation.
- Soak arguments reject NaN/infinity, excessively relaxed data gaps and invalid durations. A short smoke run cannot satisfy the 1,800-second sign-off requirement.
- Retained telemetry, explicitly replay/demo/simulation-labelled samples, booleans and invalid/stale/future timestamps are not counted as live vehicle evidence. Unlabelled synthetic publishers cannot be distinguished reliably from real publishers; do not run replay or demo tools during vehicle testing.
- Broker disconnection, subscription failure, non-online OBD states, interruption and incomplete duration fail the soak. MQTT subscriptions are restored on reconnection, but that interrupted run remains failed.
- Live checks run before and after the soak. Evidence uses flushed, fsynced temporary-file replacement, rejects non-finite JSON values and has unique filenames.
- The installer checks the exact installed baseline and required shared imports, preserves a byte-exact backup, archives the old acceptance campaign, and changes only the acceptance script. It does not update the OS, restart services, alter OBD configuration, replace the dashboard or replace the phone app.

## Validation actually performed

40 targeted Python regression cases passed in a local isolated harness. Production acceptance logic was exercised; unavailable configuration/MQTT boundaries and hardware were replaced with test doubles. These are NOT real broker, Pi, ECU, display or road tests.

Five installer sandbox checks passed: read-only inspection; rejection of an unknown installed revision; successful apply with configuration preservation and evidence archive; repeat-apply idempotence; and byte-exact code rollback. Python compilation and Bash syntax checks passed.

The new GitHub field-readiness workflow attempts source/shell syntax, lint and the full regression suite, and preserves test evidence. Its first job (run `36274881787`, job `108495576465`) failed before any steps ran; no job logs were available. Cause was not established. Full repository CI and lint therefore remain unverified. Do not interpret this as a passing full suite or as an observed code-test failure.

## Apply to a matching existing Pi installation

Run only while parked, with no acceptance command currently running:

```bash
cd /home/kali/drifter && git fetch origin && git switch hardening/in-car-readiness-20260927 && sudo bash scripts/install-field-hardening.sh --apply
```

For a read-only compatibility check, use `--check` instead of `--apply`.

An unrecognised installed version produces `HOLD` before changing it. Do not bypass that check or use the older narrow `install-obd.sh` as a substitute for a reviewed full deployment: it copies selected shared modules and is not a complete application update. Preserve local modifications; do not use `git reset --hard` to force this candidate onto the Pi.

The installer prints the private backup path and a code-rollback command. Existing drive logs and configuration remain in place. Previous acceptance state is archived so this candidate starts a fresh campaign. Re-applying the identical patch does not reset newly collected evidence.

## Test sequence

### 1. Parked preflight

Use the intended vehicle power supply, secure the Pi/display/cables clear of controls and airbags, and run the engine only outdoors. Use diagnostics only: no ECU coding, DTC clearing, raw frame injection, replay/demo tools or experiments while driving. Do not road-test a vehicle that is stalling, overheating or otherwise mechanically unsafe.

```bash
sudo drifter obd test
sudo drifter display status
sudo drifter acceptance soak --seconds 60
```

The preflight must prove both adapter and ECU, a working operator display, critical services and clean Pi power flags. Speed = 0 while stationary is normal; missing data is not the same as zero. Stop at a failure and capture `sudo drifter field-dump` before repeated resets where the Pi remains reachable.

The Pi 5 requires an appropriate regulated 5 V supply. Raspberry Pi documents 5 V/5 A operation, or 5 V/3 A with a reduced USB peripheral limit; a vehicle supply must be assessed with the attached hardware and during cranking. Never connect raw vehicle 12 V to Pi 5 V pins. See the official Raspberry Pi computer hardware documentation, Power supply section.

### 2. Ten recorded cold boots

Use proper shutdown before each intentional power removal. Use the intended vehicle power source. Do not rescue a failed boot by replugging and then call it a successful first start.

For each genuine successful start, with ignition RUN and the adapter/ECU available:

```bash
sudo drifter acceptance cold-boot --no-replug
```

For an observed failed/rescued attempt, record a failure without `--no-replug`, add a note and keep the evidence. The release target is ten consecutive recorded passing boots, not ten successes selected from a larger failed series.

### 3. Recovery checks, while parked

Unplug/reconnect the ELM, confirm an actionable disconnected state and recovery without a Pi reboot. Test display recovery only while parked. Capture evidence before recovery:

```bash
sudo drifter field-dump
sudo drifter display recover
```

Only after actually observing successful recovery, record the relevant result:

```bash
sudo drifter acceptance mark elm-recovery --pass --note "Observed unplug/replug recovery without Pi reboot"
sudo drifter acceptance mark display-recovery --pass --note "Observed display recovery without Pi power cycle"
```

Use `--fail` with an honest note for an unsuccessful test. Do not paste a pass marker merely to unlock the gate.

### 4. Existing Android companion

Use the existing Drifter Diagnostics application. On the Pi's normal `MZ1312_DRIFTER` hotspot, set host `10.42.0.1`, HTTP `8080`, WebSocket `8081`. Do not assume the gateway of home Wi-Fi is the Pi. Verify telemetry, loss-of-link indication and reconnection after a phone Wi-Fi disconnect. The phone must not display old values as live.

For an ELM327 connection the selected vehicle bridge is `drifter-obdbridge`; raw CAN is not mandatory. GPS, maps, cloud AI and optional RF features are outside this core acceptance run. Periodic background phone checks are not an immediate safety-warning system.

### 5. Thirty-minute telemetry run

After stationary checks pass and the vehicle is mechanically sound, a passenger operates the equipment during any road portion. Start before moving:

```bash
sudo drifter acceptance soak
```

Require continuous RPM, coolant, speed and voltage evidence. Stop the test for an unexplained white display, reconnect loop, power fault or misleading stale data. Do not alter OBD wiring or operate setup controls while moving.

The command must complete its requested duration. A disconnected or interrupted run remains failed even if the system later recovers. This is intentional: recovery is tested separately from uninterrupted operation.

### 6. Review evidence while parked

```bash
sudo drifter acceptance status
```

`signoff_ready: true` requires the stored vehicle gates AND current live health. `--no-live` is an evidence-inspection mode only and returns nonzero for sign-off. Core acceptance logs are under `/opt/drifter/logs/acceptance/`. Inspect and redact logs before sharing; they may contain identifying vehicle or network information.

Do not close issue #67 until the actual hardware evidence meets its criteria. This candidate does not certify crash detection, driver assistance, autonomous control, or the reliability of the Android background alert service.

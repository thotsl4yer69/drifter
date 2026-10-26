# DRIFTER — vehicle deployment review, 27 October 2026

**Candidate:** `hardening/vehicle-install-gate-20261027` based on `main` at `2edf95336411d033470eeea72945c25f723d552f`.

**Decision:** software hardening candidate for *parked* Pi validation. Not vehicle field-signed-off, not a production release.

## Evidence reviewed

- Raspberry Pi master installer, one-shot deploy stages, mode persona CLI, update service/timer, update parser, ELM transport selection, acceptance harness, deployed UI procedure, and existing tests.
- Open [Jaguar field failure #67](https://github.com/thotsl4yer69/drifter/issues/67): failed cold starts, ELM adapter/ECU setup, white LCD/crash, missing physical sign-off.
- [PR #92](https://github.com/thotsl4yer69/drifter/pull/92): Android companion with separate X-Type Medic workspace built locally but is closed/unmerged with unresolved review findings and blocked hosted Android CI. Its APK is not included in this Pi candidate.
- [PR #93](https://github.com/thotsl4yer69/drifter/pull/93): main branch repaired an updater timer test syntax error on 23 October. No current physical sign-off is recorded.

## Fixed in this branch

1. **All-service deployment surge:** `oneshot.sh` previously restarted *every* monitored service then tried to settle the selected persona. For a Pi 5 this unnecessarily launched memory-heavy services and exposed unrelated services. It now resolves the saved mode, defaults to `diag`, stops out-of-mode units first, restarts only the selected set, and fails the deployment on an invalid mode.
2. **Reboot enabling mismatch:** direct `install.sh` previously enabled every service regardless of persona. It now enables only the mode-selected units, plus the three explicit boot/checkpoint auxiliary units; out-of-mode services are disabled/stopped.
3. **Unattended updates without hardware sign-off:** the 30-minute timer remains installed, but code replacement is opt-in via `DRIFTER_AUTO_UPDATE=1` (default `0`). `sudo drifter update` remains an explicit manual action that **does not bypass vehicle-state checks**.
4. **Bad stationary-data proof:** update probe now ignores retained MQTT messages (`mosquitto_sub -R`) and rejects negative, boolean or replay/demo/simulated RPM/speed values. Unknown or incomplete telemetry defers updates.
5. Added focused regression tests covering these contracts and updated README, field runbook and example environment.

## Still required before approving installation or driving use

**Software CI on candidate `c8a0c35b1cef35332d65d03ca9cf8b353482f011`:** GitHub Actions [field-readiness run 40412778992](https://github.com/thotsl4yer69/drifter/actions/runs/40412778992) **passed**, including Python and shell syntax, Ruff and **1,562 pytest tests**. [deploy-drifter run 40412779002](https://github.com/thotsl4yer69/drifter/actions/runs/40412779002) **passed** with Ruff, another 1,562-test pytest run and production Vite build (53 modules). These ran on a hosted Linux runner, **not** the ARM64 Pi or the car. Standalone `node --test cockpit-v4/tests/*.test.mjs` and Android compilation were not part of this recorded check.

**Pi acceptance:** record exact installed revision, intended 5 V power path, adapter and display; prove 10/10 vehicle-power cold starts; blank-config ELM pairing **and ECU response**; recovery from unplug and white/blank display; uninterrupted 1,800-second RPM/coolant/speed/voltage capture; incident evidence export. Use `sudo drifter acceptance status` and preserve failure evidence. Issue #67 stays open until measured, physical evidence passes.

**Android:** integrate/review and separately test the existing DRIFTER companion and X-Type Medic on an actual phone/ELM327. Do not describe PR #92 as a shipped signed APK.

## Candidate install path after checks

On the Pi, in a controlled parked setup: fetch the candidate branch, verify a clean checkout, deploy with `sudo ./scripts/oneshot.sh` for a new install or `--skip-apt` for an existing provisioned node. Then inspect `drifter version`, `drifter mode status`, `drifter healthz`, `drifter obd test` and `drifter acceptance status`. Deploying code is not the physical acceptance gate. The test branch intentionally will not be silently fast-forwarded by the `main` updater.

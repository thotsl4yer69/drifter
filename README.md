# MAZLABZ DRIFTER VIM — Vehicle Intelligence Module

**Dedicated Raspberry Pi vehicle intelligence, diagnostics and incident-evidence platform**

> **Founding beta:** DRIFTER is being opened to technically capable vehicle testers while the primary Jaguar acceptance gate is completed. Start with [the beta tester guide](docs/BETA_TESTER_GUIDE.md) and [compatibility matrix](docs/COMPATIBILITY_MATRIX.md).

[![Status](https://img.shields.io/badge/status-hardware--integrated%20prototype-blue)](PROJECT_STATUS.md)
![Platform](https://img.shields.io/badge/platform-Raspberry%20Pi%205-red)
![Messaging](https://img.shields.io/badge/messaging-MQTT-blue)

> **Maturity: Hardware-integrated prototype / active hardening.** DRIFTER is running as a real Raspberry Pi vehicle-node project, but it is not represented as production-ready or universally compatible. Validation depends on the vehicle, OBD transport and attached hardware. See [PROJECT_STATUS.md](PROJECT_STATUS.md).

> **Current hardening state (28 Sep 2026):** PR #87 is merged to `main` with boot-deadline and OBD-proof fixes. Targeted software checks passed; full hosted CI did not receive a runner, and Pi/vehicle acceptance is still outstanding. See [the evidence audit](docs/AUDIT_2026-09-28.md).

## The wedge

**The fault vanished. The evidence didn't.**

DRIFTER is built for intermittent faults and persistent vehicle evidence, not just code reading. Its always-on incident black box can preserve a bounded **90-second pre-event window** and **45-second post-event tail**, then carry ordered sensor changes into the diagnostic workflow.

## What it is

DRIFTER turns a Raspberry Pi into a local vehicle-intelligence node: ingest vehicle telemetry, apply deterministic diagnostic logic, log drive data, surface alerts, and provide interfaces for dashboards/voice and supporting edge services.

The reference development vehicle is a **2004 Jaguar X-Type 2.5L V6**. The architecture targets standards-based OBD-II data paths, but “targets OBD-II vehicles” is deliberately different from claiming every vehicle/adapter combination has been validated.

## Core loop

```text
 Vehicle / OBD-II
      │
      ├──── raw CAN / SocketCAN
      │
      └──── ELM327 / legacy OBD transports
                    │
             ┌──────▼──────┐
             │  Pi bridge  │
             └──────┬──────┘
                    │
                 MQTT
          ┌─────────┼─────────┐
          │         │         │
     diagnostics  logger   dashboard
          │         │         │
          └──────┬──┴────┬────┘
                 │       │
              voice   vehicle/UI
```

## Demonstrated engineering work

- Raspberry Pi/Linux deployment in the vehicle-node role;
- OBD-II-oriented telemetry architecture;
- SocketCAN/raw-CAN and ELM327/K-line transport work;
- standardized PID discovery and vehicle-profile logic;
- deterministic diagnostic-rule processing;
- MQTT-based service communication;
- drive logging/session analysis;
- watchdog/service-management patterns;
- dashboard/RealDash integration work;
- voice feedback through local TTS;
- calibration and deployment tooling;
- RTL-SDR/RF experimentation as an optional lab capability.

## Compatibility language

DRIFTER **targets standards-based OBD-II vehicles**. Actual capability is bounded by:

- the protocol exposed by the vehicle;
- whether the required PID is supported;
- the adapter/transport in use;
- vehicle-specific manufacturer behaviour;
- whether the feature has been tested on that exact combination.

The Jaguar X-Type remains the primary reference platform. Additional vehicle support should be documented with real test evidence rather than inferred from standards compliance alone.

See [docs/VEHICLE_PROFILES.md](docs/VEHICLE_PROFILES.md) for the profile model.

## Operating scope

The default value of DRIFTER is **vehicle diagnostics, telemetry and situational awareness on equipment you own or are authorised to test**.

Optional RF/network research functions are secondary lab capabilities and are gated/documented separately. See [CAPABILITIES.md](CAPABILITIES.md) for the capability boundary rather than assuming every module belongs in a normal driving deployment.

## Hardware

Typical development stack:

| Role | Example hardware |
|---|---|
| Compute | Raspberry Pi 5 + storage |
| Vehicle interface | CANable/SocketCAN-class adapter **or** compatible ELM327 path |
| Display | Browser/RealDash/Android-facing UI |
| Audio | Local audio path for TTS/alerts |
| Optional RF | RTL-SDR-class receiver |

Use the exact wiring/transport guide for the target vehicle. Do not assume OBD connector pinout implies a specific protocol without checking the vehicle.

## Founding beta

DRIFTER is not yet represented as a finished retail appliance. The founding beta is collecting measured compatibility across real vehicle + adapter combinations.

- [Launch authority / current commercial state](launch/STATUS.md)
- [Beta tester guide](docs/BETA_TESTER_GUIDE.md)
- [Founding-beta hardware authority](docs/BETA_HARDWARE.md)
- [Evidence-based compatibility matrix](docs/COMPATIBILITY_MATRIX.md)
- [First-drive / field runbook](FIRST_DRIVE.md)
- [Current project status](PROJECT_STATUS.md)

Use one issue per unique vehicle + adapter combination. Never post VINs, credentials, API keys or hotspot passwords.

## Deployment

For an existing DRIFTER Pi checkout, the canonical update path is:

```bash
cd /home/kali/drifter
git fetch origin
git checkout main
git pull --ff-only
sudo ./scripts/oneshot.sh --skip-apt
drifter version
drifter healthz
drifter diagnose
```

Use `sudo ./scripts/oneshot.sh` without `--skip-apt` for a fresh/full install. A successful software deploy is not the physical acceptance gate; record the installed revision and complete the cold-boot, OBD onboarding, recovery/display and 30-minute telemetry checks in [RELEASE-CHECKLIST.md](RELEASE-CHECKLIST.md). The detailed field sequence is in [docs/FIELD_DEPLOY.md](docs/FIELD_DEPLOY.md).

## Documentation map

| Document | Purpose |
|---|---|
| [PROJECT_STATUS.md](PROJECT_STATUS.md) | Current maturity, evidence boundary and hardening work |
| [CAPABILITIES.md](CAPABILITIES.md) | What the system does / does not claim |
| [FIRST_DRIVE.md](FIRST_DRIVE.md) | Initial deployment/drive workflow |
| [AUDIT.md](AUDIT.md) | Engineering audit/history |
| [COCKPIT.md](COCKPIT.md) | Driver/cockpit integration |
| [RELEASE-CHECKLIST.md](RELEASE-CHECKLIST.md) | Release-readiness checks |
| [CHANGELOG.md](CHANGELOG.md) | Change history |

## Current hardening priorities

- current full-repository pytest/Ruff/Vite verification on merged `main`;
- deploy merged `main` to the target Pi and record `drifter version` / installed hashes;
- 10/10 genuine cold boots without rescue power cycles;
- blank-config touchscreen OBD onboarding through adapter → ECU → changing PID proof;
- parked browser/display/ELM/broker recovery testing;
- uninterrupted 30-minute live telemetry evidence;
- transport validation across additional real vehicles.

## Development provenance

DRIFTER is an authored MAZLABZ project developed with **AI coding agents as part of the engineering workflow**. AI is used for implementation, research, refactoring and testing. Architecture, hardware selection, integration, debugging, field testing and deployment remain the project owner's responsibility.

## Portfolio significance

DRIFTER is a strong example of the core MAZLABZ skill set because it crosses physical and software boundaries:

**automotive data · Raspberry Pi/Linux · telemetry · MQTT · diagnostics · deployment · interfaces · physical integration**

---

**MAZLABZ DRIFTER VIM — prototype, measure, harden.**

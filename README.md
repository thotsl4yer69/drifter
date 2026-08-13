# DRIFTER — Vehicle Intelligence Module

**Raspberry Pi vehicle telemetry, diagnostics and edge-services platform**

[![Status](https://img.shields.io/badge/status-hardware--integrated%20prototype-blue)](PROJECT_STATUS.md)
![Platform](https://img.shields.io/badge/platform-Raspberry%20Pi%205-red)
![Messaging](https://img.shields.io/badge/messaging-MQTT-blue)

> **Maturity: Hardware-integrated prototype / active hardening.** DRIFTER is running as a real Raspberry Pi vehicle-node project, but it is not represented as production-ready or universally compatible. Validation depends on the vehicle, OBD transport and attached hardware. See [PROJECT_STATUS.md](PROJECT_STATUS.md).

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

## Deployment

The repository contains multiple deployment paths and field documentation. Start with the documented flow rather than copying a generic command from an old README revision:

1. review [PROJECT_STATUS.md](PROJECT_STATUS.md);
2. review [FIRST_DRIVE.md](FIRST_DRIVE.md);
3. review [CAPABILITIES.md](CAPABILITIES.md);
4. follow the vehicle/profile and field-deployment documentation under `docs/`;
5. run the repository tests/checks before connecting the target vehicle.

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

- repeatable clean installation on the target Pi;
- transport validation across additional real vehicles;
- current hardware/wiring matrix;
- service failure and recovery testing;
- network exposure review;
- tagged builds with test evidence;
- road-test evidence tied to exact vehicle/adapter combinations.

## Development provenance

DRIFTER is an authored MAZLABZ project developed with **AI coding agents as part of the engineering workflow**. AI is used for implementation, research, refactoring and testing. Architecture, hardware selection, integration, debugging, field testing and deployment remain the project owner's responsibility.

## Portfolio significance

DRIFTER is a strong example of the core MAZLABZ skill set because it crosses physical and software boundaries:

**automotive data · Raspberry Pi/Linux · telemetry · MQTT · diagnostics · deployment · interfaces · physical integration**

---

**MZ1312 / MAZLABZ — prototype, measure, harden.**

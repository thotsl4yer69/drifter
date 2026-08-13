# Project Status — DRIFTER

**Portfolio class:** Flagship  
**Maturity:** **Hardware-integrated prototype / active hardening**  
**Primary target:** Raspberry Pi vehicle node  
**Last portfolio review:** 2026-08-13

## What is demonstrated

- Raspberry Pi/Linux deployment in the target vehicle-node role.
- OBD-II / CAN-oriented telemetry architecture with SocketCAN and ELM327/K-line paths.
- MQTT-based internal messaging and service decomposition.
- deterministic diagnostic-rule engine and telemetry logging.
- watchdog/service-management patterns.
- voice/driver-feedback and dashboard/RealDash integration work.
- vehicle-profile, calibration and deployment tooling.

## Evidence boundary

DRIFTER is **not currently represented as production-ready**. It is a substantial hardware-integrated prototype that is still being hardened before deploy-ready status.

Claims such as “any OBD-II car,” exact service counts, RF coverage or fully automatic vehicle adaptation should be read as architectural targets unless the specific vehicle/transport combination has been bench- or road-validated and documented.

The strongest currently defensible wording is: **targets standards-based OBD-II vehicles, with validation dependent on the vehicle transport/protocol and available hardware.**

## Known work remaining

- complete field hardening and failure recovery;
- confirm transport behaviour across additional vehicles;
- repeatable clean-install verification;
- current hardware matrix and wiring evidence;
- security/network exposure review;
- tagged releases with acceptance-test output;
- documented road-test evidence for each claimed integration.

## Authorship / AI assistance

Authored MAZLABZ project developed with AI coding agents as part of the engineering workflow. AI assistance is used for implementation, research, refactoring and testing; architecture, hardware selection, integration, debugging, field testing and deployment remain the project owner's responsibility.

## Portfolio takeaway

DRIFTER demonstrates cross-domain integration: **automotive data + Raspberry Pi/Linux + services + telemetry + diagnostics + physical deployment**.

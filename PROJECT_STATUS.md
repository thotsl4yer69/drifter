# Project Status — DRIFTER

**Portfolio class:** Flagship  
**Maturity:** **Hardware-integrated prototype / active hardening**  
**Primary target:** Raspberry Pi vehicle node  
**Last portfolio review:** 2026-09-28

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

## Current release state — 28 September 2026

PR #87 is merged with the boot-readiness deadline and strict OBD retained-status fixes. PR #88 adds deployment hardening: fail-closed vehicle-state guarding for self-update, a 30-minute updater timer, bounded display `systemctl` calls, staged cockpit release activation, and `sudo drifter update`. PR #89 replaces the vision no-op with CPU ONNX YOLO decode/NMS plus Raspberry Pi Picamera2/Hailo inference, packaged HEF auto-selection and Picamera2 camera fallback. Exact targeted evidence: **21 boot + 46 OBD checks**, **10 deployment/display checks**, and **12 focused vision scenarios** passed in software harnesses; hosted CI still fails before runner allocation.

These merges are **software promotion only**. The Pi has not been observed running the current merged revision, and GitHub-hosted jobs still fail before runner allocation. The physical release gate remains open: ten consecutive cold boots, blank-config touchscreen OBD onboarding, parked recovery/display tests, and an uninterrupted 1,800-second live telemetry run.

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

# DRIFTER RECON + Hailo implementation plan

Status: implementation branch. This document defines the software scope; physical Hailo/camera acceptance remains a separate hardware gate.

## Goal

Make DRIFTER a clean two-primary-profile field system:

- **DRIVE** — vehicle telemetry, local assistant and lightweight Hailo road perception.
- **RECON** — parked/mobile camera surveillance and evidence capture using the same Hailo accelerator.

Existing **DIAG**, **FOOT** and **BOTH** profiles remain available for diagnostics, legacy field tooling and bench use, but FOOT is no longer the primary surveillance concept.

## Architecture

1. Keep the existing `mode.py` service-set controller as the single authority.
2. Add a first-class `recon` mode instead of building a second mode framework.
3. Make `drifter-vision` a canonical mode-controlled service shared by DRIVE and RECON.
4. Keep forward-collision logic DRIVE-only.
5. Make ALPR and a new recon evidence indexer RECON-only.
6. Do not run the legacy `drifter-dashcam` beside `drifter-vision` on the same camera. Recon recording/evidence capture is owned by the vision process so only one process opens the camera.
7. Preserve the existing general MQTT logger; add a recon-specific append-only evidence ledger rather than duplicating all telemetry.

## Recon data flow

```
camera -> vision_engine -> Hailo HEF
                         -> detections
                         -> bounded evidence JPEGs
                         -> optional vehicle crops for ALPR
                         -> MQTT vision events
                                |
                                +-> alpr_engine -> plate event
                                +-> recon_indexer -> hash-chained JSONL ledger
                                +-> cockpit -> live Recon view
GPS ----------------------------^
```

Every indexed event carries the session ID, sequence number, timestamp, current GPS fix when available, previous-record hash and current SHA-256 hash. Evidence files are hashed when indexed.

## Mode contract

### DRIVE
Runs the existing vehicle stack plus `drifter-vision`, `drifter-perception` and `drifter-fcw`. Recon-only evidence capture and ALPR remain off.

### RECON
Runs a deliberately lean infrastructure set: dashboard, hotspot/uplink, watchdog, logger, home sync, local display and GPS, plus `drifter-vision`, `drifter-alpr` and `drifter-recon-index`. Vehicle CAN/OBD, collision warning, LLM/STT and active FOOT/offsec services remain off so surveillance has predictable resource headroom.

### FOOT
Preserved unchanged for the existing field toolkit. Camera surveillance is not coupled to FOOT.

## Operator experience

- The cockpit reads the *actual* mode from `GET /api/mode`; opening the UI must never silently switch the Pi to a locally persisted mode.
- Primary touchscreen navigation exposes DRIVE and RECON.
- Mode changes are explicit, backend-confirmed actions.
- Recon view shows Hailo/camera backend state, recording/evidence state, session ID, event count, chain head, last detection and recent ALPR results.

## Implementation gates

1. Fix pixel-vs-normalised bounding-box handling in perception/FCW.
2. Add RECON service composition and sudo permissions.
3. Canonicalise vision/FCW/ALPR/recon-index services in deploy lists.
4. Add recon-aware evidence capture to `vision_engine.py`.
5. Add `recon_indexer.py` + systemd unit + MQTT topics.
6. Make ALPR consume the bounded crops emitted only in RECON.
7. Make cockpit mode state authoritative and add the Recon surface.
8. Update installer/acceptance scripts and regression tests.
9. Run software tests/review on the exact branch head.
10. Merge only after software checks are clean. Physical Hailo, camera, storage, thermal and road/field behaviour remain hardware acceptance tests.

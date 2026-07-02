# Changelog

All notable changes to DRIFTER are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); the project is pre-1.0 and
ships from the default branch.

## [Unreleased] — Multi-vehicle (any OBD-II car)

DRIFTER now targets **any OBD-II vehicle**, not just the reference X-Type. It
identifies the car by VIN and drives thresholds, fuel math, DTC causes,
powertrain rules, and the AI prompts off a per-vehicle profile. The X-Type stays
the regression baseline — its behaviour is unchanged when its profile is active.
See `docs/VEHICLE_PROFILES.md` for the profile seam + `vehicles/<VIN>.yaml`
authoring guide.

### Added
- `src/obd_pids.py` — one canonical OBD-II Mode-01 PID table (decode math +
  powertrain applicability + support-bitmap decode) that **both** transports
  build from; adds MAP (0x0B, MAF-less cars), fuel pressure, oil/ambient temp,
  fuel rate, and the standard hybrid/EV battery-life PID (0x5B).
- Per-car **PID-support discovery** (Mode-01 0x00/0x20/0x40/0x60 bitmaps) on both
  transports — poll only what the ECU reports; powertrain-default fallback.
- `src/iso_tp.py` — ISO-TP flow control + multi-frame reassembly on the raw CAN
  path (VIN over Mode 09, long DTC lists over Mode 03/07).
- `src/obd_transport.py` — boot-time raw-CAN vs ELM327 auto-selection;
  `drifter-obdbridge` is now a first-class monitored transport that idles instead
  of double-publishing when canbridge owns the car (`DRIFTER_TRANSPORT` override).
- `src/dtc_catalog.py` — generic OBD-II DTC base + per-vehicle overlay keyed off
  the active profile.
- EV/hybrid: standard battery-life PID, powertrain-aware polling, and an
  HV-battery-health alert (no-op on ICE).
- `docs/VEHICLE_PROFILES.md` profile-authoring guide.

### Changed
- LLM prompts (analyst / Vivi / ai_diagnostics / reporter / Ask-Mechanic + the
  Vivi persona files) build vehicle identity + known issues from the active
  profile instead of hardcoding the X-Type.
- `mechanic.search` is gated off the active profile (a non-Jaguar car no longer
  gets Jaguar torque/fuse/spec advice).
- `config.VEHICLE_DEFAULTS` + `vehicles/default.yaml` are now generic — an
  unidentified car no longer inherits the X-Type's specs.

## [Unreleased] — Release-readiness hardening

A defensive, reliability- and reproducibility-focused pass across the node. See
`AUDIT.md` for the full ground-truth report and `RELEASE-CHECKLIST.md` for the
tracked items (including the on-vehicle validation gate).

### Security
- Removed two committed WPA2 PSKs from source and docs. The Wi-Fi hotspot key is
  now sourced per node (`$DRIFTER_HOTSPOT_PSK` or a unique generated PSK) and is
  recoverable only on the device (`nmcli --show-secrets`).
- Bound the MQTT broker to loopback for both brokers (`config/nanomq.conf` now
  `127.0.0.1:1883`, matching Mosquitto); hotspot clients use HTTP/WS only.
- Added `SECURITY.md` (reporting policy, authorized-use scope, secrets handling).
- `.gitignore` now treats `vehicles/<VIN>.yaml` profiles as operator PII.

### Added
- `drifter-broker.target` — a stable MQTT-broker ordering anchor; every consumer
  orders against it instead of a concrete `nanomq`/`mosquitto` unit.
- `config.atomic_write_json` / `atomic_write_text` — crash-safe state writes.
- `config.resolve_device()` + `config/99-drifter-serial.rules` — stable udev
  symlinks for serial devices with raw-path fallback; device paths are now
  env-overridable.
- Supervised `drifter-mesh-coordinator` / `drifter-mesh-bridge` units.
- `AUDIT.md`, `CAPABILITIES.md`, `RELEASE-CHECKLIST.md`, `CHANGELOG.md`.
- Regression guard tests: broker ordering, unit hygiene, atomic writes, device
  resolution.

### Changed
- MQTT broker choice in `install.sh` is deterministic: Mosquitto by default,
  NanoMQ via `--with-nanomq` (was a nondeterministic `curl | bash` race).
- Deploy scripts derive the service set from `config.SERVICES` (single source of
  truth) instead of drifting hardcoded lists.
- `install.sh` seeds `/opt/drifter/.env` from `config/.env.example` on first
  install (was an empty file).
- Converged the local LLM model tag on `qwen2.5:1.5b` across all install paths.
- Rewrote `README.md` for accurate service count (38), broker, CAN adapter, the
  operating-mode model, and honest capability scope.

### Fixed
- `drifter.db` and the secondary SQLite DBs (`corpus`, `vehicle_kb`, `fleet`)
  now open in WAL with a `busy_timeout` — power-cut-safe recovery, no more
  immediate `database is locked`, and the shutdown WAL-checkpoint is now
  meaningful.
- Atomic writes for calibration, learned TPMS map, settings, session summary,
  and home-sync state (a power-cut mid-write no longer truncates them).
- `drifter-mesh` and `drifter-replay` no longer fork unsupervised background
  children; `session_recorder.py` no longer double-runs.
- Dockerfile no longer downgrades `paho-mqtt` below the v2 API the code requires;
  `paho-mqtt` capped `<3.0`.

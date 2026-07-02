# Changelog

All notable changes to DRIFTER are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); the project is pre-1.0 and
ships from the default branch.

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

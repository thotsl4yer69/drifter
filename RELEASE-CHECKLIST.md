# DRIFTER — Release Checklist

Tracks the release-hardening work from `AUDIT.md`. `[x]` = done in this branch,
`[ ]` = still open. Items that can only be confirmed on the physical node are
marked **HW** and must be validated on the vehicle before flipping the node
from **yellow → green**.

---

## Phase 0 — Ground truth
- [x] `AUDIT.md` produced and committed before any code change.

## Phase 1 — Correctness
- [x] State writes made atomic (power-cut safe): calibration, TPMS map,
      settings, session summary, sync state → `config.atomic_write_json`.
- [x] Central `drifter.db` opened in WAL + `busy_timeout` so the shutdown
      checkpoint service is meaningful and writers don't lock out.
- [x] Untracked backgrounded systemd children fixed: `drifter-mesh` split into
      supervised coordinator/bridge units; `drifter-replay` no longer
      double-runs `session_recorder.py`.
- [ ] **(follow-up)** Apply WAL/`busy_timeout` to the secondary DBs
      (`corpus.py`, `aircraft_db.py`, `vehicle_kb.py`, `fleet_server.py`) for
      the same concurrency benefit. Lower priority — these are add-on services.

## Phase 2 — Resilience
- [x] MQTT broker ordering unified behind `drifter-broker.target`; broker choice
      in `install.sh` is deterministic (Mosquitto default, `--with-nanomq`).
- [x] Serial device paths resolve via `config.resolve_device()` (env override >
      stable udev symlink > raw fallback); `99-drifter-serial.rules` shipped.
- [x] Retry/backoff + degrade-not-exit already present on the core hardware
      lanes (`can_bridge`, `gps_publisher`) — verified in the audit, unchanged.
- [x] Watchdog auto-demote to `diag` under memory/thermal pressure — verified,
      unchanged.
- [ ] **HW** Confirm the exact VID:PID / serial for each USB dongle (OBD,
      ESP32-Marauder, LTE modem) and uncomment/fill the matching template in
      `config/99-drifter-serial.rules`:
      `udevadm info -a -n /dev/ttyUSB0 | grep -E 'idVendor|idProduct|serial'`.
- [x] `drifter-lcd` framebuffer handling reviewed: it uses an in-process
      `wait_for_fb()` (up to 90 s, resolves by sysfs driver name) — this is
      **intentionally better** than a one-shot `ConditionPathExists=/dev/fb1`
      (which would fail the unit before the panel registers ~12 s into boot).
      No change; the audit's "inconsistency with fbmirror" is by design.

## Phase 3 — Reproducibility
- [x] `install.sh` seeds `/opt/drifter/.env` from `config/.env.example` on first
      install (was an empty `touch`).
- [x] Service lists derived from `config.SERVICES` in `post-deploy-check.sh` and
      `deploy-pi5.sh`; `config.py` "(19)" comment corrected; fbmirror
      inconsistency resolved.
- [x] Docker vs Pi `paho-mqtt` conflict fixed (Dockerfile no longer downgrades
      below the v2 client the code requires).
- [x] Ollama model tag converged on `qwen2.5:1.5b` across install-vivi.sh and
      post-deploy-check.sh (matches `config.OLLAMA_MODEL` / `vivi.yaml`).
- [ ] **(follow-up)** Dependency pinning: `pyproject.toml` uses floors only and
      the Pi install uses a hand-typed unpinned list. Add bounded/locked
      versions the Pi path actually consumes so a fresh install months later
      can't drift-break. Deferred: pinning must be validated on ARM64 hardware
      to avoid breaking the install, so it is a hardware-gated follow-up.

## Phase 4 — Release polish
- [x] `CAPABILITIES.md` — plain does / does-not, defensive framing, offensive
      suite documented as gated (authorized-use only).
- [x] `RELEASE-CHECKLIST.md` — this file.
- [x] README rewrite: corrected service count (38), broker (Mosquitto default),
      CAN adapter (slcan/K-line reality), added the mode model, removed the
      stale PSK, fixed the unreachable `10.42.0.1:1883` instruction, and added
      honest capability framing (links CAPABILITIES.md).
- [x] LICENSE present and complete (MIT, © 2026 MZ1312 UNCAGED TECHNOLOGY).
- [ ] **(optional)** SECURITY.md + CHANGELOG.md.

## Secrets — must-do before public
- [x] Removed the two committed WPA2 PSKs from source + docs; hotspot key is
      now per-node.
- [ ] **HW / operator** Rotate the historically-committed API keys (OWM /
      Google Maps) provider-side — they remain recoverable from git history.
- [ ] **operator** Decide on the committed vehicle profile
      `vehicles/SAJEA51D44XD39283.yaml` (real VIN). `.gitignore` now prevents
      *new* VIN profiles from being committed; to remove the existing one from
      the public repo run `git rm --cached vehicles/SAJEA51D44XD39283.yaml`
      (it stays on the Pi under `/opt/drifter/vehicles/`).

---

## Hardware validation gate (yellow → green)
Run on the physical node and confirm before declaring green:
- [ ] **HW** `sudo ./scripts/oneshot.sh` ends with `DEPLOY: ok`.
- [ ] **HW** `curl -fsS http://127.0.0.1:8080/healthz` returns `ok` or
      `ok-hw-pending`.
- [ ] **HW** Broker: exactly one of Mosquitto/NanoMQ active, and
      `drifter-broker.target` reached after it (`systemctl status
      drifter-broker.target`).
- [ ] **HW** CAN: adapter identified, `slcan0`/`can0` up, telemetry flowing
      (and confirm whether the 2004 X-Type OBD pins are CAN or K-line).
- [ ] **HW** RTL-SDR tunes; USB audio dongle plays; USB mic enumerates.
- [ ] **HW** GPS produces a fix; SPI LCD paints; hotspot up on 10.42.0.1.
- [ ] **HW** Power-cut test: pull power mid-drive, reboot, confirm no service
      wedges on a corrupt/partial state file and the DB recovers.

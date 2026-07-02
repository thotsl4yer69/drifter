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
- [x] Applied WAL/`busy_timeout` to the secondary write DBs (`corpus.db`,
      `vehicle_kb.db`, `fleet.db`). `aircraft_db.py` is intentionally excluded —
      it opens read-only (`mode=ro`), where WAL is neither applicable nor needed.

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
- [x] `paho-mqtt` capped `>=2.0,<3.0` (pyproject + install.sh) — the code hard-
      requires the v2 callback API; this is safe because 3.x doesn't exist yet,
      so the ceiling can't fall below an installed version.
- [ ] **(follow-up, HW-gated)** Broader dependency pinning: `pyproject.toml`
      still uses floors only and the Pi install uses a hand-typed list. Adding
      blanket version ceilings blind is unsafe — a ceiling set below the version
      already resolved on the ARM64 Pi would break a fresh install. This needs a
      lock generated/validated on real hardware (`pip freeze` on a known-good
      node), so it is deferred to a hardware pass rather than guessed here.

## Phase 4 — Release polish
- [x] `CAPABILITIES.md` — plain does / does-not, defensive framing, offensive
      suite documented as gated (authorized-use only).
- [x] `RELEASE-CHECKLIST.md` — this file.
- [x] README rewrite: corrected service count (38), broker (Mosquitto default),
      CAN adapter (slcan/K-line reality), added the mode model, removed the
      stale PSK, fixed the unreachable `10.42.0.1:1883` instruction, and added
      honest capability framing (links CAPABILITIES.md).
- [x] LICENSE present and complete (MIT, © 2026 MZ1312 UNCAGED TECHNOLOGY).
- [x] SECURITY.md — vuln-reporting policy, scope/authorized-use, secrets
      handling, network exposure.
- [x] CHANGELOG.md — Keep-a-Changelog summary of the hardening pass.

## Multi-vehicle — "works in any OBD-II car" (Phase C–F)
Target: any OBD-II vehicle via standardized PIDs; VIN auto-detect + manual
override; EV/hybrid at the standard-PID level. Powertrain-aware, profile-driven.
The 2004 X-Type is the regression baseline (its behaviour is unchanged when its
profile is active — the offline suite proves it).

### Phase C — universal PID / transport
- [x] Unified PID table (`src/obd_pids.py`) — one canonical Mode-01 registry;
      both `can_bridge` and `obd_bridge` build their tables from it (no more
      divergent hand-copied tables).
- [x] MAP (0x0B) for MAF-less cars + common standard PIDs (fuel pressure, oil
      temp, ambient temp, fuel rate).
- [x] Mode-01 PID-support discovery on both transports — poll only what the ECU
      reports; powertrain-default fallback on a silent bus.
- [x] ISO-TP flow control + multi-frame reassembly on the raw CAN path
      (`src/iso_tp.py`) — VIN (Mode 09) and long DTC lists read correctly on
      strict ISO-TP ECUs.
- [x] `drifter-obdbridge` promoted to a first-class, monitored, auto-selected
      transport (`src/obd_transport.py`): CAN vs ELM327 chosen at boot; the
      non-selected transport idles instead of double-publishing. Added to
      `config.SERVICES`/modes, oneshot/install, `/healthz` + diagnose hw-optional
      sets, and brought to canbridge unit parity.

### Phase D — profile-driven diagnosis + prompts
- [x] LLM prompts (analyst, Vivi, ai_diagnostics, reporter, Ask-Mechanic,
      vivi persona files) build vehicle identity + known-issues from the active
      profile instead of hardcoding the X-Type.
- [x] DTC lookup split into a generic OBD-II base + per-vehicle overlay
      (`src/dtc_catalog.py`) keyed off the profile; alert/LCD stay overlay-exact,
      the LLM/UI get generic descriptions on any car.
- [x] Mechanic KB (`mechanic.search`) gated off the active profile so a
      non-Jaguar car doesn't get Jaguar torque/fuse/spec advice.

### Phase E — EV / hybrid + generic defaults
- [x] Standard hybrid/EV PID (0x5B battery life) + powertrain-aware polling
      (EV drops combustion PIDs, ICE drops the battery PID, hybrid keeps both).
- [x] EV-appropriate alert: HV traction-battery health (no-op on ICE).
- [x] `config.VEHICLE_DEFAULTS` + `vehicles/default.yaml` made generic (an
      unidentified car no longer inherits the X-Type's specs).

### Phase F — docs
- [x] README + CAPABILITIES reframed for multi-vehicle.
- [x] `docs/VEHICLE_PROFILES.md` — profile seam + `vehicles/<VIN>.yaml`
      authoring guide.
- [x] AGENTS.md + COCKPIT.md updated for the new modules + transport auto-select.

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

## What still requires the physical vehicle

Every non-hardware item above is done and the offline suite is green
(`pytest -q` + `ruff check src tests`). Nothing further can be closed from the
repo alone — the remaining items **all need the physical node/vehicle** (or are
operator/provider actions) and are the yellow → green gate:

- **Operator/provider actions:** rotate the historically-committed API keys
  provider-side; decide whether to `git rm --cached` the committed VIN profile.
- **Hardware validation gate** (below), including the new multi-vehicle items —
  the transport auto-select, K-line-vs-CAN confirmation on the X-Type, live PID
  discovery, and (if available) an EV/hybrid and a second-vehicle smoke.

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
- [ ] **HW** Transport auto-select: with only a raw-CAN adapter,
      `drifter-canbridge` publishes and `drifter-obdbridge` idles
      `deferring_to_canbridge` (and vice-versa with an ELM327). Confirm
      `DRIFTER_TRANSPORT=elm327` forces the ELM327 path on a K-line X-Type.
- [ ] **HW** ELM327/K-line: on the X-Type (or any K-line car) `drifter-obdbridge`
      negotiates the protocol (log line shows ISO 9141-2 / KWP) and publishes the
      same metric topics as canbridge; VIN reads over ISO-TP.
- [ ] **HW** PID discovery: confirm the poller requests only the ECU's reported
      PIDs (journal shows "ECU reports N/M known PIDs supported").
- [ ] **HW / optional** EV/hybrid smoke on an actual EV/hybrid: 0x5B battery-life
      publishes, combustion rules are suppressed, HV-battery alert fires.
- [ ] **HW / optional** Second-vehicle smoke: a non-Jaguar OBD-II car identifies
      by VIN, resolves a generic/authored profile, and telemetry + generic DTC
      descriptions flow (no Jaguar-specific advice).
- [ ] **HW** RTL-SDR tunes; USB audio dongle plays; USB mic enumerates.
- [ ] **HW** GPS produces a fix; SPI LCD paints; hotspot up on 10.42.0.1.
- [ ] **HW** Power-cut test: pull power mid-drive, reboot, confirm no service
      wedges on a corrupt/partial state file and the DB recovers.

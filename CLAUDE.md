# DRIFTER — Fleet Handoff (CLAUDE.md)

Node: **drifter** — Raspberry Pi 5 (8 GB) telemetry node in a 2004 Jaguar X-Type 2.5 V6 (AJ-V6).
Brand: MZ1312 UNCAGED TECHNOLOGY — EST 1991.
Status: **yellow** — bench-green and canbridge `_consecutive_failures` global fix landed (2026-05-08); awaiting in-vehicle smoke (OBD-II + RTL-SDR + mic).

This file is the operator-facing handoff per the fleet `DEPLOY_CONTRACT.md`. For
agent-facing architecture details see [`AGENTS.md`](AGENTS.md). For wiring see
[`docs/WIRING.md`](docs/WIRING.md).

**Doing the deploy?** → start at [`docs/FIELD_DEPLOY.md`](docs/FIELD_DEPLOY.md).
That file is the literal copy-paste runbook. The block to paste into the
fleet repo's `inventory.yaml` lives at
[`docs/fleet-inventory-drifter.yaml`](docs/fleet-inventory-drifter.yaml).

---

## Contract artefacts in this repo

| Artefact | Path | Purpose |
|---|---|---|
| One-shot deploy | [`scripts/oneshot.sh`](scripts/oneshot.sh) | Stage-gated wrapper around `install.sh` (10 apt+venv → 20 diagnose → 30 smoke → 40 enable services → curl /healthz) |
| Operator CLI | [`src/diagnose.py`](src/diagnose.py) + [`bin/drifter`](bin/drifter) | `drifter {diagnose,status,logs,restart,healthz,version}` |
| Health endpoint | `/healthz` on `drifter-dashboard` (port 8080) | Returns 200/503 + JSON of services + telemetry freshness |
| This file | `CLAUDE.md` | Operator handoff |

## One-line deploy (from a fresh Pi over SSH)

```bash
ssh kali@<pi-ip> "cd /home/kali/drifter && sudo ./scripts/oneshot.sh"
```

The expected log shape:

```
STAGE 10 START — apt + venv (delegated to install.sh)
…
STAGE 10 OK
STAGE 20 START — drifter diagnose
…
STAGE 20 OK
STAGE 30 START — post-deploy smoke
…
STAGE 30 OK
STAGE 40 START — systemctl enable + start
…
STAGE 40 OK
STAGE FINAL START — curl http://127.0.0.1:8080/healthz
{"status":"ok", …}
STAGE FINAL OK
DEPLOY: ok
```

## Health probe contract

```bash
curl -fsS http://10.42.0.1:8080/healthz
```

Returns JSON shaped like:

```json
{
  "status": "ok",
  "mode": "drive",
  "ts": 1735689600.123,
  "services": {"drifter-canbridge": true, "drifter-alerts": true, …},
  "services_failed": [],
  "services_hw_pending": [],
  "mqtt_connected": true,
  "telemetry_fresh": true,
  "ws_clients": 1
}
```

`status` is one of:
- `ok` — every expected service is active.
- `ok-hw-pending` — only hardware-dependent services (`canbridge`, `rf`, `vivi`, `voicein`, `flipper`, `bleconv`) are inactive; the dongle isn't plugged in yet. Still HTTP **200** so deploy doesn't block on a bench Pi awaiting OBD-II.
- `degraded` — a non-hardware service is down. HTTP **503**.

`mode` reflects the current persona; `MODES` in `config.py` decides which
services count as "expected" per mode. Cached for 2s server-side so high-rate
probing is cheap. Modes:
- `diag` — **the default lean floor** (`DEFAULT_MODE`). Vehicle telemetry +
  driver-safety only; no LLM (`vivi`/`analyst`/`reporter`), no STT (`voicein`),
  no `fly-catcher` ML, no recon. A fresh deploy settles here (`oneshot.sh`
  stage 45) so the node comes up guaranteed-light, and the watchdog
  auto-demotes back to it under memory/thermal pressure. Diagnostics and the
  safety pipeline run on a fraction of the RAM.
- `drive` — telemetry stack **+** the assistant/LLM/voice features (heavier).
  Switch up once the node is stable: `sudo drifter mode drive`.
- `foot` — recon/offsec persona.
- `both` — every service (bench/lab only; will not fit comfortably in 8 GB).

## `drifter` operator CLI

Installed by `install.sh` to `/usr/local/bin/drifter`. Subcommands:

```bash
drifter diagnose [--json]                # full fleet-contract probe
drifter status   [--json]                # one line per service
drifter healthz  [--json]                # probe local /healthz, pretty-print
drifter logs <service> [-n N] [-f]       # journalctl -u drifter-<service>
drifter restart [<service>|all]          # systemctl restart
drifter version                          # deployed git rev / branch
```

Service names accept both short (`canbridge`) and full (`drifter-canbridge`)
forms. `restart` with no argument restarts every unit in `SERVICES`.

`drifter diagnose` checks performed:

1. Every unit in [`src/config.py`](src/config.py) `SERVICES` is `systemctl is-active`.
2. CAN bus: `can0` (or `slcan0`) link is `UP`/`UNKNOWN` and candump sees a frame in 750ms (warn-only if no frames — ECU might just be off).
3. RealDash: TCP connect to `127.0.0.1:35000`. *Note: the contract spec calls this "RealDash UDP socket" but the actual `realdash_bridge.py` implementation is TCP — see [Discrepancies](#discrepancies-from-the-contract-spec) below.*
4. PortAudio device enumeration (falls back to `arecord -l` if pyaudio is missing).
5. RTL-SDR present in `lsusb` (`0bda:2832` / `0bda:2838` / R820T markers).
6. MQTT broker reachable on the configured `MQTT_HOST:MQTT_PORT`.
7. Local `/healthz` on the dashboard returns HTTP 200.

Exit `0` if every fatal check passes; warnings (audio, rf_sdr) don't fail the run.

## Service inventory (38 — source of truth: `src/config.py` `SERVICES`)

The canonical monitored list lives in [`src/config.py`](src/config.py) — `SERVICES`
(currently **38**; run `python3 -c "import sys;sys.path.insert(0,'src');import config;print(len(config.SERVICES))"`
for the live count). `/healthz` checks exactly this set; `scripts/oneshot.sh`
starts exactly it; [`install.sh`](install.sh) enables it plus a few boot/aux
units (`boot-manager`, `boot-reason`, `db-checkpoint`).
[`services/`](services/) holds **all** unit files (67), a superset that
includes mode-specific and mutually-exclusive alternatives (e.g.
`fbmirror` vs `lcd`) — so the unit-file count is intentionally larger than
`SERVICES`. `tests/test_deploy_service_lists.py` enforces that every `SERVICES`
entry has a unit file and that `oneshot.sh`/`install.sh` stay in sync. The
older "(19)"/"(25)" inventories below are historical snapshots — trust
`config.py`, not the prose.

```
drifter-canbridge   drifter-alerts      drifter-logger
drifter-anomaly     drifter-analyst     drifter-voice
drifter-vivi        drifter-hotspot     drifter-homesync
drifter-watchdog    drifter-realdash    drifter-rf
drifter-wardrive    drifter-dashboard   drifter-fbmirror
drifter-voicein     drifter-flipper     drifter-opsec
drifter-bleconv
```

`drifter-llm.service` was removed (superseded by `drifter-analyst`); `install.sh`
will tear down a leftover unit file from older deploys.

## Network layout

| Endpoint | Where | Protocol | Use |
|---|---|---|---|
| Wi-Fi hotspot | `MZ1312_DRIFTER` / *(see NetworkManager)* | WPA2 | phone tethers here |
| Hotspot subnet | `10.42.0.1/24` | DHCP | shared-mode NM |
| Dashboard | `10.42.0.1:8080` | HTTP | UI + `/healthz` |
| Telemetry WS | `10.42.0.1:8081` | WS | live MQTT fan-out |
| Audio WS | `10.42.0.1:8082` | WS (binary WAV) | TTS over phone speaker |
| RealDash | `10.42.0.1:35000` | **TCP** (CAN 0x44) | RealDash app |
| MQTT broker | `localhost:1883` | Mosquitto (NanoMQ optional via `--with-nanomq`) | inter-service bus |

## Discrepancies from the contract spec

The deploy task description had a couple of items that don't match the codebase
as-shipped. The contract layer here matches the **code**, not the spec:

- Spec says "drifter-dashboard (FastAPI on 8080)". Code uses
  `http.server.HTTPServer` + `SimpleHTTPRequestHandler`
  ([`src/web_dashboard.py`](src/web_dashboard.py)). `/healthz` was added to the
  existing handler; no FastAPI dependency added.
- Spec says "RealDash UDP socket reachable". Code uses TCP on 35000
  ([`src/realdash_bridge.py:291`](src/realdash_bridge.py)). `drifter diagnose`
  checks TCP.

If the fleet contract requires literal FastAPI / UDP, raise it as a separate
ticket — the rewrite touches service semantics and I deliberately stayed out of
those per the deploy brief.

## Things this repo can't verify on its own

This repo is the *node*; the *fleet* lives at `thotsl4yer69/fleet`. The following
need to happen there (and on the live Pi) for the node to flip from
**yellow → green**:

1. **Push to the Pi.** The repo is already at `thotsl4yer69/drifter`; on the Pi:
   ```bash
   cd /home/kali/drifter
   git remote add origin git@github.com:thotsl4yer69/drifter.git  # if missing
   git fetch && git checkout main
   ```
2. **Run the contract deploy locally on the Pi:**
   ```bash
   sudo ./scripts/oneshot.sh
   ```
   Expected last line: `DEPLOY: ok`.
3. **Update fleet inventory.** Paste the full block from
   [`docs/fleet-inventory-drifter.yaml`](docs/fleet-inventory-drifter.yaml)
   into `thotsl4yer69/fleet/inventory.yaml`. Replace `<pi-ip>` with
   whatever `arp -a` reveals (last known: `10.246.228.156`). Keep
   `status: yellow` until `mesh deploy drifter` returns exit 0.
4. **Run `mesh deploy drifter` from a separate machine.** Verify exit 0
   and `mesh status drifter` returns `ok`.

## DEPLOY status

```
DEPLOY: ok
```

Verified: `sudo ./install.sh` (cherry-pick branch) completed on the live Pi (2026-05-19) over an oneshot.sh baseline from 2026-05-18.
Bench result: 25 services tracked, 14 active under foot mode (10 drive-only services correctly inactive per mode-aware design), 0 services_failed. `/healthz` returns `ok-hw-pending` (only `drifter-voicein` waiting on USB mic).

Status remains **green**.

### Service inventory now 25 (was 21)

Cherry-pick branch `cherry-pick/v2-first-wave` (PR #9) landed 4 new services + 1 library module from `feature/drifter-v2`:

- `drifter-batcher` — telemetry rolling stats publisher (`drifter/telemetry/window` + `drifter/telemetry/stats`)
- `drifter-trip` — per-trip distance + fuel from MAF (`drifter/trip/{stats,fuel,cost,event}`)
- `drifter-thresholds` — drift-capped adaptive baseline learner (`drifter/thresholds/{learned,update}`)
- `drifter-reporter` — post-drive markdown report via LLM (`drifter/session/{report,summary}`)
- `vivi_memory.py` — library (SQLite persistent memory; no service yet)

Plus `src/llm_client.py` rewritten as a Claude→Groq→Ollama cascade with caching/retry/health (`LLM_CASCADE_ORDER=['ollama']` default; existing callers preserved via backward-compat shims `query_chat`, `stream_chat_ollama`, `query_llm`, `SYSTEM_PROMPT`, `CHAT_SYSTEM_PROMPT`).

Full service list:

```
drifter-canbridge      drifter-alerts       drifter-logger
drifter-anomaly        drifter-analyst      drifter-voice
drifter-vivi           drifter-hotspot      drifter-homesync
drifter-watchdog       drifter-realdash     drifter-rf
drifter-rfaudio        drifter-wardrive     drifter-dashboard
drifter-fbmirror       drifter-voicein      drifter-flipper
drifter-opsec          drifter-bleconv      drifter-gps
drifter-batcher        drifter-trip         drifter-thresholds
drifter-reporter
```

### Weather + Location enrichment (feature/drifter-v2)

Wired the third-party API keys (OpenWeatherMap + Google Maps/Elevation/Places)
into the v2 brain. Keys are read from the environment (or `/opt/drifter/.env`,
git-ignored; see [`config/.env.example`](config/.env.example)) via **`src/api_keys.py`** —
they default to empty and the owning service idles when a key is absent. No
secrets live in source. `config.py` re-exports the values so the rest of the
fleet imports from one place. (Earlier revisions hardcoded live keys; those are
in git history and must be rotated provider-side — treat them as compromised.)

Two new services own *all* external API traffic and fan the results out over
MQTT — every consumer reads the topics, so the real-time/safety path never
blocks on the network and Google/OWM are hit from exactly one place each:

- `drifter-weather` (`weather_service.py`) — OpenWeatherMap One Call (degrades
  to the 2.5 endpoints if the key lacks One Call 3.0). Polls every 15 min for
  the live GPS position (falls back to `DEFAULT_LAT/LON` = Bendigo VIC).
  Publishes `drifter/weather/{current,forecast,alerts}`. Derives actionable
  advisories — `rain_soon` (minutely "windows-up" nudge), `fog`, `ice`,
  `high_wind`.
- `drifter-location` (`location_service.py`) — Google Elevation (current road
  grade %, from consecutive samples) + Places (nearby fuel/mechanic/…).
  Publishes `drifter/location/{elevation,nearby}`; on-demand POI lookups via
  the `drifter/location/query` request topic.

Consumers wired to the new topics:
- `nav_engine` → forwards weather hazards + steep-grade warnings as `nav_alert`.
- `safety_engine` → wet-road tightens the hard-brake threshold; fog speed
  warning; steep descent/climb advisories (all deterministic, no network).
- `trip_computer` → weather + elevation/grade overlay on trip stats.
- `ai_diagnostics` → weather folded into the LLM diagnosis prompt (cold/heat/
  humidity correlation).
- `driver_assist` → prefers the OWM feed over its Open-Meteo fallback; sets a
  `rain`/`fog`/`ice` driving mode.
- `vivi_v2` → answers "nearest petrol/mechanic/car wash" from cached Places
  results, speaks proactive rain/fog/ice warnings, and pre-warms the location
  service via `location_query` when asked about a POI it hasn't cached.

Both new services are SHARED-mode (run in drive + foot) and auto-installed via
the `services/*.service` glob + the `SERVICES` list in `install.sh`. Offline
tests in `tests/test_weather_location.py`.

### Security hardening (2026-05-18, commit `eed90b9`)

Pre-deploy review of the rfaudio surface found two issues that have been fixed and re-verified on this Pi with the RTL-SDR plugged in:

- `POST /api/rfaudio/command` now gates on `_is_local_peer` (127.0.0.1 + 10.42.0.0/24) and rejects actions outside the allowlist `{start, stop, scan, test_tone, list_bands}`. Was unauthenticated on the hotspot subnet.
- `rfaudio._handle_command` validates `freq_mhz ∈ [24, 1766]`, `gain ∈ [0, 49.6]`, `mode ∈ {nfm,wfm,fm,am,usb,lsb,raw}`, and catches `TypeError`/`ValueError` from `float()` casts (previously killed the worker thread silently).

Mosquitto also rebound to `127.0.0.1:1883` (was `0.0.0.0`) — hotspot clients reach drifter via HTTP/WS, never direct MQTT. All 21 services reconnected cleanly to the loopback listener.

Bench smoke test with RTL-SDR Blog V4 dongle confirmed:
- rtl_fm tunes cleanly to 476.525 MHz nfm (UHF-CB Ch5)
- All four invalid-param paths return clear operator-facing MQTT errors
- Supervisor cleanly handles aplay-not-present (no zombie processes, state flips back to idle)
- 492/492 unit tests pass

### Outstanding before next vehicle session

- **Plug in the C-Media USB audio dongle** (`plughw:0,0`) — bench has no playback device so `aplay` exits immediately after rtl_fm tunes. Not a code defect; rtl_fm side is verified working.
- ~~Rotate the hotspot PSK~~ — done 2026-05-18. PSK now lives only in NetworkManager; docs reference `nmcli --show-secrets connection show MZ1312_DRIFTER` rather than embedding the value. There is a duplicate `MZ1312_DRIFTER 1` infrastructure-mode profile from an older NM merge that also got the rotation; clean it up at next opportunity with `sudo nmcli connection delete "MZ1312_DRIFTER 1"`.
- **Optional follow-up:** add a `speaker`/`usb_audio` probe to `hw_probe` so the dashboard surfaces "Plug in USB audio dongle" the same way the `microphone` probe does, before the operator tries `start`.

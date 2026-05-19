# DRIFTER — Fleet Handoff (CLAUDE.md)

Node: **drifter** — Raspberry Pi 5 (8 GB) telemetry node in a 2004 Jaguar X-Type 2.5 V6 (AJ-V6).
Brand: MZ1312 UNCAGED TECHNOLOGY — EST 1991.
Status: **yellow** — bench-green and canbridge `_consecutive_failures` global fix landed (2026-05-08); awaiting in-vehicle smoke (OBD-II + RTL-SDR + mic).

This file is the operator-facing handoff per the fleet `DEPLOY_CONTRACT.md`. For
agent-facing architecture details see [`AGENTS.md`](AGENTS.md). For wiring see
[`docs/WIRING.md`](docs/WIRING.md).

---

## Contract artefacts in this repo

| Artefact | Path | Purpose |
|---|---|---|
| One-shot deploy | [`scripts/oneshot.sh`](scripts/oneshot.sh) | Stage-gated wrapper around `install.sh` (10 apt+venv → 20 diagnose → 30 smoke → 40 enable services → curl /healthz) |
| Diagnose CLI | [`src/diagnose.py`](src/diagnose.py) + [`bin/drifter`](bin/drifter) | `drifter diagnose` — JSON or text fleet-contract probe |
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

`mode` reflects the current persona (`drive` / `foot`); `MODES` in `config.py` decides which services count as "expected" per mode. Cached for 2s server-side so high-rate probing is cheap.

## `drifter diagnose`

Shell:

```bash
drifter diagnose            # text output
drifter diagnose --json     # machine-readable
```

Checks performed:

1. Every unit in [`src/config.py`](src/config.py) `SERVICES` is `systemctl is-active`.
2. CAN bus: `can0` (or `slcan0`) link is `UP`/`UNKNOWN` and candump sees a frame in 750ms (warn-only if no frames — ECU might just be off).
3. RealDash: TCP connect to `127.0.0.1:35000`. *Note: the contract spec calls this "RealDash UDP socket" but the actual `realdash_bridge.py` implementation is TCP — see [Discrepancies](#discrepancies-from-the-contract-spec) below.*
4. PortAudio device enumeration (falls back to `arecord -l` if pyaudio is missing).
5. RTL-SDR present in `lsusb` (`0bda:2832` / `0bda:2838` / R820T markers).
6. MQTT broker reachable on the configured `MQTT_HOST:MQTT_PORT`.
7. Local `/healthz` on the dashboard returns HTTP 200.

Exit `0` if every fatal check passes; warnings (audio, rf_sdr) don't fail the run.

## Service inventory (19)

Canonical list lives in [`src/config.py`](src/config.py) — `SERVICES`. Keep in sync with
[`services/`](services/) and the `SERVICES` array in [`install.sh`](install.sh).

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
   git fetch && git checkout claude/drifter-fleet-compliant-GSdKl
   ```
2. **Run the contract deploy locally on the Pi:**
   ```bash
   sudo ./scripts/oneshot.sh
   ```
   Expected last line: `DEPLOY: ok`.
3. **Update fleet inventory.** In `thotsl4yer69/fleet/inventory.yaml`:
   ```yaml
   drifter:
     ssh_host: kali@<pi-ip>           # was 10.246.228.156 last; chase with arp -a
     repo: git@github.com:thotsl4yer69/drifter.git
     branch: main                      # or claude/drifter-fleet-compliant-GSdKl until merged
     deploy: scripts/oneshot.sh
     healthz: http://10.42.0.1:8080/healthz
     status: yellow                    # bump to green once `mesh deploy drifter` is exit 0
   ```
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

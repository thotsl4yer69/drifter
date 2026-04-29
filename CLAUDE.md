# DRIFTER — Fleet Handoff (CLAUDE.md)

Node: **drifter** — Raspberry Pi 4 telemetry node in a 2004 Jaguar X-Type 2.5 V6 (AJ-V6).
Brand: MZ1312 UNCAGED TECHNOLOGY — EST 1991.
Status: **yellow** (contract layer present, not yet exercised end-to-end on the live Pi).

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
  "ts": 1735689600.123,
  "services": {"drifter-canbridge": true, "drifter-alerts": true, …},
  "services_failed": [],
  "mqtt_connected": true,
  "telemetry_fresh": true,
  "ws_clients": 1
}
```

HTTP **200** when no services failed, **503** otherwise. Cached for 2s server-side
so high-rate probing is cheap.

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

## Service inventory (15)

Canonical list lives in [`src/config.py`](src/config.py) — `SERVICES`. Keep in sync with
[`services/`](services/) and the `SERVICES` array in [`scripts/oneshot.sh`](scripts/oneshot.sh).

```
drifter-canbridge   drifter-alerts      drifter-logger
drifter-anomaly     drifter-analyst     drifter-voice
drifter-hotspot     drifter-homesync    drifter-watchdog
drifter-realdash    drifter-rf          drifter-wardrive
drifter-dashboard   drifter-fbmirror    drifter-voicein
```

`drifter-llm.service` ships in the repo but is **disabled** — superseded by
`drifter-analyst`. `install.sh` runs `systemctl disable --now drifter-llm`.

## Network layout

| Endpoint | Where | Protocol | Use |
|---|---|---|---|
| Wi-Fi hotspot | `MZ1312_DRIFTER` / `uncaged1312` | WPA2 | phone tethers here |
| Hotspot subnet | `10.42.0.1/24` | DHCP | shared-mode NM |
| Dashboard | `10.42.0.1:8080` | HTTP | UI + `/healthz` |
| Telemetry WS | `10.42.0.1:8081` | WS | live MQTT fan-out |
| Audio WS | `10.42.0.1:8082` | WS (binary WAV) | TTS over phone speaker |
| RealDash | `10.42.0.1:35000` | **TCP** (CAN 0x44) | RealDash app |
| MQTT broker | `localhost:1883` | NanoMQ or Mosquitto fallback | inter-service bus |

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
DEPLOY: needs-human
```

Reason: contract artefacts are committed, but the live `mesh deploy drifter`
round-trip from a separate machine has not been run yet (cloud sandbox has no
SSH path to the Pi or the fleet repo). Steps 1–4 above are the remaining work.

Once those four steps pass, flip this section to:

```
DEPLOY: ok
```

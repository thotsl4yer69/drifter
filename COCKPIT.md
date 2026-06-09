# COCKPIT.md — first-drive readiness

This file is the operator handoff for the cockpit at `http://127.0.0.1:8080/`.
For the agent/architecture handoff see `AGENTS.md`; for deploy contract see
`CLAUDE.md`.

## Current state — green for first drive

Every endpoint the cockpit calls returns 200. Every retained MQTT topic
that used to leak phantom/stale data is now non-retained. No baked-in
placeholder strings remain in the UI (the wordmark serial reads
`/etc/hostname` via `/healthz.node_id`). Cold-start of every expected
service (the `config.py` `SERVICES` set — 38; `/healthz` is the live count)
reaches `status: ok`/`ok-hw-pending` in under 60s with zero ERROR-level
journal entries.

| Surface          | Source of truth                         | Empty state                            |
|------------------|-----------------------------------------|----------------------------------------|
| Map              | inline Leaflet, origin from `/api/feeds/summary` (120s gated) | world view + "AWAITING GPS FIX" overlay |
| Surveil tab      | `/api/ble/recent` + `/api/ble/persistent` + `/api/wardrive` | "Nothing live right now" with stale count |
| Aircraft tab     | `/api/aircraft/recent` (60s freshness gate)               | "Awaiting GPS fix before scanning ADS-B" |
| Incidents tab    | `/api/alerts/recent` ring + `/api/dtcs/recent`           | "No incidents recorded this session"    |
| Hardware tab     | `/api/hardware` summary                                    | full probe list with action text per item |
| Tires tab        | `/api/tpms/recent`                                         | "No TPMS readings yet — drive briefly to populate" |
| System tab       | `/healthz` + `/api/state` watchdog                         | "no watchdog metrics yet"               |
| Mechanic overlay | `POST /api/query` (LLM)                                    | inline degraded banner (see C4 below)   |
| Vivi overlay     | `POST /api/query` (LLM)                                    | same                                   |
| Sessions overlay | `/api/trip/recent` + `/api/sessions`                       | "No completed sessions yet"             |
| Settings overlay | `GET/POST /api/settings`                                   | 17-field form, server-side allowlist    |
| RF overlay       | `/api/flipper/{status,captures,results}` + `/api/rf/spectrum` | hardware-pending status + empty log    |

### Voice (Vivi)

- Deterministic RF intents bypass the LLM and publish MQTT in <12 ms:
  start/stop monitor, scan emergency bands, list bands, stop tuner.
- Replay phrases ("replay that capture") are recognised but explicitly
  NOT dispatched by voice — Vivi tells the operator to confirm at the
  cockpit RF panel. HIGH-risk TX always requires a UI confirmation.
- Conversational queries fall through to the local LLM (`qwen2.5:1.5b`
  by default, see C4 below for latency reality).
- LLM-offline fallback refuses to quote spec ranges; returns "LLM
  offline. I can't answer right now — try again when Ollama is back."

### Real-data discipline

The "no fake/test/placeholder data" rule is encoded structurally,
not just by convention:

- `POST /api/gps/manual` rejects fixes with `accuracy_m > 100m`
  (IP-geolocation is typically 1000m+).
- `feeds.origin()` returns `awaiting` for any fix older than 120s.
- ADS-B aircraft snapshots are not retained on MQTT; cockpit further
  gates on `AIRCRAFT_FRESH_WINDOW_SEC=60`.
- All transient origin-dependent feeds (weather, EMV, POIs, summary)
  publish non-retained; summary loop zeroes location-dependent fields
  when origin is awaiting.
- `_post_settings` filters request body against `SETTINGS_DEFAULTS`
  allowlist; unknown keys silently dropped.
- 11 regression tests in `tests/test_phantom_data_regression.py`
  pin each of these.

## Known incomplete (deferred — not blockers)

1. **rfaudio scanner UI** — operator can `POST /api/rfaudio/command`
   with `{action: scan|start|stop|test_tone|list_bands}` but there is
   no panel UI for frequency tuning or preset selection. Vivi voice
   can trigger emergency-band scan. Full scanner UI deferred until
   hardware is plugged in for visual verification.

2. **LLM latency on Pi 5** — `qwen2.5:3b` (used by `/api/query`)
   regularly exceeds 60s per query under bench load; `qwen2.5:1.5b`
   (used by Vivi) responds in 20-46s. Mechanic/Vivi overlay now shows
   a banner: "Local LLM on a Pi 5 — first response after idle can
   take 30-180s." Cloud cascade (`LLM_CASCADE_ORDER=['groq','claude']`)
   is wired in `llm_client.py` but unused (no keys configured).

3. **Telemetry on bench** — `engine_*` topics show NO DATA because
   there's no CAN traffic without the OBD-II adapter plugged into the
   vehicle. Cockpit honestly shows empty for telemetry until that
   happens. Tests confirm `is_engine_running` answers "I don't have
   a current reading on the rpm sensor right now" rather than guess.

## Hardware checklist for first drive

In order, before turning the key:

- [ ] USB GPS dongle plugged in (or phone tethered to `MZ1312_DRIFTER`
      with browser geolocation enabled — must report `accuracy_m ≤ 100m`).
- [ ] USB2CANFD adapter on `can0`/`slcan0` connected to the X-Type
      OBD-II port. `drifter diagnose` should show `[PASS] can0 — link
      UP, frames seen` once ignition is on.
- [ ] C-Media USB audio dongle for voice alerts via the cabin speaker.
- [ ] USB microphone for `drifter-voicein` if push-to-talk or
      wake-word voice input is wanted.
- [ ] RTL-SDR Blog V4 if TPMS/ADS-B/spectrum scanning is wanted.
- [ ] Flipper Zero on USB-C if sub-GHz capture/replay is wanted.

None of these are required to boot the system — services log
"hardware pending" cleanly and the cockpit shows empty states.

## What to watch in the first 10 minutes

1. **GPS lock** — top-bar GPS cell should transition from `AWAITING`
   to `GPS`/`BROWSER`. Map origin marker appears, view zooms to
   actual location (NOT a default city).

2. **CAN frames** — top-bar CAN cell flips from `OFF` to `UP` once
   ignition is on. `engine_rpm` populates in `/api/state`. If the
   cell stays OFF after 30s of running engine, check that the USB2CANFD
   adapter is plugged in and `can0` is up: `ip link show can0`.

3. **TPMS auto-learn** — first 5-10 minutes after a TPMS sensor
   transmits (varies; some sensors only transmit when wheel is rotating),
   Tires tab populates corner by corner. If 30 minutes of driving yields
   no readings, run `drifter diagnose` and check RTL-SDR is detected.

4. **No phantom data** — Aircraft, EMV, POI, Weather panels should
   stay empty until GPS lock. After lock, give them one polling cycle
   (30s) to populate. If they show data from a wrong city, file a bug.

5. **Voice latency** — first Vivi query after boot loads the model
   (cold start can be 60-90s). Subsequent queries 20-46s. If a query
   takes >3 minutes, treat it as offline; the drawer tabs read live
   MQTT state without the LLM.

6. **Service watchdog** — `journalctl -u drifter-watchdog -f` while
   driving. The watchdog publishes system load every 10s. Restart
   loops (NRestarts climbing) on any unit are a real bug.

## Quick commands

```bash
# Health
curl -fsS http://127.0.0.1:8080/healthz | python3 -m json.tool

# Live MQTT firehose
mosquitto_sub -h localhost -t 'drifter/#' -v

# Diagnostic CLI
drifter diagnose

# Restart everything
sudo systemctl restart drifter-{canbridge,gps,bleconv,rf,rfaudio,flipper,alerts,anomaly,analyst,thresholds,batcher,trip,logger,reporter,feeds,realdash,hotspot,homesync,voice,voicein,vivi,watchdog,dashboard,fbmirror}

# Tail dashboard
journalctl -u drifter-dashboard -f
```

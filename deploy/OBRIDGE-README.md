# DRIFTER OBD bridge — X-Type activation and hardening notes

Vehicle: 2004 Jaguar X-Type 2.5 V6 (AJ-V6). This car is a **K-line** OBD-II
vehicle (ISO 9141-2 / KWP2000 typical of EU petrol 2000–2004), not a CAN-era
car — see `deploy/corpus/xtype/05-obd-protocol.md`. A CANable wired to the DLC
sees silence here because DLC pins 6/14 carry no traffic on this model year.
The ELM327/serial path in `src/obd_bridge.py` is therefore the transport that
will actually produce telemetry.

## Activation change-set (when `/dev/ttyUSB0` appears)

Ordered operator steps:

1. **Identify the chipset** — most ELM327 clones are CH340 or CP210x:
   `udevadm info -a -n /dev/ttyUSB0 | grep -E 'idVendor|idProduct|serial'`
   (CH340 = `1a86:7523`, CP210x = `10c4:ea60`).
2. **Uncomment the matching template** in `config/99-drifter-serial.rules`
   (both variants are pre-written, commented out). If another device shares
   the chipset — an ESP32-Marauder can also be CP210x — add
   `ATTRS{serial}=="<your-serial>"` to disambiguate. Then:
   `sudo udevadm control --reload-rules && sudo udevadm trigger`.
   `config.resolve_device()` already prefers `/dev/drifter-obd`
   (`src/config.py` `OBD_SERIAL_DEV`) so no further config is needed.
3. **Force the transport**: add `DRIFTER_TRANSPORT=elm327` to
   `/opt/drifter/.env` (unit loads `EnvironmentFile=-/opt/drifter/.env`,
   `services/drifter-obdbridge.service`). Accepted values include
   `elm327|elm|obd|obdbridge|serial|kline|k-line` (`src/obd_transport.py`
   `_ENV_ELM`). This defeats auto-selection so a plugged-in CANable cannot
   win arbitration.
4. **Unplug the CANable** from the X-Type OBD port — it cannot reach K-line
   and its presence suppresses the ELM327 transport via
   `obd_transport` arbitration.
5. **Install/enable**: `sudo ./scripts/install-obd.sh` (installs pyserial,
   deploys config, runs `systemctl enable drifter-obdbridge`; idempotent).
   Ordering is safe: unit is `After=drifter-broker.target` and treated as
   hardware-optional by health checks, so early enabling does not fail boot.
6. **Verify**:
   - `journalctl -u drifter-obdbridge -n 30` — expect the
     `ELM327 ready ... protocol:` line with a real K-line label
     ("ISO 9141-2 (K-line)" or a KWP variant), not "auto (not yet determined)".
   - `mosquitto_sub -t 'drifter/#' -v` — expect retained
     `drifter/obd/status` online plus a populated `drifter/snapshot`.

## Pre-hardware hardening set (testable on the bench)

Current behaviour in `src/obd_bridge.py`, and what to change before the
hardware arrives:

1. **Long first-query deadline.** The serial open uses `timeout=1`
   (`obd_bridge.py` `_connect_elm`) and init uses fixed 0.5 s sleeps with a
   single `read(64)` per command. ISO 9141-2 slow bus-init can exceed that,
   so the first session on a cold K-line car may be lost. Replace
   fixed-sleep reads with read-until-`>` loops, and give the post-`ATSP0`
   first probe (`0100`) a 10–15 s deadline that explicitly classifies
   `SEARCHING...` / `BUS INIT: OK` / `BUS INIT: ERROR` /
   `UNABLE TO CONNECT` / `NO DATA`.
2. **Detect protocol after first success.** `detect_protocol()` (ATDPN) is
   called immediately after init, *before* any PID has succeeded — on a slow
   K-line init it reports "auto (not yet determined)". Move it after the
   first successful PID response and re-publish the label on
   `drifter/obd/status`.
3. **Env-tunable baud/poll.** `OBD_SERIAL_BAUD = 38400` and
   `OBD_POLL_HZ = 5` are hard constants in `src/config.py` despite the unit
   comment implying `.env` configurability — wrap them in `os.getenv` ints.
4. **Kill the dead-config trap.** `scripts/install-obd.sh` deploys
   `config/obd.yaml`, but nothing under `src/` loads it (verified by grep).
   Either wire `obd_bridge.py` to read it or stop deploying it.
5. **Mode-03 DTC poll.** Add stored-DTC publishing to
   `drifter/diag/dtc` (`{'stored': [...]}`) so ai_diagnostics/vivi keep
   parity with the CAN path.

## Automated ATSP ladder (design)

`ATSP0` alone relies on adapter auto-detect; if it stalls, walk explicitly:
`ATSP3` (ISO 9141-2 — most likely for this car) → `ATSP4` (KWP 5-baud) →
`ATSP5` (KWP fast) → `ATSP6` sanity, issuing `ATZ` between attempts (some
clones need reset on protocol switch). Persist the winner (e.g.
`data/obd_proto.cache`, keyed on VIN from `drifter/vehicle/profile`) to skip
the ladder next boot; invalidate after repeated unreachable periods. Always
keep `ATS1` (spaces on — the parser depends on it) and `ATL0`; consider
`ATAT1`/`ATST` bumps for slow K-line ECUs. Never send `ATS0` on this path.

## Layered health probe (extend retained `drifter/obd/status`)

Today failures collapse into coarse states (`hw_pending`, deferring-to-
canbridge). Split them into layered substates instead:

- `adapter_ok` — fresh `ATI` contains "ELM327" (cable + chip alive; retry
  once at 9600 before declaring failure).
- `power_ok` — `ATLV` ≥ ~11.5 V (DLC pin 16 fed; works ignition-off).
- `bus_state` — last bus-init text + consecutive `NO DATA` counter.

Published states:

| state | meaning | alert? |
|---|---|---|
| `online` | PIDs flowing | no |
| `idle_ignition_off` | init OK + ≥5 consecutive NO DATA + ATLV ok | no — expected parked state |
| `bus_unreachable` | repeated UNABLE TO CONNECT + ATLV ok | yes — wiring/protocol; trigger ATSP ladder |
| `adapter_error` | no ATI prompt at known baud | yes |
| `hw_pending` | unchanged (no device / deferring) | no |

Run `ATI`+`ATLV` only when no successful PID within T seconds, so steady-state
cost is nil. This cleanly separates ignition-off (layers 1+2 pass, layer 3
silent-after-init) from wrong-protocol (layer 3 cannot init) from dead cable
(layer 1 fails).

## Voltage gap: PID 0x42 absent on K-line

The X-Type's K-line PCM likely does not support PID `0142`, so the `voltage`
key would silently vanish from snapshots. Fallback: when
`supported_from_bitmaps` excludes `0x42`, poll `ATLV` each voltage cycle,
strip the trailing `V`, publish `{'value': v, 'unit': 'V'}` to
`drifter/power/voltage` and include `voltage` in `drifter/snapshot` so
vivi keeps rendering "Battery: …V". Clones give ~0.1 V resolution — fine for
context/alerts.

## Snapshot contract (keys vivi_v2 renders)

From `src/vivi_v2.py` `_telemetry_context` — all flow automatically via
PID_DEFS once discovery succeeds; the only structural gaps are voltage (above)
and Mode-03 DTC:

| key | PID | topic |
|---|---|---|
| rpm | 010C | drifter/engine/rpm |
| coolant | 0105 | drifter/engine/coolant |
| load | 0104 | drifter/engine/load |
| speed | 010D | drifter/vehicle/speed |
| stft1 / ltft1 | 0106 / 0107 | drifter/engine/stft1 / ltft1 |
| stft2 / ltft2 | 0108 / 0109 | drifter/engine/stft2 / ltft2 |
| iat | 010F | drifter/engine/iat |
| maf | 0110 | drifter/engine/maf |
| voltage | 0142 or ATLV | drifter/power/voltage |

Note `010A` is fuel pressure in the registry — correctly excluded from the
context keys.

## Bench validation checklist (engine running)

1. `mosquitto_sub -t 'drifter/obd/status' -v` → `online` + detected protocol string.
2. `mosquitto_sub -t 'drifter/snapshot' -v` → all keys above populated.
3. Key-off test → expect `idle_ignition_off`, **not** `hw_pending` — proving
   the probe distinguishes ignition-off from cable-dead.

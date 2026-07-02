# DRIFTER — Vehicle Profiles (any OBD-II car)

DRIFTER runs on **any OBD-II vehicle**. It identifies the car by VIN, resolves a
*profile*, and the diagnostic / safety / trip pipeline adapts to that profile —
thresholds, engine topology, fuel math, DTC causes, and the LLM prompts all key
off it. This guide explains the profile seam and how to author a
`vehicles/<VIN>.yaml` for your car.

---

## How identification works

1. **`drifter-vehicleid`** (`src/vehicle_id.py`) reads the VIN over OBD-II
   (Mode 09 PID 02, reassembled over ISO-TP), then resolves a profile with this
   precedence (later overrides earlier):
   - **config base / `vehicles/default.yaml`** — the generic starting point for
     an unidentified car (never the X-Type's numbers);
   - **deterministic offline VIN decode** (`src/vin_decoder.py`) — make + model
     year from the VIN structure, no network, no LLM;
   - **`vehicles/<VIN>.yaml`** — a hand-authored profile you drop in (this is
     what this guide is about); if absent,
   - **AI generation** — the LLM cascade decodes the VIN into a profile and
     caches it to `vehicles/<VIN>.json` for next boot.
2. It writes the resolved profile to `config.VEHICLE_PROFILE_FILE`
   (`/opt/drifter/vehicle.yaml`) and publishes it retained on
   **`drifter/vehicle/profile`**.
3. **`src/vehicle_profile.py`** merges that profile over a config-derived base
   and is what every consumer reads. A profile only needs to specify the fields
   that differ from the base; anything it omits falls through.

If no profile is active (bench, tests, a fresh boot before VIN detection), every
accessor returns the config default, so behaviour is unchanged.

### Manual override

- Drop a `vehicles/<VIN>.yaml` (hand-authored **wins** over AI generation and
  over the deterministic decode's non-authoritative fields).
- Force the transport (raw CAN vs ELM327) with `DRIFTER_TRANSPORT=can|elm327`
  in `/opt/drifter/.env` — see the transport note below.

---

## Authoring `vehicles/<VIN>.yaml`

Create a file named for the 17-character VIN, e.g.
`vehicles/1HGCM82633A004352.yaml`. On the Pi these live at
`/opt/drifter/vehicles/`. **VIN files are git-ignored** (they are personal
identifiers) — keep yours on the node, not in the repo.

Every field is optional; unspecified fields fall through to the base. A minimal
profile:

```yaml
vin: "1HGCM82633A004352"
make: "Honda"
model: "Accord"
year: 2003
engine: "3.0 V6"          # free text; used in prompts + bank derivation
fuel_type: "petrol"        # petrol | diesel | hybrid | ev
```

### Full field reference

```yaml
# ── Identity (prompts + UI) ──
vin: "1HGCM82633A004352"
make: "Honda"
model: "Accord"
year: 2003
engine: "3.0 V6"
engine_code: "J30A4"       # optional manufacturer code, shown as "(J30A4)"

# ── Powertrain + topology ──
fuel_type: "petrol"         # petrol | diesel | hybrid | ev  (drives EV/ICE gating)
cylinder_count: 6
bank_count: 2               # OMIT to auto-derive from engine (V/flat=2, inline=1, EV=0)
redline_rpm: 6800
drivetrain: "FWD"
transmission: "5AT"

# ── Fuel / trip math ──
tank_litres: 65
avg_consumption_l_per_100km: 9.5

# ── Tyres (TPMS + advisories) ──
tire_size: "205/60R16"
tire_pressure_front: 32     # PSI
tire_pressure_rear: 32

# ── Known failure modes (grounds the LLM prompts + DTC hints) ──
known_issues:
  - "VTC actuator rattle on cold start"
  - "Rear main seal seepage"

# ── Alert threshold overrides (merged key-wise over the base) ──
thresholds:
  coolant_critical: 118

# ── Calibration baseline overrides (merged key-wise) ──
calibration:
  idle_rpm_baseline: 680

# ── Engine operating windows (coolant/idle/MAF/warmup) ──
engine_params:
  coolant_normal_low: 85
  coolant_normal_high: 100
  idle_rpm_warm_low: 620
  idle_rpm_warm_high: 720
```

### What each block changes

| Block | Consumed by | Effect |
|---|---|---|
| `fuel_type` | alert_engine, trip_computer, obd_pids | EV suppresses combustion rules + combustion-only PIDs; diesel switches the fuel AFR/density; hybrid keeps both. |
| `bank_count` | alert_engine | Dual-bank rules (bank imbalance, whole-engine vacuum) are dropped below 2 banks. |
| `redline_rpm` | safety_engine | Over-rev threshold. |
| `tank_litres`, `avg_consumption_l_per_100km` | trip_computer | Range + baseline economy. |
| `tire_pressure_*` | alert_engine (TPMS) | Low-pressure/rapid-loss thresholds. |
| `known_issues` | analyst / Vivi / ai_diagnostics / Ask-Mechanic prompts | The vehicle-context block the LLM is grounded on. |
| `thresholds`, `calibration`, `engine_params` | alert_engine, adaptive_thresholds | Per-vehicle tuning; merged key-wise so you override one value without dropping the rest. |

---

## Transport: raw CAN vs ELM327 (K-line)

Some cars (many pre-2008, incl. the 2004 Jaguar X-Type) are **not CAN** on the
OBD-II link — they are ISO 9141-2 / ISO 14230 (KWP2000) on the K-line, which a
raw SocketCAN adapter cannot reach. DRIFTER ships **two telemetry transports**:

- **`drifter-canbridge`** — raw SocketCAN (a CANable / gs_usb adapter). Fast.
- **`drifter-obdbridge`** — a generic ELM327 serial adapter. Handles K-line,
  J1850, 29-bit and 250k CAN that raw SocketCAN can't.

Both are monitored services and **auto-select at boot** (`src/obd_transport.py`):
the transport that isn't chosen idles and publishes a hardware-pending status
instead of double-publishing. Precedence: `DRIFTER_TRANSPORT` env override → a
live SocketCAN interface → a plugged CAN serial adapter → an ELM327 device →
default CAN. Force it per node with `DRIFTER_TRANSPORT=elm327` (K-line car with
an ELM327) or `=can`.

## PID discovery

At startup each bridge probes the ECU's **Mode-01 support bitmaps**
(0x00/0x20/0x40/0x60) and polls only the PIDs the car reports — so it adapts to
whatever the vehicle exposes. MAF-less (speed-density) cars report MAP (0x0B);
EV/hybrid cars report battery life (0x5B). If the ECU is silent, it falls back
to the powertrain-appropriate default set for the active `fuel_type`.

## EV / hybrid

Full support at the **standard-PID level** (deep manufacturer EV metrics are out
of scope). Set `fuel_type: ev` (or `hybrid`) and DRIFTER:

- polls the standard hybrid/EV battery-life PID (0x5B) and drops combustion-only
  PIDs on a pure EV;
- suppresses combustion diagnostic rules (fuel trim, coolant, MAF, cold-start …)
  on a pure EV and raises an HV-battery-health alert instead;
- keeps everything on a hybrid (it has both an engine and a traction battery).

---

## Testing a profile offline

```bash
python3 - <<'PY'
import sys; sys.path.insert(0, 'src')
import vehicle_profile as vp
vp.set_active({"make": "Honda", "model": "Accord", "engine": "3.0 V6",
               "fuel_type": "petrol"})
print("identity :", vp.prompt_identity())
print("banks    :", vp.bank_count())
print("is_ev    :", vp.is_ev())
print("issues   :", vp.known_issues())
PY
```

The offline test suite (`pytest -q`) exercises the seam end-to-end; the shipped
2004 Jaguar X-Type is the regression baseline.

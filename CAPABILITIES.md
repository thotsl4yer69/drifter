# DRIFTER — Capabilities

A plain statement of what this node **does** and **does not** do. DRIFTER is a
defensive, situational-awareness and vehicle-diagnostics platform for a car you
own. It is not a covert or offensive tool. This document is the honest scope
that the README and the code back up.

> **Authorized-use only.** Everything here is intended for use on your own
> vehicle, your own networks and devices, and passive observation of public
> RF/airspace where that is lawful in your jurisdiction. Radio, Wi-Fi, and
> automotive regulations vary by country. You are responsible for operating
> within the law where you are. See [§ Does NOT do](#does-not-do) and
> [§ Advanced / gated capabilities](#advanced--gated-capabilities-authorized-use-only).

---

## Operating modes (the master gate)

DRIFTER runs one persona at a time. **A fresh node boots in `diag`** — the
lean, defensive floor — and stays there until an operator deliberately switches
(`sudo drifter mode <name>`). This mode system is the primary capability gate:

| Mode | What runs | Default? |
|---|---|---|
| `diag` | Vehicle telemetry + driver-safety only. No LLM, no STT, no ML, **no recon/offsec.** | **Yes** (`DEFAULT_MODE`) |
| `drive` | Telemetry stack **+** assistant/LLM/voice. Still no recon/offsec. | opt-in |
| `foot` | Recon / situational-awareness persona (Wi-Fi/BLE survey, RF). | opt-in |
| `both` | Everything (bench/lab only; will not fit 8 GB comfortably). | opt-in |

The advanced network-testing tools live **only** in `foot`/`both`, so the
default and the in-car `drive` persona never load them.

---

## Does do

**Any OBD-II vehicle (petrol / diesel / hybrid / EV)**
- Identifies the car by VIN and adapts thresholds, fuel math, DTC causes,
  powertrain rules, and the AI prompts to that vehicle's profile.
- Reads telemetry over **raw CAN (SocketCAN)** or a **generic ELM327 adapter**
  (incl. K-line / J1850 / 29-bit) — auto-selected per car — with per-car PID
  support discovery. EV/hybrid support is at the **standard-PID level**
  (e.g. hybrid battery life); deep manufacturer-specific EV metrics are out of
  scope. Full guide: [docs/VEHICLE_PROFILES.md](docs/VEHICLE_PROFILES.md).

**Vehicle diagnostics & telemetry (the core)**
- Reads OBD-II / CAN bus telemetry (RPM, coolant, fuel trims, MAF/MAP, voltage,
  speed, DTCs) from a car you own, decoded for the active vehicle profile.
- Deterministic driver-safety alert engine (24 powertrain-aware rules):
  overheating, lean/rich fuel trims, alternator/battery faults, over-rev, TPMS
  pressure/temperature, and EV/hybrid HV-battery health.
- Adaptive baseline learning, rolling telemetry stats, per-trip distance/fuel,
  anomaly detection, and a post-drive report.
- RealDash feed (TCP CAN) and a local web cockpit with a `/healthz` endpoint.
- Optional local LLM assistant ("Vivi") and session analyst, running **offline**
  on-device via Ollama by default (no cloud unless you add a key).

**Situational awareness (passive)**
- TPMS sensor reading and passive RF spectrum survey via RTL-SDR (**receive
  only** — the SDR cannot transmit).
- Passive BLE presence/awareness (e.g. surfacing nearby trackers) and passive
  Wi-Fi/Bluetooth survey.
- GPS position for the cockpit map and geo-tagging.
- On-demand listening to public/emergency RF bands you are permitted to monitor.
- Optional weather and points-of-interest enrichment (needs your own API keys).

**Reliability**
- Degrades gracefully when hardware is absent or late at boot (retries with
  backoff; missing dongles don't take the node down).
- Power-cut-safe state writes and a WAL database (survives unclean shutdown).
- Auto-demotes to the lean `diag` mode under memory/thermal pressure.

---

## Does NOT do

- **No RF jamming / denial of service.** The RTL-SDR is receive-only.
- **No attacks against third-party vehicles or infrastructure.** CAN tooling is
  scoped to the vehicle you own.
- **No covert tracking of people.** BLE/Wi-Fi observation is for the operator's
  own situational awareness, not surveillance of individuals.
- **No cloud exfiltration by default.** The stack is offline-first; external
  API calls happen only for features you explicitly enable with your own keys,
  and are isolated to two services so the safety path never blocks on network.
- **No secrets in the repo.** The hotspot PSK is generated per node; API keys
  live only in `/opt/drifter/.env` (git-ignored).

---

## Advanced / gated capabilities (authorized-use only)

DRIFTER also ships network- and RF-testing tooling for **authorized security
testing of your own equipment**. These are **not part of the default or
in-vehicle (`drive`) experience** — they run only in the deliberately-selected
`foot` persona, and several carry additional in-code friction. Use them only on
networks and devices you own or are explicitly authorized to test, and only
where lawful.

| Capability | Unit | Gating |
|---|---|---|
| Wi-Fi/BLE recon (Kismet, wardrive) | `drifter-kismet(-bridge)`, `drifter-wardrive` | `foot` mode only; passive survey |
| Wi-Fi handshake/PMKID audit (bettercap) | `drifter-wifi-audit` | `foot` mode only; allowlist-scoped to your APs |
| ESP32-Marauder Wi-Fi/BT bridge | `drifter-marauder` | `foot` mode; random/rickroll beacon spam **refused in code** (`BEACON_SPAM_*_REFUSE`); disruptive actions require a confirm-token round-trip |
| CAN UDS discovery / fuzz (CaringCaribou) | `drifter-can-discovery` | your own vehicle's bus only |
| HID / BadUSB injection | `drifter-hid` | `foot` mode; ARM → CONFIRM → RUN two-step, never auto-fires |
| Flipper Zero bridge | `drifter-flipper` | `foot` mode; sub-GHz capture — do not replay against third-party systems |
| Counter-surveillance correlator | `drifter-ghost(-voice)` | passive; correlates tracker/RF signals for the operator's awareness |

If you are publishing a fork and want a strictly defensive build, remove these
units from `config.SERVICES` and their `install-*.sh` installers, or simply
never switch out of `diag`/`drive`. See `RELEASE-CHECKLIST.md`.

---

## Hardware it expects

Raspberry Pi 5 (8 GB) on Kali ARM64, plus (all optional — the node degrades
without them): a USB2CANFD / CANable OBD-II adapter, an RTL-SDR dongle, a USB
GPS, a USB microphone + audio out, and optionally a 3.5" SPI LCD. See
`README.md` and `docs/WIRING.md` for the bill of materials and wiring.

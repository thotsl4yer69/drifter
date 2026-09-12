# FIRST DRIVE — touchscreen field runbook

This runbook reflects the **12 Sep 2026 first real vehicle test** and the field-hardening work that followed it. Normal in-car operation is now **touchscreen-first**. Shell commands are engineering/debug fallbacks, not part of the operator workflow.

Primary acceptance vehicle: **2004 Jaguar X-Type**. Primary telemetry path: **ELM327** with automatic OBD-II protocol negotiation.

## Non-negotiable field goals

DRIFTER is not vehicle-ready until it can:

1. cold-boot from vehicle power without requiring a replug;
2. recover its operator display instead of leaving a white panel;
3. discover and configure the attached ELM327 from the touchscreen;
4. prove **adapter communication** separately from **ECU communication**;
5. show live engine data and the transport actually carrying it;
6. operate the RTL-SDR through understandable field missions rather than raw sweep controls;
7. keep one authoritative owner of the RTL-SDR so survey/hunt/listen/capture do not fight over USB;
8. preserve enough evidence to diagnose a failed boot or runtime crash.

## Jaguar retest — normal operator flow

### 1. Power on once

Power DRIFTER and **do not immediately replug it** if the screen takes time to initialise. The display service is configured to keep retrying after controller resets instead of permanently hitting a restart limit.

When the cockpit is available, open **FIELD OPS**. If no vehicle link has been configured, FIELD OPS opens the **VEHICLE** tab automatically.

If the Linux node is alive but the panel is blank/white:

**FIELD OPS → SYSTEM → RECOVER WHITE / BLANK DISPLAY**

A white panel is not automatically treated as a dead Pi.

### 2. Connect one ELM327

For the first Jaguar acceptance run use **one OBD reader at a time**. Plug the ELM327 into the diagnostic connector and turn ignition to **RUN**. Do not add the CANable during this test; a second telemetry transport only makes diagnosis ambiguous.

### 3. Tap AUTO DETECT

Open:

**FIELD OPS → VEHICLE → AUTO DETECT**

DRIFTER attempts the configured link first, then usable serial/USB candidates, paired Bluetooth readers and a reachable Wi-Fi ELM endpoint. An adapter is not saved merely because it was visible: the ELM AT-command handshake must succeed first.

If AUTO DETECT cannot finish setup, tap **SCAN ADAPTERS**. The touchscreen lists:

- USB/serial candidates;
- Bluetooth devices, prioritising likely OBD/ELM names and showing paired/unpaired state;
- visible Wi-Fi networks that resemble ELM/OBD adapters.

### 4. Bluetooth reader

For an already-paired reader tap **CONNECT + SAVE**.

For an unpaired reader, enter the reader PIN on-screen and tap **PAIR + CONNECT**. Common clone defaults are often `1234` or `0000`, but use the reader's documented PIN where available.

The selected Bluetooth reader is only persisted after the ELM handshake succeeds.

### 5. Wi-Fi reader

Choose the ELM Wi-Fi network, enter its password if required and tap **JOIN + AUTO DETECT**.

When a second Wi-Fi adapter is present, DRIFTER prefers that interface for the ELM network so the primary control/hotspot radio is not unnecessarily displaced. With only one Wi-Fi radio, the local Pi touchscreen remains the primary recovery/control surface even if network topology changes while joining the reader.

### 6. Prove ECU communication

Tap **TEST ECU**.

The touchscreen distinguishes four states:

- **adapter not reachable** — DRIFTER cannot yet communicate with the ELM transport;
- **adapter connected / ECU waiting** — ELM communication works but the vehicle ECU has not answered;
- **ECU link online** — standard Mode 01 communication is proven;
- **error** — a specific link/protocol failure was returned.

For a successful Jaguar test, DRIFTER should negotiate the vehicle protocol through the ELM rather than assuming CAN. When supported, RPM is used as an additional live sample proof.

### 7. Confirm live vehicle data

Return to the cockpit and confirm at minimum:

- RPM changes with engine speed;
- coolant temperature is plausible and updates;
- speed is zero while stationary and changes on-road;
- voltage is plausible;
- the UI describes the active ELM/OBD link rather than showing stale CAN-only instructions.

The direct SPI LCD's vehicle empty-state is transport-neutral as well: it reports **vehicle link waiting**, not `can0 idle`.

## RTL-SDR field workflow

Open **FIELD OPS → RF**.

### SURVEY

Tap **SURVEY**. DRIFTER performs a broad environmental sweep, estimates the local noise floor, ranks peaks that clear the adaptive threshold, then automatically performs finer scans around the strongest candidates.

The result is a **Findings** list rather than a raw heat strip. Each finding can include:

- frequency;
- local peak and noise levels;
- delta above local noise;
- rough occupied bandwidth;
- frequency-range context;
- source/classifier information;
- NEW/KNOWN state relative to a saved baseline.

Frequency labels are context, not identity claims. For example, energy inside a cellular allocation is reported as **cellular-band energy**; the spectrum sweep alone does not claim to identify an IMSI catcher or other specific transmitter.

### HUNT

Select a finding and tap **HUNT**. DRIFTER repeatedly measures a narrow span around that frequency and shows current relative power plus a simple **STRONGER / WEAKER / STEADY** trend. This is proximity hunting, not true direction finding.

### ZOOM

Tap **ZOOM ±250K** for a finer spectrum around the selected finding. The targeted result is shown directly in the signal-investigation panel.

### LISTEN

Tap **LISTEN**. DRIFTER chooses a conservative demodulation suggestion from frequency context (for example AM in airband, WFM in FM broadcast, otherwise NFM) and exposes a manual AM/NFM/WFM/USB/LSB override.

### CAPTURE IQ

Choose **5 / 15 / 30 seconds**, then tap **CAPTURE IQ**. The capture is stored as SigMF data + metadata with frequency, sample rate, time, region and a recent GPS fix when one is available.

### BASELINE

After surveying a known environment, tap **SAVE BASELINE**. Later surveys mark peaks that closely match that baseline as KNOWN and surface new candidates as NEW.

### One SDR, one owner

The field stack uses a cross-process SDR lease for on-demand survey/hunt/capture/listen operations. Legacy `rtl_433` monitoring is paused before an operator mission and resumed afterwards. `rfaudio` uses the same lease. The UI exposes the current SDR owner so a busy receiver is explicit rather than becoming a mysterious `device busy` failure.

A second RTL-SDR can still be useful later for genuinely simultaneous continuous monitoring plus operator hunting, but the single-dongle software path must pass acceptance first.

## System recovery

**FIELD OPS → SYSTEM** exposes current boot/watchdog/LCD/network state and the display-recovery action.

If the whole node appears unstable, preserve evidence before repeated power cycling whenever possible. Engineering fallback commands remain available:

```bash
drifter field-dump
drifter obd status
drifter obd test
drifter display status
systemctl --failed
```

These are debugging tools, not required normal operation.

## Acceptance gate

Do not mark DRIFTER field-ready until all of these are green:

- **10/10 cold starts** from the intended vehicle power source without manual replug;
- touchscreen goes from blank vehicle configuration to proved ELM adapter + ECU communication;
- Bluetooth and Wi-Fi ELM setup can be completed without opening a terminal;
- adapter unplug/replug recovers or produces a clear actionable state;
- Jaguar live telemetry remains stable for a **30-minute** stationary/road session;
- display/controller failure can recover without full power cycling when Linux remains alive;
- **RF SURVEY** produces ranked findings without requiring SDR terminology;
- HUNT, ZOOM, LISTEN and IQ CAPTURE operate from the selected finding;
- changing RF modes does not produce unresolved `device busy` contention;
- unplug/replug of the RTL-SDR recovers or produces an explicit missing-hardware state;
- the cockpit never calls unknown hardware READY;
- failures remain diagnosable through the field evidence bundle.

## Generic vehicle scope

The target is **OBD-II-compliant vehicles supported by the attached ELM327**, not literally every car ever produced. DRIFTER should negotiate through the adapter, discover what the ECU actually supports, and degrade with a specific explanation when a vehicle, ECU, PID or protocol is unavailable. The Jaguar remains the primary acceptance platform until this gate passes.

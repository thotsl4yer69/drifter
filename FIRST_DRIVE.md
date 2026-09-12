# FIRST DRIVE — field runbook

This runbook reflects the **12 Sep 2026 first real vehicle test**. The previous version was
bench-oriented and assumed the CANable path. For the 2004 Jaguar X-Type, the acceptance path
is now ELM327-first because the adapter can negotiate the OBD-II physical protocol instead of
assuming SocketCAN/CAN.

## Non-negotiable field goals

Before DRIFTER is considered vehicle-ready it must:

1. cold-boot from vehicle power without requiring a replug;
2. show a usable operator screen rather than a white panel;
3. identify or clearly guide setup of the attached ELM327;
4. prove **adapter communication** separately from **ECU communication**;
5. show live engine data and make its transport/status obvious;
6. preserve logs from failed boots and recoverable crashes.

## First Jaguar retest

### 1. Boot before touching OBD

Power DRIFTER and leave it alone for the first boot attempt.

If the SPI panel stays white or the unit appears dead, **do not immediately keep power-cycling it**.
If you can SSH in, collect evidence first:

```bash
drifter field-dump
sudo drifter display recover
```

`field-dump` records the current and previous boot journals, service restart counts, Pi throttle /
undervoltage flags, framebuffer/SPI state, USB/Bluetooth/network state, retained OBD status and
recent vehicle telemetry. The LCD service now keeps retrying instead of permanently hitting its
restart-rate limit.

After the unit is stable, check:

```bash
drifter display status
drifter obd status
```

### 2. Plug the ELM327 into the Jaguar

Use **one reader at a time** for the first acceptance test. Turn ignition to **RUN**. Engine running
is useful for the RPM proof but is not required for the initial supported-PID response.

Do not plug the CANable in during this test; it adds a second transport and makes diagnosis less
clear.

### 3. Discover the adapter

```bash
drifter obd scan
```

The scan lists:

- serial/USB ELM candidates;
- Bluetooth devices, marking likely ELM/OBD readers and whether they are paired;
- visible Wi-Fi networks that look like OBD/ELM adapters.

### 4A. Bluetooth ELM327

If the reader is shown as **unpaired**:

```bash
sudo drifter obd pair AA:BB:CC:DD:EE:FF
```

Use the MAC printed by `drifter obd scan`. If the adapter requests a PIN, common clone defaults
are `1234` or `0000`.

If it is already paired:

```bash
sudo drifter obd setup
```

or select it explicitly:

```bash
sudo drifter obd use-bt AA:BB:CC:DD:EE:FF
```

### 4B. Wi-Fi ELM327

Join the reader's Wi-Fi network with the DRIFTER Wi-Fi interface, then run:

```bash
sudo drifter obd setup
```

If automatic gateway probing does not identify the TCP endpoint, select the reader explicitly:

```bash
sudo drifter obd use-wifi <ELM-IP> --port 35000
```

Use the address supplied by the adapter/network rather than guessing it.

### 5. Prove the link

Run:

```bash
drifter obd test
```

A useful result has **two independent PASS conditions**:

```text
[PASS] adapter  ELM327 ...
[PASS] link     bluetooth://...   # or wifi:// / serial://
[PASS] ECU      responding via <detected OBD protocol>
[PASS] sample   engine RPM = ...  # when PID 010C is supported/responding
```

Interpret failures literally:

- **adapter FAIL** — DRIFTER cannot communicate with the ELM327 transport yet;
- **adapter PASS / ECU WAIT** — reader is connected, but the vehicle ECU is not answering;
- **NO DATA** — check ignition state and ECU/PID availability;
- **UNABLE TO CONNECT** — the ELM327 could not establish a vehicle OBD protocol.

`ATSP0` is used so the ELM can negotiate a supported vehicle protocol instead of DRIFTER assuming
CAN. That is the correct generic path for older OBD-II vehicles such as the X-Type as well as newer
CAN-based cars, subject to what the adapter itself supports.

### 6. Confirm DRIFTER is actually collecting data

```bash
drifter obd status
mosquitto_sub -h 127.0.0.1 -t 'drifter/engine/#' -v -C 12 -W 5
```

The cockpit **Hardware** page now puts **Vehicle Link** first and reports the selected transport,
ELM/CAN state, whether ECU telemetry is live, and the exact next action if it is not.

For the Jaguar acceptance test, confirm at minimum:

- RPM changes with engine speed;
- coolant temperature is plausible and updates;
- vehicle speed remains zero while parked and changes on the road;
- voltage is plausible;
- no stale CAN-only labels are being used to describe an ELM session.

### 7. If anything fails in the car

Capture the bundle **before rebooting when possible**:

```bash
drifter field-dump
```

Then use the focused checks:

```bash
drifter obd status
drifter obd test
drifter display status
systemctl --failed
```

If the panel alone is white but the Pi is reachable:

```bash
sudo drifter display recover
```

Do not diagnose a white panel as a full Pi boot failure until SSH/network/service evidence confirms
that the Pi itself is down.

## Field acceptance gate

Do not mark DRIFTER vehicle-ready until it passes all of these:

- **10 cold starts** from the intended vehicle power source, 10/10 without manual replug;
- ELM327 setup can go from blank configuration to proved adapter + ECU connection;
- adapter unplug/replug is recovered or produces an explicit actionable state;
- Jaguar live telemetry remains stable for a 30-minute stationary/road session;
- display service/controller failure can recover without a full power cycle;
- after an induced/recovered failure, `drifter field-dump` contains the relevant current and
  previous boot evidence;
- the cockpit never reports raw CAN as the required vehicle path when ELM327 owns telemetry.

## Generic vehicle scope

The target is **OBD-II-compliant vehicles supported by the attached ELM327**, not literally every
car ever produced. DRIFTER should auto-negotiate through the adapter, detect what the ECU actually
supports, and degrade with a specific explanation when a vehicle, ECU, PID or protocol is not
available. The 2004 Jaguar X-Type is the primary acceptance vehicle until this field gate is green.

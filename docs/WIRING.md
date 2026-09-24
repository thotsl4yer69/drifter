# DRIFTER VIM Wiring & Hardware Paths

This guide reflects the **founding-beta vehicle product**, not the wider DRIFTER R&D platform.

The primary Jaguar validation path is **ELM327-first**. Do not assume the vehicle exposes ISO 15765 CAN on OBD pins 6/14 until it is physically proven on that exact vehicle.

## Core founding-beta topology

```text
Vehicle OBD-II port
      │
      └── ELM327-compatible adapter
              │
              ├── Bluetooth ─────┐
              ├── Wi-Fi ─────────┼──> Raspberry Pi 5
              └── USB/serial ────┘        │
                                          ├── touchscreen / browser UI
                                          ├── local telemetry + diagnostics
                                          └── incident evidence storage
```

Use **one OBD transport at a time** during first validation. A second adapter makes transport diagnosis ambiguous.

## 1. Vehicle interface — reference path

### Bluetooth ELM327

1. Plug the ELM327 into the vehicle OBD-II connector.
2. Turn ignition to RUN.
3. Pair the adapter through **FIELD OPS → VEHICLE**.
4. Use **AUTO DETECT**, then **TEST ECU**.
5. Do not treat Bluetooth pairing as proof that the ECU is responding.

DRIFTER distinguishes:
- adapter unreachable;
- adapter connected / ECU waiting;
- ECU link online;
- live PID flow.

### Wi-Fi ELM327

1. Plug the adapter into OBD-II.
2. Join the adapter network from **FIELD OPS → VEHICLE**.
3. Use **JOIN + AUTO DETECT**, then **TEST ECU**.
4. Keep the Pi touchscreen available as the local recovery path if the network changes.

### USB/serial ELM327

Connect the adapter to a Pi USB port. DRIFTER's serial-device resolution prefers an explicit environment override, then a stable udev symlink, then the historical raw device path.

## 2. Raw CAN / SocketCAN path — optional until proven

A CANable/USB2CANFD-class adapter is supported by the architecture, but it is **not the primary Jaguar acceptance transport** until the car proves CAN/ISO-15765 responses on the diagnostic connector.

Do not wire a raw CAN adapter merely because the connector has pins 6 and 14 populated.

If raw CAN is being tested on a vehicle that is confirmed to expose OBD-II over CAN:

```text
OBD-II pin 6  (CAN-H) ──> adapter CAN-H
OBD-II pin 14 (CAN-L) ──> adapter CAN-L
adapter USB ─────────────> Raspberry Pi 5
```

Then run:

```bash
drifter diagnose
ip -brief link show can0
ip -brief link show slcan0
```

A CAN request with no ECU response is evidence to investigate, not proof that the dashboard is broken. On older vehicles the correct path may be ISO 9141/KWP through an ELM327.

## 3. Power

### Bench / development

Use a Pi 5 supply path that can sustain the node under peak load. Record any Raspberry Pi undervoltage/throttle flags during acceptance.

Typical development paths:
- suitable USB-C Pi supply;
- suitable USB-C power bank.

### Vehicle development

The permanent product power architecture is **not yet locked**.

Current candidate classes:
- automotive-rated 12 V → regulated 5 V USB-C supply sized for Pi 5 peak load;
- UPS/HAT path with controlled ride-through/shutdown.

The production choice must pass:
- 10/10 cold boot gate;
- cranking / transient behaviour;
- no undervoltage flags;
- clean recovery after abrupt power loss;
- acceptable idle/off-current behaviour;
- thermal testing in the enclosure.

Do not freeze a retail BOM until that test evidence exists.

## 4. Display

DRIFTER supports more than one operator surface, but the reference physical node currently includes a local display.

Supported development paths include:
- the existing SPI framebuffer panel used by `drifter-lcd`;
- browser cockpit;
- HDMI touchscreen where configured.

Record the exact panel/controller in each beta vehicle report. “3.5-inch screen” is not enough for a reproducible BOM.

## 5. Audio

**Raspberry Pi 5 has no built-in 3.5 mm analogue headphone jack.**

Use an audio device that Linux actually enumerates, for example:
- USB audio adapter;
- HDMI/display audio;
- another explicitly configured USB audio interface.

Confirm with:

```bash
aplay -l
arecord -l
```

Audio is not allowed to block the core vehicle telemetry/incident-evidence path.

## 6. GPS / optional sensors

GPS and other peripheral services are hardware-optional in the beta product unless a test explicitly requires them. Use stable USB device naming where possible and record the exact hardware in the beta issue.

## 7. What is *not* required for VIM sign-off

The broader DRIFTER repository includes SDR/RF and network-research hardware. Those devices are not required to prove the commercial vehicle product.

The VIM physical sign-off is based on:
- vehicle transport;
- ECU/live PID proof;
- repeatable power/boot;
- local display recovery on the reference unit;
- sustained telemetry;
- incident evidence.

## 8. Evidence before changing wiring

When the node fails in-car, capture evidence before rewiring:

```bash
drifter field-dump
drifter obd status
drifter obd test
drifter display status
systemctl --failed
vcgencmd get_throttled
```

That keeps power, display, adapter, ECU and application failures separate instead of changing several variables at once.

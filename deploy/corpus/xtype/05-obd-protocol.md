---
topic: xtype-obd-protocol
tags: [obd, protocol, iso9141, kwp2000, can, elm327, j1962]
vehicle: jaguar-xtype-2004
confidence: medium
---

# X-Type OBD-II protocol and connector reference

What the 2004 X-Type speaks on its diagnostic port, how to confirm it on the physical connector, and what that means for adapter selection. This car predates the CAN-era OBD mandate, so K-line assumptions are correct until proven otherwise on a specific vehicle.

## Protocol context for model year 2004

European petrol cars of this vintage were built to EOBD rules (mandatory for petrol from the 2001 model year in the EU), which permit ISO 9141-2 or ISO 14230 (KWP2000) over a K-line. ISO 9141-2 is the protocol typical of European vehicles built between about 2000 and 2004, and this Jaguar is squarely in that cohort. The US-market CAN requirement (ISO 15765-4) only became mandatory from 2008, so a 2004 X-Type should not be assumed CAN-capable at the DLC. Some Ford-family products used SAE J1850 PWM in North American markets; if a scanner reports J1850 on one of these cars it will be a US-delivery example.

## The practical answer

Treat the MY2004 UK/ROW X-Type as ISO 9141-2 / KWP2000 slow-init on the K-line. An ELM327-class adapter handles both automatically; raw-CAN-only hardware will not talk to this car's powertrain diagnostics at all. If in doubt, the definitive check costs nothing: look into the DLC and see which pins carry metal contacts, then read the pin map below.

## J1962 DLC pinout

The connector is the standard 16-pin J1962 under the driver's side dash. Pins common to every protocol: pin 4 chassis ground, pin 5 signal ground, pin 16 battery positive (fused supply). Protocol pins: pin 7 is the K-line (ISO 9141-2 / KWP2000 bidirectional data), with pin 15 as an optional L-line that many cars omit. Pin 6 and pin 14 are CAN high and CAN low — populated only on CAN-era vehicles. Pin 2 (and pin 10 for PWM) carries J1850 bus traffic. So: contacts in 7 (+ maybe 15) mean K-line car; contacts in 6 and 14 mean CAN car; contacts in 2 mean a J1850 vehicle.

## ISO 9141-2 versus KWP2000 in practice

Electrically the two are near-identical on the wire — same K-line physical layer, asynchronous serial at 10.4 kbaud — differing mainly in initialisation handshake (ISO 9141-2 uses a slow 5-baud init address; KWP2000 adds fast-init patterns). Modern ELM327 firmware tries inits automatically and the distinction rarely matters to software above the transport layer. Expect modest poll rates: batched PID requests complete in tens to hundreds of milliseconds each, so keep poll loops patient rather than hammering.

## Why raw SocketCAN may idle forever

A CAN bus adapter wired to this car's DLC sees silence because pins 6/14 carry no CAN traffic on this model year — the in-vehicle networks (powertrain/body buses) are not exposed as diagnostics-on-CAN at the port. Transport auto-selection logic should therefore prefer the ELM327/K-line path when the vehicle identifies as pre-CAN, and treat a live SocketCAN interface as pending rather than failed when the car simply cannot speak CAN. Battery voltage at pin 16 with no protocol response is the signature of "right car, wrong transport".

## Adapter notes for reliable sessions

Key-on-engine-off is the standard session state; some modules sleep with ignition off and others stay awake briefly after key-off. Keep supply healthy — below roughly 11 V the PCM starts misbehaving during init. Prefer USB or quality Bluetooth serial adapters; cheap clones drop K-line connections mid-session. After connecting, expect a short delay while monitors report; P1000-style readiness codes after any code clearing are normal Ford-family behaviour, not faults.

## Modules behind the port

Legislated OBD emissions data comes from the engine PCM, but the same K-line era also exposes other controllers (transmission, ABS, body systems) through manufacturer-side tooling rather than generic OBD modes — generic scanners will see emissions-related powertrain data primarily. Body-module quirks (central locking, climate) live on the vehicle's own network architecture and generally need Jaguar-specific software.

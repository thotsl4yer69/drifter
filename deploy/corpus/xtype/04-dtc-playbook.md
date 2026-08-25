---
topic: xtype-dtc-playbook
tags: [dtc, p0171, p0174, misfire, p0128, obd, diagnostics]
vehicle: jaguar-xtype-2004
confidence: high
---

# X-Type DTC playbook

How to read and act on the trouble codes this car actually throws. The 2.5 petrol's PCM is a Ford-family ECU, so its code behaviour follows Ford patterns. Codes are starting points, not verdicts — always confirm with live data before replacing parts.

## P0171 and P0174 — both banks lean together

The single most common code pair on this car. Both banks coding lean at once means an engine-wide cause, not two coincidental bank faults. Work through in order of probability. First the three age-related air leaks: the PCV breather hose from valve cover to intake (it splits on its hidden underside), the brake booster vacuum line (cracks at the check-valve bulge), and the IMT valve O-rings on the intake side joints. Second, clean the MAF sensor with proper electronics cleaner — contamination makes it under-report air so the ECU under-fuels. Third, check the flexible intake boot for hidden rips and that its clamp rings are actually fitted. Only then consider fuel delivery: tired pump, blocked filter, or injectors that cannot keep up at load (trims worsen with rpm rather than improving). Positive trims around +20% are roughly where these codes set.

## Single-bank lean variants

When only one bank codes lean, think bank-local causes before engine-wide ones: a leaking intake runner gasket or IMT flap seal on that side, a weak or dirty injector feeding that bank, a small exhaust leak upstream of that bank's oxygen sensor fooling it into reporting lean, or low compression on that bank. A smoke test through the intake will find runner-level leaks the three-hose checklist misses.

## Misfire codes P0300 to P0306

P0300 is random/multiple-cylinder; P0301–P0306 name cylinders 1–6 (bank 1 = cylinders 1-3, bank 2 = 4-6). On this coil-on-plug engine the usual culprits are a failed coil stick, a worn plug, or water ingress into a plug well after intake work. A single-cylinder misfire with otherwise healthy trims points at ignition; a misfire plus positive trims on that bank alone points at air or fuel localisation (runner leak, injector). Persistent misfires overheat the catalyst — treat active flashing-misfire conditions as urgent. Note limiter events and very rough roads can log transient misfire counts too.

## P0128 — coolant below thermostat regulating temperature

The ECU watched coolant temperature fail to reach regulating range within its expected time. On this car that almost always means the thermostat is stuck open or was replaced with the wrong temperature rating — sometimes a faulty coolant temperature sensor reporting low. Symptom context: gauge sitting well below the high-eighties normal after sustained driving, poor cabin heat, fuel economy dip. Fix is a thermostat and housing gasket (the housing is a known leak item anyway) accessed from below.

## MAF and IAT codes — P0100-P0103, P0110-P0113

Range/performance or circuit codes for airflow and intake temperature. P0101-style range codes on this car usually mean genuine sensor drift/contamination rather than wiring, but check the connector and that no unmetered air exists downstream first. IAT readings implausibly far from ambient (or pegged at extreme values like -40°C) indicate a sensor or wiring fault, not weather.

## Catalyst codes P0420 / P0430

Catalyst efficiency below threshold on bank 1 / bank 2 respectively. Before condemning a cat: clear the code, confirm trims are near zero (a running-lean engine mimics cat failure), confirm no misfires, and compare the upstream/downstream oxygen sensor waveforms if possible — a lazy downstream sensor can fake it. These cars' cats also suffer when coolant or ignition problems have been ignored. Rear-cat replacement on an AWD car is not cheap, so verify properly.

## Oxygen sensor heater codes — P0135, P0155 and kin

Heater circuit codes on the pre-cat sensors are usually exactly what they say: dead heater element or blown fuse/wiring. The sensors themselves may still switch fine when warm; heaters matter mainly for closed-loop speed. Post-cat sensors failing electrically tend to show as efficiency codes instead.

## P1000 — monitors not complete

A Ford-family code meaning OBD self-test monitors have not completed since memory was last cleared — common after battery disconnection or code clearing. It is not a fault. Drive a mixed cycle (cold start, steady cruise, decel, warm idle) and it will clear itself once readiness sets. Never clear codes right before an emissions inspection for this reason.

## Charging faults without codes

Alternator and battery problems generate no standard DTCs. Watch the voltage telemetry instead: healthy running shows roughly 13.8–14.4 V; resting battery near 12.6 V; cranking dipping below about 9.6 V means battery or starter trouble. Sustained running voltage under ~13.5 V or erratic swings point at the alternator or its known-fragile cabling rather than the battery.

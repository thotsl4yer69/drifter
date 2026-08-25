---
topic: xtype-telemetry-signatures
tags: [telemetry, fuel-trim, maf, coolant, voltage, signatures]
vehicle: jaguar-xtype-2004
confidence: high
---

# X-Type live telemetry signatures

Pattern-to-cause mapping for the live PID set this car reports: rpm, coolant, voltage, load, stft1/ltft1, stft2/ltft2, iat, maf. Values are for a warm engine in closed loop unless stated.

## Fuel trim thresholds

Short-term trims swing constantly; long-term trims are the memory of those corrections and are the diagnostic signal. Healthy LTFT sits within roughly ±5–8%. Beyond ±10% is a fault worth chasing; around +20% is where lean codes P0171/P0174 set; -20% or worse sets rich codes. Trims reset to zero when codes are cleared or battery power is lost, so a freshly-cleared car needs a drive cycle before its trims mean anything.

## Vacuum leak signature — trims fall with rpm

Positive trims at idle that fall back toward zero when revs rise to 1500–2500 rpm is the classic unmetered-air pattern: at idle the leak is a large fraction of total airflow, at higher airflow it dilutes into insignificance. On this car check the PCV breather hose underside, brake booster hose at the check valve, IMT O-rings and intake boot clamps. Idle rpm may also sit slightly high or hunt. Both banks usually affected together because the plenum is shared.

## MAF under-reporting signature — trims worsen with load

Trims near normal at idle but climbing positive as rpm/load increase points at the MAF reading low rather than a leak (or at fuel delivery struggling). The distinction from weak fuel delivery: a contaminated MAF typically shows its worst at steady cruise, while a tired pump shows steadily worsening positive trim with demand and may also show brief lean spikes on hard acceleration. Clean the MAF element with proper electronics cleaner before condemning it.

## Fuel delivery signature

LTFT drifting slowly upward over weeks of driving suggests gradually clogging injectors or filter. Trims strongly positive under load with normal idle, plus hesitation or stutter on rapid acceleration, fit restricted fuel supply. Confirm with fuel pressure if available; spec region is 3.0–3.5 bar.

## Single-bank versus both-bank logic

stft1/ltft1 cover bank 1 (cylinders 1-3), stft2/ltft2 cover bank 2 (4-6). Both banks equally positive = shared cause (leak, MAF, fuel pressure). One bank markedly worse = bank-local cause: runner gasket, injector, exhaust leak near that bank's pre-cat sensor, or coil/plug issues on that side. Compare the two banks' trims against each other, not just against zero.

## Exhaust leak and lazy-sensor artefacts

Small STFT oscillations are normal closed-loop hunting. A persistent single-bank positive bias with an audible tick at cold start suggests an exhaust leak upstream of that sensor diluting its reading with fresh air. Trims pinned hard at one limit regardless of conditions suggest a failed sensor or wiring rather than a real mixture fault.

## Coolant signatures

Fully-warm coolant settles in the high-eighties to mid-nineties Celsius (thermostat opening near 88°C). A gauge stuck in the sixties-seventies after twenty minutes of driving means thermostat stuck open — expect P0128 eventually and rich-biased trims (the ECU stays in warm-up fuelling). Readings that spike suddenly toward red after running fine indicate real overheating: expansion tank failure, hose loss, water pump, or head-gasket level loss. Cold-start readings far below ambient temperature mean sensor/wiring fault.

## IAT sanity checks

Intake air temperature should track ambient closely at startup and rise moderately with heat soak in traffic (forties Celsius on a hot day is normal; sitting in summer traffic can push higher still). IAT reading implausible extremes (-40°C or +100°C-plus) means sensor or wiring fault. Wildly wrong IAT corrupts load calculation and can masquerade as mixture complaints.

## Charging system voltage bands

Running voltage healthy band: 13.8–14.4 V. Resting battery: about 12.4–12.7 V (below 12.4 V means undercharge or parasitic drain). Cranking dip below roughly 9.6 V flags battery or starter problems. Sustained running below 13.5 V or erratic jumps implicate the alternator or its known-fragile charge cables — no DTC will be set for any of this, so voltage telemetry is the only witness.

## Airflow sanity values for the 2.5

Rule-of-thumb checkpoints for a healthy naturally-aspirated 2.5-litre V6: warm idle draws roughly 2–4 g/s; smooth cruise at motorway speeds lands somewhere in the mid-teens g/s; wide-open throttle approaching peak rpm should approach ten times displacement, i.e. around 25 g/s give or take. MAF readings well below these at matching conditions support the under-reporting diagnosis; readings that flatline or jitter indicate electrical trouble rather than dirt.

## Load percentage expectations

Calculated load at warm idle typically reads in the tens of percent (around 20–35%); light cruise drops it; WOT approaches 90–100%. Idle load pegged much higher with stable rpm hints at hidden friction or accessory drag or sensor offset. Load combined with MAF and rpm cross-checks each other — disagreement between them is itself a finding.

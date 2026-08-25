---
topic: xtype-engine-ajv6
tags: [engine, ajv6, duratec, v6, petrol, specs]
vehicle: jaguar-xtype-2004
confidence: high
---

# Jaguar X-Type 2.5 V6 — AJ-V6 engine reference

Reference for the AJ-V6 2.5 litre petrol engine fitted to the 2004 Jaguar X-Type (X400 platform). Symptom and failure detail lives in the known-failures and DTC files; this file is the mechanical baseline that diagnosis starts from.

## Architecture and origin

The AJ-V6 is Jaguar's version of the Ford Duratec V6 family, a 60-degree all-aluminium V6 whose original development involved Porsche and which uses Cosworth-derived cylinder-head casting methods. Jaguar-specific parts include fracture-split forged powder-metal connecting rods, one-piece cast camshafts, and direct-acting mechanical bucket tappets instead of shim-and-bucket lash adjusters. Block and heads are aluminium; there are no liners to worry about at this displacement. It shares its general layout with the contemporary Ford Mondeo Mk3 V6 but is not an interchangeable drop-in.

## Displacement and key dimensions

Displacement is 2495 cc from a bore of 81.6 mm and stroke of 79.5 mm — undersquare, torque-biased rather than rev-happy. Compression ratio is 10.3:1 and it runs fine on regular unleaded in most markets, though premium was recommended in some. Do not confuse it with the US Duratec 25 (2544 cc, 82.4 mm bore) used in the Contour/Mondeo; the X-Type unit is the shorter-stroke, higher-compression derivative.

## Output and rev limit

Rated output is 193 hp (144 kW / 196 PS) at 6800 rpm with peak torque of about 244 Nm at 3000 rpm. Maximum engine speed is 6800 rpm — the ECU rev limiter sits there, so sustained readings near that ceiling indicate either WOT acceleration or limiter events (misfire logging territory). The engine makes best usable torque low-to-mid range; it needs to be wound out to feel quick, which owners describe as needing revs versus the 3.0's low-end pull.

## Timing drive

The camshafts are driven by chains, not a belt. There is no scheduled timing-belt replacement interval on this engine. Chain-related noise (cold-start rattle from the front of the engine) should be treated as abnormal and investigated rather than dismissed as a maintenance item coming due; healthy examples run quiet chains for very high mileages when oil changes have been kept up.

## Valvetrain, VVT and intake tuning

DOHC, four valves per cylinder, 24 valves total. Intake camshaft variable valve timing (VCT/VVT) adjusts cam phasing per bank using oil-pressure actuation commanded by solenoids; sluggish or dirty oil degrades VCT behaviour quickly. The inlet manifold has an intake manifold tuning (IMT) arrangement of vacuum-operated runner flaps on each side. Each IMT valve joint contains an O-ring seal that hardens with age; a failed O-ring is one of the classic causes of lean codes and rough idle on this car.

## Ignition and fuel system

Coil-on-plug ignition with six individual coils, sequential multi-point injection. Fuel pressure is in the region of 3.0–3.5 bar. Plugs are specified at roughly 70k-mile intervals and replacement requires removing the upper intake manifold, so plug jobs are also manifold-gasket jobs. Double-platinum or iridium plugs gapped around 1.3 mm are the norm; the aluminium heads do not forgive overtightening.

## Cylinder banks and firing order

Firing order is 1-4-2-5-3-6. Bank 1 is the right-hand bank carrying cylinders 1-3; Bank 2 is the left-hand bank carrying cylinders 4-6. On this transverse installation the two banks sit front and back of the engine bay respectively, so "bank" language maps onto front/rear physical position. Both banks feed a common intake plenum, which is why engine-wide causes (vacuum leaks, MAF under-reporting) set lean codes on both banks together while single-bank causes (one injector, one coil, one IMT flap) do not.

## Normal operating ranges

Warm idle sits around 650–780 rpm; cold fast-idle runs roughly 1000–1400 rpm until coolant passes the low forties Celsius. The thermostat begins opening near 88°C and normal fully-warm coolant settles in the high eighties to mid nineties. A gauge reading pinned well below that after twenty minutes of driving means a stuck-open thermostat or missing heat, not a cold day. Oil capacity is approximately 5.5 litres with the filter changed; specification is a 5W-30 meeting Ford WSS-M2C913-type approvals.

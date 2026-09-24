# MAZLABZ DRIFTER VIM — Investor One-Pager

**Stage:** hardware-integrated prototype / founding beta  
**Location:** Victoria, Australia  
**Engineering repository:** public development repository  
**Commercial position:** pre-revenue; product and compatibility validation in progress

## The problem

Intermittent vehicle faults are difficult to diagnose because the useful evidence often disappears before a scan tool is connected or before the driver can describe what happened.

Phone-based OBD tools are useful for codes and live gauges, but the DRIFTER product thesis is different: a **dedicated vehicle node should already be watching when the fault occurs**.

## The product

**MAZLABZ DRIFTER VIM** is a Raspberry Pi/Linux vehicle-intelligence module for:

- persistent OBD-II telemetry;
- deterministic diagnostic and alert logic;
- touchscreen vehicle-link state;
- drive/session logging;
- automatic/manual incident capture;
- local evidence retained around intermittent faults.

The lead capability is a bounded vehicle incident black box:

**90 seconds before → event → 45 seconds after**

The evidence pipeline records material sensor changes and correlation flags for later diagnosis. Correlation is not represented as proof of mechanical causation.

## Why it is different

The first product wedge is not “another code reader.”

DRIFTER combines:

1. a dedicated in-car compute node;
2. persistent telemetry rather than on-demand scanning;
3. pre/post incident evidence;
4. explicit separation of adapter connectivity, ECU communication and live PID flow;
5. local-first Raspberry Pi/Linux services;
6. an extensible vehicle-profile and compatibility evidence model.

## What exists now

Demonstrated engineering includes:

- Raspberry Pi vehicle-node deployment;
- ELM327/K-line and SocketCAN-oriented transport work;
- standardized PID discovery;
- deterministic diagnostic rules;
- drive/session logging;
- incident black box;
- local touchscreen/cockpit;
- recovery tooling;
- safe self-update/rollback architecture.

Primary validation vehicle: **2004 Jaguar X-Type 2.5L V6**.

## What is not claimed yet

DRIFTER is not represented as:

- production-ready;
- universally compatible with all OBD-II vehicles;
- automotive-grade hardware;
- a dealer-tool replacement;
- predictive maintenance proven across fleets;
- a system that proves fault causation.

## Validation plan

### Gate 1 — reference vehicle
- 10/10 cold boots;
- stable 30-minute telemetry;
- deterministic vehicle-link onboarding;
- OBD adapter recovery;
- display recovery;
- useful incident evidence.

### Gate 2 — compatibility
- second vehicle + adapter validation;
- first external founding-beta tester;
- evidence-based compatibility matrix;
- repeatable clean installation.

### Gate 3 — product hardware
- exact reference BOM;
- automotive power path;
- enclosure/thermal validation;
- support burden measured;
- unit cost and assembly time known.

## Market entry

Initial users:

1. DIY owners chasing intermittent faults;
2. Raspberry Pi / automotive electronics builders;
3. older or unusual OBD-II vehicle communities;
4. advanced DIY mechanics and small workshops after reliability proof.

The commercial sequence is deliberately conservative:

**software beta → founding kit → preconfigured node → finished appliance**

## Defensibility direction

No claim is made that the public source code alone is a proprietary moat.

Potential defensibility develops from:

- accumulated compatibility evidence across vehicle/adapter combinations;
- validated onboarding/recovery workflows;
- vehicle profiles and observed edge cases;
- incident evidence datasets;
- product hardware/power/enclosure integration;
- brand, distribution and support;
- proprietary analysis methods only where kept private or otherwise protectable.

## Current traction proxies

There are no fabricated revenue or customer claims.

Current progress includes:

- public founding-beta intake;
- structured vehicle validation protocol;
- media outreach to Hackaday and Raspberry Pi Official Magazine;
- funding-fit enquiries to CSIRO Kick-Start, Breakthrough Victoria and Future Frontier;
- public engineering repository with a working hardware-integrated prototype.

## Capital purpose

Capital should resolve defined commercialisation risks, not fund generic promotion.

Priority uses:

- additional vehicle/adapter test fleet;
- automotive power and enclosure engineering;
- environmental/reliability testing;
- clean-install and manufacturing process;
- beta hardware builds;
- external technical testing;
- product/legal/IP work;
- targeted customer discovery.

## 12-month evidence milestones

- reference vehicle fully accepted;
- exact beta BOM locked;
- multiple validated vehicle/adapter combinations;
- external beta cohort;
- measured onboarding success;
- first paid founding units only after reliability gates;
- commercial name cleared;
- documented unit economics and support burden.

## Investment thesis

If DRIFTER can make intermittent vehicle faults more observable through a persistent, affordable edge node, it can occupy a product category between cheap phone dongles and expensive professional diagnostic equipment.

That thesis remains to be validated through real vehicle evidence and willingness-to-pay, which is the purpose of the founding beta.

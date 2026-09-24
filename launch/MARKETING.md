# DRIFTER VIM — Launch & Marketing System

**Public product name:** DRIFTER VIM  
**Expansion:** Vehicle Intelligence Module  
**Brand:** MAZLABZ  
**Current commercial stage:** Founding beta / field-validation build  
**Primary CTA until vehicle acceptance passes:** Join the beta / follow the build / request early access

## 1. Product wedge

DRIFTER VIM is not positioned as another Bluetooth code reader.

It is a dedicated, local vehicle-intelligence node built around a Raspberry Pi. It watches live OBD-II data, records drive evidence, surfaces diagnostics on a touchscreen and preserves what happened around intermittent faults.

The lead feature is the always-on incident black box:

- bounded 90-second pre-fault evidence window;
- 45-second post-fault tail;
- automatic and manual incident capture;
- ordered sensor-change evidence;
- deterministic correlation flags;
- live touchscreen state.

That is the launch story: **when the fault disappears before you can diagnose it, DRIFTER keeps the evidence.**

## 2. Positioning

### One-line
A dedicated local vehicle computer that logs, diagnoses and preserves the evidence around faults.

### Short
DRIFTER VIM turns a Raspberry Pi into an always-on vehicle intelligence module. It combines OBD-II telemetry, touchscreen diagnostics, drive logging and an incident black box so owners can see what the car was doing before, during and after a fault.

### Category
Vehicle intelligence / automotive diagnostics / Raspberry Pi edge appliance.

### Do not position it as
- a cheap code reader;
- a universal dealer replacement;
- an autonomous repair system;
- compatible with every vehicle without validation;
- a finished production appliance before the physical acceptance gate passes.

## 3. Initial buyers

1. DIY car owners chasing intermittent faults.
2. Enthusiasts who want a dedicated in-car data display rather than a phone app.
3. Owners of older or unusual OBD-II vehicles where generic apps provide poor context.
4. Raspberry Pi / embedded / car-hacking builders who value local control and extensibility.
5. Small workshops and advanced DIY mechanics interested in persistent evidence capture.

The first wedge is **intermittent-fault evidence**, not generic gauge display.

## 4. Differentiators to prove visually

| Capability | DRIFTER VIM story |
|---|---|
| Dedicated node | Lives with the vehicle; no phone required for the core loop |
| Incident black box | 90 s before + 45 s after fault trigger |
| Touchscreen workflow | Vehicle setup, link state, diagnostics and incident capture |
| Transport flexibility | ELM327/K-line plus SocketCAN-oriented paths |
| Local-first architecture | MQTT/service architecture on the Pi |
| Drive evidence | Logging, incident bundles, post-drive analysis |
| Recovery | Explicit display and adapter recovery states |
| Updates | Conservative self-update path that defers while vehicle telemetry indicates activity |
| Extensibility | Open Linux/Raspberry Pi architecture instead of a sealed scanner |

## 5. Claim discipline

Until the field acceptance gate is green, use:
- “hardware-integrated prototype”
- “founding beta”
- “targets standards-based OBD-II vehicles”
- “primary validation vehicle: 2004 Jaguar X-Type”
- “compatibility depends on vehicle, protocol, adapter and supported PIDs”

Do not use:
- “works with every car”
- “production ready”
- “dealer-grade”
- “predicts failures”
- “proves root cause”
- “fully autonomous diagnostics”

Sensor sequencing and correlations are evidence, not proof of causation.

## 6. Naming rule

Use **DRIFTER VIM** or **DRIFTER Vehicle Intelligence Module** in external material, not the bare word DRIFTER.

Reason: an unrelated active mobility/vehicle-data company already markets under Drifter. A formal trade-mark/domain clearance should happen before paid promotion, packaging or hardware manufacture.

## 7. Visual system

Direction:
- black / graphite background;
- instrumentation white;
- restrained amber for warnings;
- electric cyan only for live/connected states;
- dense but legible automotive instrumentation;
- no cyberpunk clutter;
- no fake maps, fake fault codes or fake telemetry;
- show real dashboard screenshots whenever possible.

Image hierarchy:
1. product installed in car;
2. touchscreen with real live telemetry;
3. incident timeline showing BEFORE / EVENT / AFTER;
4. Pi + display + OBD adapter physical stack;
5. evidence bundle / session summary.

## 8. Funnel

### Awareness
Short clips and stills showing one concrete problem:
“Intermittent fault vanished? The evidence shouldn’t.”

### Interest
Landing page explains the dedicated-node difference and black-box workflow.

### Proof
Public build log:
- cold boot count;
- 30-minute Jaguar telemetry run;
- ELM disconnect/recovery;
- white-screen recovery;
- incident capture demonstration.

### Conversion
Before acceptance: beta request / email list.
After acceptance: founding kit / assembled beta unit / software image.

## 9. Channel plan

### Highest priority
- YouTube Shorts / Instagram Reels: 15–35 s fault-evidence demos.
- Reddit: r/CarHacking, r/raspberry_pi and vehicle-specific communities with engineering-first posts.
- GitHub: public technical proof and build documentation.
- Hackaday.io / maker media: build log and technical story.
- Vehicle-specific forums: real Jaguar validation first, then second vehicle.

### Later
- Product Hunt only when onboarding is coherent enough for strangers.
- Automotive creators once a repeatable demo unit exists.
- Workshops/mechanics only after field reliability is defensible.

## 10. Content pillars

1. **Catch the fault** — pre/post incident evidence.
2. **See the link** — adapter vs ECU vs PID flow, no mystery “connected” state.
3. **Own the data** — dedicated local node.
4. **Old car, modern telemetry** — Jaguar reference build.
5. **Built in public** — acceptance tests with pass/fail evidence.
6. **Not another phone dongle** — persistent appliance vs transient app.

## 11. First 12 posts

1. Hero: “Your check-engine light remembers less than DRIFTER.”
2. 90 s BEFORE / fault / 45 s AFTER explainer.
3. Jaguar cold-start test counter.
4. ELM327 adapter vs ECU vs live PID status.
5. Manual “CAPTURE INCIDENT NOW” demo.
6. White-screen recovery demo.
7. 30-minute telemetry validation graph.
8. Pi 5 + display + OBD physical build.
9. “What changed first?” incident summary.
10. Generic OBD-II compatibility explained honestly.
11. Self-update while parked / defers while active.
12. Founding beta call for additional vehicle testers.

Every post should show real evidence, not rendered telemetry pretending to be field data.

## 12. Beta offer

**Founding Beta**

What participants get:
- DRIFTER image/software and documented hardware recipe initially;
- onboarding workflow;
- structured vehicle compatibility test;
- ability to submit field evidence and influence supported profiles.

What MAZLABZ gets:
- real compatibility evidence;
- onboarding failure data;
- second/third vehicle validation;
- testimonials only after real use.

No fixed retail price should be announced until the hardware BOM, install time, enclosure/display/power path and support burden are validated.

## 13. Launch gates

### Gate A — market now
Allowed now:
- build-in-public content;
- landing page;
- beta interest;
- waitlist;
- technical demos using the actual Jaguar/Pi.

### Gate B — founding beta
Required:
- 10/10 cold boots;
- stable 30-minute Jaguar telemetry;
- ELM unplug/replug recovery or explicit actionable failure;
- display recovery;
- deterministic vehicle onboarding;
- clean deployment on the target Pi.

### Gate C — paid kit/preorder
Add:
- locked hardware BOM;
- automotive power solution;
- enclosure/mount;
- second-vehicle validation;
- install guide;
- support/returns model;
- repeatable image/update path.

## 14. Success metrics

Before paid launch:
- beta signups;
- qualified tester replies;
- video completion rate;
- landing-page CTA rate;
- GitHub stars/forks/watchers;
- number of validated vehicle/adapter combinations;
- percentage of onboarding sessions completed without shell access.

The central product KPI is not follower count. It is **how many strangers can connect a supported vehicle and capture useful evidence without expert intervention**.

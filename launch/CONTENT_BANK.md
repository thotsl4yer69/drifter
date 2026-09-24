# MAZLABZ DRIFTER VIM — Launch Content Bank

**Rule:** publish only claims supported by current engineering evidence. Replace brackets with real footage/results only after those assets exist; do not fabricate metrics or screenshots.

## Content system

Every piece of content should do one job:

- show the problem;
- show the evidence mechanism;
- show real field proof;
- recruit a technically relevant tester.

Avoid generic “AI car computer” posts. The strongest story is observable, physical and specific.

## Post 1 — Hero

**Hook:** The fault vanished. The evidence didn’t.

**Body:**  
Most scan tools start watching after you connect them. DRIFTER VIM is being built to stay with the vehicle. Its incident black box can preserve a bounded 90 seconds before an event and 45 seconds after it, so an intermittent fault leaves evidence instead of a memory.

Hardware-integrated prototype. Founding beta now being field-validated.

**Asset:** real installed DRIFTER node or the approved launch creative.

**CTA:** Apply to test a real vehicle + adapter combination.

## Post 2 — 90 / EVENT / 45

**Hook:** What happened *before* the RPM dropped?

**Body:**  
DRIFTER keeps a rolling pre-event buffer. When an incident is captured, the system preserves the lead-up, the event and the post-event tail, then orders material sensor changes for later diagnosis.

90 s BEFORE → EVENT → 45 s AFTER.

Sequencing is measured evidence, not proof of causation.

**Asset:** simple timeline animation using real interface language, no fake telemetry.

## Post 3 — Connected is not connected

**Hook:** Bluetooth connected ≠ ECU connected.

**Body:**  
One field problem we hit early was ambiguity. An OBD adapter can be visible while the ECU is still not answering.

DRIFTER now separates:
1. adapter reachable;
2. adapter connected / ECU waiting;
3. ECU online;
4. live PID flow.

That distinction sounds small until you are troubleshooting a car in a driveway.

**Asset:** real FIELD OPS vehicle-link screen.

## Post 4 — Jaguar validation

**Hook:** We are not claiming “works on every car.”

**Body:**  
The reference validation vehicle is a 2004 Jaguar X-Type 2.5L V6. Before DRIFTER is treated as vehicle-ready it has to pass repeatable cold boots, a 30-minute live telemetry soak, adapter recovery and display recovery.

Compatibility will be added from evidence, not guessed from the OBD-II label.

**Asset:** Jaguar + installed node + current acceptance status.

## Post 5 — Failure is content

**Hook:** This is what failed on the first real in-car test.

**Body:**  
The first field run exposed exactly what bench work missed: unreliable cold boots, unclear ELM327 onboarding and a white display state.

Those failures became the release gate, not something to hide.

**Asset:** issue #67 excerpt + recovery screen + corrected flow.

## Post 6 — Incident button

**Hook:** When the car does the weird thing: press this.

**Body:**  
DRIFTER includes **CAPTURE INCIDENT NOW** for the faults that never happen on command. It freezes the surrounding telemetry window so the later diagnosis starts from data.

**Asset:** real manual capture UI + resulting incident bundle.

## Post 7 — Dedicated node vs phone accessory

**Hook:** Why put a Pi in the car when a Bluetooth dongle exists?

**Body:**  
Because the product thesis is persistence.

A phone scanner is excellent when you choose to scan. DRIFTER is being built as a dedicated node that can already be logging when the intermittent event starts.

That premise now has to earn its value in real beta testing.

**Asset:** split view: dedicated node / phone, without naming or disparaging competitors.

## Post 8 — Build stack

**Hook:** What is DRIFTER actually made of?

**Body:**  
Reference development stack:
- Raspberry Pi 5;
- local display;
- one OBD transport;
- local Linux services;
- persistent evidence storage.

The exact commercial BOM is deliberately not locked yet. We are measuring the real adapter, display, power and thermal requirements before pricing hardware.

**Asset:** labelled real hardware photo after issue #78 capture.

## Post 9 — Power test

**Hook:** A car computer that only boots on the bench is not a car product.

**Body:**  
The release gate includes 10 unique cold starts from the intended vehicle power path with no rescue replug and no Raspberry Pi undervoltage/throttle flags.

**Asset:** cold-boot acceptance counter from real hardware.

## Post 10 — 30-minute soak

**Hook:** One good PID sample proves almost nothing.

**Body:**  
DRIFTER’s reference gate includes a continuous 30-minute telemetry soak across RPM, coolant, speed and voltage, while recording OBD failure states and maximum gaps.

**Asset:** actual acceptance JSON/graph after pass.

## Post 11 — Founding beta

**Hook:** Got an OBD-II car and a Raspberry Pi?

**Body:**  
We are recruiting technically capable founding testers to validate real vehicle + adapter combinations.

One issue = one vehicle + adapter combination. No VINs or credentials in public reports.

**CTA:** GitHub founding-beta vehicle-test form.

## Post 12 — Build in public

**Hook:** The compatibility matrix starts with one car.

**Body:**  
A standards-based architecture is not the same thing as measured compatibility.

DRIFTER uses four evidence levels:
Target → Observed → Functional → Validated.

The goal is to grow that table with reproducible tests rather than marketing assumptions.

**Asset:** compatibility matrix.

## Short-video sequence — first real demo

Once the Jaguar gate produces clean evidence, make one 25–35 second cut:

1. 0–3 s — “THE FAULT VANISHED.”
2. 3–7 s — installed node / live telemetry.
3. 7–12 s — “90 SECONDS BEFORE.”
4. 12–16 s — manual or controlled event capture.
5. 16–20 s — “45 SECONDS AFTER.”
6. 20–27 s — ordered evidence / field bundle.
7. 27–32 s — “THE EVIDENCE DIDN’T.”
8. end — MAZLABZ DRIFTER VIM / FOUNDING BETA.

Use real footage and real UI only.

## Editorial tone

- precise;
- engineering-first;
- no fake urgency;
- no “revolutionary” filler;
- show failures and the fix;
- distinguish demonstrated from targeted capability;
- use the beta to create proof, not to simulate traction.

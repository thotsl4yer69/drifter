# DRIFTER VIM — 30-Day Launch Execution

## Objective

Turn the existing hardware-integrated DRIFTER prototype into a credible public beta with proof-driven marketing.

## Week 1 — proof foundation

- Deploy latest `main` to the Pi.
- Run the physical acceptance harness.
- Capture clean video of boot, onboarding, live telemetry and incident capture.
- Record every failed acceptance item as evidence, not as marketing copy.
- Create the first product photos: installed dashboard, Pi stack, OBD interface.
- Publish landing page in beta mode.

Deliverable: one credible 30–60 second end-to-end demo.

## Week 2 — audience seeding

- Publish the engineering demo to short-form channels.
- Post the build to Raspberry Pi / car-hacking communities with code and test evidence.
- Open founding-beta applications for additional vehicle/adapter combinations.
- Publish one technical post explaining the incident black box.
- Publish one failure/recovery post to establish engineering credibility.

Deliverable: first qualified beta testers.

## Week 3 — compatibility expansion

- Test a second OBD-II vehicle.
- Convert test results into a compatibility matrix.
- Improve touchscreen onboarding based on tester friction.
- Publish comparison content focused on “persistent appliance vs phone scan tool”, without unsupported competitor claims.

Deliverable: second validated vehicle path and clearer onboarding.

## Week 4 — commercial decision

If acceptance + second-vehicle validation are green:
- lock a founding-kit BOM;
- price the real hardware + assembly + support;
- open a paid founding batch.

If not:
- keep beta CTA;
- publish the remaining engineering gate publicly;
- do not fake a launch date.

## Immediate technical blockers

From the current repository:
- issue #67 remains open;
- 10/10 cold boot gate still needs physical proof;
- 30-minute Jaguar telemetry proof still needs physical proof;
- display recovery and ELM recovery need physical proof;
- historical API keys should be rotated provider-side before broad public exposure;
- the committed real VIN profile should be removed from the public repository or replaced with a sanitised example.

## Public identity

Use:
**DRIFTER VIM**
Vehicle Intelligence Module
by MAZLABZ

Do not build paid campaigns around the unqualified bare name “DRIFTER” until formal name clearance is complete.

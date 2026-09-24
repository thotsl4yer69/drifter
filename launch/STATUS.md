# MAZLABZ DRIFTER VIM — Launch Authority

**Last updated:** 24 September 2026  
**Engineering codename:** DRIFTER  
**Public beta name:** MAZLABZ DRIFTER VIM — Vehicle Intelligence Module  
**Current stage:** Founding beta / physical acceptance  
**Main launch commit:** `39d9173bef924603c0ea2fdda19c6db7621e9448`

This file is the launch authority. If older copy, mockups, posts or ideas conflict with it, this file wins until explicitly updated.

## Product promise

**The fault vanished. The evidence didn't.**

DRIFTER VIM is a dedicated Raspberry Pi vehicle-intelligence node focused on persistent OBD-II telemetry, diagnostics, drive logging and incident evidence.

The lead capability is the incident black box:

- bounded 90-second pre-event window;
- automatic or manual capture;
- 45-second post-event tail;
- ordered material sensor changes;
- deterministic correlation flags;
- local evidence preserved for later diagnosis.

## What we are selling today

Nothing is represented as a finished retail appliance yet.

The current offer is **Founding Beta access** for technically capable testers who can produce physical vehicle + adapter evidence.

Public CTA hierarchy:

1. Apply for founding beta.
2. Review the engineering repository.
3. Follow field-validation progress.

Do not announce hardware preorders, production dates or final retail pricing yet.

## Runtime authority

The reference VIM beta runtime is the lean vehicle-first `diag` persona.

The repository's `foot` / `both` recon, network-audit, HID and unrelated RF research features are R&D platform capabilities. They are not part of the VIM customer promise.

Vehicle reliability outranks AI/voice features.

## Claims allowed now

- hardware-integrated prototype;
- Raspberry Pi/Linux vehicle node;
- OBD-II-oriented telemetry architecture;
- ELM327/K-line and SocketCAN-oriented transport work;
- MQTT-based service architecture;
- deterministic diagnostics and alerts;
- drive/session logging;
- 90 s pre-event + 45 s post-event incident evidence;
- touchscreen field workflow;
- conservative self-update/rollback architecture;
- primary validation vehicle: 2004 Jaguar X-Type 2.5L V6;
- targets standards-based OBD-II vehicles.

## Claims not allowed yet

- production ready;
- works with every OBD-II vehicle;
- dealer-tool replacement;
- proven predictive maintenance;
- proves mechanical root cause;
- every ELM327 clone supported;
- automotive-grade hardware;
- validated EV/hybrid compatibility;
- final retail price or ship date.

## Physical gate — issue #67

Paid hardware launch remains blocked until at least:

- [ ] 10/10 cold starts from intended vehicle power;
- [ ] stable 30-minute Jaguar RPM/coolant/speed/voltage telemetry;
- [ ] deterministic blank-to-ECU onboarding;
- [ ] ELM unplug/replug recovery or specific actionable failure;
- [ ] display recovery without full power cycle where Linux remains alive;
- [ ] useful field-dump / incident evidence after a fault.

## Commercial gate

Before paid promotion or manufactured packaging:

- [ ] rotate historically committed OpenWeatherMap / Google Maps credentials provider-side (#73);
- [ ] decide whether to rewrite git history for historical identifier/key removal (#73);
- [ ] complete final product-name/trademark/domain/handle clearance (#75);
- [ ] lock hardware BOM, power path, display and enclosure;
- [ ] validate a second vehicle + adapter combination;
- [ ] measure clean-install and support burden.

## Naming authority

Use **MAZLABZ DRIFTER VIM** throughout founding beta.

Do not build paid campaigns around the bare word **DRIFTER**. An unrelated mobility/vehicle-data company is actively using Drifter in overlapping commercial territory. Final commercial naming is tracked in #75.

## Visual authority

- black / graphite base;
- instrumentation white;
- restrained amber = warning/event;
- electric cyan = live/connected only;
- real vehicle/Pi/dashboard imagery;
- clean technical instrumentation;
- no fake telemetry values;
- no supercar stock imagery;
- no cyberpunk clutter.

## Audience order

1. DIY owners chasing intermittent faults.
2. Raspberry Pi / embedded automotive builders.
3. Older/unusual OBD-II vehicle enthusiasts.
4. Advanced DIY mechanics / small workshops after reliability proof.
5. Broader consumer buyers only after appliance-level onboarding exists.

## Distribution authority

Highest-value current channels:

- GitHub engineering proof;
- Hackaday;
- Raspberry Pi Official Magazine;
- Hackster/Electromaker project pages when accounts are available;
- vehicle-specific forums after real validation evidence;
- short-form video built around one real incident-capture demo.

Do not spend paid media before naming and hardware gates are resolved.

## Current assets

- `README.md` — public project entry.
- `site/index.html` — founding-beta landing page source.
- `site/press.html` — public media facts source.
- `docs/QUICKSTART_BETA.md` — one-path setup.
- `docs/BETA_TESTER_GUIDE.md` — beta test protocol.
- `docs/COMPATIBILITY_MATRIX.md` — evidence-only compatibility.
- `docs/VIM_PRODUCT_PROFILE.md` — product/R&D scope boundary.
- `launch/MARKETING.md` — marketing system.
- `launch/PRESS_KIT.md` — messaging/press facts.
- `launch/MARKET_SNAPSHOT_2026-09-24.md` — dated market reference.
- `launch/OUTREACH.md` — external media contact log.
- `launch/FUNDING.md` — funding execution path.
- `launch/FUNDING_OUTREACH.md` — capital/program contact log.

## Hosting / CI state

GitHub Actions currently fails before runner allocation with no workflow steps executed. This affects both repository CI and the Pages deployment. Treat this as infrastructure-unverified, not CI-green and not a code failure.

The site source is complete in-repo; public Pages URL is not yet confirmed live.

## Next execution priority

1. Pass Jaguar physical acceptance.
2. Capture real demo footage/screenshots during acceptance.
3. Convert real evidence into launch media.
4. Add first external beta vehicle.
5. Lock hardware BOM and commercial form factor.
6. Resolve final product name.
7. Only then open paid founding hardware.

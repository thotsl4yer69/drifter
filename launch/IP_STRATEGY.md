# MAZLABZ DRIFTER VIM — IP & Public-Disclosure Strategy

**Purpose:** preserve future commercial options while acknowledging that substantial DRIFTER engineering is already public.

This is an operational IP map, not legal advice or a patentability opinion.

## Ground truth

The DRIFTER GitHub repository is public and licensed under MIT.

That means:

- published source code is intentionally available under the repository licence;
- earlier public commits and documentation already constitute public disclosure of substantial technical material;
- a future commercial moat cannot be based on pretending the existing public code is secret.

## What can still be protected commercially

### 1. Brand

The final product name, visual identity and marks can be protected separately from software copyright.

Current action:
- founding beta uses **MAZLABZ DRIFTER VIM**;
- final commercial naming is unresolved because of overlapping Drifter use in mobility/vehicle-data markets;
- issue #75 owns clearance before paid launch.

### 2. Copyright

New code, documentation, UI assets, diagrams, photographs and marketing assets have copyright protection subject to their licences/ownership.

Keep:
- clear authorship;
- source files;
- dated commits;
- contractor/third-party ownership terms where applicable.

### 3. Trade secrets / know-how

Do not put every future commercial advantage into the public repository by default.

Potential private know-how can include:
- production calibration/process data;
- proprietary compatibility datasets;
- manufacturing/test fixtures;
- supplier and unit-cost data;
- customer/support data;
- private model/data pipelines;
- unreleased analysis methods;
- commercial deployment/operations processes.

A public-core / private-commercial-layer model is possible if deliberately managed.

### 4. Patents

Do not assume patent rights survive prior public disclosure in every jurisdiction.

Before spending heavily on patent drafting:
- inventory what was disclosed publicly and when;
- identify genuinely new technical subject matter not already disclosed;
- obtain qualified patent advice on novelty, inventive step, ownership and jurisdiction-specific grace periods;
- file before future public disclosure where protection is strategically justified.

Never describe a feature as “patent pending” unless an application has actually been filed.

## Recommended repository boundary

### Public
- core vehicle-node architecture;
- beta onboarding;
- compatibility reporting format;
- developer documentation;
- enough implementation for an open technical community.

### Consider private where commercially justified
- manufacturing tooling;
- production test systems;
- supplier/cost sheets;
- private field datasets;
- customer-specific vehicle data;
- unreleased commercial analytics;
- internal sales/support systems;
- future protectable inventions before IP review.

## Data rights

Vehicle telemetry and incident evidence can be sensitive.

Before a hosted/fleet product:
- define who owns raw vehicle data;
- define consent for diagnostic uploads;
- minimize VIN/account/location exposure;
- define retention and deletion rules;
- separate public compatibility evidence from private customer records.

The current beta intake already prohibits public posting of VINs, API keys and credentials.

## Open-source commercial model

MIT licensing allows others to use the public code subject to the licence terms. Commercial differentiation therefore needs to come from execution:

- validated hardware;
- easy installation;
- reliable updates;
- compatibility evidence;
- support;
- brand;
- private services/data where justified.

Do not depend on code secrecy that the project has already given away.

## Immediate actions

- [ ] preserve a dated public-disclosure inventory;
- [ ] capture ownership of all new design/code/media work;
- [ ] resolve final mark/name (#75);
- [ ] keep customer/vehicle datasets outside the public repo;
- [ ] conduct patent review before publishing any genuinely new protectable invention;
- [ ] decide whether future commercial layers remain MIT/public or use separate private repositories/services.

## Public-disclosure register

Known public technical themes already include:
- Raspberry Pi vehicle node;
- OBD-II telemetry architecture;
- ELM327/K-line and SocketCAN paths;
- MQTT service decomposition;
- incident evidence window;
- deterministic diagnostic logic;
- vehicle profiles;
- dashboard/recovery architecture;
- self-update/rollback concepts.

A patent professional should be given the repository history, not only the current README.

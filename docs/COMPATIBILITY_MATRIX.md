# DRIFTER VIM — Compatibility Matrix

This matrix records **evidence**, not assumptions. A standards-compatible vehicle is not labelled validated until it passes the physical test sequence.

| Vehicle | Powertrain | Adapter / transport | Protocol | Status | Evidence |
|---|---|---|---|---|---|
| 2004 Jaguar X-Type 2.5L V6 | Petrol | ELM327 path | ISO 9141/KWP target; physical confirmation pending | Primary validation | Issue #67 acceptance gate open |

## Status definitions

- **Target** — architecture should support the combination but no physical proof has been recorded.
- **Observed** — adapter + ECU communication or valid PID flow has been observed.
- **Functional** — normal onboarding and live telemetry work without shell intervention.
- **Validated** — repeatability gate passed, including sustained telemetry and recovery behaviour.

## Promotion rule

Marketing may say DRIFTER VIM **targets standards-based OBD-II vehicles**. It may name only rows marked **Validated** as tested vehicle/adapter combinations.

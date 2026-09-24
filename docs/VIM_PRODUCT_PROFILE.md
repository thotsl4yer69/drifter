# MAZLABZ DRIFTER VIM — Product Profile

DRIFTER's repository contains a wider R&D platform. **DRIFTER VIM** is the focused vehicle product built from it.

## Included in the VIM story

- OBD-II vehicle-link discovery and proof;
- ELM327/K-line and SocketCAN-oriented telemetry paths;
- live vehicle dashboard;
- deterministic alerts and diagnostics;
- drive/session logging;
- incident black box;
- vehicle profiles and PID discovery;
- field evidence capture;
- display/adapter recovery;
- local update path;
- optional post-drive analysis where hardware resources allow it.

## Not part of the commercial VIM story

The repository's `foot` / `both` research personas, Wi-Fi auditing, HID tooling, recon features and unrelated RF experiments are **lab capabilities**, not VIM product features.

They should not appear in:
- consumer landing pages;
- pricing;
- beta promises;
- compatibility claims;
- product demos intended to explain DRIFTER VIM.

## Founding-beta runtime

Use the default lean `diag` persona as the reference VIM runtime until physical acceptance demonstrates that heavier local-AI services can be enabled without reducing vehicle-node reliability.

```bash
sudo drifter mode diag
```

Reliability of vehicle telemetry and incident evidence outranks assistant features.

## Promotion hierarchy

1. **Incident evidence** — the wedge.
2. **Dedicated vehicle node** — the form factor.
3. **Transparent link state** — adapter vs ECU vs PID proof.
4. **Persistent logging** — the data story.
5. **Local diagnostics** — the intelligence layer.
6. **AI/voice** — optional enhancement, not the core promise.

## Product gate

Do not convert the beta into a paid hardware product until:
- primary vehicle acceptance passes;
- power/display/enclosure BOM is locked;
- second vehicle is validated;
- clean install is repeatable;
- support burden is measured;
- commercial naming has been cleared.

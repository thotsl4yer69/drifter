# AirTag detection — verification notes

`config/ble_targets.yaml` ships an `airtag` target that is **disabled and
unverified** by design. This file is the gate for flipping it on. Read
through before setting `enabled: true` and `verified: true`.

## TL;DR

The airtag target as written in `ble_targets.yaml` will, with the current
matcher, match **every Apple device in BLE range** — iPhones, AirPods,
Apple Watches, MacBooks. Don't enable it without first reading the
[Matcher limitation](#matcher-limitation-or-vs-and) section.

## How AirTags advertise

Apple's "Find My" protocol uses the Apple manufacturer ID `0x004C` with a
"Continuity" payload. The first byte of the manufacturer data is the
sub-type:

| Sub-type | Used by                                  |
|----------|------------------------------------------|
| `0x05`   | AirDrop                                  |
| `0x07`   | AirPods proximity                        |
| `0x09`   | Apple TV, HomeKit                        |
| `0x10`   | Nearby Info (iPhones, iPads broadcasting) |
| `0x12`   | **Offline Finding (AirTag, Find My accessories)** |

For `0x12` Offline Finding, the second byte is the length — typically
`0x19` (25 bytes). So an AirTag-class advertisement starts with the
manufacturer data prefix `12 19` (hex), which is what
`ble_targets.yaml`'s `manufacturer_data_prefix: "1219"` is targeting.

The remaining 25 bytes are the rotating advertising key plus status
bits (battery, hint flags). Apple rotates this key every ~15 minutes,
so AirTag MAC addresses are not stable.

References (offline-known, verify before relying on):
- Apple's *Find My Network Accessory Specification* (developer.apple.com/find-my)
- Heinrich et al., "Who Can Find My Devices? Security and Privacy of
  Apple's Crowd-Sourced Bluetooth Location Tracking System" (PoPETs 2021)

## Matcher limitation: OR vs AND

`src/ble_passive.py::target_matches` runs **OR** across a target's
criteria — any one match is enough:

```python
return (
    matches_oui(target, mac) or
    matches_manufacturer_id(target, mfr_data) or
    matches_manufacturer_data_prefix(target, mfr_data) or
    matches_service_uuid(target, service_uuids)
)
```

The `airtag` target sets BOTH `manufacturer_id: 0x004C` and
`manufacturer_data_prefix: "1219"`. With OR semantics, **any** Apple
advertisement (manufacturer ID `0x004C`) is a match — the prefix never
tightens the rule, it only loosens it.

Consequences:
- An iPhone in your pocket will fire `airtag` detections.
- AirPods nearby will fire detections.
- A neighbour's MacBook on the kerb will fire detections.

This makes the current target useless for AirTag-specific detection and
will fill `ble-events.db` with noise.

## Two ways to fix

### Option A: drop manufacturer_id, keep only the prefix

Edit `ble_targets.yaml`:

```yaml
- name: "airtag"
  match:
    manufacturer_data_prefix: "1219"
  # leave manufacturer_id unset
```

`matches_manufacturer_data_prefix` walks the advertisement's
manufacturer data dict and checks the bytes for `0x004C` (it reads
`want_id = target['match'].get('manufacturer_id')`). With
`manufacturer_id` unset it short-circuits to False — so this option
**won't work as-is** without code change. See option B.

### Option B (recommended): add AND semantics to the matcher

Extend `target_matches` to honour `match_mode: "all"` per target so an
AirTag rule can require both manufacturer ID AND prefix:

```yaml
- name: "airtag"
  match_mode: all
  match:
    manufacturer_id: 0x004C
    manufacturer_data_prefix: "1219"
```

Implementation sketch (in `src/ble_passive.py`):

```python
def target_matches(target, mac, mfr_data, service_uuids):
    mode = target.get('match_mode', 'any')
    checks = []
    if target['match'].get('oui_prefixes'):
        checks.append(matches_oui(target, mac))
    if target['match'].get('manufacturer_id') is not None:
        checks.append(matches_manufacturer_id(target, mfr_data))
    if target['match'].get('manufacturer_data_prefix'):
        checks.append(matches_manufacturer_data_prefix(target, mfr_data))
    if target['match'].get('service_uuids'):
        checks.append(matches_service_uuid(target, service_uuids))
    if not checks:
        return False
    return all(checks) if mode == 'all' else any(checks)
```

This is a non-trivial change because every existing target currently
relies on `any` semantics; the default must stay `any` and `all` is
opt-in. Add a test mirroring `test_combined_match_or_semantics` for
the AND path.

## Verification protocol (before flipping `verified: true`)

1. Place a known AirTag (paired or unpaired) within 1m of the Pi's BLE
   antenna.
2. With the bleconv service running and the target configured per
   Option B above, enable it: `enabled: true`, `verified: false`
   (the runtime gate forces unverified targets off — bypass briefly
   for the test by also setting `verified: true` on a scratch branch).
3. Watch:
   ```bash
   mosquitto_sub -h localhost -t 'drifter/ble/detection' -v
   ```
4. Confirm a hit fires within ~30s with `manufacturer_id: 0x004c` and
   `raw_advertisement` starting `1219`.
5. **Negative test**: bring an iPhone into range with the AirTag absent
   and a known AirPods case — confirm those do NOT trigger
   `target: airtag`. (They may trigger nothing, or trigger a different
   target if added.)
6. Document the firmware version of the AirTag tested, the date, and
   the antenna location in a follow-up commit's body before flipping
   `verified: true`.

## Privacy and legal

Detecting AirTags has been written into law in some jurisdictions as
a defensive feature (anti-stalking). Detecting **all Apple devices**
has not — this is mass-surveillance grade output. Don't ship a target
that conflates the two.

The `home_sync` exclusion list (`drifter/ble/+`) keeps detection
events from leaving the Pi, but that doesn't change the legal weight
of the local SQLite log. Keep `BLE_LOG_RETENTION_DAYS` short.

## Tile (same family, different signal)

`tile` is configured with the legacy Tile service UUID
`0000feed-0000-1000-8000-00805f9b34fb`. That UUID is registered to
multiple vendors — it is not a stable Tile signal on its own. Verify
against current Tile firmware (Tile Mate gen 4+, Tile Pro 2024+) before
enabling. Tile's protocol has shifted with the Amazon Sidewalk merger;
older docs may no longer apply.

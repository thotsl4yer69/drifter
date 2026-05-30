# DRIFTER BLE Stack — Reference

Phases 4.5 → 4.8 added a complete passive-BLE counter-surveillance
pipeline to drifter. This document is the single place to look up
how it works, why specific numbers were chosen, and what's known
broken.

UNCAGED TECHNOLOGY — EST 1991

---

## 1. Overview

**Passive scanner (4.5–4.6).** `drifter-bleconv` listens to BLE
advertisements via BlueZ (bleak), matches them against
`config/ble_targets.yaml`, and publishes hits to MQTT topic
`drifter/ble/detection`. No probe requests, no GATT connections, no
transmissions — listening only. Targets ship with `verified=false`
forced disabled at runtime; only the Axon-OUI (`00:25:DF`) target is
verified on. AND-mode matching is supported via `match_mode: all` so
AirTag-style rules (`manufacturer_id` AND `manufacturer_data_prefix`)
can tighten rather than loosen.

**Persistence (4.7).** Every detection is written to
`/opt/drifter/state/ble_history.db` with GPS (when available) and a
`drive_id` that resets after a 30-min idle gap. The scanner's MQTT
publish path runs FIRST, then a try/except DB insert — a sqlite outage
can never block the live tile or Vivi context. The same DB also
absorbs external publishes to `drifter/ble/detection` (synthetic tests,
future sensor bridges) via a loopback subscriber that skips messages
tagged `source=scanner` to avoid double-persist.

**Follower scoring (4.8).** `score_persistent_contacts` reads the
history, collapses raw MACs into stable identities (mfr+name
fingerprint, stable-MAC class, AirTag/anon, OUI-fallback), runs DBSCAN
geo-clustering on the GPS-bearing rows, and ranks identities by
`follower_score = unique_geo_clusters × unique_drive_ids × confidence`.
Filters drop single-drive, low-count, no-GPS, and single-cluster
identities. Output is exposed at `/api/ble/persistent` and on the
dashboard's "Persistent Contacts" panel. Vivi gets a one-line summary
on demand only — no proactive comments until the scoring is
ground-truthed against real data.

---

## 2. Topology

```
                ┌──────────────┐
                │  hci0 (BLE)  │      ← Pi 5 onboard radio, listen-only
                └──────┬───────┘
                       ▼
          ┌────────────────────────┐
          │   bleak (BleakScanner) │  ← passive scan, no GATT
          └────────────┬───────────┘
                       ▼
   ┌────────────────────────────────────┐
   │ ble_passive.BLEScanner             │
   │   _detection_callback              │  match against ble_targets.yaml
   │   ↓                                │  rate-limit per (target, mac)
   │   _record_detection                │  attach GPS if fresh (10s)
   └─────┬─────────────────────┬────────┘
         │ MQTT publish        │ inline DB insert
         │ (tagged source=     │ ble_history.insert_detection
         │  scanner)           │  (try/except — never blocks publish)
         ▼                     ▼
   drifter/ble/detection   ble_history.db (WAL)
         │                          ▲
         │ (broker echoes back)     │
         ▼                          │
   _on_mqtt subscriber              │
   _persist_external_detection      │ external mqtt_pub
   (skips source=scanner)──────────►│ → DB
                                    │
                                    ├─ query_history
                                    │     │
                                    │     ▼
                                    │ /api/ble/recent       (live tile)
                                    │ /api/ble/history      (24h panel)
                                    │ /api/ble/drives       (drive list)
                                    │ /map/ble              (Leaflet)
                                    │
                                    └─ score_persistent_contacts
                                          │
                                          ▼
                                       /api/ble/persistent  (panel)
                                       ble_persistence.get_persistent_contact_summary
                                          │
                                          ▼
                                       vivi.py PERSISTENT_CONTACTS context
```

Other consumers:
- `drifter-dashboard` web UI — live tile (5s poll), 24h history panel,
  persistent-contacts panel, `/map/ble` Leaflet view.
- `drifter-vivi` — pulls a cached (60s TTL) persistent-contact summary
  into the prompt context block when non-empty. On-demand only — no
  proactive comments yet.
- `drifter-homesync` — explicitly EXCLUDES `drifter/ble/+` and
  `drifter/audio/+` from the home-node mirror (`HOMESYNC_EXCLUDE_TOPICS`
  in `config.py`). BLE detection data stays local-only.
- `drifter-ble-export` (CLI) — CSV / GeoJSON / JSON dump from
  ble_history.db with --since / --until / --target / --drive-id filters.

---

## 3. Data model — `detections` table

Single table at `/opt/drifter/state/ble_history.db`. WAL journal mode,
busy_timeout 5000 ms, schema versioned via `PRAGMA user_version`
(currently 1).

| Column            | Type    | Source                                 | Nullable | Notes |
|-------------------|---------|----------------------------------------|----------|-------|
| `id`              | INTEGER | PK autoincrement                       | no       |       |
| `ts`              | REAL    | `time.time()` at scan time             | no       | UNIX seconds (float) |
| `target`          | TEXT    | matched rule's `name` field            | no       | e.g. `axon`, `axon-class`, `tile`, `airtag` |
| `mac`             | TEXT    | `device.address`                       | no       | 6-octet `AA:BB:CC:DD:EE:FF` |
| `rssi`            | INTEGER | advertisement RSSI                     | yes      | dBm; null if `int(...)` of None |
| `manufacturer_id` | TEXT    | `0x{first manufacturer key:04x}`       | yes      | hex string |
| `adv_name`        | TEXT    | `advertisement_data.local_name`        | yes      | accepts wire fields `adv_name` or `advertised_name` or `name` |
| `lat`             | REAL    | last fresh GPS fix (≤10s old)          | yes      | null when no fresh fix |
| `lng`             | REAL    | last fresh GPS fix                     | yes      | null when no fresh fix |
| `is_alert`        | INTEGER | `rssi >= target.rssi_alert_threshold`  | no       | 0 or 1 |
| `drive_id`        | TEXT    | `ble_history.current_drive_id()`       | no       | `drive-YYYYMMDD-HHMMSS-<6char>` |

Indexes: `(ts)`, `(target, ts)`, `(drive_id)`, `(mac, ts)`.

`drive_id` rules:
- File at `/opt/drifter/state/current_drive_id` holds the active id.
- If file mtime > 30 min ago (or missing): mint a new id and write.
- Otherwise: reuse.
- `touch_drive_id()` refreshes the mtime, debounced to once per 60s,
  so an active drive extends naturally without thrashing the FS.

---

## 4. Identity branches

`ble_identity.compute_identity(detection)` returns `(identity_str,
confidence)` using the first matching branch:

| # | Branch                                    | Identity format                          | Conf | Rationale |
|---|-------------------------------------------|------------------------------------------|------|-----------|
| 1 | mfr + non-generic name (len>3, ∉ GENERIC) | `mfr:{id}\|name:{name}`                  | 0.9  | The pair is essentially a serial. "Bose QC45 Steve" stays stable across the device's lifetime. |
| 2 | target ∈ {`axon`, `axon-class`}           | `mac:{full}`                             | 0.85 | LE has no anti-tracking incentive — Axon ships factory MACs that don't rotate. |
| 3 | target == `tile`                          | `mac:{full}`                             | 0.85 | Tile WANTS to be findable; MAC stable by design. (Pre-Sidewalk; verify against current Tile FW before relying.) |
| 4 | target ∈ {`airtag`, `find-my`}            | `mfr:{id}\|name:{name or 'anon'}`        | 0.4  | Apple rotates the Find My advertising key every ~15 min specifically to defeat anyone but the owner. Fingerprint is acknowledged-weak. |
| 5 | fallback                                  | `mac-prefix:{first-3-octets}\|target:{}` | 0.2  | Coarsest signal. Useful only when the persistence layer correlates across drives + clusters. |

`GENERIC_NAMES` blocks `BLE`, `Device`, `Unknown`, `iPhone`, `AirPods`,
`AirPods Pro`, `iPad`, `Mac`, `MacBook`, `Apple Watch`, `Watch`,
`Headphones`, `Speaker`, `Phone`, `Earbuds`, `''` from anchoring branch
1. Conservative on purpose — any addition risks fragmenting a real
fingerprint into many.

References:
- Heinrich et al., *Who Can Find My Devices?* (PoPETs 2021) — Apple
  Find My key rotation
- Apple *Find My Network Accessory Specification* — Continuity sub-types
- IEEE OUI registry — Axon `00:25:DF` (formerly Taser International)

---

## 5. Scoring rationale

`follower_score = unique_geo_clusters × unique_drive_ids × confidence`

Why multiplicative? Each factor on its own is noisy:
- High `unique_geo_clusters` alone = device that drives around. Many
  devices do (Ubers, delivery vans, neighbour driving the same route).
- High `unique_drive_ids` alone = device pinged across many of MY
  outings. But if it's always at one location, that's locality.
- High `confidence` alone = strong fingerprint, but irrelevant if seen
  once.

Multiplicative product naturally rewards both spread (across drives
AND across geographic clusters) and reliability of the identity itself.
Anything weak on one axis pulls the score down. A single-axis spike
lands at tier=`weak` and falls below the operator's attention.

**Tier thresholds:**

| Tier   | Rule                                  | Example                          |
|--------|---------------------------------------|----------------------------------|
| high   | score ≥ 6 AND confidence ≥ 0.7        | 3 drives × 3 clusters × 0.85 = 7.65 |
| medium | score ≥ 3 (any confidence)            | 2 drives × 2 clusters × 0.85 = 3.4  |
| weak   | anything else passing filters         | 2 × 2 × 0.4 (airtag) = 1.6          |

These are guesses. They have NOT been ground-truthed against real
driving patterns. The thresholds and the multiplicative form are both
candidates for tuning after a week of soak data — see Phase 4.9
candidate work.

**Filter rules (applied before scoring):**

| Rule                                  | Why                                               |
|---------------------------------------|---------------------------------------------------|
| `detection_count < 3`                 | Two hits aren't a pattern.                        |
| `unique_drive_ids < 2`                | Single drive = locality, not following.           |
| All hits in `cluster_id == -1` (no GPS) | Can't tell if device travelled or sat in soup.  |
| `unique_geo_clusters < 2` (4.8.1)     | Device pinged across drives but always at one place = home/work, not a tail. |

---

## 6. Tunable parameters

Every magic number in the BLE stack:

| Parameter                       | Location                                  | Current | ↑ does                                  | ↓ does                                    |
|---------------------------------|-------------------------------------------|---------|-----------------------------------------|-------------------------------------------|
| `BLE_RATE_LIMIT_SEC`            | `config.py:647` (env override)            | `30`    | Less per-MAC noise, miss bursts of repeated hits | More noise, may flood DB |
| `BLE_GPS_FRESH_SEC`             | `config.py:648`                           | `10`    | Stale GPS attaches more often → bad geo signal | Fewer rows have GPS |
| `BLE_LOG_RETENTION_DAYS`        | `config.py:646`                           | `30`    | Bigger DB, longer-window scoring works  | DB stays small, lose forensic depth |
| `DRIVE_IDLE_SECONDS`            | `ble_history.py:30`                       | `1800`  | Longer drives merge across stops        | More short drives, more drive_id boundaries |
| `DRIVE_TOUCH_DEBOUNCE`          | `ble_history.py:31`                       | `60.0`  | Higher mtime-FS load                    | Drive id may roll over mid-active |
| `query_history` default `limit` | `ble_history.py` (param)                  | `200`   | Heavier responses                       | More truncation of older rows in window |
| `query_history` max `limit`     | `ble_history.py` (clamped)                | `2000`  | Heavier responses, slower /map/ble       | Map silently drops oldest in busy windows |
| `eps_meters` (DBSCAN)           | `ble_geocluster.py:35`                    | `150`   | Larger clusters, fewer "different" places | More clusters, harder to cross-cluster spread reach 2 |
| `min_samples` (DBSCAN)          | `ble_geocluster.py:36`                    | `2`     | Tighter clusters, more noise points     | Looser, single hits become "clusters" |
| Branch 1 confidence             | `ble_identity.py:60`                      | `0.9`   | mfr+name identities easier to reach high tier | Stronger fingerprints undervalued |
| Branch 2/3 confidence           | `ble_identity.py:64,68`                   | `0.85`  | Axon/tile easier to reach high tier      | Underweights stable-MAC classes |
| Branch 4 confidence             | `ble_identity.py:74`                      | `0.4`   | AirTag-class can climb out of weak       | Caps acknowledged-weak more tightly |
| Branch 5 confidence             | `ble_identity.py:82`                      | `0.2`   | OUI-only "device class" can rank        | Nearly never surfaces |
| Filter: detection_count         | `ble_persistence.py:84`                   | `3`     | Stricter — miss real but rare followers | Looser — more two-hit noise |
| Filter: unique_drive_ids        | `ble_persistence.py:87`                   | `2`     | Stricter — miss followers seen on first day | Looser — single-drive locality leaks in |
| Filter: unique_geo_clusters     | `ble_persistence.py:99`                   | `2`     | Stricter — miss legitimate single-route tails | Looser — home/work stable devices pollute list |
| Tier `high` threshold           | `ble_persistence.py:113`                  | `6`     | Fewer high alerts                       | More noise at high                |
| Tier `high` conf gate           | `ble_persistence.py:113`                  | `0.7`   | Excludes airtag/branch-5 from high      | Lets weak-fingerprint identities reach high |
| Tier `medium` threshold         | `ble_persistence.py:115`                  | `3`     | Smaller medium pool                     | Larger medium pool |
| Vivi summary cache TTL          | `vivi.py:444` (`_PERSISTENT_TTL`)         | `60.0`  | Slower data freshness in Vivi context   | More per-turn DB opens |

---

## 7. Known limitations

These are documented and accepted; not bugs.

- **MAC randomisation defeats fingerprinting for iOS-class devices.**
  iPhones rotate ~15 min, AirTags rotate continuously to defeat
  exactly this kind of tracking. The airtag/find-my branch is
  documented as `confidence=0.4`, and architect-confirmed they
  mathematically cannot reach the `high` tier under the current
  scoring (0.4 × any cluster/drive product < the conf≥0.7 high gate).

- **Ambient noise produces false positives.** Stable named devices at
  home, the gym, or a partner's place will appear in the persistent
  contacts list. The `unique_geo_clusters ≥ 2` filter (4.8.1) catches
  most single-locality noise; the rest is operator triage.

- **Antimeridian wrap-around unhandled.** `ble_geocluster.py` buckets
  longitude with the same `eps_deg` as latitude. A point at `+179.999`
  and one at `-179.999` will land in different buckets and never be
  compared. Non-issue for SF deployment; deferred.

- **Longitude bucketing inefficient at high latitudes.** Same root
  cause as above — uses the latitude constant for both axes. Cells
  become rectangular near the poles; the haversine check inside the
  cell window keeps correctness but performance degrades. Vehicle is
  in SF, no impact.

- **Polkit allowlist not tightened.** `services/51-drifter-bluetooth.rules`
  grants drifter user all `org.bluez.*` actions. Tightening to a
  minimum allowlist requires capturing live polkit decisions during
  a known-good run, which Kali's bluez does not expose via a policy
  file. Deferred.

- **`drive_id` boundaries are mtime-based, not GPS-derived.** A 30-min
  parked-with-radios-on stop will roll the drive id even if the same
  drive resumes. False-positive cost is acceptable; matches the
  "errand-then-coffee" pattern. GPS-speed integration deferred until
  the canbridge / GPS service is reliably populated.

- **Map page silently drops oldest rows in busy windows.** `query_history`
  applies `LIMIT N` AFTER `ORDER BY ts DESC`, so a 24-hour window with
  > 2000 rows truncates the OLDEST. Documented in the function
  docstring; a future operator's "where did my data go" debug session
  has a starting point.

- **Vivi proactive comments NOT triggered on persistent contacts.**
  By design. Without ground-truth data the false-positive cost is too
  high. Phase 4.9 candidate after a week of soak data.

---

## 8. Operator runbook

**Reading the persistent-contacts panel:**

The dashboard has a collapsed "Persistent Contacts (7d)" section
below the BLE history. Expand → it fetches `/api/ble/persistent`
and renders one row per identity, sorted by score.

| Tier   | Colour | Action                                                   |
|--------|--------|----------------------------------------------------------|
| high   | red    | Inspect the identity. Do you recognise the MAC OUI? Is it a vehicle that has plausibly been around you across the listed drives? Cross-check with `/map/ble` filtered to that drive_id. |
| medium | amber  | Possibly real, possibly carpool partner / colleague's gear / your own kit you forgot about. Note the identity and watch over the next week. |
| weak   | dim    | Likely noise. Triaging weak-tier rows is rarely productive unless the score is climbing over time. |

The "X candidates rejected by filters" line on the panel is
informational — it tells you how many distinct identities were seen
in the window but didn't clear the filter rules (single-drive,
single-cluster, low-count, no-GPS).

**If you see a high-tier contact you don't recognise:**

1. Click into `/map/ble` and filter to the most recent drive_id
   listed for that contact. Does the marker pattern look like
   somewhere a stranger could plausibly have followed you?
2. Run `drifter-ble-export --target {target} --since 7d --format
   csv` and inspect the timestamps. Are they correlated with stops
   you actually made?
3. Cross-reference the manufacturer_id and adv_name (if any) against
   IEEE OUI lookup. Body cams, vehicle telematics, and dashcams have
   identifiable OUIs.
4. If still concerned, capture the next sighting verbally
   (Vivi has the data on demand: ask "who's been following me this
   week?" and Vivi will read out the PERSISTENT_CONTACTS context).

**Marking a known-benign identity:** NOT YET IMPLEMENTED. There is
no allowlist for known-benign devices. Phase 4.9 candidate — would
need an operator-managed yaml under `config/ble_known_benign.yaml`
and a filter pass before scoring.

**Soak report:** `drifter-ble-soak-report` (or
`/usr/local/bin/drifter-ble-soak-report`) prints a plain-text
diagnostic dump for the last 7 days. Use it after a week of real
driving to ground-truth the scoring against actual data.

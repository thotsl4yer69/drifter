# DRIFTER BLE Stack — Phase 4.5 → 4.8 handoff

One-page summary of everything that landed across the BLE work.
For the full reference see [`BLE_STACK.md`](BLE_STACK.md).

---

## Phase 4.5 — Passive scanner

**Commit:** `c8c9ef6` — `feat(ble): Phase 4.5 — passive BLE scanner (drifter-bleconv)`
**Follow-up:** `bf7bff9` — `fix(install): enable drifter-bleconv in service-enable list`

**Shipped:**
- New service `drifter-bleconv` (bleak + BlueZ, listen-only)
- Target registry at `config/ble_targets.yaml` (Axon enabled; Tile, AirTag staged but unverified-disabled)
- Per-(target, mac) 30s rate limit, fresh-only GPS attachment (10s)
- Privacy gate: `home_sync` excludes `drifter/ble/+` and `drifter/audio/+`
- Vivi context line surfaces last-5min hits as hardware-family labels only
- Polkit grant for drifter user → BlueZ at `/etc/polkit-1/rules.d/51-drifter-bluetooth.rules`

**Deferred:** polkit allowlist tightening (Kali bluez ships no enumerable policy file).
**Known weak:** Tile + AirTag targets unverified; runtime forces them off.

---

## Phase 4.6 — Polish

**Commit:** `8171e12` — `feat(ble): Phase 4.6 — polish (dashboard tile, AND-match, MQTT race, AirTag doc)`

**Shipped:**
- Dashboard BLE live tile (5s poll on `/api/ble/recent`)
- `match_mode: any|all` opt-in for the matcher (default `any`); `airtag` target now uses `all` so `manufacturer_id` AND `prefix` AND together
- `_connect_mqtt` retry (10× linear backoff) — eliminates the boot-time crash-restart cycle when nanomq isn't ready
- `docs/AIRTAG_DETECTION.md` — Apple Continuity sub-types, matcher gap explanation, hardware verification protocol

**Deferred:** polkit allowlist tightening (still); AirTag/Tile hardware verification.

---

## Phase 4.7 — Forensic persistence + map

**Commit:** `f66aec7` — `feat(ble): Phase 4.7 — forensic persistence + map overlay`
**Follow-ups:** `ae48210` (HEAD routing + synthetic-MQTT persistence), `c795932` (popup XSS escape + LIMIT note)

**Shipped:**
- `src/ble_history.py` — single-table SQLite (WAL, busy_timeout=5000) with drive_id partitioning, schema versioning via `PRAGMA user_version`
- `drive_id` minted as `drive-YYYYMMDD-HHMMSS-<6char>` after a 30-min mtime gap; touched (debounced 60s) on every detection
- `_record_detection` MQTT-publish-first, DB-persist-second (sqlite outage cannot block live tile or Vivi)
- `_persist_external_detection` MQTT loopback subscriber accepts external `drifter/ble/detection` publishes; scanner-tagged messages skipped to prevent double-persist
- HTTP: `/api/ble/history` (filterable), `/api/ble/drives` (per-drive summary), `/map/ble` (Leaflet, vendored at `/static/leaflet/`)
- Dashboard: collapsible 24h history panel below the live tile
- CLI: `scripts/drifter-ble-export` (CSV / GeoJSON / JSON, `--since` / `--until` / `--target` / `--drive-id`), symlinked at `/usr/local/bin/`
- `do_HEAD = do_GET` so `curl -I` works on custom routes
- Map popup HTML-escaped (closes stored-XSS via MQTT-poisoned target / adv_name)

**Deferred:** GPS-derived drive boundaries (need canbridge / GPS data first); paginating `query_history` past LIMIT 2000.
**Known weak:** map silently drops oldest rows when a window exceeds the 2000-row cap (documented in `query_history` docstring).

---

## Phase 4.8 — Persistent contact detection

**Commit:** `7573a92` — `feat(ble): Phase 4.8 — persistent contact detection (follower analysis)`
**Follow-up:** `347c326` — `fix(ble): Phase 4.8.1 architect follow-ups — cluster filter + Vivi cache`

**Shipped:**
- `src/ble_identity.py` — 5-branch stable-identity resolver with per-branch confidence (0.9 / 0.85 / 0.85 / 0.4 / 0.2)
- `src/ble_geocluster.py` — pure-python DBSCAN with haversine, ~eps-sized grid index (1000 random points cluster in <500ms on Pi 5)
- `src/ble_persistence.py` — score = clusters × drives × confidence; tiers high (≥6 + conf≥0.7) / medium (≥3) / weak; filters drop count<3, drives<2, all-no-GPS, clusters<2
- `/api/ble/persistent` endpoint (window=24h|7d|30d|all, min_tier=weak|medium|high)
- Dashboard "Persistent Contacts" panel below history block, collapsed default, window dropdown
- Vivi prompt-context hook (`PERSISTENT_CONTACTS:` line; on-demand only, 60s cache)

**Deferred:** Phase 4.9 proactive Vivi alerts (need real-data ground-truth first); operator-managed known-benign allowlist; cosine longitude correction; antimeridian handling.
**Known weak:** scoring thresholds (6 / 3) and identity confidence bands are unvalidated guesses; soak data needed.

---

## What's known broken / weak across the stack

| Issue                                         | Where                                                | Severity | Status                                  |
|-----------------------------------------------|------------------------------------------------------|----------|-----------------------------------------|
| Polkit allowlist too broad (`org.bluez.*`)    | `services/51-drifter-bluetooth.rules`                | low      | deferred — needs live decision capture  |
| Antimeridian / cosine-correction longitude    | `ble_geocluster.py:63`                               | low      | deferred — non-issue for SF             |
| `query_history` LIMIT cuts oldest rows        | `ble_history.py` (documented in docstring)           | medium   | known — paginate when needed            |
| Tile + AirTag targets unverified              | `config/ble_targets.yaml`                            | medium   | runtime forces disabled                 |
| Scoring thresholds + confidences unvalidated  | `ble_persistence.py:113-115`, `ble_identity.py`      | medium   | soak period in progress                 |
| `drive_id` mtime-based, not GPS-speed         | `ble_history.py:81-100`                              | low      | acceptable false positives              |
| No operator-managed known-benign allowlist    | (not built)                                          | low      | Phase 4.9 candidate                     |

---

## Test count

365 (was 25 pre-4.5; net +340 across 4.5 → 4.8.1).

| File                              | Tests |
|-----------------------------------|-------|
| `tests/test_ble_passive.py`       | 20    |
| `tests/test_ble_history.py`       | 14    |
| `tests/test_ble_identity.py`      | 8     |
| `tests/test_ble_geocluster.py`    | 7     |
| `tests/test_ble_persistence.py`   | 13    |

---

## What the next person needs to know

1. The whole BLE stack runs in **listen-only mode**. Do not "improve"
   it by adding GATT connections or active probing — that breaks the
   counter-surveillance threat model and the legal posture.
2. The follower scoring is **heuristic**. Do not build proactive Vivi
   alerts on top until a week of soak data has confirmed which tiers
   actually correspond to real followers vs ambient noise. See
   `BLE_STACK.md` §5 for the rationale.
3. **Privacy:** `home_sync` exclusion of `drifter/ble/+` and `/audio/+`
   is load-bearing — BLE detection data is never to leave the Pi.
4. **DB schema is versioned.** If you change the schema, bump
   `SCHEMA_VERSION` in `ble_history.py` and add a migration in
   `_migrate(conn)`. Don't break old rows.
5. **`source: scanner`** on detection payloads is what prevents double-
   persist when the broker echoes back. Don't strip it.
6. The dashboard ACL on `/api/ble/*` is hotspot-only (127.0.0.1 +
   10.42.0.0/24). The map page also enforces this. If you add a new
   BLE endpoint, run the same `_is_local_peer(peer)` check.
7. After significant scanner / scoring changes, run
   `drifter-ble-soak-report` to sanity-check the data shape; it's the
   fastest way to catch a regression that pytest can't see.

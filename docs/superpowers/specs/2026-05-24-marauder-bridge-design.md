# Marauder Bridge — Full Capability Design

**Date:** 2026-05-24
**Scope:** New service `drifter-marauder` (Python), USB-or-Flipper-proxy transport, four feature phases (passive recon, active Wi-Fi, BLE, EvilPortal). Backend only — no dashboard UI; that lands in a separate spec (`OPSEC dashboard expansion`).
**Status:** Approved for spec write, awaiting plan-stage review.

**Supersedes:** `docs/MARAUDER.md` lines 63–65 ("cockpit must not surface DEAUTH, BEACON SPAM, or EVIL TWIN — those are kept out of the operator surface by spec"). This spec rescinds that constraint. The replacement rule: offensive features ARE surfaced, but only behind the three-layer gate defined in §5 (local-peer + per-command confirm + per-target allowlist). `docs/MARAUDER.md` to be updated as the first implementation step.

---

## 1. Architecture

New systemd service `drifter-marauder.service`, runs as `User=drifter` with `SupplementaryGroups=dialout` for `/dev/ttyACM*` + `/dev/ttyUSB*` access (matches `drifter-rfaudio`'s pattern of `drifter` + `plugdev/audio`; the privilege needed is USB serial, not raw socket — no reason to run as root). Same hardening posture as `drifter-kismet`: `ProtectHome=true`, `StateDirectory=drifter-marauder`, `WorkingDirectory=/var/lib/drifter-marauder`, narrow `ReadWritePaths`.

Joins `FOOT_ONLY_SERVICES` in `src/config.py`. `/healthz` automatically picks it up — when in foot mode the service is "expected"; when no hardware is present, it lands in `services_hw_pending`, never `services_failed`.

**Module layout** under `src/`:

```
marauder_bridge.py        # main loop, dispatch, command lock, MQTT client
marauder_transport.py     # autodetect direct-USB vs Flipper-proxy, serial I/O
marauder_protocol.py      # CLI command builders + line-event parser
marauder_allowlist.py     # wraps audit_targets.yaml + Marauder scope
marauder_features/
  __init__.py
  passive.py              # Phase 1: scanap / scansta / sniffprobe
  active_wifi.py          # Phase 2: deauth detect/attack, beacon spam, probe flood
  ble.py                  # Phase 3: scan, AirTag, skimmer, spam
  evilportal.py           # Phase 4: rogue AP + portal + cred capture
```

**Two-axis safety wrap** for every command:

1. Risk classification (LOW/MED/HIGH) — same pattern as `flipper_bridge.classify_risk()` (`src/flipper_bridge.py:412`). HIGH = confirmation token required.
2. Allowlist gate — every command that emits RF or targets a specific MAC/SSID must resolve to an entry in the allowlist (§5). Empty allowlist → HIGH commands refused outright.

**MQTT root:** `drifter/marauder/`.
**HTTP root:** `/api/marauder/*` mounted on `:8090` OPSEC dashboard (foot-mode home), not the `:8080` cockpit. Local-peer gate (`_is_local_peer`) reused from `opsec_dashboard.py`.

---

## 2. Transport autodetect

`marauder_transport.MarauderTransport` probes hardware once at service start, sticks with the first match for the session. Re-probe is operator-triggered (`POST /api/marauder/probe`) or fires on a udev USB add/remove event — no timer polling.

**Probe order:**

1. **Direct USB-C.** Walk `/dev/serial/by-id/` for known Marauder VID:PIDs:
    - Espressif ESP32-S2 `303a:1001`
    - Espressif ESP32-S3 `303a:1014`
    - Silicon Labs CP210x (common dev-board USB-UART) `10c4:ea60`

   For each candidate: open at 115200 8N1, send `stopscan\r\n`, read for 500 ms, accept if response contains `Marauder`, `ESP32`, or prompt `>` + newline. First match wins → **direct mode**, store port path, return.

2. **Flipper-proxy.** Query `drifter-flipper`'s `/api/flipper/hardware` (existing endpoint). If `marauder_module_present == true` (set by `_looks_like_marauder` in `flipper_bridge.py:778`), use **proxy mode** — commands wrapped in a `marauder_passthrough` envelope, published on `drifter/flipper/cmd`, responses arrive on `drifter/flipper/event`.

3. **Nothing.** Service stays running, enters `idle`/`no_hardware` state, publishes `drifter/marauder/status {state:"no_hardware"}` every 30 s. **Never publishes fake events.** Health probe reports `hw_pending`.

**If both transports are present**, direct wins (lower latency, fewer hops). No operator override knob in this spec — add only if a real workflow demands it.

**Single transport, single direction:** sync command/response in the main thread; background reader thread parses unsolicited lines (Marauder spams events continuously during scans) and publishes them directly to MQTT.

---

## 3. Marauder CLI protocol layer

`marauder_protocol.py` has two responsibilities, kept in separate sub-sections of the module.

### 3.1 Command builders

Pure functions, no I/O. Easy to unit-test without hardware.

```python
def cmd_scan_ap() -> str
def cmd_scan_sta() -> str
def cmd_scan_probes() -> str
def cmd_stop() -> str
def cmd_select_ap(index: int) -> str
def cmd_attack_deauth(target_idx: int | None = None,
                     mode: str = "single") -> str   # "single" | "all"
def cmd_attack_beacon(mode: str,                    # "list" | "random" | "rickroll"
                     list_idx: int | None = None) -> str
def cmd_attack_probe_flood(list_idx: int) -> str
def cmd_ble_scan(mode: str) -> str                  # "all" | "airtag" | "skim"
def cmd_ble_spam(mode: str) -> str                  # "swift" | "samsung" | "apple" | "all"
def cmd_evilportal_load_template(html_bytes: bytes) -> list[str]  # upload chunks
def cmd_evilportal_start(ssid: str) -> str
def cmd_evilportal_stop() -> str
```

### 3.2 Event parser

`parse_event(line: str) -> dict | None`. One compiled-regex table at the top of the module is the single place to edit when Marauder firmware bumps line format.

| Marauder line shape (current Marauder ~v0.13) | Parsed event |
|---|---|
| `RSSI: -67 Ch: 6 BSSID: aa:bb:.. ESSID: CoffeeShop` | `{type:"ap", rssi, ch, bssid, ssid, ts}` |
| `RSSI: -82 BSSID: aa:bb:.. STA: cc:dd:.. ESSID: CoffeeShop` | `{type:"station", ap_bssid, sta_mac, rssi, ts}` |
| `Probe req: cc:dd:.. → "MyHomeWifi"` | `{type:"probe", sta_mac, looking_for_ssid, ts}` |
| `Deauth detected from aa:bb:.. → cc:dd:..` | `{type:"deauth_seen", from_mac, to_mac, ts}` |
| `BLE: AirTag spotted aa:bb:cc:dd:ee:ff RSSI -55` | `{type:"airtag", mac, rssi, ts}` |
| `BLE: skimmer fingerprint aa:bb:..` | `{type:"skimmer", mac, ts}` |
| `Sent deauth pkt #N target=aa:bb:..` | `{type:"deauth_tx", pkt_n, target_bssid, ts}` |
| `Sent beacon pkt #N ssid="..."` | `{type:"beacon_tx", pkt_n, ssid, ts}` |
| `Portal client connected mac=aa:bb:..` | `{type:"portal_client_connect", mac, ts}` |
| `Captured: <key=val>+` (EvilPortal) | **special-case — see §3.3** |

Unmatched lines return `{type:"unknown", raw:line}` — never `None`. The service tracks an unknown-rate metric and logs at DEBUG; this makes firmware drift detectable instead of silent.

### 3.3 Credential capture handling (the sensitive path)

`cred_capture` events bypass MQTT entirely. The parser:

1. Builds the field map from the captured form post.
2. Writes one JSONL line to `/opt/drifter/state/marauder/evilportal/captures-<session_id>.jsonl` (mode `0600`, owner `drifter:drifter`).
3. Publishes only a redacted notification to MQTT: `drifter/marauder/event {type:"cred_capture_count", session, count:N}` and updates `drifter/marauder/portal/status.captures_count`.

Rationale: MQTT topics on the operator hotspot have multiple subscribers. Raw creds never live on the wire even on loopback. Viewing the contents requires the operator confirm-token flow in §4.

---

## 4. MQTT topics + HTTP API

### 4.1 MQTT

Root: `drifter/marauder/`.

| Topic | Direction | Retained | Shape |
|---|---|---|---|
| `cmd` | op → svc | no | `{id, command, args?, confirm_token?}` |
| `event` | svc → bus | no | `{id, ok, response, ts}` — sync ack for the `cmd` it answers |
| `scan/ap` | svc → bus | no | one event per parsed AP |
| `scan/sta` | svc → bus | no | one event per station |
| `scan/probe` | svc → bus | no | one event per probe request |
| `ble/device` | svc → bus | no | one event per BLE sighting |
| `ble/airtag` | svc → bus | no | AirTag sightings (separate, defensive stream) |
| `ble/skimmer` | svc → bus | no | skimmer-fingerprint sightings |
| `attack/status` | svc → bus | no | `{mode, target?, packets_sent, started_ts, will_stop_at_ts}` published every 2 s while active |
| `portal/status` | svc → bus | no | `{ssid, template, connected_clients, captures_count}` — count only, never content |
| `status` | svc → bus | **yes** | `{state, mode, transport, hw_detail, ts}`; `state ∈ {idle, scanning, attacking, portal, no_hardware, error}` |
| `warning` | svc → bus | no | `{type, msg, ts}` — collateral notices (e.g., first-time iOS-crash warning) |
| `error` | svc → bus | no | `{id?, level, msg, ts}` — hardware drops, parse failures, allowlist refusals |

### 4.2 HTTP API (`:8090`)

All routes gated by `_is_local_peer` (127.0.0.1 + 10.42.0.0/24).

| Method + path | Body | Returns |
|---|---|---|
| `GET /api/marauder/status` | — | latest `status` snapshot (server-cached 1 s) |
| `GET /api/marauder/scan/recent?stream=ap\|sta\|probe&n=200` | — | last N events from in-memory ring |
| `GET /api/marauder/ble/airtags?since=<ts>` | — | AirTag sightings since timestamp |
| `GET /api/marauder/portal/sessions` | — | list of portal session IDs + counts (no creds) |
| `GET /api/marauder/portal/session/<id>/captures.jsonl` | (header `X-Drifter-Op-Confirm: <token>`; optional `?wipe=1`) | streams the capture file once, consumes the token. With `wipe=1`, zeroes-and-deletes the file after streaming |
| `POST /api/marauder/cmd` | `{command, args?, confirm_token?}` | publishes to `drifter/marauder/cmd`, returns assigned `id` + initial ack |
| `POST /api/marauder/probe` | — | re-runs transport autodetect |
| `POST /api/marauder/stop` | — | sends `stop` + any teardown current mode requires |

**No new WebSocket.** Live updates use the existing MQTT-over-WebSocket fan-out on `:8081`.

### 4.3 State storage layout

```
/opt/drifter/state/marauder/
  scans/<session_id>.jsonl              # ring-buffered, cap 50 MB / session
  ble/airtags.jsonl                     # persistent — defensive value of history
  ble/skimmers.jsonl                    # persistent
  attacks/<session_id>.json             # one per HIGH-risk attack, audit-grade
  evilportal/<session_id>.json          # one per portal session, audit-grade
  evilportal/captures-<session_id>.jsonl # 0600, drifter:drifter, MQTT-isolated
  sessions.json                         # session index
```

Disk cap on `scans/`: 500 MB total; oldest sessions evicted (one log line per deletion). `attacks/` and `evilportal/` audit JSONs are append-only and never auto-evicted.

---

## 5. Allowlist + authorization model

Three layers, every command passes through all three in order. Any failure → command refused, reason published on `drifter/marauder/error`.

### 5.1 Layer 1 — Local-peer gate (network)

Reuse `_is_local_peer` from `opsec_dashboard.py`. POST origin must be `127.0.0.1` or `10.42.0.0/24`. Mosquitto already binds loopback-only (2026-05-18 hardening). For MQTT-direct command injection, see §10.5 (optional ACL).

### 5.2 Layer 2 — Risk classification + confirmation

Same flow as `flipper_bridge.pending_confirms`: HIGH commands return `{ok:false, response:"Confirmation required", confirm_token:UUID}`. Operator re-POSTs with `confirm_token` within 120 s. Tokens are per-command, single-use, in-memory only.

| Risk | Commands | Gate |
|---|---|---|
| LOW | `scan {ap,sta,probe}`, `ble_scan {all,airtag,skim}`, `stop`, `probe`, status reads | none |
| MED | `select`, channel hop changes, scan parameter tweaks | none (no RF emission) |
| HIGH | `deauth_attack`, `beacon_spam_*`, `probe_flood`, `ble_spam_*`, `evilportal_*` | confirm token required |

`deauth_detect` is LOW (passive listen).

### 5.3 Layer 3 — Allowlist scope gate

Every HIGH command must resolve to a target inside the allowlist. The allowlist defines what the operator has authorization to attack.

**File:** `/opt/drifter/etc/audit_targets.yaml`. Extend the existing file (used by `drifter-wifi-audit`) with a top-level `marauder:` block so scope is explicit and separately auditable:

```yaml
# Existing — used by drifter-wifi-audit (unchanged)
networks:
  - ssid: "ACME-Pentest-Guest"
    bssid: "aa:bb:cc:dd:ee:ff"

# New — used by drifter-marauder
marauder:
  wifi:
    - ssid: "ACME-Pentest-Guest"
    - bssid: "aa:bb:cc:dd:ee:ff"
  ble:
    - mac: "11:22:33:44:55:66"
    # OR for indiscriminate-spam areas:
    - area_authorized: true
      area_label: "ACME HQ pen-test lab room 204"
  evilportal:
    - ssid: "ACME-Pentest-Guest"
      template: "acme-guest"          # references /opt/drifter/etc/marauder/portals/<name>/
      max_captures: 50
      authorized_use: "ACME contract #1234 valid 2026-05-01 → 2026-06-30"
```

`marauder_allowlist.is_target_allowed(category, **fields) -> (bool, reason)`. Reasons surface on `drifter/marauder/error` — never silently dropped.

**Empty-allowlist behaviour** matches `wifi_audit`: service starts, LOW commands work, every HIGH command returns `{ok:false, response:"allowlist empty — refusing", scope:"marauder.<cat>"}`. Dashboard shows `ALLOWLIST EMPTY` (same UX pattern as the existing PWNAGOTCHI gate).

**Re-read on every HIGH command.** No long-lived cache. File is tiny, latency is fine, scope edits take effect immediately without service restart.

### 5.4 Non-goals (deliberate)

- No global panic-disable beyond `systemctl stop drifter-marauder` + existing OPSEC kill switches.
- No per-operator identity / RBAC — single-operator Pi.
- No remote unlock. Only way to add scope is editing YAML on the Pi.

---

## 6. Phase 1 — Passive recon (first ship)

Proves the whole stack end-to-end: transport autodetect → CLI → parser → MQTT → API → session storage. Subsequent phases reuse this plumbing.

**Wired commands (all LOW risk, no allowlist):** `scan ap`, `scan sta`, `scan probe`.

**Behaviour:** Operator POSTs `/api/marauder/cmd {command:"scan", mode:"ap", duration_s:60}`. Service:

1. Acquires command lock (Marauder firmware can't run two scans concurrently).
2. Sends `scanap\r\n` via transport.
3. Reader thread parses lines → MQTT events + ring buffer + JSONL session file.
4. After `duration_s`, sends `stopscan\r\n`, releases lock, publishes session-end status.

`duration_s` default 60, hard cap 600. Operator can `POST /stop` early.

**Session model:** UUID `session_id` per invocation. Append-only `sessions.json` record: `{id, started_ts, ended_ts, mode, transport_used, event_count, file_path}`. No mutation after `ended_ts` is set.

**No dashboard panel ships with Phase 1.** Panel work belongs in the OPSEC dashboard expansion brainstorm — every phase gets one panel, designed together for visual consistency. Per the no-skeleton rule, a half-built panel now would lie about what data is available.

**Phase 1 acceptance criteria:**

1. `systemctl status drifter-marauder` → active.
2. `curl -fsS http://127.0.0.1:8090/api/marauder/status` → `{state:"idle", transport:"direct"|"proxy", hw_detail:...}`.
3. With Devboard plugged in: `POST .../cmd {command:"scan", mode:"ap", duration_s:30}` then `mosquitto_sub -t 'drifter/marauder/scan/ap' -v` shows real APs within ~5 s.
4. Without hardware: `status.state == "no_hardware"`, scan command returns `{ok:false, response:"no transport available"}`. No fake events on the bus, ever.
5. `/healthz` reports `drifter-marauder` active (hw present) or in `services_hw_pending` (hw absent), never `services_failed` for hw-only reasons.

---

## 7. Phase 2 — Active Wi-Fi

Every command in this phase is HIGH risk → confirm + allowlist.

| Operator command | Marauder CLI | Allowlist scope | Notes |
|---|---|---|---|
| `deauth_detect` | `attack -t deauth -d` | none — defensive | Background-friendly `mode:"watch"` runs concurrently with no scan/attack (special exception to the command lock). Events → `drifter/marauder/event {type:"deauth_seen"}`. Rolling 5-min counter exposed on `status`. |
| `deauth_attack` | `attack -t deauth -a` (single AP) or `-s` (single station) | `marauder.wifi[].bssid` must include target | Operator references a target by `(session_id, ap_index)`. Service resolves to BSSID and re-checks allowlist before sending. No raw "deauth this MAC" command — every target by indexed reference, every reference re-validated. |
| `beacon_spam_random` | `attack -t beacon -r` (random SSIDs) | **refused unconditionally** | Random SSIDs can't be allowlisted by definition. Hardcoded refusal regardless of confirm/allowlist state. Operator who wants it edits `BEACON_SPAM_RANDOM_REFUSE = True` in `config.py` and redeploys — deliberate friction. |
| `beacon_spam_list` | `attack -t beacon -l <list_idx>` | every SSID in the list must be in `marauder.wifi[].ssid` | List files at `/opt/drifter/etc/marauder/beacon_lists/<name>.txt`, one SSID per line. Service loads list, checks every entry, refuses whole list if any entry out of scope. Loaded lists also publish `drifter/marauder/beacon/list_loaded {name, size, all_in_scope}` so operator sees what's armed. |
| `beacon_spam_rickroll` | `attack -t rickroll` | **refused** unless `marauder.wifi[].ssid == "*"` is present (deliberately ugly opt-in) | Same reasoning as random beacon spam. |
| `probe_flood` | `attack -t probe` | every probed SSID must be in `marauder.wifi[].ssid` | Sends probe requests as a fake client looking for these SSIDs. |

**Hard max attack duration: 300 s.** Operator can pass shorter `duration_s` but never longer. Service enforces. Reason: prevents forgot-it-was-running, makes accidental over-attack impossible, forces 5-min re-engagement decisions.

**Attack status stream** (`drifter/marauder/attack/status`, 2 Hz while active):

```json
{"session_id":"...","mode":"deauth_attack","target_bssid":"aa:bb:..",
 "target_ssid":"ACME-Pentest-Guest","packets_sent":1240,
 "started_ts":..., "will_stop_at_ts":...,
 "allowlist_revalidated_at":...}
```

**Per-attack audit record** at `state/marauder/attacks/<id>.json`:

```json
{"id":"...","operator_ip":"10.42.0.5","started_ts":...,"ended_ts":...,
 "mode":"deauth_attack","target_bssid":"...","target_ssid":"...",
 "allowlist_path":"/opt/drifter/etc/audit_targets.yaml",
 "allowlist_sha256":"...",
 "confirm_token_consumed":"...","packets_sent":1240,
 "transport":"direct",
 "marauder_fw_banner":"Marauder v0.13.4-AP-Devboard",
 "stop_reason":"duration_elapsed"}
```

`allowlist_sha256` snapshot lets an investigator reconstruct what scope was in effect at attack time even if scope is later edited.

---

## 8. Phase 3 — BLE recon + spam

### 8.1 Recon (LOW risk)

| Command | Marauder CLI | Stream |
|---|---|---|
| `ble_scan_all` | `blescan -t all` | `drifter/marauder/ble/device` |
| `ble_scan_airtag` | `blescan -t airtag` | `drifter/marauder/ble/airtag` + persistent JSONL |
| `ble_scan_skim` | `blescan -t skim` | `drifter/marauder/ble/skimmer` + persistent JSONL |

AirTag stream is the foot-mode privacy headline. Can run continuously as background `mode:"watch"` (same special-case as `deauth_detect`). Aggregation logic ("AirTag X has been near you on 4 separate days") lives in `marauder_features/ble.py`, not the dashboard layer.

### 8.2 Spam (HIGH risk, special allowlist rules)

| Command | Marauder CLI | Effect |
|---|---|---|
| `ble_spam_swift_pair` | `blespam -t swift` | Microsoft Swift Pair fake notifications on nearby Windows |
| `ble_spam_easy_setup` | `blespam -t samsung` | Samsung Easy Setup fake prompts on Galaxy |
| `ble_spam_apple_proximity` | `blespam -t apple` | Apple proximity action prompts; **known to crash iOS < 17 at volume** |
| `ble_spam_all` | `blespam -t all` | all three concurrent |

**Allowlist semantics for BLE spam are different.** Spam is indiscriminate — no target MAC. So:

- A spam command requires the `marauder.ble` block to contain an entry `{area_authorized: true, area_label: "<string>"}`.
- The `area_label` is captured into the per-attack audit record (`area_label_at_runtime` field). Forces the operator to write down WHERE they are authorized before they can run.

Friction by design. BLE spam is the most likely-to-grief feature in the spec.

Same hard 300 s cap. Same audit record format.

**iOS-crash collateral warning.** First time `ble_spam_apple_proximity` runs after service start, publishes `drifter/marauder/warning {type:"collateral_warning", msg:"This affects ALL nearby iOS devices, not just consenting test devices"}`. Dashboard renders red, requires per-session ack before the command executes. Once acked, no re-prompt for the session but the warning is re-logged for audit.

---

## 9. Phase 4 — EvilPortal / Karma

Heaviest, most sensitive. Designed with the most gating.

### 9.1 Architecture

Marauder firmware provides: rogue AP, captive portal HTTP server, credential capture to serial. Marauder's baked-in templates (Google, Twitter, etc.) are **not used** — recognizable signatures and out-of-spec authorship.

### 9.2 Portal template store

`/opt/drifter/etc/marauder/portals/<name>/`:

```
acme-guest/
  portal.html       # HTML with {{captive_post_url}} placeholder
  meta.yaml         # {ssid_default, description, authorized_use, created}
```

Templates are author-provided files. No service-side generation. No web editor. No LLM authoring.

### 9.3 Start flow

`POST /api/marauder/cmd {command:"evilportal_start", template:"acme-guest", ssid:"ACME-Pentest-Guest", duration_s:1800}`:

1. Local-peer gate, confirm gate, allowlist gate (§5).
2. Allowlist match: `marauder.evilportal[].ssid == "ACME-Pentest-Guest" AND template == "acme-guest"` — **both SSID and template must match a single allowlist entry**. Per-pair authorization, not per-field.
3. Load `portal.html`, validate (must contain `{{captive_post_url}}`, must be ≤ 64 KB, must not contain `<script src="http`).
4. Substitute placeholder, upload to Marauder via `evilportal -P` chunks, send `evilportal -s`.
5. Mark session active. **Hard duration cap 1800 s** (30 min) — longer than attacks because portal pentests need realistic dwell time, shorter than infinite because runaway-portal is high-blast-radius.

### 9.4 Capture path

The single most important non-default in the spec:

1. Marauder emits captured form post on serial.
2. Parser writes one line to `state/marauder/evilportal/captures-<session_id>.jsonl` (`0600`, `drifter:drifter`).
3. **No MQTT publish of contents.** Only `portal/status.captures_count` and `event {type:"cred_capture_count"}`.
4. Dashboard panel shows count only. Viewing contents:
    - Operator clicks "Reveal capture (single-use)" → service generates one-shot HMAC token.
    - Panel POSTs `GET .../captures.jsonl` with `X-Drifter-Op-Confirm: <token>`.
    - Service streams file once, consumes token.
    - Optional `?wipe=1` zeroes-and-deletes the file after stream. Default is keep.

### 9.5 Auto-stop conditions

Portal session ends on ANY of:

- `duration_s` elapsed (default 1800).
- Operator `POST /stop`.
- Service receives SIGTERM (clean teardown — sends `evilportal -s stop` to Marauder, verifies rogue AP is down before exit).
- Connection to Marauder lost > 30 s (can't trust state — teardown safer than risking a stuck rogue AP).
- Capture count exceeds `marauder.evilportal[].max_captures` (default 50). Prevents runaway evidence accumulation.

### 9.6 Audit record

`state/marauder/evilportal/<session_id>.json`:

```json
{"id":"...","operator_ip":"...","started_ts":...,"ended_ts":...,
 "ssid":"ACME-Pentest-Guest","template_name":"acme-guest",
 "template_sha256":"...",
 "allowlist_sha256":"...",
 "allowlist_entry":{"ssid":"...","template":"...",
                    "max_captures":50,
                    "authorized_use":"..."},
 "duration_s":1800,"hard_cap_hit":false,"max_captures_hit":false,
 "transport":"direct","marauder_fw_banner":"...",
 "captures_count":12,"captures_file":"captures-<id>.jsonl",
 "captures_revealed_at":[ts...],
 "captures_wiped":false,"wiped_at":null,
 "stop_reason":"duration_elapsed"}
```

### 9.7 Explicit non-goals for Phase 4

- No portal template editor in the UI. Files only — forces version-control, prevents clicky-creation of phishing infrastructure with no review.
- No exfil. Captures stay on the Pi. Operator `scp`s them off if needed. Manual = deliberate.
- No LLM-generated templates. Convincing phishing with zero human review is bad.

---

## 10. Cross-phase concerns

### 10.1 Audit surfaces (three, distinct)

| Surface | What it is | Purpose |
|---|---|---|
| `drifter-logger` subscribing to `drifter/marauder/#` | SQLite telemetry log of every bus message (including allowlist refusals) | "What happened on the bus" |
| Per-session JSON under `state/marauder/{attacks,evilportal}/` | Structured audit trail per HIGH-risk session | End-of-engagement client report material |
| `journalctl -u drifter-marauder` | Hardware events, protocol errors, parser failures | Operational |

**Never logged anywhere:** captured credentials in MQTT or systemd journal. They exist only in their `0600` session-specific JSONL files plus a count in the audit JSON.

### 10.2 Systemd unit (`services/drifter-marauder.service`)

```ini
[Unit]
Description=MZ1312 DRIFTER Marauder bridge (Wi-Fi/BLE recon + active)
After=network-online.target mosquitto.service drifter-flipper.service
Wants=mosquitto.service
StartLimitIntervalSec=120
StartLimitBurst=5

[Service]
Type=simple
User=drifter
Group=drifter
SupplementaryGroups=dialout
Environment=PYTHONUNBUFFERED=1
StateDirectory=drifter-marauder
WorkingDirectory=/var/lib/drifter-marauder
ExecStart=/opt/drifter/venv/bin/python /opt/drifter/src/marauder_bridge.py
Restart=on-failure
RestartSec=10

ProtectHome=true
PrivateTmp=true
ProtectSystem=full
ReadWritePaths=/opt/drifter/state /opt/drifter/etc
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
LockPersonality=true
RestrictSUIDSGID=true
NoNewPrivileges=true
DeviceAllow=char-ttyACM rw
DeviceAllow=char-ttyUSB rw
DevicePolicy=closed

[Install]
WantedBy=multi-user.target
```

Kismet lesson applied upfront: `StateDirectory=` + `WorkingDirectory=` keep `ProtectHome=true` compatible. `ReadWritePaths` narrowly scoped.

### 10.3 `config.py` changes

Add to `FOOT_ONLY_SERVICES`:

```python
FOOT_ONLY_SERVICES = [
    "drifter-wardrive",
    "drifter-flipper",
    "drifter-opsec",
    "drifter-kismet",
    "drifter-kismet-bridge",
    "drifter-wifi-audit",
    "drifter-marauder",      # NEW
]
```

`/healthz` then auto-classifies. Hard refusals (`BEACON_SPAM_RANDOM_REFUSE`) also live in `config.py`.

### 10.4 `install.sh` changes

Two blocks:

1. Add `drifter-marauder` to the `SERVICES` variable (line 472).
2. Add Marauder config tree deployment:

   ```bash
   mkdir -p /opt/drifter/etc/marauder/portals \
            /opt/drifter/etc/marauder/beacon_lists
   chown -R drifter:drifter /opt/drifter/etc/marauder

   if [ ! -f /opt/drifter/etc/audit_targets.yaml ]; then
       install -m 0640 -o drifter -g drifter \
           "${REPO_DIR}/config/audit_targets.sample.yaml" \
           /opt/drifter/etc/audit_targets.yaml
       ok "audit_targets.yaml seeded (EMPTY — operator must populate)"
   fi
   ```

   Never overwrite operator scope. Sample file ships with `marauder:` block present but every list empty.

### 10.5 Mosquitto ACL (optional, recommended)

`/etc/mosquitto/conf.d/drifter.acl` or equivalent:

```
pattern read  drifter/marauder/#
pattern write drifter/marauder/cmd
```

Defense in depth — mosquitto is already loopback-bound, but explicit ACL beats implicit trust.

### 10.6 First implementation step: update `docs/MARAUDER.md`

The supersede notice at the top of this spec is real — `docs/MARAUDER.md` must be updated as part of the implementation, before any code lands. The updated doc:

- Points at this spec for the offensive surface.
- Documents the change of stance ("passive-only rule rescinded; offensive features gated by §5 of marauder-bridge-design").
- Keeps the firmware-flash workflow content (still accurate).
- Adds a "How to authorize a target" pointer to the allowlist YAML format.

---

## 11. Test plan

### 11.1 Unit tests (no hardware) — `tests/test_marauder_*.py`

- `test_marauder_protocol.py` — every command builder produces the documented string; every parser fixture maps to the documented event; unknown lines return `{type:"unknown"}` not `None` (so unparsed-rate is observable).
- `test_marauder_allowlist.py` — every category (`wifi`, `ble`, `evilportal`) gates correctly: SSID match, BSSID match, BLE-area `area_authorized`, evilportal `(ssid,template)` pair match, empty file, malformed YAML.
- `test_marauder_classify_risk.py` — every command in this spec maps to the documented LOW/MED/HIGH.
- `test_marauder_session_record.py` — attack & portal session JSON include `allowlist_sha256`, `template_sha256`, `operator_ip`, `stop_reason`. Reject writes missing any required field (invariant: every audit record is complete or doesn't exist).

### 11.2 Integration tests — fake-serial loopback

`tests/integration/test_marauder_transport.py`. `pty.openpty()` creates a fake serial pair. "Marauder side" is fed scripted line sequences from `tests/fixtures/marauder/` (captured from real Marauder output). Verifies:

- Autodetect picks fake-direct when fake banner present.
- Reader thread parses streamed AP events without loss under burst.
- Command lock prevents concurrent attacks.
- Hard duration cap fires `stop` at exactly `duration_s + ε`.
- SIGTERM during portal triggers `evilportal -s stop` before exit.

Runs in CI without USB hardware. Same pattern as existing `flipper_bridge` tests.

### 11.3 Bench tests (real hardware, manual) — `scripts/test-bench-marauder.sh`

New script in the existing `scripts/test-bench-*` family. Modes:

- `probe` — runs autodetect, prints which transport won.
- `passive` — runs `scanap` for 30 s, prints AP count from MQTT.
- `deauth_detect` — runs detector for 60 s, prints any deauths seen.
- `allowlist_refuse` — sends `deauth_attack` to a BSSID NOT in allowlist, asserts refusal.
- `portal_dryrun` — loads `tests/fixtures/marauder/portals/test-portal/` for 10 s, asserts SSID appears in a parallel `scanap` from another transport, then tears down. **Captures nothing** — SSID nobody would connect to.

### 11.4 Acceptance gate (pre-merge)

- All unit tests green.
- All integration tests green.
- Bench `probe` + `passive` + `allowlist_refuse` pass with real hardware.
- `/healthz` returns 200 in both hw-present and hw-absent states.
- `mosquitto_sub -R -t 'drifter/marauder/#'` review: no retained MQTT message contains raw captured creds.

---

## 12. Out of scope (explicit non-goals)

- Multi-operator support / RBAC — single-operator Pi.
- Remote control via cloud / Bluetooth — loopback + hotspot subnet only.
- On-Pi WPA/WPA2 handshake cracking — `drifter-wifi-audit`'s lane (capture only; cracking off-Pi).
- New Marauder commands not in current firmware — design constrained to what `Marauder ~v0.13` exposes.
- GPS-tagging of Marauder captures — foot mode has no GPS; if added later, separate spec.
- Auto-flash of Marauder firmware — operator flashes via `tools/flash_marauder.sh`; service speaks to whatever's there.
- Carsenal-launcher integration on `:8090` — Task #3 (OPSEC dashboard expansion) territory, not this spec.
- Dashboard panels — same as above. No panels until Task #3.

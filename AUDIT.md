# DRIFTER — Release-Readiness Audit (Phase 0: Ground Truth)

**Node:** drifter — Raspberry Pi 5 (8 GB), Kali ARM64, in a 2004 Jaguar X-Type 2.5 V6.
**Audit date:** 2026-07-01 · **Scope:** read-only inventory of the repo as it stands on
branch `claude/drifter-release-audit-jjf3sk`. No code was changed to produce this report.
**Method:** every claim below was read out of the actual unit files, scripts, and source —
line-cited. Items that cannot be verified without live hardware are called out explicitly in
[§7 Assumptions to confirm on hardware](#7-assumptions-to-confirm-on-hardware).

**Headline:** the node is bench-functional and much of it is already well-built (real
degrade-and-retry loops in `can_bridge`/`gps_publisher`, a watchdog that auto-demotes under
RAM/thermal pressure, atomic writers in `feeds`/`gps_publisher`, tightly-scoped sudoers). The
gap to a *reliable, reproducible, respectable public release* is concentrated in six areas:

1. **Broker ordering is nondeterministic** — 58 units order `After=nanomq.service`, 6 after
   `mosquitto.service`, and `install.sh` picks the broker by a network race. No unified target.
2. **Reproducibility is not "one command"** — the repo has *three disagreeing service lists*,
   *three LLM model tags*, a Docker-vs-Pi dependency contradiction, unpinned installs, and a
   documented `.env.example` that the installer never actually installs.
3. **Secrets are committed** — two real Wi-Fi PSKs (`54232105`, `uncaged1312`) live in source
   and docs; historical API keys are in git history; a real VIN is a committed filename.
4. **Power-cut safety is partial** — several state writers are non-atomic, and the central
   `drifter.db` is opened without WAL/`busy_timeout` even though a shutdown WAL-checkpoint
   service assumes WAL.
5. **Device nodes are enumeration-fragile** — raw `/dev/ttyUSB0`, `/dev/ttyACM0`, `plughw:0,0`
   with no `by-id`/`by-path` symlinks; some units lack device-presence guards others have.
6. **Docs have drifted hard** — README is largely a v1 snapshot (16 services vs 38 real),
   documents the wrong broker, wrong CAN adapter, an unreachable MQTT endpoint, and hides the
   entire offensive/recon suite rather than scoping it. No CAPABILITIES.md / SECURITY.md /
   CHANGELOG / RELEASE-CHECKLIST.

Counts, verified live: **`config.SERVICES` = 38** (`src/config.py:639-684`); **68 `.service`
files** in `services/` (a deliberate superset with mode-alternatives + add-ons); **30 of those
68 units are not in `SERVICES`** (see §1b).

---

## 1. Systemd unit inventory

### 1a. The 38 monitored services (`config.SERVICES`, `src/config.py:639-684`)

`/healthz` checks exactly this set; `scripts/oneshot.sh` starts it; `install.sh` enables it plus
a few aux units. Each has a matching `services/<name>.service` file (verified — none missing).

| Unit | One-line purpose | Type | Restart | HW/interface |
|---|---|---|---|---|
| drifter-canbridge | Primary CAN→MQTT telemetry bridge (`can_bridge.py`), `ExecStartPre=drifter-setup-can` | simple | on-failure/15s, no StartLimit (deliberate) | **CAN** (slcan/USB2CANFD) |
| drifter-alerts | Deterministic alert engine over CAN telemetry (`alert_engine.py`, 23 rules) | simple | on-failure/15s, no StartLimit | MQTT (CAN-derived) |
| drifter-logger | Telemetry log writer (`logger.py`) | simple | on-failure/5s | MQTT |
| drifter-anomaly | Rolling-window anomaly detector (`anomaly_monitor.py`) | simple | on-failure/5s | MQTT + CAN |
| drifter-analyst | LLM session analyst (`session_analyst.py`) | simple | on-failure/10s | MQTT + Ollama |
| drifter-voice | Cabin TTS for alerts (`voice_alerts.py`) | simple | on-failure/10s | audio/ALSA |
| drifter-vivi | Vivi v2 voice assistant (`vivi_v2.py`) | simple | on-failure/10s | audio + Ollama |
| drifter-hotspot | Bring up MZ1312_DRIFTER AP via nmcli | oneshot,RemainAfterExit | none | NetworkManager/Wi-Fi |
| drifter-homesync | rsync/MQTT bridge to home node (`home_sync.py`) | simple | on-failure/30s | network |
| drifter-watchdog | Service/system health + auto-demote (`watchdog.py`) | simple | on-failure/10s, no StartLimit | MQTT + systemctl |
| drifter-realdash | RealDash **TCP** bridge :35000 (`realdash_bridge.py`) | simple | on-failure/5s | MQTT + CAN |
| drifter-rf | RTL-SDR TPMS + spectrum (`rf_monitor.py`) | simple | on-failure/10s | **RTL-SDR** |
| drifter-wardrive | Passive Wi-Fi/BT scan per drive (`wardrive.py`) | simple | on-failure/10s | Wi-Fi/BT NIC |
| drifter-dashboard | Web cockpit + `/healthz` + audio WS (`web_dashboard.py`, http.server) | simple | on-failure/5s | HTTP :8080 |
| drifter-voicein | Wake-word + Whisper STT (`voice_input.py`) | simple | on-failure/10s | **mic/audio** |
| drifter-flipper | Flipper Zero USB-serial bridge (`flipper_bridge.py`) | simple | on-failure/10s | **Flipper (USB serial)** |
| drifter-opsec | OPSEC dashboard :8090 (`opsec_dashboard.py`) | simple | on-failure/5s | HTTP :8090 |
| drifter-bleconv | Passive BLE scanner (`ble_passive.py`, bleak/BlueZ) | simple | on-failure/10s | **BLE/bluetooth** |
| drifter-gps | gpsd→MQTT publisher (`gps_publisher.py`) | simple | on-failure/10s | **GPS/gpsd** |
| drifter-rfaudio | On-demand RTL-SDR→speaker (`rfaudio.py`) | simple | on-failure/10s | **RTL-SDR + audio** |
| drifter-batcher | Rolling telemetry window (`telemetry_batcher.py`) | simple | on-failure/5s | MQTT |
| drifter-trip | Per-trip distance + fuel (`trip_computer.py`) | simple | on-failure/5s | MQTT |
| drifter-thresholds | Adaptive baseline learner (`adaptive_thresholds.py`) | simple | on-failure/5s | MQTT |
| drifter-reporter | Post-drive markdown report (`session_reporter.py`) | simple | on-failure/5s | MQTT + Ollama |
| drifter-weather | OpenWeatherMap poller (`weather_service.py`) | simple | on-failure/5s | network + OWM key |
| drifter-location | Google Elevation+Places (`location_service.py`) | simple | on-failure/5s | network + Google key |
| drifter-kismet | Headless Kismet Wi-Fi/BLE recon (root) | simple | on-failure/10s | Wi-Fi monitor NIC |
| drifter-kismet-bridge | Kismet REST→MQTT (`kismet_bridge.py`) | simple | on-failure/10s | MQTT |
| drifter-wifi-audit | bettercap PMKID/handshake, allowlist-scoped (`wifi_audit.py`) | simple | on-failure/10s | Wi-Fi monitor NIC |
| drifter-marauder | ESP32 Marauder Wi-Fi/BT bridge (`marauder_bridge.py`) | simple | on-failure/10s | **ESP32 (serial)** |
| drifter-fly-catcher | ADS-B ghost detector, in-proc ML (`fly_catcher.py`) | simple | on-failure/10s | RTL-SDR/ADS-B |
| drifter-feeds | ADS-B aircraft producer (`feeds.py`) | simple | on-failure/10s | network/decoder |
| drifter-can-discovery | CaringCaribou UDS/fuzz bridge (`can_discovery.py`) | simple | on-failure/10s | **CAN** |
| drifter-hid | HID/BadUSB injector (`hid_inject.py`, CAP_DAC_OVERRIDE) | simple | on-failure/5s | USB gadget /dev/hidg0 |
| drifter-lcd | 3.5" SPI LCD triage console (`lcd_dashboard.py`, root) | simple | on-failure/5s | framebuffer /dev/fb1 + SPI/GPIO |
| drifter-autoconnect | Wi-Fi auto-connect + AP fallback (`auto_connect.py`, root) | simple | on-failure/5s | NetworkManager |
| drifter-ghost | Counter-surveillance correlator (`ghost_protocol.py`) | simple | on-failure/10s | MQTT (+ optional Shade Core) |
| drifter-ghost-voice | Speaks ghost alerts (`ghost_voice.py`) | simple | on-failure/10s | audio |

Non-`.service` artefacts in `services/`: `51-drifter-bluetooth.rules` (polkit → BlueZ grant for
user `drifter`; removing it crash-loops `drifter-bleconv`), `drifter-journald.conf` (journal size
caps for SD-card protection), `drifter-llm-keepwarm.timer` (OnBootSec=60s/OnUnitActiveSec=5min),
`drifter-mode.sudoers` / `drifter-opsec.sudoers` / `drifter-service.sudoers` (tightly-enumerated
NOPASSWD grants — the arsenal sudoers explicitly excludes `drifter-rf`), `drifter.logrotate`.

### 1b. The 30 unit files NOT in `SERVICES` (superset)

These ship in `services/` but are **not** monitored by `/healthz`, **not** enabled by the
`oneshot.sh` loop, and **not** in `config.SERVICES`. Two sub-classes:

- **Aux / boot / lifecycle units** (intended to be enabled separately by `install.sh`):
  `drifter-boot-manager`, `drifter-boot-reason`, `drifter-db-checkpoint`, `drifter-zram`,
  `drifter-llm-keepwarm`. These are legitimately outside `SERVICES` (oneshot/one-shot lifecycle,
  not steady-state daemons).
- **Add-on units enabled ONLY by their own `scripts/install-*.sh`** (never by the base deploy):
  `drifter-safety`, `drifter-aidiag`, `drifter-alpr`, `drifter-assist`, `drifter-comms`,
  `drifter-crash`, `drifter-dashcam`, `drifter-fcw`, `drifter-sentry`, `drifter-vehicleid`,
  `drifter-learn`, `drifter-kb`, `drifter-session-recorder`, `drifter-replay`, `drifter-nav`,
  `drifter-obdbridge`, `drifter-vision`, `drifter-mesh`, `drifter-fleet`, `drifter-discord`,
  `drifter-home`, `drifter-spotify`, `drifter-satellite`, `drifter-rf-baseline`,
  `drifter-fbmirror`.

**Flags:**
- `drifter-safety` (Tier-1 safety engine, `safety_engine.py`) ships but is **not in the default
  service set** — a stock `oneshot.sh` deploy never enables it. Driver-safety on a stock node is
  carried entirely by `drifter-alerts` (`alert_engine.py`). This should be a conscious decision,
  not an accident of which install script the operator happened to run.
- `drifter-fbmirror` is presented as an active unit by README (`README.md:181,368`),
  `fleet-inventory-drifter.yaml:48`, and an `install.sh:646-649` NOTE ("the deploy enables both"
  fbmirror + lcd) — but it is **absent from both `config.SERVICES` and the `install.sh:645`
  `SERVICES=` enable-array**. So it is neither health-checked nor enabled by the enable loop.
- `AGENTS.md:160` instructs keeping `drifter-{fleet,mesh,replay,discord,home,satellite}` "in sync
  with the SERVICES list" — none of the six are in `SERVICES`. Orphaned instruction.

---

## 2. Dependency graph (After / Requires / Wants / BindsTo / PartOf)

**Structural facts (whole tree):** the only `Requires=` anywhere is
`drifter-db-checkpoint → Requires=local-fs.target` (a standard target — safe). **No drifter unit
uses `BindsTo=`, `PartOf=`, or hard-`Requires=` on another drifter unit.** All inter-drifter
ordering is *weak* (`After=`/`Wants=`). Good news: no lane can cascade-stop another. Bad news:
nothing *guarantees* the broker or a device is actually up before a consumer starts — everything
leans on in-process reconnect loops.

```
external targets/daemons
  network-online.target  ← wanted/after: boot-reason, feeds, homesync, kismet, location,
                            marauder, opsec, weather, wifi-audit
  network.target         ← after: canbridge, discord, fleet, hid, mesh, satellite
  NetworkManager.service ← after/wants: autoconnect, boot-manager, hotspot
  local-fs.target        ← Requires+After: db-checkpoint;  After: zram, boot-reason
  gpsd.service           ← after/wants: gps  → rf-baseline (After gps)
  bluetooth.service      ← after/wants: bleconv
  sound.target           ← after: rfaudio, vivi, voice, voicein
  ollama.service         ← after/wants: llm-keepwarm
  sysinit/swap.target    ← Before: zram (DefaultDependencies=no)

broker edge — SPLIT (see §2 broker note)
  nanomq.service    ← After/Wants of 58 units  (the near-universal edge)
  mosquitto.service ← After/Wants of ONLY 6: boot-reason, can-discovery, feeds, hid,
                       marauder, rfaudio   (can-discovery/hid/rfaudio list BOTH; feeds/
                       marauder/boot-reason list mosquitto ONLY)

drifter → drifter edges
  drifter-canbridge  ← After by: alerts, anomaly, dashboard, realdash, session-recorder,
                        watchdog, can-discovery, db-checkpoint      (the one real hub — 8 deps)
  drifter-logger     ← After by: analyst, db-checkpoint
  drifter-rf, drifter-gps ← After by: rf-baseline
  drifter-boot-manager (Before=drifter-lcd) → drifter-lcd (After boot-manager)
  drifter-kismet     ← After/Wants by: kismet-bridge
  drifter-flipper    ← After by: marauder
  shutdown/umount/final.target ← Before + Conflicts: db-checkpoint
```

### Broker note (highest-priority ordering finding)

Verified counts: **58** unit files reference `nanomq.service`, **6** reference `mosquitto.service`.
`install.sh` step 3 (`install.sh:126-141`) **prefers NanoMQ** — it runs
`curl -s https://assets.emqx.com/images/install-nanomq-deb.sh | bash 2>/dev/null` and only falls
back to `apt-get install mosquitto` if that fails — yet `CLAUDE.md:187` and the README both call
the default broker "mosquitto"/"NanoMQ" inconsistently. Consequences:

- If NanoMQ wins the install race → the 6 mosquitto-only units (esp. `drifter-feeds`, which orders
  after mosquitto but **not** nanomq) have no ordering against the real broker.
- If NanoMQ's remote install script 404s/fails and mosquitto is used → the 58 `After=nanomq.service`
  edges become **no-ops** (`After=` on a non-existent unit is silently ignored; `Wants=` is weak),
  so those consumers start with **zero ordering guarantee** relative to the actual broker.

Which broker a fresh flash ends up with is therefore a function of network conditions at install
time. **Recommended fix (Phase 2):** a `drifter-broker.target` (or a stable `mqtt.target`) that
whichever broker `Alias=`/`WantedBy=`, and rewrite every unit to order against that one target.

### Hardware-readiness ordering risks

Units that need a device node but order only after broker/network with **no device guard**
(rely entirely on in-process degrade — which some, but not all, actually implement):

| Unit | Needs | Guard present? |
|---|---|---|
| drifter-canbridge | CAN adapter | No `ConditionPathExists`; `ExecStartPre=drifter-setup-can` has **no `-` prefix** (a setup-can failure fails the unit). In-proc degrade **is** implemented (`can_bridge.py:306-339`, stays alive & retries). |
| drifter-rf / drifter-rf-baseline | RTL-SDR | No device guard on `drifter-rf`; rf-baseline at least chains `After=drifter-rf drifter-gps`. |
| drifter-rfaudio | RTL-SDR + audio | `After=sound.target` (audio ok) but nothing asserts the SDR. |
| drifter-flipper | Flipper USB-serial | No device guard (contrast marauder's `DeviceAllow=char-ttyACM/ttyUSB`). |
| drifter-obdbridge | ELM327 serial/BT | No device guard. |
| drifter-lcd | /dev/fb1 + SPI + GPIO | **No `ConditionPathExists=/dev/fb1`**, although `drifter-fbmirror` **does** guard it — inconsistent. `ExecStartPre=-…` SPI rebind swallows errors. (`lcd_dashboard.py:194` does resolve fb by sysfs driver name + waits `LCD_FB_WAIT_SEC=90` — good in-proc mitigation.) |
| drifter-vision / drifter-alpr / drifter-dashcam / drifter-sentry / drifter-fcw | camera / Hailo | No device guard (all add-on units). |
| drifter-satellite | ESP32 | No device guard. |

Well-guarded counter-examples to emulate: `drifter-fbmirror` (`ConditionPathExists=/dev/fb1`),
`drifter-gps` (`After=gpsd.service`), `drifter-bleconv` (`After=bluetooth.service`),
`drifter-marauder` (`DeviceAllow` ttyACM/ttyUSB), audio units (`After=sound.target`).

### Readiness-signal risk (Type=simple as an ordering hub)

`drifter-canbridge` is `Type=simple` yet 8 units order `After=` it expecting *telemetry to exist*.
`After=` on a `simple` unit only guarantees the process was **spawned**, not that CAN is up or MQTT
is publishing — so alerts/anomaly/realdash/dashboard/session-recorder/watchdog can race it. Same
pattern for socket-binding simple units that *could* emit a READY signal but don't:
`drifter-dashboard` (:8080), `drifter-realdash` (:35000), `drifter-fleet`, `drifter-opsec` (:8090).
No unit uses `Type=notify`/`sd_notify`, and there are **no `ExecStartPost` socket/HTTP health
probes** anywhere to compensate.

### Untracked background children

- `drifter-mesh`: two `ExecStartPost=/bin/sh -c '… mesh_coordinator.py &' / '… mesh_bridge.py &'`
  — backgrounded, unsupervised; if they die the parent stays "active" and nothing restarts them.
- `drifter-replay`: `ExecStartPost=/bin/sh -c '… session_recorder.py &'` — **and** `session_recorder.py`
  has its **own** unit `drifter-session-recorder.service`, so it can run twice / double-subscribe.

### Error-swallowing in units

- `-`-prefixed `EnvironmentFile=` is pervasive (`-/opt/drifter/.env`, `-/etc/default/drifter`,
  `-/etc/drifter/kismet.env`) — intentional, but a **missing/typo'd env file is silently ignored**,
  so key-dependent services (weather, location, aidiag, analyst, reporter) come up "green" but idle.
- `drifter-llm-keepwarm`: `SuccessExitStatus=0 7 28 56` deliberately treats curl connect/timeout as
  success (documented; keep-warm is best-effort). No literal `|| true` found in unit files.
- Crash-loop guards: `StartLimitIntervalSec`+`Burst` present on every simple unit **except**
  `canbridge`/`alerts`/`watchdog`, where they're deliberately omitted (documented — an old
  `StartLimitAction=reboot-force` caused reboot loops on the telemetry core).

---

## 3. Per-service hardware/interface dependency (summary)

| Interface | Services that require it |
|---|---|
| **CAN bus** (slcan0/can0) | canbridge, alerts*, anomaly*, realdash, session-recorder, can-discovery, db-checkpoint (ordering), watchdog* (*consume CAN telemetry via MQTT) |
| **RTL-SDR** | rf, rf-baseline, rfaudio, fly-catcher, feeds |
| **GPS/gpsd** | gps → (rf-baseline, and MQTT consumers: feeds/location/nav/trip) |
| **MQTT broker** | ~all (58 order nanomq, 6 mosquitto) |
| **Bluetooth/BLE** | bleconv (BlueZ), wardrive (BT scan) |
| **audio/ALSA** | voice, vivi, voicein (mic), rfaudio, ghost-voice |
| **framebuffer /dev/fb1 + SPI/GPIO** | lcd, fbmirror, boot-manager |
| **Wi-Fi monitor-mode NIC** | kismet, wifi-audit, wardrive, marauder(host), opsec |
| **Flipper / ESP32 / USB-serial** | flipper, marauder, satellite, obdbridge, comms(modem) |
| **camera / Hailo NPU** | vision, alpr, dashcam, sentry, fcw |
| **USB HID gadget /dev/hidg0** | hid |
| **Ollama daemon (:11434)** | analyst, vivi, reporter, aidiag, llm-keepwarm |
| **external network + API key** | weather (OWM), location (Google), homesync, feeds, discord, spotify |

---

## 4. Hardcoded values

### 4a. Secrets / credentials / PSKs — RELEASE BLOCKERS

| Value | Location | Severity |
|---|---|---|
| `PHONE_HOTSPOT_PSK` default `"54232105"` | `src/config.py` (autoconnect block) | **CRITICAL** — real phone-hotspot password as env default in tracked source. Rotate provider-side. |
| AP PSK `wifi-sec.psk "uncaged1312"` | `install.sh:583` | **CRITICAL** — real WPA2 PSK for MZ1312_DRIFTER, hardcoded. **Not env-overridable** (see below). Rotate. |
| Same PSK echoed to operator | `install.sh:704` | CRITICAL (same secret) |
| Same PSK in docs | `docs/FIELD_DEPLOY.md:136`, `docs/fleet-inventory-drifter.yaml:24`, `README.md:103` | CRITICAL (same secret in 3 docs; CLAUDE/AGENTS claim it was already rotated out of docs — it wasn't) |
| Historical live OWM + Google Maps keys | git history (per `CLAUDE.md:275`, `config/.env.example:51`) | CRITICAL (historical) — recoverable from history; rotate provider-side |
| Real VIN as filename + content | `vehicles/SAJEA51D44XD39283.yaml` | Medium — personal-identifier disclosure |
| Real-format VIN | `config/home.yaml:5` (`SAJDA01N04FK00000` — looks placeholder) | Low |

**Config trap:** `config/.env.example:61` documents `DRIFTER_HOTSPOT_PSK` as "consumed by
install.sh", but `install.sh` never reads it — it hardcodes `"uncaged1312"` at line 583. The env
override is a **no-op**; the only way to change the AP PSK today is editing source or `nmcli`
post-install. (Env defaults that are the empty string — `GROQ_API_KEY`, `ANTHROPIC_API_KEY`,
`NANOB_PASS`, the api_keys.py OWM/Google keys — are fine. `FLEET_JWT_SECRET` is auto-generated
0600, fine. `config/fleet.yaml:17-18` documents a dev-only `/api/auth/login` that issues tokens to
any caller — an auth gap to note, not a hardcoded secret.)

### 4b. Hardcoded IPs / hostnames

| Value | Location | Env? |
|---|---|---|
| `NANOB_HOST = "192.168.1.159"` | `src/config.py:455` | No |
| `10.42.0.1/24` (AP subnet) | install.sh (581/584/705/706), fleet-inventory (19/25), deploy.ps1:109, opsec_dashboard.py:193 | No |
| `PING_HOST` default `8.8.8.8` | `src/config.py` | Yes |
| `MQTT_HOST = "localhost"` | `src/config.py:258` | No (feeds/docker use `DRIFTER_MQTT_PORT`) |
| `NAV_OSRM_HOST`, `WEATHER_API_HOST`, OWM/Google/Groq base URLs | `src/config.py` | No (external API hosts) |
| `FLEET_API_HOST="0.0.0.0"`, dashboard/WS bind `0.0.0.0` | config.py:890, web_dashboard.py (97/159/340/342), fleet.yaml:5, nanomq.conf:4 | No (all-interface bind) |
| `sentient.local` / `homeassistant.local` | config/mesh.yaml, fleet.yaml, home.yaml | in yaml |
| last-known LAN IP `10.246.228.156`, `ssh_host: kali@<pi-ip>` | `docs/fleet-inventory-drifter.yaml:10,19` | doc |
| `DEFAULT_LAT/LON` = Bendigo VIC | `src/config.py` | Yes (`DRIFTER_DEFAULT_LAT/LON`) |

### 4c. Hardcoded ports

MQTT 1883, dashboard 8080, WS telemetry 8081, WS audio 8082, RealDash 35000, OPSEC 8090,
Fleet 8420, Satellite discovery 8421, Ollama 11434 (env), Kismet 2501, Spotify callback 8888,
NANOB 1883. Most are literals in `config.py` / `web_dashboard.py` / `opsec_dashboard.py`; only
Ollama, Kismet, Fleet (yaml), Spotify (yaml) are overridable.

### 4d. Device paths — enumeration-fragile (Phase 2 udev target)

| Value | Location | Stable? |
|---|---|---|
| `OBD_SERIAL_DEV = "/dev/ttyUSB0"` | config.py, obd.yaml:8, docker-compose:203/207, tools/flash_marauder.sh:10 | **RAW — fragile** |
| `COMMS_MODEM_DEV = "/dev/ttyUSB2"` | config.py:866 | **RAW — fragile (fixed index 2)** |
| `NAV_GPS_DEVICE = "/dev/ttyACM0"` | config.py, nav.yaml:5 | **RAW — fragile** |
| `RFAUDIO_APLAY_DEVICE = 'plughw:0,0'` | config.py, rdkx5.yaml:46 | **RAW ALSA index** |
| Whisper capture `hw:0,0` | voice_input.py | RAW ALSA index |
| slcan scan over `/dev/ttyACM* /dev/ttyUSB*` | config/setup-can.sh:60 | globbed (order-dependent, but scans all + VID:PID-filtered in can_bridge.py) |
| `/dev/fb0`, `/dev/fb1` | fbmirror/lcd/boot-manager units | RAW fb index (lcd resolves by sysfs name in-proc — good) |
| GPIO `PTT_GPIO_PIN=17`, `LCD_BTN_PREV/NEXT/ACTION=17/27/22` | config.py | **17 collides** (PTT vs LCD prev — flagged in-code) |

Existing udev rules to extend: `config/80-can.rules`, `services/51-drifter-bluetooth.rules`.
`lcd_dashboard.py`'s sysfs-name framebuffer resolution is the in-repo precedent for stable
device binding.

### 4e. Hardcoded absolute paths

`DRIFTER_DIR = Path("/opt/drifter")` (`config.py:62`) is the root anchor for **every** derived
path — no env override, breaks on any other layout. User `drifter`/`kali` hardcoded across
sudoers + `docs/fleet-inventory-drifter.yaml:14` + `scripts/start-hud.sh:12`
(`/home/kali/.Xauthority`). `RTL433_BIN='/usr/local/bin/rtl_433'`, absolute font paths (with
fallback list), `config/fleet.yaml:8` db path.

### 4f. MQTT topics

Central registry `src/_config_topics.py` (`config.TOPICS`, ~200 topics) is the intended source of
truth, **but ~46 inline `"drifter/…"` literals** are scattered across ≥10 modules
(`status.py` 13, `opsec_marauder_client.py` 9, `marauder_bridge.py` 8, `realdash_bridge.py` 5,
`home_sync.py`/`calibrate.py` 3 each, plus web_dashboard/alert_engine/logger/watchdog) — drift
risk from the registry. `config/mesh.yaml:5-11` and `HOMESYNC_EXCLUDE_TOPICS` also carry raw
literals.

---

## 5. Power-cut / durability posture

Bench→vehicle gap #1 (dirty power). Mixed:

- **Atomic writers (good, reuse these):** `feeds.atomic_write_json/bytes` (feeds.py:151-160),
  `gps_publisher` (72-74), `marauder_storage` (82-84), `rf_baseline` (96), `rf_monitor` TPMS-assign
  (499), `web_dashboard_handlers` (1910) — all write-tmp-then-`os.replace`.
- **Non-atomic writers (power-cut can truncate):**
  - `src/calibrate.py:173` — **calibration baselines** (`json.dump` direct). Truncation loses
    calibration; no atomic write.
  - `src/config.py:251` — **settings.json** (`save_settings`). `load_settings` catches the parse
    error and falls back to defaults, so it self-heals but silently discards operator settings.
  - `src/rf_monitor.py:223` — **learned TPMS sensor map** (`json.dump`). Truncation loses a 5-min
    learn cycle.
  - `src/home_sync.py:100` — sync state.
  - `src/logger.py:150` — session summary.
- **SQLite:** central `drifter.db` opened in `db.py:60` with **no `journal_mode=WAL` and no
  `busy_timeout`**. Yet `drifter-db-checkpoint.service` runs `PRAGMA wal_checkpoint(TRUNCATE)` on
  shutdown — a **no-op against a non-WAL DB**, so the shutdown-hardening is ineffective and
  concurrent writers get immediate `database is locked`. (WAL + `busy_timeout` *is* correctly set
  in `ble_history.py`, `ble_passive.py`, `vivi_memory.py` — the pattern exists, just not on the
  main DB.)

---

## 6. Reproducibility gaps

### 6a. Three disagreeing "canonical" service lists

| Source | Count |
|---|---|
| `config.SERVICES` (source of truth) | **38** |
| `install.sh:645` `SERVICES=` (+3 aux = 41) | 38 + boot-manager/boot-reason/db-checkpoint |
| `scripts/oneshot.sh:143-154` | 38 (separate copy — drift risk) |
| `scripts/deploy-pi5.sh:122-128` | 18 (stale) |
| `scripts/post-deploy-check.sh:101` | 20 (stale) |

`tests/test_deploy_service_lists.py` reportedly enforces oneshot/install sync, but deploy-pi5 /
post-deploy-check are unguarded and stale.

### 6b. What `sudo ./scripts/oneshot.sh` does NOT cover (fresh-flash manual steps)

1. **Getting the repo onto the Pi** (oneshot runs from inside it; nothing clones).
2. **Populating `/opt/drifter/.env`** — `install.sh:518` only `touch`es an empty file; the
   documented `config/.env.example` is **never copied**. Every API-key feature stays idle.
3. **Wi-Fi boot-hang fix + autologin** — `fix-wifi-boot.sh` / `setup-autoboot.sh` are **not**
   called by oneshot/install; the documented brcmfmac boot hang can block reaching a login prompt.
4. **SPI LCD** — `setup-lcd.sh` (overlay, fb1 udev, deps) is manual.
5. **Native CAN-FD / RDK X5** — `setup-can-fd.sh`, `install-rdkx5.sh` manual.
6. **Every `install-*.sh` add-on** (OBD, nav, **safety**, vision, mesh, fleet, discord, spotify,
   vivi-whisper) is a separate manual step — and their units aren't in the oneshot 38-list, so a
   stock oneshot never brings up OBD/nav/safety/vision even though the code ships.
7. **LLM model drift** — `install.sh:202` pulls `qwen2.5:1.5b`, `install-vivi.sh:98` pulls
   `llama3.2:3b`, `post-deploy-check.sh:212` tells the operator to pull `qwen2.5:7b`. Three tags.
8. **Best-effort model/tool fetches** — Piper/Vosk/torch/sentence-transformers are all
   `|| warn`; a partial/networkless install still reports `DEPLOY: ok` with silent degradation.
9. Hardware wiring + `calibrate.py --auto`, **reboot** (install.sh:700), and fleet-side steps.

### 6c. Idempotency / silent failure

- **Boot-config SPI grep trap:** `install.sh:550-561` skips appending its CAN dtoverlay block if
  `grep -q "dtparam=spi=on"` already matches — but `setup-lcd.sh:50` writes exactly that string, so
  running LCD setup first can make install.sh **silently drop the CAN overlay**. Order-dependent.
- `install.sh` uses `set -eo pipefail` — but nearly every meaningful step is `2>/dev/null` +
  `|| warn`, defeating it. Two unverified remote-root pipes: `curl … install-nanomq-deb.sh | bash`
  (134), `curl … ollama.com/install.sh | sh` (184).
- `.env` ownership/mode contested: `install.sh:529` sets `chmod 640 drifter:drifter`;
  `install-discord.sh:38` sets `chmod 600` — last writer wins.

### 6d. Dependency pinning

- `pyproject.toml` has **floors only, no ceilings**; **no lockfile, no requirements.txt**.
- **The Pi deploy ignores `pyproject.toml`** — `install.sh:289-335` pip-installs a hand-typed,
  mostly-unpinned list. Only `paho-mqtt>=2.0` is bounded.
- **Docker contradicts the project:** `Dockerfile:28` installs `paho-mqtt<2.0` while pyproject +
  install.sh require `>=2.0` — a container and a Pi get incompatible MQTT client majors.
- High-churn unpinned deps (numpy, websockets, Pillow, faster-whisper, sentence-transformers/torch,
  opencv, onnxruntime, discord.py) will drift-break a fresh install months out.

---

## 6e. Documentation / config drift (README is the most-drifted file)

- **Service counts:** `config.py:638` comment still says "**19**" directly above the 38-item list;
  `README.md:351` says "18 units" (lists 17); README services table has 16 rows;
  `fleet-inventory-drifter.yaml:33` says 15; `CLAUDE.md` carries stale "25"/"21" snapshots
  (self-flagged historical). Accurate: `CLAUDE.md:149` header ("38"), `COCKPIT.md:15`, install.sh.
- **Verified-accurate discrepancies (keep):** RealDash is **TCP** not UDP
  (`realdash_bridge.py:403-408`); dashboard is **http.server** not FastAPI
  (`web_dashboard.py:43,97`).
- **Broker:** README diagram/table/troubleshooting treat NanoMQ as default; actual install prefers
  NanoMQ-then-mosquitto by race; `config.MQTT_HOST="localhost"`.
- **MQTT reachability bug:** README tells operators to point RealDash at "MQTT → 10.42.0.1:1883"
  (`README.md:105`), but the broker was rebound to `127.0.0.1:1883` (CLAUDE.md hardening note) — a
  phone on 10.42.0.x **cannot** reach it. Broken instruction.
- **CAN adapter:** README assumes gs_usb USB2CANFD → `can0`; bench reality is CANable/slcan
  (`0483:5740`) → `slcan0` (`FIRST_DRIVE.md:12`, `FIELD_DEPLOY.md:173-179`). K-line-vs-CAN
  uncertainty for the 2004 X-Type is absent from README.
- **Modes:** `DEFAULT_MODE="diag"` (lean) is never mentioned in README.
- **"No transmit" is false at the system level:** RTL-SDR is RX-only, but the node ships
  `drifter-marauder` (Wi-Fi/BT deauth, beacon spam, evil portal), `drifter-flipper` (sub-GHz
  replay), `drifter-hid` (BadUSB), `drifter-wifi-audit` (PMKID/handshake capture),
  `drifter-can-discovery` (CAN fuzz). README **omits this entire suite** rather than framing it.
- **Pi 4 vs Pi 5:** `fleet-inventory-drifter.yaml:23` says Pi 4; everything else says Pi 5.

### Capability-scope decision (blocks the README/CAPABILITIES rewrite)

The release goal is **defensive / situational-awareness only**. These shipped services are
offensive or need explicit scoping — a decision is required before publishing:

| Service | Capability | Disposition needed |
|---|---|---|
| drifter-marauder | ESP32 deauth / beacon spam / evil portal / BLE spam (`MARAUDER_COMMANDS`, config.py) | **Gate hard, or remove for the public release.** Clearly against-third-parties. |
| drifter-hid | BadUSB keystroke injection | Gate/remove. |
| drifter-wifi-audit | bettercap PMKID/handshake capture | Gate/remove or scope to owned-AP-only. |
| drifter-flipper | sub-GHz capture **+ replay** | Scope to RX/monitor for release, or gate replay. |
| drifter-can-discovery | CaringCaribou UDS/fuzz (self-vehicle) | Framable (own vehicle) but "fuzz" wording needs care. |
| drifter-ghost / ghost-voice | tracker/IMSI/ALPR/RF correlator | Defensively framable (counter-surveillance) but bundles IMSI + plate-reading — frame carefully. |
| drifter-kismet(-bridge) / wardrive | passive Wi-Fi/BLE recon | Framable as situational awareness (passive). |
| drifter-opsec | "OPSEC dashboard (Kali aesthetic)" | Branding/framing for a "respectable" project. |

### Missing release artifacts

CAPABILITIES.md ❌ · SECURITY.md ❌ · CHANGELOG.md ❌ · RELEASE-CHECKLIST.md ❌ ·
CODE_OF_CONDUCT.md ❌ · CONTRIBUTING.md ✅ (present but thin). **LICENSE ✅ — MIT, complete**
(© 2026 MZ1312 UNCAGED TECHNOLOGY, full standard body). No license action needed.

---

## 7. Assumptions to confirm on hardware

These cannot be verified from the repo alone and must be checked against live-node output before
flipping yellow→green:

1. **Which broker actually installed** (NanoMQ vs mosquitto) on a given flash — `systemctl status
   nanomq mosquitto` — because the ordering correctness depends on it (§2 broker note).
2. **CAN adapter identity + interface** — is it CANable/slcan → `slcan0` (bench) or USB2CANFD →
   `can0`? And whether the 2004 X-Type 2.5 is CAN or K-line on the OBD pins at all
   (FIELD_DEPLOY/FIRST_DRIVE flag this as unconfirmed).
3. **Device enumeration order** — actual `/dev/ttyUSB*`/`ttyACM*` assignment with OBD + RTL-SDR +
   mic + Flipper all plugged (drives the udev-symlink rules).
4. **ALSA card index** for the USB audio dongle (`plughw:0,0` assumption) — `aplay -l`.
5. **Framebuffer index** the SPI panel registers as (`/dev/fb1` assumption) and its sysfs driver
   name — `ls /sys/class/graphics/`.
6. **GPIO wiring** — whether a dedicated PTT button exists (pin 17 PTT vs LCD-prev collision).
7. **Ollama model actually present** and whether the 1.5b/3b/7b tag matches what services request.
8. **Whether `/opt/drifter/.env` was populated** with real API keys on the target (weather/location
   idle vs live).

---

## 8. Recommended remediation order (proposed — pending confirmation)

**P0 — release blockers (secrets + reproducibility correctness):**
1. Purge/rotate committed PSKs (`config.py`, `install.sh:583/704`, 3 docs); wire `install.sh` to
   actually read `DRIFTER_HOTSPOT_PSK`; rotate historical API keys provider-side; scrub the
   committed VIN.
2. Unify the broker: `drifter-broker.target`, deterministic broker choice in `install.sh`, rewrite
   unit ordering; fix the README `10.42.0.1:1883` instruction.
3. Collapse the service lists to one generated source; fix `config.py:638` "(19)" comment and the
   `fbmirror`-not-in-SERVICES inconsistency.

**P1 — power-cut + hardware resilience (bench→vehicle):**
4. Make `calibrate.py` / `rf_monitor` TPMS / `config.save_settings` / `logger` / `home_sync`
   writes atomic (reuse `feeds.atomic_write_json`).
5. Set WAL + `busy_timeout` on `db.py` so the checkpoint service is meaningful and writers don't
   lock out.
6. udev by-id/by-path symlinks for OBD/GPS/audio/Flipper/ESP32; point config at the symlinks; add
   `ConditionPathExists` / device guards consistently (esp. `drifter-lcd`).
7. Fix untracked `ExecStartPost &` children (mesh/replay) and the replay/session-recorder
   double-run.

**P2 — reproducibility:**
8. Make `install.sh` install `.env` from `.env.example`; single owner for `.env`; pin deps
   (lockfile or bounded requirements the Pi path actually uses); fix Docker `paho-mqtt` conflict;
   converge on one Ollama tag; stop `DEPLOY: ok` on failed model fetches.

**P3 — release polish:**
9. Decide the offensive-capability scope; write CAPABILITIES.md + SECURITY.md + CHANGELOG +
   RELEASE-CHECKLIST; rewrite README (38 services, correct broker/CAN/modes, no PSK).

---

*End of Phase 0. Per the working method, no code has been changed. Awaiting confirmation of
priorities before beginning Phase 1.*

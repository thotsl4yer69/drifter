# DRIFTER — AI Agent Instructions

Vehicle intelligence system originally built for a **2004 Jaguar X-Type 2.5L V6 (AJ-V6)** on Raspberry Pi 5 (Kali ARM64). v2 generalises it: VIN-driven vehicle profiles, multi-tier diagnostics with a Claude→Groq→Ollama cascade, infotainment, ADAS, and a Hailo Pi5 vision node.
Brand: **MZ1312 UNCAGED TECHNOLOGY — EST 1991**

## Architecture

**~40 Python modules** in `src/` — flat layout, no sub-packages — deployed to `/opt/drifter/`.
Every module imports shared constants from [`src/config.py`](src/config.py): paths, thresholds, MQTT topics, vehicle specs, DTC lookup, v2 cascade settings, and the canonical `SERVICES` list (~33 services after v2).

**Data flow (v2)**:
- Ingest: `can_bridge.py` (primary) or `obd_bridge.py` (ELM327 fallback) → MQTT (NanoMQ)
- Aggregation: `telemetry_batcher.py` produces a rolling-window summary
- Tier 1: `safety_engine.py` (local deterministic safety rules)
- Tier 2: `ai_diagnostics.py` (Claude via `llm_client_v2.py`)
- Tier 3: `session_reporter.py` (post-drive markdown narrative)
- Voice: `vivi_v2.py` ↔ `vivi_memory.py` (streaming Claude with SQLite memory)
- Vehicle: `vehicle_id.py` resolves VIN → profile in `vehicles/<VIN>.yaml`
- Learning: `adaptive_thresholds.py`, `vehicle_kb.py`, `vehicle_learn.py`
- Infotainment: `spotify_bridge.py`, `nav_engine.py`, `trip_computer.py`
- ADAS / safety: `crash_detect.py`, `driver_assist.py`, `sentry_mode.py`, `comms_bridge.py`
- Vision (separate Pi5 + Hailo node): `vision_engine.py`, `alpr_engine.py`, `dashcam.py`, `forward_collision.py`

**MQTT topics** use the `TOPICS` dict from config — never hardcode. v2 added namespaces: `drifter/safety/*`, `drifter/diag/ai/*`, `drifter/session/*`, `drifter/vehicle/*`, `drifter/telemetry/*`, `drifter/thresholds/*`, `drifter/kb/*`, `drifter/learn/*`, `drifter/vivi2/*`, `drifter/spotify/*`, `drifter/nav/*`, `drifter/trip/*`, `drifter/crash/*`, `drifter/driver/*`, `drifter/sentry/*`, `drifter/comms/*`, `drifter/obd/*`, `drifter/vision/*`.
**RealDash**: TCP CAN 0x44 protocol on port 35000. Frames: 4-byte header `[0x44,0x33,0x22,0x11]` + 4-byte LE frame_id + 8-byte data.
**LLM cascade** (v2): `llm_client_v2.query()` / `query_json()` / `stream()` — Claude (primary) → Groq → Ollama with prompt cache, retries, and per-backend health tracking. Always prefer this over `llm_client.py` for new code.

## Code Style

- **Python 3**, snake_case everywhere, `UPPER_SNAKE_CASE` for constants
- Every file starts with `#!/usr/bin/env python3` and a docstring: `MZ1312 DRIFTER — <Name>\n<desc>.\nUNCAGED TECHNOLOGY — EST 1991`
- Logging: `logging.basicConfig(format='%(asctime)s [TAG] %(message)s', datefmt='%H:%M:%S')` — TAG is UPPERCASE module name
- MQTT: `paho-mqtt<2.0` (v1.x API — `mqtt.Client(client_id="drifter-<name>")`, no `CallbackAPIVersion`)
- Paths: `pathlib.Path` for all filesystem operations

### Service skeleton (every daemon follows this):

```python
def main():
    running = True
    def _handle_signal(sig, frame):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    # MQTT connect-retry loop (3s sleep), client.loop_start(), main while-loop, cleanup
```

## Diagnostic Rules (`alert_engine.py`)

23 rules: 13 core OBD + 3 TPMS + 7 X-Type specific. Each rule is a function:

```python
def rule_<name>(state: VehicleState) -> Optional[tuple[int, str]]:
    # 1. Extract via state.avg() / state.latest() / state.trend()
    # 2. Return None if data is None (mandatory null guard)
    # 3. Suppress during cold start if fuel-trim related (coolant < WARMUP_COOLANT_THRESHOLD)
    # 4. Subtract calibration baseline before threshold comparison
    # 5. Check RED before AMBER before INFO
    # 6. Return (LEVEL_RED/AMBER/INFO, "message with values + X-Type mechanic guidance") or None
```

- Append new rules to `ALL_RULES` list. Update `test_rule_count` assertion.
- X-Type specific rules: prefix `rule_xtype_<name>`
- Messages include actual values with units and actionable X-Type repair guidance
- `evaluate_rules()` publishes only the highest-severity alert, with retain=True

## Adding a New Sensor

1. `config.py`: add threshold constants to `THRESHOLDS`, add topic to `TOPICS`
2. `can_bridge.py`: add PID to `PIDS` dict with decode lambda, unit, hz
3. `alert_engine.py`: add `deque` to `VehicleState`, add routing in `on_message()`, write rule(s), append to `ALL_RULES`
4. `realdash_bridge.py`: add frame packer function, add to `handle_client()` send loop
5. `realdash/drifter_channels.xml`: add matching `<frame>` with correct conversion formula
6. `tests/test_alert_engine.py`: add trigger, OK, and no-data test cases

## Build & Test

```bash
# Syntax check all modules
python -m py_compile src/config.py src/can_bridge.py src/alert_engine.py ...

# Run tests (from repo root)
pytest tests/ -v

# Test bench (requires MQTT broker running)
./scripts/test-bench.sh [idle|vacuum|overheat|alternator|coldstart|thermostat|dtc|all]

# Deploy (on Pi)
sudo ./install.sh && sudo reboot
```

**Dependencies**: `python-can`, `paho-mqtt<2.0`, `psutil` — installed in venv at `/opt/drifter/venv`.  
**Test path setup**: `conftest.py` inserts `src/` into `sys.path`. Import directly: `from config import ...`

## Project Conventions

- **No hardcoded MQTT topics** — always use `TOPICS['key']` from config
- **No class-based services** — flat `main()` + `if __name__ == '__main__': main()` pattern
- **~33 systemd services** in `SERVICES` list (config.py) must match `services/*.service` files
- **install.sh** `SRC_FILES` variable must list every `.py` file in `src/` that should be deployed
- **RealDash XML** frame IDs and conversions must match `realdash_bridge.py` pack functions exactly
- **DTC codes**: add to `XTYPE_DTC_LOOKUP` in config with `desc`, `cause`, `action`, `severity` keys
- **TPMS thresholds**: tuned for 205/55R16 at factory 30 PSI (warn 26, crit 20)
- **Vehicle profile** (v2): per-VIN YAML in `vehicles/`. Use `vehicle_id.resolve_profile()` to read at runtime — do not hardcode VEHICLE/VEHICLE_YEAR for behaviour gates
- **LLM calls** (v2): use `llm_client_v2.query()` / `query_json()` / `stream()` — never reach out to a backend directly
- **README.md**: keep module/service counts and feature lists in sync when adding modules

## v2 Module Reference

| Module | Role | MQTT keys |
|--------|------|-----------|
| `telemetry_batcher.py` | rolling-window stats | `telemetry_window`, `telemetry_stats` |
| `safety_engine.py` | Tier 1 local safety rules | `safety_alert`, `safety_status` |
| `ai_diagnostics.py` | Tier 2 Claude diagnoses | `ai_diag_*` |
| `session_reporter.py` | Tier 3 post-drive narrative | `session_report`, `session_summary` |
| `llm_client_v2.py` | Claude→Groq→Ollama cascade (library) | — |
| `vehicle_id.py` | VIN auto-detect + profile resolution | `vehicle_id`, `vehicle_profile` |
| `adaptive_thresholds.py` | per-vehicle baseline learning | `thresholds_learned`, `thresholds_update` |
| `vehicle_kb.py` | per-vehicle KB query/store | `kb_query`, `kb_response`, `kb_update` |
| `vehicle_learn.py` | continuous learning into KB | `learn_event` |
| `vivi_v2.py` + `vivi_memory.py` | Claude voice brain with persistent memory | `vivi2_*` |
| `spotify_bridge.py` | Spotify Connect commands | `spotify_*` |
| `nav_engine.py` | GPS, speed-cameras, OSRM | `nav_*` |
| `trip_computer.py` | distance/fuel/cost | `trip_*` |
| `crash_detect.py` | accel+OBD crash detection | `crash_event`, `crash_sos`, `crash_status` |
| `driver_assist.py` | score / fatigue / weather | `driver_*` |
| `sentry_mode.py` | parked-car monitor | `sentry_*` |
| `comms_bridge.py` | SMS + ntfy/Telegram/Discord | `comms_*` |
| `obd_bridge.py` | ELM327 serial fallback | `obd_status`, `obd_pid` (publishes metric topics) |
| `vision_engine.py` | YOLO on Hailo (ONNX fallback) | `vision_object`, `vision_status` |
| `alpr_engine.py` | plate OCR | `alpr_plate` |
| `dashcam.py` | ffmpeg segmented recording | `dashcam_status`, `dashcam_clip` |
| `forward_collision.py` | time-to-collision warnings | `fcw_warning`, `fcw_status` |

## API Keys (v2)

`/opt/drifter/.env` is sourced by systemd via `EnvironmentFile=-/opt/drifter/.env`:
```
ANTHROPIC_API_KEY=sk-ant-...
GROQ_API_KEY=gsk_...
```
`llm_client_v2` reads both from os.environ; missing keys skip those backends and fall through to the next tier. The cascade survives all-backends-down by raising RuntimeError that v2 services catch and translate to a `level: error` status.

## Vivi Voice Assistant (`src/vivi.py`)

Two-way voice conversation layer: faster-whisper STT → Ollama LLM → Piper TTS.  
MQTT client_id: `drifter-vivi`. Log tag: `[VIVI]`.

**Topics** (all from `TOPICS` in config — never hardcoded):
- `vivi_query` — inbound text/voice query (`{"query": "..."}` or bare string)
- `vivi_response` — outbound response (`{"query", "response", "ts"}`)
- `vivi_status` — state machine (`idle/listening/transcribing/thinking/speaking/wake_listening`)
- `audio_wav` — base64-encoded WAV for web dashboard audio bridge (shared with `voice_alerts.py`)

**Input modes** (set in `config/vivi.yaml` or `/opt/drifter/vivi.yaml`):
- `ptt` — press Enter to record (default)
- `wake_word` — activates on configurable phrase (default: "hey vivi")
- `always_on` — continuous transcription

**RAG**: queries `mechanic.py:search()` and `mechanic.py:get_advice_for_alert()` for offline X-Type knowledge before each LLM call.

**Adding Vivi features**: follow service skeleton in AGENTS.md (signal handlers, MQTT connect-retry, loop_start). Config lives in `vivi.yaml` — add new keys there, not as magic constants in the module. Tests live in `tests/test_vivi.py` — keep topic contract tests up to date.

## Security

- Zero cloud — all processing is local on the Pi
- Home sync uses `NANOB_USER` ("sentient") with `username_pw_set()` (no password)
- Wi-Fi hotspot: SSID `MZ1312_DRIFTER`, PSK `uncaged1312`, subnet `10.42.0.1/24`
- RTL-SDR decodes only — no transmit capability. Emergency bands detected but encrypted traffic (TETRA) is not decoded

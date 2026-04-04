# DRIFTER — AI Agent Instructions

Vehicle intelligence system for a **2004 Jaguar X-Type 2.5L V6 (AJ-V6)** on Raspberry Pi 5 (Kali ARM64).  
Brand: **MZ1312 UNCAGED TECHNOLOGY — EST 1991**

## Architecture

**22 Python modules** in `src/`, all flat (no sub-packages), deployed to `/opt/drifter/`.  
Organised in three tiers:

### Tier 1 — Core Diagnostics
`config.py` (single source of truth) · `can_bridge.py` · `alert_engine.py` · `logger.py` · `voice_alerts.py` · `realdash_bridge.py` · `watchdog.py` · `home_sync.py` · `rf_monitor.py` · `calibrate.py` · `status.py`

### Tier 2 — Intelligence
`llm_mechanic.py` · `mechanic.py` (97KB knowledge base) · `llm_client.py` · `tool_executor.py` · `field_ops_kb.py` · `anomaly_monitor.py` · `session_analyst.py` · `db.py`

### Tier 3 — Interface
`web_dashboard.py` (HTTP + WebSocket + audio bridge) · `voice_input.py` (Vosk STT + OpenWakeWord) · `wardrive.py`  
Non-Python: `screen_dash.html` · `fbmirror.c` · `start-hud.sh`

Every module imports shared constants from [`src/config.py`](src/config.py) — the single source of truth for paths, thresholds, MQTT topics, vehicle specs, DTC lookup, and service list.

**Data flow**: `can_bridge.py` → MQTT (NanoMQ) → `alert_engine.py` / `logger.py` / `voice_alerts.py` / `realdash_bridge.py` / `web_dashboard.py` / `llm_mechanic.py` / `anomaly_monitor.py` / `session_analyst.py`  
**MQTT topics** use the `TOPICS` dict from config — never hardcode topic strings. Hierarchy: `drifter/{domain}/{metric}`.  
**RealDash**: TCP CAN 0x44 protocol on port 35000. Frames: 4-byte header `[0x44,0x33,0x22,0x11]` + 4-byte LE frame_id + 8-byte data.  
**Web Dashboard**: HTTP on port 8080, WebSocket telemetry on 8081, audio on 8082.  
**LLM Mechanic**: Ollama chat API with tool calling. RAG over `mechanic.py` knowledge base + `field_ops_kb.py`.

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

# Deploy from Windows
.\deploy.ps1 -PiHost <ip>
```

**Dependencies**: `python-can`, `paho-mqtt<2.0`, `psutil`, `websockets`, `requests` — installed in venv at `/opt/drifter/venv`.  
**Optional deps**: `vosk`, `pyaudio`, `openwakeword` (voice input); `ollama` (LLM mechanic).  
**Test path setup**: `conftest.py` inserts `src/` into `sys.path`. Import directly: `from config import ...`

## Project Conventions

- **No hardcoded MQTT topics** — always use `TOPICS['key']` from config
- **No hardcoded MQTT host/port** — always use `MQTT_HOST`, `MQTT_PORT` from config
- **No class-based services** — flat `main()` + `if __name__ == '__main__': main()` pattern
- **Signal handlers inside `main()`** — never at module level (prevents import side effects)
- **16 systemd services** must match `SERVICES` list in config and `services/*.service` files
- **install.sh** `SRC_FILES` variable must list all 22 `.py` files for deployment
- **RealDash XML** frame IDs and conversions must match `realdash_bridge.py` pack functions exactly
- **DTC codes**: add to `XTYPE_DTC_LOOKUP` in config with `desc`, `cause`, `action`, `severity` keys
- **TPMS thresholds**: tuned for 205/55R16 at factory 30 PSI (warn 26, crit 20)
- **README.md**: keep rule count, diagnostic table, and repo structure in sync when adding rules

## Security

- Zero cloud — all processing is local on the Pi (Groq/Anthropic API in `llm_client.py` is optional, disabled by default)
- Home sync uses `NANOB_USER` ("sentient") with `username_pw_set()` (no password)
- Wi-Fi hotspot: SSID `MZ1312_DRIFTER`, PSK `uncaged1312`, subnet `10.42.0.1/24`
- RTL-SDR decodes only — no transmit capability. Emergency bands detected but encrypted traffic (TETRA) is not decoded
- LLM tool execution (`tool_executor.py`) has risk-level gating — dangerous commands require explicit authorization

#!/usr/bin/env python3
"""
MZ1312 DRIFTER — MQTT topic registry (pure data).

Extracted from config.py to keep the central config module lean. The complete
drifter/* MQTT topic map. No logic, no imports — config.py re-imports TOPICS so
the public API (`config.TOPICS` / `from config import TOPICS`) is unchanged.

UNCAGED TECHNOLOGY — EST 1991
"""

# ── MQTT Topics ──
TOPICS = {
    'rpm': 'drifter/engine/rpm',
    'coolant': 'drifter/engine/coolant',
    'stft1': 'drifter/engine/stft1',
    'stft2': 'drifter/engine/stft2',
    'ltft1': 'drifter/engine/ltft1',
    'ltft2': 'drifter/engine/ltft2',
    'load': 'drifter/engine/load',
    'speed': 'drifter/vehicle/speed',
    'throttle': 'drifter/engine/throttle',
    'voltage': 'drifter/power/voltage',
    'iat': 'drifter/engine/iat',
    'maf': 'drifter/engine/maf',
    'timing': 'drifter/engine/timing',
    'o2_b1s1': 'drifter/engine/o2_b1s1',
    'o2_b2s1': 'drifter/engine/o2_b2s1',
    'run_time': 'drifter/engine/run_time',
    'baro': 'drifter/engine/baro',
    'fuel_lvl': 'drifter/vehicle/fuel_lvl',
    'alert_level': 'drifter/alert/level',
    'alert_message': 'drifter/alert/message',
    'snapshot': 'drifter/snapshot',
    'system_status': 'drifter/system/status',
    'dtc': 'drifter/diag/dtc',
    'calibration': 'drifter/diag/calibration',
    'watchdog': 'drifter/system/watchdog',
    'drive_session': 'drifter/session',
    # RF / TPMS
    'tpms_fl': 'drifter/rf/tpms/fl',
    'tpms_fr': 'drifter/rf/tpms/fr',
    'tpms_rl': 'drifter/rf/tpms/rl',
    'tpms_rr': 'drifter/rf/tpms/rr',
    'tpms_snapshot': 'drifter/rf/tpms/snapshot',
    'rf_signal': 'drifter/rf/signals',
    'rf_spectrum': 'drifter/rf/spectrum',
    'rf_emergency': 'drifter/rf/emergency',
    'rf_status': 'drifter/rf/status',
    'rf_command': 'drifter/rf/command',
    'rf_adsb': 'drifter/rf/adsb',
    'rfaudio_command': 'drifter/rfaudio/command',
    'rfaudio_status': 'drifter/rfaudio/status',
    # Wardrive
    'wardrive_wifi': 'drifter/wardrive/wifi',
    'wardrive_bt': 'drifter/wardrive/bt',
    'wardrive_status': 'drifter/wardrive/status',
    'wardrive_snapshot': 'drifter/wardrive/snapshot',
    # Analyst
    'analysis_report': 'drifter/analysis/report',
    'analysis_request': 'drifter/analysis/request',
    'anomaly_event': 'drifter/anomaly/event',
    # Voice Input
    'voice_transcript': 'drifter/voice/transcript',
    'voice_command': 'drifter/voice/command',
    'voice_status': 'drifter/voice/status',
    'hud_navigate': 'drifter/hud/navigate',
    # Vivi voice assistant
    'vivi_query': 'drifter/vivi/query',
    'vivi_response': 'drifter/vivi/response',
    'vivi_status': 'drifter/vivi/status',
    'vivi_control': 'drifter/vivi/control',
    # Audio (shared with voice_alerts)
    'audio_wav': 'drifter/audio/wav',
    # Flipper Zero
    'flipper_status': 'drifter/flipper/status',
    'flipper_command': 'drifter/flipper/command',
    'flipper_result': 'drifter/flipper/result',
    'flipper_subghz': 'drifter/flipper/subghz',
    # HID injection (drifter-hid — Rubber Ducky / BadUSB, foot-only)
    'hid_command': 'drifter/hid/command',
    'hid_status': 'drifter/hid/status',
    'hid_result': 'drifter/hid/result',
    'hid_audit': 'drifter/hid/audit',
    # Tool Executor
    'tool_request': 'drifter/tool/request',
    'tool_result': 'drifter/tool/result',
    # Conversation mode (Vivi ↔ voice_input loop)
    'voice_listen_now': 'drifter/voice/listen_now',
    'vivi_conversation_mode': 'drifter/vivi/conversation_mode',
    'vivi_say': 'drifter/vivi/say',
    # Phase 5 — cockpit interrupt + voice control of HUD layers
    'adsb_police': 'drifter/adsb/police',
    'drone_detection': 'drifter/drone/detection',
    'hud_map_layer': 'drifter/hud/map/layer',
    # BLE passive scanner (Phase 4.5)
    'ble_detection': 'drifter/ble/detection',
    'ble_raw': 'drifter/ble/raw',
    # GPS (cached by ble_passive for detection geo-tagging)
    'gps_fix': 'drifter/gps/fix',
    # v2 — Telemetry Batcher
    'telemetry_window': 'drifter/telemetry/window',
    'telemetry_stats': 'drifter/telemetry/stats',
    # v2 — Trip Computer
    'trip_stats': 'drifter/trip/stats',
    'trip_event': 'drifter/trip/event',
    'trip_fuel': 'drifter/trip/fuel',
    'trip_cost': 'drifter/trip/cost',
    # v2 — Adaptive Thresholds
    'thresholds_learned': 'drifter/thresholds/learned',
    'thresholds_update': 'drifter/thresholds/update',
    # Recon / audit expansion (Agent B)
    'wifi_devices': 'drifter/wifi/devices',
    'ble_devices': 'drifter/ble/devices',
    'wifi_audit': 'drifter/wifi/audit',
    'airspace_aircraft': 'drifter/airspace/aircraft',
    'airspace_aircraft_classified': 'drifter/airspace/aircraft_classified',
    # v2 — Session Reporter
    'session_report': 'drifter/session/report',
    'session_summary': 'drifter/session/summary',
    'safety_alert': 'drifter/safety/alert',
    'ai_diag_response': 'drifter/ai/diag/response',

    # ── v2/v2.1 additions (from feature/drifter-v2) ──
    'ai_diag_request': 'drifter/diag/ai/request',
    'ai_diag_status': 'drifter/diag/ai/status',
    'alpr_plate': 'drifter/vision/alpr/plate',
    'can_dbc_generated': 'drifter/can/dbc/generated',
    'can_decode_request': 'drifter/can/decode/request',
    'can_decode_response': 'drifter/can/decode/response',
    'can_sniff_frame': 'drifter/can/sniff/frame',
    'can_sniff_status': 'drifter/can/sniff/status',
    'can_sniff_summary': 'drifter/can/sniff/summary',
    'comms_inbound': 'drifter/comms/inbound',
    'comms_notify': 'drifter/comms/notify',
    'comms_sms': 'drifter/comms/sms',
    'crash_event': 'drifter/crash/event',
    'crash_sos': 'drifter/crash/sos',
    'crash_status': 'drifter/crash/status',
    'dashcam_clip': 'drifter/vision/dashcam/clip',
    'dashcam_status': 'drifter/vision/dashcam/status',
    'discord_inbound': 'drifter/discord/inbound',
    'discord_outbound': 'drifter/discord/outbound',
    'discord_status': 'drifter/discord/status',
    'driver_event': 'drifter/driver/event',
    'driver_fatigue': 'drifter/driver/fatigue',
    'driver_score': 'drifter/driver/score',
    'driver_weather': 'drifter/driver/weather',
    'fcw_status': 'drifter/vision/fcw/status',
    'fcw_warning': 'drifter/vision/fcw/warning',
    'fleet_alert': 'drifter/fleet/alert',
    'fleet_command': 'drifter/fleet/command',
    'fleet_heartbeat': 'drifter/fleet/heartbeat',
    'fleet_register': 'drifter/fleet/register',
    'fleet_status': 'drifter/fleet/status',
    'fleet_telemetry': 'drifter/fleet/telemetry',
    'fuzz_command': 'drifter/fuzz/command',
    'fuzz_status': 'drifter/fuzz/status',
    'home_command': 'drifter/home/command',
    'home_event': 'drifter/home/event',
    'home_status': 'drifter/home/status',
    'kb_query': 'drifter/kb/query',
    'kb_response': 'drifter/kb/response',
    'kb_update': 'drifter/kb/update',
    'learn_event': 'drifter/learn/event',
    'llm_query': 'drifter/llm/query',
    'llm_response': 'drifter/llm/response',
    'marauder_cmd': 'drifter/marauder/cmd',
    'mesh_announce': 'drifter/mesh/announce',
    'mesh_bridge': 'drifter/mesh/bridge',
    'mesh_node': 'drifter/mesh/node',
    'mesh_status': 'drifter/mesh/status',
    'mesh_topology': 'drifter/mesh/topology',
    'nav_alert': 'drifter/nav/alert',
    'nav_camera': 'drifter/nav/camera',
    'nav_geofence': 'drifter/nav/geofence',
    'nav_position': 'drifter/nav/position',
    'nav_route': 'drifter/nav/route',
    'nav_status': 'drifter/nav/status',
    'obd_pid': 'drifter/obd/pid',
    'obd_status': 'drifter/obd/status',
    'presence_event': 'drifter/presence/event',
    'presence_status': 'drifter/presence/status',
    'recorder_command': 'drifter/recorder/command',
    'recorder_session': 'drifter/recorder/session',
    'recorder_status': 'drifter/recorder/status',
    'replay_command': 'drifter/replay/command',
    'replay_progress': 'drifter/replay/progress',
    'replay_status': 'drifter/replay/status',
    'safety_status': 'drifter/safety/status',
    'satellite_announce': 'drifter/satellite/announce',
    'satellite_command': 'drifter/satellite/command',
    'satellite_status': 'drifter/satellite/status',
    'satellite_telemetry': 'drifter/satellite/telemetry',
    'sentry_clip': 'drifter/sentry/clip',
    'sentry_event': 'drifter/sentry/event',
    'sentry_status': 'drifter/sentry/status',
    'spotify_command': 'drifter/spotify/command',
    'spotify_duck': 'drifter/spotify/duck',
    'spotify_status': 'drifter/spotify/status',
    'spotify_track': 'drifter/spotify/track',
    'vehicle_id': 'drifter/vehicle/id',
    'vehicle_profile': 'drifter/vehicle/profile',
    'vision_object': 'drifter/vision/object',
    'vision_status': 'drifter/vision/status',
    'vivi2_memory': 'drifter/vivi2/memory',
    'vivi2_proactive': 'drifter/vivi2/proactive',
    'vivi2_query': 'drifter/vivi2/query',
    'vivi2_response': 'drifter/vivi2/response',
    'vivi2_status': 'drifter/vivi2/status',
    'vivi2_stream': 'drifter/vivi2/stream',

    # ── RDK X5 port — native CAN FD bridge + toolkit (can_native.py) ──
    # The bridge republishes the same per-PID metric topics as can_bridge.py
    # (rpm/coolant/…/snapshot/dtc/system_status above); these are the
    # native-bridge-specific control + status + toolkit-output channels.
    'can_native_status': 'drifter/can/native/status',
    'can_native_command': 'drifter/can/native/command',
    'can_native_frame': 'drifter/can/native/frame',
    'can_native_fuzz': 'drifter/can/native/fuzz',
    'can_native_replay': 'drifter/can/native/replay',

    # ── Counter-surveillance (ghost_protocol.py) ──
    'ghost_status': 'drifter/ghost/status',
    'ghost_alert': 'drifter/ghost/alert',
    'ghost_tracker': 'drifter/ghost/tracker',     # AirTag / Tile / SmartTag follower
    'ghost_stingray': 'drifter/ghost/stingray',   # IMSI-catcher / cell anomaly
    'ghost_alpr': 'drifter/ghost/alpr',           # ALPR camera awareness
    'ghost_rf': 'drifter/ghost/rf',               # anomalous RF / surveillance band

    # ── Weather (weather_service.py — OpenWeatherMap One Call) ──
    'weather_current': 'drifter/weather/current',   # temp/humidity/wind/visibility/condition
    'weather_forecast': 'drifter/weather/forecast', # hourly outlook
    'weather_alerts': 'drifter/weather/alerts',     # gov + derived (rain_soon/fog/ice/wind)

    # ── Location enrichment (location_service.py — Google Elevation + Places) ──
    'location_elevation': 'drifter/location/elevation',  # elevation_m + road grade %
    'location_nearby': 'drifter/location/nearby',        # nearby POIs (fuel/mechanic/...)
    'location_query': 'drifter/location/query',          # request: {"type": "gas_station"}

    # ── In-car LCD + boot orchestration + Wi-Fi auto-connect ──
    # The 3.5" SPI LCD dashboard, the headless boot sequencer, and the
    # hotspot auto-connector all talk over these topics so the operator can
    # triage the node at the car without dragging an HDMI monitor out.
    'network_status': 'drifter/network/status',  # auto_connect → {ssid,ip,internet,ap_fallback,state}
    'lcd_command': 'drifter/lcd/command',        # remote control: {"action":"next"|"prev"|"refresh"|"screen","screen":"network"}
    'lcd_status': 'drifter/lcd/status',          # lcd_dashboard heartbeat → {screen,ts,fb}
    'boot_status': 'drifter/boot/status',        # boot_manager stage progress → {stage,detail,ok,ts}
}

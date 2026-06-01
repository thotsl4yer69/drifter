#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Home Assistant Bridge
Bidirectional MQTT bridge to Home Assistant. Publishes vehicle telemetry
as HA discovery devices/sensors under `homeassistant/drifter/<vin>/*`,
and translates HA service calls back to drifter command topics.
UNCAGED TECHNOLOGY — EST 1991
"""

import json
import logging
import signal
import time

import paho.mqtt.client as mqtt

from config import (
    DRIFTER_DIR,
    HOME_BRIDGE_DISCOVERY,
    HOME_BRIDGE_PREFIX,
    MQTT_HOST,
    MQTT_PORT,
    TOPICS,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [HOME] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger(__name__)

CONFIG_PATH = DRIFTER_DIR / "home.yaml"

# (drifter_topic_key, ha_object_id, friendly_name, unit, device_class)
PUBLISHED_SENSORS = [
    ('rpm', 'rpm', 'Engine RPM', 'rpm', None),
    ('coolant', 'coolant', 'Coolant Temp', '°C', 'temperature'),
    ('speed', 'speed', 'Speed', 'km/h', 'speed'),
    ('voltage', 'voltage', 'Battery Voltage', 'V', 'voltage'),
    ('throttle', 'throttle', 'Throttle', '%', None),
    ('load', 'load', 'Engine Load', '%', None),
    ('alert_level', 'alert_level', 'Alert Level', None, None),
]


def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        import yaml
        return yaml.safe_load(CONFIG_PATH.read_text()) or {}
    except Exception as e:
        log.warning(f"home.yaml load failed: {e}")
        return {}


def _publish_discovery(client: mqtt.Client, ha_prefix: str, vin: str) -> None:
    device = {
        'identifiers': [f"drifter_{vin}"],
        'name': f"Drifter {vin}",
        'manufacturer': 'MZ1312 Uncaged Technology',
        'model': 'DRIFTER v2',
    }
    for key, obj_id, friendly, unit, dev_class in PUBLISHED_SENSORS:
        topic = f"{ha_prefix}/sensor/drifter_{vin}_{obj_id}/config"
        state_topic = f"{HOME_BRIDGE_PREFIX}/{vin}/{obj_id}"
        cfg = {
            'name': friendly,
            'unique_id': f"drifter_{vin}_{obj_id}",
            'state_topic': state_topic,
            'value_template': '{{ value_json.value }}',
            'device': device,
        }
        if unit:
            cfg['unit_of_measurement'] = unit
        if dev_class:
            cfg['device_class'] = dev_class
        client.publish(topic, json.dumps(cfg), retain=True)
    log.info(f"HA discovery published for vin={vin} ({len(PUBLISHED_SENSORS)} sensors)")


def _publish_command_buttons(client: mqtt.Client, ha_prefix: str, vin: str) -> None:
    """Expose drifter commands as HA buttons that round-trip through home_command."""
    device = {
        'identifiers': [f"drifter_{vin}"],
        'name': f"Drifter {vin}",
    }
    buttons = [
        ('start_recording', 'Start Recording'),
        ('stop_recording', 'Stop Recording'),
        ('start_sentry', 'Start Sentry'),
        ('stop_sentry', 'Stop Sentry'),
    ]
    cmd_topic = f"{HOME_BRIDGE_PREFIX}/{vin}/command"
    for action, friendly in buttons:
        cfg_topic = f"{ha_prefix}/button/drifter_{vin}_{action}/config"
        cfg = {
            'name': friendly,
            'unique_id': f"drifter_{vin}_{action}",
            'command_topic': cmd_topic,
            'payload_press': json.dumps({'action': action}),
            'device': device,
        }
        client.publish(cfg_topic, json.dumps(cfg), retain=True)


def main() -> None:
    log.info("DRIFTER Home Assistant Bridge starting...")
    cfg = _load_config()
    ha_prefix = cfg.get('discovery_prefix', 'homeassistant')
    vin = cfg.get('vin', 'default')

    running = [True]

    def _handle_signal(sig, frame):
        running[0] = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    client = mqtt.Client(client_id="drifter-home-bridge")

    sensor_topic_map = {TOPICS[key]: ha_id for key, ha_id, *_ in PUBLISHED_SENSORS if key in TOPICS}

    def on_message(_c, _u, msg) -> None:
        topic = msg.topic
        # forward drifter → HA
        if topic in sensor_topic_map:
            ha_id = sensor_topic_map[topic]
            try:
                data = json.loads(msg.payload)
            except (json.JSONDecodeError, UnicodeDecodeError):
                data = {'value': msg.payload.decode('utf-8', errors='replace')}
            client.publish(
                f"{HOME_BRIDGE_PREFIX}/{vin}/{ha_id}",
                json.dumps(data),
                retain=False,
            )
            return
        # HA → drifter (button presses on home_command)
        if topic == f"{HOME_BRIDGE_PREFIX}/{vin}/command":
            try:
                data = json.loads(msg.payload)
            except (json.JSONDecodeError, UnicodeDecodeError):
                return
            action = data.get('action', '')
            log.info(f"HA command: {action}")
            client.publish(TOPICS['home_command'], json.dumps({'action': action, 'ts': time.time()}))
            # translate to native drifter commands
            if action == 'start_recording':
                client.publish(TOPICS['recorder_command'], json.dumps({'action': 'start'}))
            elif action == 'stop_recording':
                client.publish(TOPICS['recorder_command'], json.dumps({'action': 'stop'}))

    client.on_message = on_message

    connected = False
    while not connected and running[0]:
        try:
            client.connect(MQTT_HOST, MQTT_PORT, 60)
            connected = True
        except Exception as e:
            log.warning(f"Waiting for MQTT broker... ({e})")
            time.sleep(3)

    if not running[0]:
        return

    subs = [(t, 0) for t in sensor_topic_map]
    subs.append((f"{HOME_BRIDGE_PREFIX}/{vin}/command", 1))
    client.subscribe(subs)
    client.loop_start()

    if HOME_BRIDGE_DISCOVERY:
        _publish_discovery(client, ha_prefix, vin)
        _publish_command_buttons(client, ha_prefix, vin)

    client.publish(TOPICS['home_status'], json.dumps({'status': 'up', 'vin': vin, 'ts': time.time()}), retain=True)
    log.info(f"Home Bridge LIVE — vin={vin} ha_prefix={ha_prefix}")

    while running[0]:
        time.sleep(1)

    client.publish(TOPICS['home_status'], json.dumps({'status': 'down', 'ts': time.time()}), retain=True)
    client.loop_stop()
    client.disconnect()
    log.info("Home Bridge stopped")


if __name__ == '__main__':
    main()

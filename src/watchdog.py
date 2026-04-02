#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Watchdog Service
Monitors all DRIFTER services, CAN bus health, and MQTT liveness.
Publishes system health and restarts failed services.
UNCAGED TECHNOLOGY — EST 1991
"""

import json
import time
import signal
import subprocess
import logging
import psutil
import paho.mqtt.client as mqtt

from config import (
    MQTT_HOST, MQTT_PORT, SERVICES, TOPICS,
    WATCHDOG_INTERVAL, WATCHDOG_MQTT_TIMEOUT, DRIFTER_DIR
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [WATCHDOG] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

# ── State ──
last_mqtt_data = {}        # topic → timestamp of last message
service_restarts = {}      # service → restart count
running = True


def get_service_status(name):
    """Return systemd service state."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", name],
            capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip()
    except Exception:
        return "unknown"


def restart_service(name):
    """Attempt to restart a failed service."""
    count = service_restarts.get(name, 0)
    if count >= 5:
        log.error(f"Service {name} has been restarted {count} times — giving up")
        return False

    try:
        subprocess.run(
            ["systemctl", "restart", name],
            capture_output=True, timeout=30
        )
        service_restarts[name] = count + 1
        log.warning(f"Restarted {name} (attempt {count + 1})")
        return True
    except Exception as e:
        log.error(f"Failed to restart {name}: {e}")
        return False


def get_system_metrics():
    """Collect system-level metrics."""
    try:
        cpu_temp = None
        temp_file = "/sys/class/thermal/thermal_zone0/temp"
        try:
            with open(temp_file) as f:
                cpu_temp = int(f.read().strip()) / 1000.0
        except (IOError, ValueError):
            pass

        disk = psutil.disk_usage(str(DRIFTER_DIR))
        return {
            'cpu_percent': psutil.cpu_percent(interval=1),
            'memory_percent': psutil.virtual_memory().percent,
            'cpu_temp': cpu_temp,
            'disk_used_gb': round(disk.used / (1024**3), 1),
            'disk_free_gb': round(disk.free / (1024**3), 1),
            'disk_percent': disk.percent,
            'uptime_seconds': time.time() - psutil.boot_time(),
        }
    except Exception as e:
        log.warning(f"Failed to collect metrics: {e}")
        return {}


def on_message(client, userdata, msg):
    """Track last message time per topic."""
    last_mqtt_data[msg.topic] = time.time()


def check_health(mqtt_client):
    """Full health check — services, MQTT liveness, system metrics."""
    now = time.time()
    health = {
        'ts': now,
        'services': {},
        'mqtt_stale': [],
        'system': get_system_metrics(),
        'overall': 'healthy',
    }

    issues = []

    # Check each service
    for svc in SERVICES:
        status = get_service_status(svc)
        health['services'][svc] = status

        if status == 'failed':
            issues.append(f"{svc} is FAILED")
            restart_service(svc)
        elif status not in ('active', 'activating'):
            # Could be 'inactive' if not enabled — don't restart those
            if status != 'inactive':
                issues.append(f"{svc} is {status}")

    # Check MQTT data freshness (only for critical topics)
    critical_topics = ['drifter/engine/rpm', 'drifter/snapshot']
    for topic in critical_topics:
        last_time = last_mqtt_data.get(topic)
        if last_time and now - last_time > WATCHDOG_MQTT_TIMEOUT:
            health['mqtt_stale'].append(topic)
            issues.append(f"Stale data on {topic} ({now - last_time:.0f}s)")

    # System health checks
    sys_metrics = health['system']
    if sys_metrics.get('cpu_temp') and sys_metrics['cpu_temp'] > 80:
        issues.append(f"CPU temp: {sys_metrics['cpu_temp']:.0f}°C")
    if sys_metrics.get('disk_percent', 0) > 90:
        issues.append(f"Disk {sys_metrics['disk_percent']:.0f}% full")
    if sys_metrics.get('memory_percent', 0) > 90:
        issues.append(f"Memory {sys_metrics['memory_percent']:.0f}% used")

    if issues:
        health['overall'] = 'degraded'
        health['issues'] = issues
        log.warning(f"Health issues: {'; '.join(issues)}")
    else:
        log.debug("All systems healthy")

    # Publish health report
    mqtt_client.publish(TOPICS['watchdog'], json.dumps(health), retain=True)

    return health


def main():
    global running

    log.info("DRIFTER Watchdog starting...")

    def _handle_signal(sig, frame):
        global running
        running = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    client = mqtt.Client(client_id="drifter-watchdog")
    client.on_message = on_message

    connected = False
    while not connected and running:
        try:
            client.connect(MQTT_HOST, MQTT_PORT, 60)
            connected = True
        except Exception as e:
            log.warning(f"Waiting for MQTT broker... ({e})")
            time.sleep(5)

    # Subscribe to all drifter topics to monitor liveness
    client.subscribe("drifter/#")
    client.loop_start()

    log.info(f"Watchdog is LIVE — checking {len(SERVICES)} services every {WATCHDOG_INTERVAL}s")

    while running:
        check_health(client)
        time.sleep(WATCHDOG_INTERVAL)

    client.loop_stop()
    client.disconnect()
    log.info("Watchdog stopped")


if __name__ == '__main__':
    main()

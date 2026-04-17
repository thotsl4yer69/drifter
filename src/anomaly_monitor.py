#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Real-Time Anomaly Monitor
Statistical z-score anomaly detection on OBD-II sensor streams.
Writes anomaly events to SQLite during drive sessions.
UNCAGED TECHNOLOGY — EST 1991
"""

import json
import math
import time
import signal
import logging
from collections import deque
from typing import Optional, List

import paho.mqtt.client as mqtt

import db
from config import (
    MQTT_HOST, MQTT_PORT, TOPICS,
    ANOMALY_ROLLING_WINDOW, ANOMALY_WARN_Z, ANOMALY_HIGH_Z, ANOMALY_CRITICAL_Z,
    ANOMALY_IDLE_RPM_STDDEV, WARMUP_COOLANT_THRESHOLD,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s [ANOMALY] %(message)s',
                    datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

# Sensors to monitor with z-score detection
MONITORED_SENSORS = {
    'stft_b1': TOPICS['stft1'],
    'stft_b2': TOPICS['stft2'],
    'ltft_b1': TOPICS['ltft1'],
    'ltft_b2': TOPICS['ltft2'],
    'rpm':     TOPICS['rpm'],
    'coolant': TOPICS['coolant'],
    'iat':     TOPICS['iat'],
    'maf':     TOPICS['maf'],
    'throttle': TOPICS['throttle'],
    'voltage': TOPICS['voltage'],
}


class SensorWindow:
    """Rolling window with z-score anomaly detection."""

    MIN_READINGS = 5  # need at least this many before detecting

    def __init__(self, window_size: int = ANOMALY_ROLLING_WINDOW):
        self.window = deque(maxlen=window_size)

    def add(self, value: float):
        self.window.append(value)

    # Absolute floor to prevent divide-by-near-zero when sensors are pinned.
    # 0.5 covers typical noise floors for %, °C, RPM (×10), voltage (×10), etc.
    _STD_FLOOR = 0.5

    def check(self, value: float) -> Optional[dict]:
        """Return anomaly dict if value is anomalous, else None."""
        if len(self.window) < self.MIN_READINGS:
            return None
        # Reject non-finite incoming values — they would pollute z-score math.
        if not math.isfinite(value):
            return None
        mean = sum(self.window) / len(self.window)
        variance = sum((x - mean) ** 2 for x in self.window) / len(self.window)
        # Floor stddev to avoid infinite z-scores on constant sensors and
        # to prevent the old `max(0.01, |mean|*0.01)` trap where mean=0
        # produced std=0.01 → any tiny jitter looked like a critical anomaly.
        std = max(math.sqrt(variance), self._STD_FLOOR)
        z = abs(value - mean) / std
        if z < ANOMALY_WARN_Z:
            return None
        severity = 'warning'
        if z >= ANOMALY_CRITICAL_Z:
            severity = 'critical'
        elif z >= ANOMALY_HIGH_Z:
            severity = 'high'
        return {'z_score': round(z, 2), 'severity': severity, 'mean': round(mean, 2)}


class AnomalyMonitor:
    """Main anomaly monitor — subscribes to MQTT and logs events."""

    def __init__(self):
        self.windows = {name: SensorWindow() for name in MONITORED_SENSORS}
        self.rpm_idle_window = deque(maxlen=10)  # for instability check
        self.current_session_id: Optional[str] = None
        self.current_coolant: float = 0.0
        self.current_speed: float = 0.0
        self.current_snapshot: dict = {}
        self.running = True

        db.init_db()
        self.client = mqtt.Client(client_id="drifter-anomaly-monitor")
        self.client.on_message = self._on_message

    def _on_message(self, client, userdata, msg):
        try:
            data = json.loads(msg.payload)
            if not isinstance(data, dict):
                return
            topic = msg.topic
            value = data.get('value')
            if value is None:
                return
            try:
                value = float(value)
            except (TypeError, ValueError):
                return
            # Reject non-finite values (NaN / ±Inf) — they corrupt stddev math.
            if not math.isfinite(value):
                return

            # Track state for context snapshots
            if topic == TOPICS['coolant']:
                self.current_coolant = float(value)
            elif topic == TOPICS['speed']:
                self.current_speed = float(value)

            # Track session
            if topic == TOPICS['drive_session']:
                event = data.get('event')
                if event == 'start':
                    self.current_session_id = data.get('session_id')
                    log.info(f"Session started: {self.current_session_id}")
                elif event == 'end':
                    self.current_session_id = None

            # Update snapshot for context
            sensor_name = self._topic_to_sensor(topic)
            if sensor_name:
                self.current_snapshot[sensor_name] = round(float(value), 2)

            # Check for anomalies
            if sensor_name and sensor_name != 'rpm':
                events = self._check_sensor(sensor_name, float(value))
                for e in events:
                    db.insert_anomaly_event(e)
                    if e['severity'] == 'critical':
                        # Match the schema used by alert_engine so downstream
                        # consumers (realdash_bridge, voice_alerts, dashboard)
                        # do not KeyError on missing fields.
                        from config import LEVEL_AMBER, LEVEL_NAMES
                        self.client.publish(
                            TOPICS.get('alert_message', 'drifter/alert/message'),
                            json.dumps({
                                'level': LEVEL_AMBER,
                                'name': LEVEL_NAMES[LEVEL_AMBER],
                                'message': f"Critical anomaly: {sensor_name} z={e['z_score']:.1f}",
                                'ts': time.time(),
                            }))

            # RPM: update window + check instability
            if sensor_name == 'rpm':
                self.windows['rpm'].add(float(value))
                if self.current_speed == 0 and self.current_session_id:
                    self.rpm_idle_window.append(float(value))
                    for e in self._check_rpm_instability():
                        db.insert_anomaly_event(e)

        except Exception as e:
            log.warning(f"Message error: {e}")

    def _topic_to_sensor(self, topic: str) -> Optional[str]:
        for name, t in MONITORED_SENSORS.items():
            if topic == t:
                return name
        return None

    def _check_sensor(self, sensor_name: str, value: float) -> List[dict]:
        """Check a single sensor value. Returns list of anomaly events (0 or 1)."""
        if not self.current_session_id:
            return []
        if self.current_coolant < WARMUP_COOLANT_THRESHOLD:
            # Do NOT add cold-start readings to the warm-engine baseline —
            # fuel trims, IAT, MAF etc. are radically different below warmup
            # temperature and would poison the z-score baseline once warm.
            return []  # suppressed during cold start
        result = self.windows[sensor_name].check(value)
        self.windows[sensor_name].add(value)
        if result is None:
            return []
        context = dict(self.current_snapshot)
        context[sensor_name] = round(value, 2)
        return [{
            'session_id': self.current_session_id,
            'ts': time.time(),
            'sensor': sensor_name,
            'value': round(value, 2),
            'z_score': result['z_score'],
            'severity': result['severity'],
            'context_json': json.dumps(context),
        }]

    def _check_rpm_instability(self) -> List[dict]:
        """Detect RPM instability at idle (stddev > threshold)."""
        if len(self.rpm_idle_window) < 5 or not self.current_session_id:
            return []
        vals = list(self.rpm_idle_window)
        mean = sum(vals) / len(vals)
        std = math.sqrt(sum((v - mean) ** 2 for v in vals) / len(vals))
        if std < ANOMALY_IDLE_RPM_STDDEV:
            return []
        return [{
            'session_id': self.current_session_id,
            'ts': time.time(),
            'sensor': 'rpm_instability',
            'value': round(std, 1),
            'z_score': round(std / ANOMALY_IDLE_RPM_STDDEV, 2),
            'severity': 'high' if std > ANOMALY_IDLE_RPM_STDDEV * 1.5 else 'warning',
            'context_json': json.dumps({**self.current_snapshot, 'rpm_stddev': round(std, 1)}),
        }]

    def start(self):
        log.info("Anomaly Monitor starting...")
        connected = False
        while not connected and self.running:
            try:
                self.client.connect(MQTT_HOST, MQTT_PORT, 60)
                connected = True
            except Exception as e:
                log.warning(f"MQTT connect failed: {e}")
                time.sleep(3)
        # Subscribe to only the sensors we actually monitor + session lifecycle
        for topic in MONITORED_SENSORS.values():
            self.client.subscribe(topic)
        self.client.subscribe(TOPICS.get('session', 'drifter/session'))
        self.client.subscribe(TOPICS.get('rpm', 'drifter/engine/rpm'))  # for idle detection
        self.client.loop_start()
        log.info("Anomaly Monitor LIVE")
        while self.running:
            time.sleep(1)
        self.client.loop_stop()
        self.client.disconnect()


def main():
    monitor = AnomalyMonitor()
    def _stop(sig, frame):
        monitor.running = False
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    monitor.start()


if __name__ == '__main__':
    main()

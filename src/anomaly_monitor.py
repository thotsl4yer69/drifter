#!/usr/bin/env python3
"""DRIFTER real-time statistical anomaly monitor."""
from __future__ import annotations

import json
import logging
import math
import signal
import time
from collections import deque

import db
from config import (
    ANOMALY_CRITICAL_Z, ANOMALY_HIGH_Z, ANOMALY_IDLE_RPM_STDDEV,
    ANOMALY_ROLLING_WINDOW, ANOMALY_WARN_Z, LEVEL_AMBER, LEVEL_NAMES,
    MQTT_HOST, MQTT_PORT, TOPICS, WARMUP_COOLANT_THRESHOLD, make_mqtt_client,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s [ANOMALY] %(message)s',
                    datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

MONITORED_SENSORS = {
    'stft_b1': TOPICS['stft1'], 'stft_b2': TOPICS['stft2'],
    'ltft_b1': TOPICS['ltft1'], 'ltft_b2': TOPICS['ltft2'],
    'rpm': TOPICS['rpm'], 'coolant': TOPICS['coolant'], 'iat': TOPICS['iat'],
    'maf': TOPICS['maf'], 'throttle': TOPICS['throttle'], 'voltage': TOPICS['voltage'],
}
ALERT_COOLDOWN_SEC = 60.0
ALERT_ESCALATION_DELTA = 0.5


class SensorWindow:
    MIN_READINGS = 5
    _STD_FLOOR = 0.5

    def __init__(self, window_size: int = ANOMALY_ROLLING_WINDOW):
        self.window = deque(maxlen=window_size)

    def add(self, value: float):
        self.window.append(value)

    def check(self, value: float) -> dict | None:
        if len(self.window) < self.MIN_READINGS or not math.isfinite(value):
            return None
        mean = sum(self.window) / len(self.window)
        variance = sum((x - mean) ** 2 for x in self.window) / len(self.window)
        std = max(math.sqrt(variance), self._STD_FLOOR)
        z = abs(value - mean) / std
        if z < ANOMALY_WARN_Z:
            return None
        severity = 'critical' if z >= ANOMALY_CRITICAL_Z else (
            'high' if z >= ANOMALY_HIGH_Z else 'warning'
        )
        return {'z_score': round(z, 2), 'severity': severity, 'mean': round(mean, 2)}


class AnomalyMonitor:
    def __init__(self):
        self.windows = {name: SensorWindow() for name in MONITORED_SENSORS}
        self.rpm_idle_window = deque(maxlen=10)
        self.current_session_id: str | None = None
        self.current_coolant = 0.0
        self.current_speed = 0.0
        self.current_snapshot: dict = {}
        self.running = True
        self._alert_state: dict = {}
        db.init_db()
        self.client = make_mqtt_client("drifter-anomaly-monitor")
        self.client.on_message = self._on_message

    def _reset_session_state(self) -> None:
        """Drop rolling baselines and context that must not cross drive sessions."""
        for sensor_window in self.windows.values():
            sensor_window.window.clear()
        self.rpm_idle_window.clear()
        self.current_coolant = 0.0
        self.current_speed = 0.0
        self.current_snapshot.clear()
        self._alert_state.clear()

    def _should_publish_alert(self, sensor_name: str, z_score: float,
                              now: float | None = None):
        now = time.time() if now is None else now
        state = self._alert_state.get(sensor_name)
        if state is None:
            self._alert_state[sensor_name] = {
                'last_alert_ts': now, 'last_z': z_score, 'suppression_count': 0,
            }
            return True, None
        elapsed = now - state['last_alert_ts']
        if z_score - state['last_z'] >= ALERT_ESCALATION_DELTA:
            state.update(last_alert_ts=now, last_z=z_score, suppression_count=0)
            return True, None
        if elapsed < ALERT_COOLDOWN_SEC:
            state['suppression_count'] += 1
            return False, None
        summary = {
            'sensor': sensor_name, 'z_score': round(z_score, 2),
            'suppression_count': state['suppression_count'],
            'cooldown_sec': ALERT_COOLDOWN_SEC, 'still_anomalous': True, 'ts': now,
        }
        state.update(last_alert_ts=now, last_z=z_score, suppression_count=0)
        return False, summary

    def _publish_anomaly(self, event: dict) -> None:
        """Persist and publish every anomaly so the black box can freeze it live."""
        db.insert_anomaly_event(event)
        payload = dict(event)
        context = payload.get('context_json')
        if isinstance(context, str):
            try:
                payload['context'] = json.loads(context)
            except json.JSONDecodeError:
                pass
        self.client.publish(
            TOPICS.get('anomaly_event', 'drifter/anomaly/event'),
            json.dumps(payload),
        )

    def _publish_critical_alert(self, sensor_name: str, event: dict) -> None:
        publish, summary = self._should_publish_alert(sensor_name, event['z_score'])
        if publish:
            self.client.publish(
                TOPICS.get('alert_message', 'drifter/alert/message'),
                json.dumps({
                    'level': LEVEL_AMBER, 'name': LEVEL_NAMES[LEVEL_AMBER],
                    'message': f"Critical anomaly: {sensor_name} z={event['z_score']:.1f}",
                    'ts': time.time(),
                }),
            )
        elif summary is not None:
            self.client.publish(
                TOPICS.get('alert_message', 'drifter/alert/message'),
                json.dumps({
                    'level': LEVEL_AMBER, 'name': LEVEL_NAMES[LEVEL_AMBER],
                    'message': (
                        f"Still anomalous: {sensor_name} z={summary['z_score']:.1f} "
                        f"(suppressed {summary['suppression_count']}x in "
                        f"{int(ALERT_COOLDOWN_SEC)}s)"
                    ),
                    'suppression_count': summary['suppression_count'],
                    'still_anomalous': True, 'ts': summary['ts'],
                }),
            )

    def _on_message(self, client, userdata, msg):
        try:
            data = json.loads(msg.payload)
            if not isinstance(data, dict):
                return
            topic = msg.topic

            # Session messages have no `value`; process lifecycle BEFORE the
            # numeric-sensor guard. The old order silently left session_id None
            # and therefore suppressed the entire anomaly detector.
            if topic == TOPICS.get('drive_session', 'drifter/session'):
                event = data.get('event')
                if event == 'start':
                    self._reset_session_state()
                    self.current_session_id = data.get('session_id')
                    log.info("Session started: %s", self.current_session_id)
                elif event == 'end':
                    self.current_session_id = None
                    self._reset_session_state()
                return

            value = data.get('value')
            if value is None:
                return
            try:
                value = float(value)
            except (TypeError, ValueError):
                return
            if not math.isfinite(value):
                return

            if topic == TOPICS['coolant']:
                self.current_coolant = value
            elif topic == TOPICS['speed']:
                self.current_speed = value

            sensor_name = self._topic_to_sensor(topic)
            if sensor_name:
                self.current_snapshot[sensor_name] = round(value, 2)

            if sensor_name and sensor_name != 'rpm':
                for event in self._check_sensor(sensor_name, value):
                    self._publish_anomaly(event)
                    if event['severity'] == 'critical':
                        self._publish_critical_alert(sensor_name, event)

            if sensor_name == 'rpm':
                self.windows['rpm'].add(value)
                if self.current_speed <= 2 and self.current_session_id:
                    self.rpm_idle_window.append(value)
                    for event in self._check_rpm_instability():
                        self._publish_anomaly(event)
        except Exception as exc:
            log.warning("Message error: %s", exc)

    def _topic_to_sensor(self, topic: str) -> str | None:
        for name, candidate in MONITORED_SENSORS.items():
            if topic == candidate:
                return name
        return None

    def _check_sensor(self, sensor_name: str, value: float) -> list[dict]:
        if not self.current_session_id:
            return []
        if self.current_coolant < WARMUP_COOLANT_THRESHOLD:
            return []
        result = self.windows[sensor_name].check(value)
        self.windows[sensor_name].add(value)
        if result is None:
            return []
        context = dict(self.current_snapshot)
        context[sensor_name] = round(value, 2)
        return [{
            'session_id': self.current_session_id, 'ts': time.time(),
            'sensor': sensor_name, 'value': round(value, 2),
            'z_score': result['z_score'], 'severity': result['severity'],
            'context_json': json.dumps(context),
        }]

    def _check_rpm_instability(self) -> list[dict]:
        if len(self.rpm_idle_window) < 5 or not self.current_session_id:
            return []
        vals = list(self.rpm_idle_window)
        mean = sum(vals) / len(vals)
        std = math.sqrt(sum((v - mean) ** 2 for v in vals) / len(vals))
        if std < ANOMALY_IDLE_RPM_STDDEV:
            return []
        return [{
            'session_id': self.current_session_id, 'ts': time.time(),
            'sensor': 'rpm_instability', 'value': round(std, 1),
            'z_score': round(std / ANOMALY_IDLE_RPM_STDDEV, 2),
            'severity': 'high' if std > ANOMALY_IDLE_RPM_STDDEV * 1.5 else 'warning',
            'context_json': json.dumps({
                **self.current_snapshot, 'rpm_stddev': round(std, 1),
            }),
        }]

    def start(self):
        log.info("Anomaly Monitor starting...")
        connected = False
        while not connected and self.running:
            try:
                self.client.connect(MQTT_HOST, MQTT_PORT, 60)
                connected = True
            except Exception as exc:
                log.warning("MQTT connect failed: %s", exc)
                time.sleep(3)
        if not self.running:
            return
        for topic in set(MONITORED_SENSORS.values()):
            self.client.subscribe(topic)
        self.client.subscribe(TOPICS.get('drive_session', 'drifter/session'))
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

#!/usr/bin/env python3
"""MZ1312 DRIFTER — Vehicle-test evidence capture.
Observe the running telemetry pipeline without opening or writing to the ECU.
UNCAGED TECHNOLOGY — EST 1991
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from itertools import pairwise
from pathlib import Path

from config import LOG_DIR, MQTT_HOST, MQTT_PORT, TOPICS, make_mqtt_client, subscribe_on_connect

REQUIRED = ('rpm', 'speed', 'coolant')
LIMITS = {'rpm': (0, 16384), 'speed': (0, 255), 'coolant': (-40, 215), 'voltage': (0, 65.535)}


class Evidence:
    def __init__(self, started: float):
        self.started = started
        self.samples = {key: [] for key in LIMITS}
        self.sources = set()
        self.dtc = None
        self.status = None
        self.rejected = 0

    def receive(self, topic: str, data: dict, *, retained=False, now=None):
        now = time.time() if now is None else now
        if retained or not isinstance(data, dict):
            return
        if topic == TOPICS['obd_status']:
            self.status = data
            return
        if topic == TOPICS['dtc']:
            self.dtc = data
            return
        key = next((k for k in self.samples if TOPICS[k] == topic), None)
        if key is None:
            return
        value, ts, source = data.get('value'), data.get('ts'), data.get('source')
        valid = (isinstance(value, (int, float)) and not isinstance(value, bool)
                 and math.isfinite(value) and isinstance(ts, (int, float))
                 and math.isfinite(ts) and self.started <= ts <= now + 1
                 and now - ts <= 15 and source in ('obd_bridge', 'can_bridge'))
        if not valid or not LIMITS[key][0] <= value <= LIMITS[key][1]:
            self.rejected += 1
            return
        self.sources.add(source)
        if self.samples[key] and ts <= self.samples[key][-1][0]:
            return
        self.samples[key].append((ts, value))

    def report(self, finished: float, *, engine_running=False):
        metrics = {}
        for key, values in self.samples.items():
            gaps = [b[0] - a[0] for a, b in pairwise(values)]
            metrics[key] = {
                'samples': len(values), 'last': values[-1][1] if values else None,
                'last_age_s': round(finished - values[-1][0], 2) if values else None,
                'median_interval_s': round(statistics.median(gaps), 3) if gaps else None,
                'max_gap_s': round(max(gaps), 3) if gaps else None,
                'first_delay_s': round(values[0][0] - self.started, 2) if values else None,
                'pass': bool(len(values) >= 2 and gaps and max(gaps) <= 15
                             and values[0][0] - self.started <= 15
                             and finished - values[-1][0] <= 15),
            }
        failures = [f'{key}: missing, stale or interrupted readings' for key in REQUIRED if not metrics[key]['pass']]
        if len(self.sources) != 1:
            failures.append('Expected exactly one real telemetry source')
        if engine_running and (not self.samples['rpm'] or self.samples['rpm'][-1][1] < 400):
            failures.append('Engine-running check requires measured RPM >= 400')
        if self.rejected:
            failures.append(f'{self.rejected} invalid, stale or synthetic metric messages')
        return {'passed': not failures, 'failures': failures,
                'started': self.started, 'finished': finished,
                'duration_s': round(finished - self.started, 2),
                'engine_running_required': engine_running,
                'sources': sorted(self.sources), 'metrics': metrics,
                'adapter': self.status, 'dtc': self.dtc,
                'notes': ['Voltage and DTC availability depend on ECU support.',
                          'This validates telemetry continuity; it is not a mechanical all-clear.']}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=int, default=60)
    parser.add_argument('--engine-running', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    if not 10 <= args.seconds <= 3600:
        parser.error('--seconds must be between 10 and 3600')
    evidence = Evidence(time.time())
    client = make_mqtt_client(f'drifter-vehicle-check-{int(time.time())}')

    def receive(_c, _u, msg):
        try:
            evidence.receive(msg.topic, json.loads(msg.payload), retained=msg.retain)
        except (ValueError, TypeError):
            evidence.rejected += 1

    client.on_message = receive
    subscribe_on_connect(client, [*(TOPICS[k] for k in LIMITS), TOPICS['obd_status'], TOPICS['dtc']])
    broker_error = None
    try:
        client.connect(MQTT_HOST, MQTT_PORT, 30)
        deadline = time.monotonic() + args.seconds
        # Keep receive + report in one thread; no mutable sample-list races.
        while time.monotonic() < deadline:
            rc = client.loop(timeout=min(1, max(0, deadline - time.monotonic())))
            if rc != 0:
                broker_error = f'MQTT connection failed (rc={rc})'
                break
    except (OSError, KeyboardInterrupt) as exc:
        broker_error = str(exc) or 'Capture interrupted'
    finally:
        client.disconnect()
    report = evidence.report(time.time(), engine_running=args.engine_running)
    if broker_error:
        report['passed'] = False
        report['failures'].append(broker_error)
    output = args.output or LOG_DIR / ('vehicle-check-' + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '.json')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    print(('PASS' if report['passed'] else 'FAIL') + f' — evidence: {output}')
    for failure in report['failures']:
        print('  ' + failure)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

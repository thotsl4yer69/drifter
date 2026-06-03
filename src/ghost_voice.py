#!/usr/bin/env python3
"""MZ1312 DRIFTER — Ghost voice bridge (counter-surveillance speech).

ghost_protocol.py and the Shade Core hardware bridge both publish
`drifter/ghost/alert`, but nothing in Drifter turns those into speech. This
service subscribes to `drifter/ghost/alert` and republishes a
voice_alerts-compatible payload on `drifter/alert/message` so drifter-alerts
announces counter-surveillance events in-cabin.

Additive glue only: it does NOT modify ghost_protocol.py and never drives a
radio. Pure receive → re-publish. Centralising speech here means both the
software correlator and the Shade Core hardware feed are spoken once (no
double-speak).

UNCAGED TECHNOLOGY — EST 1991
"""
from __future__ import annotations

import json
import logging
import signal
import time

from config import MQTT_HOST, MQTT_PORT, TOPICS, make_mqtt_client

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [GHOST-VOICE] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger(__name__)

# Map every severity dialect we might see (ghost_protocol uses amber/red;
# the Shade Core bridge uses warning/critical/info) → voice_alerts numeric
# level. voice_alerts speaks: >=3 "Critical alert.", ==2 "Warning.", else plain.
LEVEL = {
    'red': 3, 'critical': 3, 'high': 3,
    'amber': 2, 'warning': 2, 'medium': 2,
    'green': 1, 'info': 1, 'low': 1,
}

# Fallback phrasing when an alert carries no ready-made message (the Shade Core
# bridge sends type-only summaries). Keyed by `type` (bridge) or `kind` head.
PHRASE = {
    'ble': 'a tracker is following the vehicle',
    'tracker': 'a tracker is following the vehicle',
    'tracker_follower': 'a tracker is following the vehicle',
    'cell': 'a possible cell-site simulator is nearby',
    'stingray': 'a possible cell-site simulator is nearby',
    'imsi_catcher_suspect': 'a possible cell-site simulator is nearby',
    'alpr': 'a license plate reader is active nearby',
    'alpr_activity': 'a license plate reader is active nearby',
    'rf': 'anomalous surveillance R F detected',
}

running = [True]


def _phrase_for(data: dict) -> str:
    if data.get('message'):
        return str(data['message'])
    key = str(data.get('type') or data.get('kind') or '').lower()
    return PHRASE.get(key, 'a surveillance event was detected')


def on_message(client, userdata, msg) -> None:
    try:
        data = json.loads(msg.payload)
    except (json.JSONDecodeError, ValueError):
        return
    if not isinstance(data, dict):
        return
    sev = str(data.get('severity', 'warning')).lower()
    payload = {
        'level': LEVEL.get(sev, 2),
        'message': _phrase_for(data),
        'name': 'ghost',
        'ts': time.time(),
    }
    # voice_alerts.py reads {level, message}; web_dashboard_state mirrors this
    # into the Incidents ring. Publish the dict shape both expect.
    client.publish(TOPICS['alert_message'], json.dumps(payload))
    log.info("spoke ghost alert [L%d]: %s", payload['level'], payload['message'])


def main() -> None:
    def _stop(*_):
        running[0] = False
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    client = make_mqtt_client('drifter-ghost-voice')
    client.on_message = on_message
    while running[0]:
        try:
            client.connect(MQTT_HOST, MQTT_PORT, 60)
            break
        except Exception as e:  # noqa: BLE001 — broker may not be up yet
            log.warning("waiting for MQTT broker… (%s)", e)
            time.sleep(3)
    else:
        return

    client.subscribe(TOPICS['ghost_alert'])
    client.loop_start()
    log.info("ghost voice bridge up — %s → %s",
             TOPICS['ghost_alert'], TOPICS['alert_message'])
    while running[0]:
        time.sleep(0.5)
    client.loop_stop()
    log.info("ghost voice bridge down")


if __name__ == '__main__':
    main()

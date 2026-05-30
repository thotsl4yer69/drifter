#!/usr/bin/env python3
"""
MZ1312 DRIFTER — CAN AI Decoder
Listens to summarised CAN sniffer output and asks the LLM cascade what
each arbitration ID is most likely encoding (RPM, speed, coolant, etc).
Caches results so an ID is only queried once per session.
UNCAGED TECHNOLOGY — EST 1991
"""

import json
import logging
import signal
import threading
import time

import paho.mqtt.client as mqtt

from config import (
    CAN_AI_MIN_SAMPLES,
    MQTT_HOST,
    MQTT_PORT,
    TOPICS,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [CAN-AI] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger(__name__)

# Per-ID stats accumulated from sniffer summaries
_lock = threading.Lock()
_id_samples: dict[str, list[dict]] = {}
_id_known: dict[str, dict] = {}


def _query_llm(arb_id: str, samples: list[dict]) -> dict | None:
    try:
        from llm_client_v2 import query_json
    except ImportError:
        log.debug("llm_client_v2 not available")
        return None
    prompt = (
        "You are reverse-engineering an automotive CAN bus.\n"
        f"Arbitration ID: {arb_id}\n"
        f"Sample count: {len(samples)}\n"
        f"Average frequency (Hz): {samples[-1].get('hz', 0):.2f}\n"
        f"Recent payloads (hex):\n"
        + "\n".join(s['last_data'] for s in samples[-12:])
        + "\n\nReturn JSON with: signal_name (one of rpm, speed, coolant, throttle, "
          "voltage, brake, steering, gear, fuel, unknown), confidence (0-1), "
          "byte_layout (string like '0-1 BE *0.25'), reason (one sentence)."
    )
    try:
        return query_json(prompt, max_tokens=200)
    except Exception as e:
        log.debug(f"LLM query failed for {arb_id}: {e}")
        return None


def _on_summary(client: mqtt.Client, msg) -> None:
    try:
        data = json.loads(msg.payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return
    if not isinstance(data, dict):
        return
    with _lock:
        for entry in data.get('ids', []):
            arb_id = entry.get('id')
            if not arb_id or arb_id in _id_known:
                continue
            _id_samples.setdefault(arb_id, []).append(entry)
            if len(_id_samples[arb_id]) >= CAN_AI_MIN_SAMPLES // 50:
                # enough hz-summary ticks → ask the LLM
                samples = _id_samples.pop(arb_id)
                threading.Thread(
                    target=_classify_and_publish,
                    args=(client, arb_id, samples),
                    daemon=True,
                ).start()


def _on_request(client: mqtt.Client, msg) -> None:
    try:
        data = json.loads(msg.payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return
    arb_id = data.get('id')
    if not arb_id:
        return
    with _lock:
        known = _id_known.get(arb_id)
        samples = list(_id_samples.get(arb_id, []))
    if known:
        client.publish(TOPICS['can_decode_response'], json.dumps({'id': arb_id, **known}))
        return
    if samples:
        threading.Thread(target=_classify_and_publish, args=(client, arb_id, samples), daemon=True).start()


def _classify_and_publish(client: mqtt.Client, arb_id: str, samples: list[dict]) -> None:
    result = _query_llm(arb_id, samples)
    if not result:
        return
    with _lock:
        _id_known[arb_id] = result
    log.info(f"classified {arb_id} -> {result.get('signal_name')} ({result.get('confidence', 0):.2f})")
    client.publish(
        TOPICS['can_decode_response'],
        json.dumps({'id': arb_id, **result, 'ts': time.time()}),
        qos=1,
    )


def main() -> None:
    log.info("DRIFTER CAN AI Decoder starting...")

    running = [True]

    def _handle_signal(sig, frame):
        running[0] = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    client = mqtt.Client(client_id="drifter-can-decoder-ai")

    def on_message(_c, _u, msg) -> None:
        if msg.topic == TOPICS['can_sniff_summary']:
            _on_summary(client, msg)
        elif msg.topic == TOPICS['can_decode_request']:
            _on_request(client, msg)

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

    client.subscribe([
        (TOPICS['can_sniff_summary'], 0),
        (TOPICS['can_decode_request'], 1),
    ])
    client.loop_start()
    log.info("CAN AI Decoder LIVE")

    while running[0]:
        time.sleep(1)

    client.loop_stop()
    client.disconnect()
    log.info("CAN AI Decoder stopped")


if __name__ == '__main__':
    main()

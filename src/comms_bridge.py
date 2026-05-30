#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Comms Bridge
Outbound SMS via an AT-modem (e.g. ttyUSB2 on a Quectel/Huawei dongle),
plus a notification fan-out for ntfy/Telegram/Discord. Inbound SMS (when
the modem path is connected) is forwarded onto TOPICS['comms_inbound']
so other services can react.
UNCAGED TECHNOLOGY — EST 1991
"""

import json
import logging
import signal
import threading
import time
from pathlib import Path
from typing import Optional

import paho.mqtt.client as mqtt
import requests

from config import (
    MQTT_HOST, MQTT_PORT, TOPICS,
    DRIFTER_DIR, COMMS_MODEM_DEV, COMMS_NOTIFY_BACKENDS,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [COMMS] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

CONFIG_PATH = DRIFTER_DIR / "comms.yaml"


def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        import yaml
        return yaml.safe_load(CONFIG_PATH.read_text()) or {}
    except Exception as e:
        log.warning(f"comms.yaml load failed: {e}")
        return {}


def _open_modem(dev: str) -> Optional[object]:
    try:
        import serial
    except ImportError:
        log.warning("pyserial not installed — SMS disabled")
        return None
    try:
        ser = serial.Serial(dev, 115200, timeout=2)
        # Wake the modem
        ser.write(b'AT\r')
        time.sleep(0.2)
        ser.read(64)
        return ser
    except Exception as e:
        log.warning(f"Modem open failed ({dev}): {e}")
        return None


def _send_sms(ser, number: str, text: str) -> bool:
    if ser is None or not number:
        return False
    try:
        ser.write(b'AT+CMGF=1\r')
        time.sleep(0.3)
        ser.read(64)
        ser.write(f'AT+CMGS="{number}"\r'.encode())
        time.sleep(0.3)
        ser.read(64)
        ser.write(text.encode() + b'\x1a')
        time.sleep(2)
        resp = ser.read(256).decode('ascii', errors='replace')
        ok = '+CMGS' in resp or 'OK' in resp
        log.info(f"SMS {'sent' if ok else 'failed'}: {number}")
        return ok
    except Exception as e:
        log.warning(f"SMS send failed: {e}")
        return False


def _notify_ntfy(cfg: dict, title: str, message: str, priority: str = 'default') -> bool:
    topic_url = cfg.get('ntfy_url') or cfg.get('ntfy_topic')
    if not topic_url:
        return False
    if not topic_url.startswith('http'):
        topic_url = f"https://ntfy.sh/{topic_url}"
    try:
        resp = requests.post(
            topic_url,
            data=message.encode('utf-8'),
            headers={
                'Title': title[:200],
                'Priority': priority,
            },
            timeout=8,
        )
        return resp.status_code < 400
    except Exception as e:
        log.debug(f"ntfy failed: {e}")
        return False


def _notify_telegram(cfg: dict, title: str, message: str) -> bool:
    token = cfg.get('telegram_token')
    chat_id = cfg.get('telegram_chat_id')
    if not token or not chat_id:
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={'chat_id': chat_id, 'text': f"*{title}*\n{message}", 'parse_mode': 'Markdown'},
            timeout=8,
        )
        return resp.status_code < 400
    except Exception as e:
        log.debug(f"telegram failed: {e}")
        return False


def _notify_discord(cfg: dict, title: str, message: str) -> bool:
    webhook = cfg.get('discord_webhook')
    if not webhook:
        return False
    try:
        resp = requests.post(
            webhook,
            json={'content': f"**{title}**\n{message}"},
            timeout=8,
        )
        return resp.status_code < 400
    except Exception as e:
        log.debug(f"discord failed: {e}")
        return False


def _fan_out(cfg: dict, title: str, message: str, priority: str = 'default') -> dict:
    results = {}
    if 'ntfy' in COMMS_NOTIFY_BACKENDS:
        results['ntfy'] = _notify_ntfy(cfg, title, message, priority)
    if 'telegram' in COMMS_NOTIFY_BACKENDS:
        results['telegram'] = _notify_telegram(cfg, title, message)
    if 'discord' in COMMS_NOTIFY_BACKENDS:
        results['discord'] = _notify_discord(cfg, title, message)
    return results


def _read_inbound_loop(ser, client: mqtt.Client, running_ref: list) -> None:
    """Best-effort inbound SMS reader. Modems vary wildly — this is a heuristic."""
    if ser is None:
        return
    try:
        ser.write(b'AT+CMGF=1\r')
        time.sleep(0.3)
        ser.read(64)
        ser.write(b'AT+CNMI=2,2,0,0,0\r')  # forward incoming SMS to TE
        time.sleep(0.3)
        ser.read(64)
    except Exception as e:
        log.warning(f"Inbound config failed: {e}")
        return
    buf = b''
    while running_ref[0]:
        try:
            data = ser.read(256)
            if not data:
                time.sleep(0.3)
                continue
            buf += data
            if b'+CMT:' in buf:
                try:
                    text = buf.decode('utf-8', errors='replace')
                    client.publish(TOPICS['comms_inbound'], json.dumps({
                        'raw': text,
                        'ts': time.time(),
                    }))
                except Exception:
                    pass
                buf = b''
        except Exception as e:
            log.debug(f"inbound read: {e}")
            time.sleep(0.5)


def main() -> None:
    log.info("DRIFTER Comms Bridge starting...")
    cfg = _load_config()
    modem_dev = cfg.get('modem_dev', COMMS_MODEM_DEV)
    ser = _open_modem(modem_dev) if cfg.get('enable_modem', True) else None

    running = [True]

    def _handle_signal(sig, frame):
        running[0] = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    client = mqtt.Client(client_id="drifter-comms")

    def on_message(_c, _u, msg) -> None:
        try:
            data = json.loads(msg.payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        if not isinstance(data, dict):
            return
        topic = msg.topic
        if topic == TOPICS['comms_sms']:
            number = data.get('number') or cfg.get('default_recipient', '')
            text = data.get('message', '')
            if number and text:
                _send_sms(ser, number, text)
        elif topic == TOPICS['comms_notify']:
            _fan_out(
                cfg,
                title=data.get('title', 'DRIFTER'),
                message=data.get('message', ''),
                priority=data.get('priority', 'default'),
            )
        elif topic == TOPICS['crash_sos']:
            # SOS — try SMS first, then notify backends
            number = data.get('number') or cfg.get('default_recipient', '')
            text = data.get('message', 'DRIFTER crash detected')
            sms_ok = _send_sms(ser, number, text) if number else False
            _fan_out(cfg, title='DRIFTER SOS', message=text, priority='urgent')
            log.error(f"SOS handled: sms_sent={sms_ok}")

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
        (TOPICS['comms_sms'], 0),
        (TOPICS['comms_notify'], 0),
        (TOPICS['crash_sos'], 0),
    ])
    client.loop_start()
    log.info(f"Comms Bridge LIVE (modem={'on' if ser else 'off'})")

    inbound_thread = threading.Thread(
        target=_read_inbound_loop, args=(ser, client, running), daemon=True,
    )
    inbound_thread.start()

    while running[0]:
        time.sleep(1)

    client.loop_stop()
    client.disconnect()
    if ser is not None:
        try:
            ser.close()
        except Exception:
            pass
    log.info("Comms Bridge stopped")


if __name__ == '__main__':
    main()

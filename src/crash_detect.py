#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Crash Detection + SOS
Watches accelerometer (MPU6050/MPU9250) for sudden g-spikes and the
OBD speed channel for emergency-stop decelerations. On detection, raises
a TOPICS['crash_event'] and starts an SOS countdown which can be cancelled
from the dashboard or by voice. On timeout, fires TOPICS['crash_sos'] for
the comms bridge to deliver via SMS/notify.
UNCAGED TECHNOLOGY — EST 1991
"""

import json
import logging
import math
import signal
import threading
import time
from collections import deque
from pathlib import Path
from typing import Optional

import paho.mqtt.client as mqtt

from config import (
    MQTT_HOST, MQTT_PORT, TOPICS,
    DRIFTER_DIR, CRASH_ACCEL_G_THRESHOLD, CRASH_DECEL_KPH_PER_S,
    CRASH_AIRBAG_GRACE_SEC, CRASH_SOS_NUMBER,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [CRASH] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

CONFIG_PATH = DRIFTER_DIR / "crash.yaml"


def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        import yaml
        return yaml.safe_load(CONFIG_PATH.read_text()) or {}
    except Exception as e:
        log.warning(f"crash.yaml load failed: {e}")
        return {}


class CrashState:
    def __init__(self, grace: int, sos_number: str) -> None:
        self.speed_hist: deque = deque(maxlen=10)
        self.accel_peak_g: float = 0.0
        self.event_active: bool = False
        self.cancelled: bool = False
        self.armed_at: float = 0.0
        self.grace = grace
        self.sos_number = sos_number


def _smbus_present() -> bool:
    try:
        import smbus2  # noqa: F401
        return True
    except ImportError:
        return False


def _read_mpu6050_loop(state: CrashState, running_ref: list, on_event) -> None:
    """Continuous MPU6050 read over I2C. Falls back to no-op when unavailable."""
    try:
        import smbus2
    except ImportError:
        log.warning("smbus2 not installed — accelerometer disabled")
        return

    bus = None
    for bus_id in (1, 0):
        try:
            bus = smbus2.SMBus(bus_id)
            break
        except Exception:
            continue
    if bus is None:
        log.warning("I2C bus not available — accelerometer disabled")
        return

    # MPU6050 wake up
    try:
        bus.write_byte_data(0x68, 0x6B, 0)  # PWR_MGMT_1 = 0
    except Exception as e:
        log.warning(f"MPU6050 init failed: {e}")
        return

    def read_word(addr):
        try:
            high = bus.read_byte_data(0x68, addr)
            low = bus.read_byte_data(0x68, addr + 1)
        except Exception:
            return 0
        val = (high << 8) | low
        if val >= 0x8000:
            val = -((65535 - val) + 1)
        return val

    log.info("MPU6050 accelerometer active")
    while running_ref[0]:
        ax = read_word(0x3B) / 16384.0
        ay = read_word(0x3D) / 16384.0
        az = read_word(0x3F) / 16384.0
        # Remove 1g of gravity from total magnitude
        magnitude = abs(math.sqrt(ax * ax + ay * ay + az * az) - 1.0)
        state.accel_peak_g = max(state.accel_peak_g, magnitude)
        if magnitude >= CRASH_ACCEL_G_THRESHOLD and not state.event_active:
            on_event(reason=f"accel {magnitude:.2f}g", magnitude=magnitude)
        time.sleep(0.02)


def _trigger(client: mqtt.Client, state: CrashState, reason: str, magnitude: float = 0.0) -> None:
    state.event_active = True
    state.cancelled = False
    state.armed_at = time.time()
    client.publish(TOPICS['crash_event'], json.dumps({
        'active': True,
        'reason': reason,
        'magnitude_g': round(magnitude, 2),
        'speed_kph': state.speed_hist[-1] if state.speed_hist else None,
        'armed_at': state.armed_at,
        'grace_seconds': state.grace,
        'ts': time.time(),
    }), retain=True)
    log.warning(f"CRASH triggered: {reason} (g={magnitude:.2f})")
    threading.Thread(target=_sos_countdown, args=(client, state), daemon=True).start()


def _sos_countdown(client: mqtt.Client, state: CrashState) -> None:
    deadline = state.armed_at + state.grace
    while time.time() < deadline:
        if state.cancelled:
            client.publish(TOPICS['crash_event'], json.dumps({
                'active': False, 'cancelled': True, 'ts': time.time(),
            }), retain=True)
            log.info("Crash cancelled by user")
            state.event_active = False
            return
        client.publish(TOPICS['crash_status'], json.dumps({
            'remaining_s': round(deadline - time.time(), 1),
            'ts': time.time(),
        }))
        time.sleep(0.5)

    # Fire SOS
    client.publish(TOPICS['crash_sos'], json.dumps({
        'number': state.sos_number,
        'message': 'DRIFTER crash detected — manual cancel timed out',
        'ts': time.time(),
    }))
    log.error("SOS fired — comms bridge should deliver")


def main() -> None:
    log.info("DRIFTER Crash Detection starting...")
    cfg = _load_config()
    state = CrashState(
        grace=int(cfg.get('grace_seconds', CRASH_AIRBAG_GRACE_SEC)),
        sos_number=cfg.get('sos_number', CRASH_SOS_NUMBER) or '',
    )

    running = [True]

    def _handle_signal(sig, frame):
        running[0] = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    client = mqtt.Client(client_id="drifter-crash")

    def on_event(reason: str, magnitude: float = 0.0) -> None:
        _trigger(client, state, reason, magnitude)

    def on_message(_c, _u, msg) -> None:
        try:
            data = json.loads(msg.payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        topic = msg.topic
        if topic == TOPICS['snapshot'] and isinstance(data, dict):
            speed = data.get('speed')
            if speed is not None:
                try:
                    state.speed_hist.append(float(speed))
                except (TypeError, ValueError):
                    pass
                if len(state.speed_hist) >= 2:
                    decel = state.speed_hist[-2] - state.speed_hist[-1]
                    if decel >= CRASH_DECEL_KPH_PER_S and not state.event_active:
                        on_event(reason=f"hard decel -{decel:.0f} km/h/s")
        elif topic == TOPICS['crash_event'] and isinstance(data, dict):
            if data.get('cancel'):
                state.cancelled = True

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

    client.subscribe([(TOPICS['snapshot'], 0), (TOPICS['crash_event'], 0)])
    client.loop_start()
    client.publish(TOPICS['crash_status'], json.dumps({
        'state': 'armed',
        'accel_available': _smbus_present(),
        'sos_number_set': bool(state.sos_number),
        'ts': time.time(),
    }), retain=True)
    log.info(f"Crash Detection LIVE (grace={state.grace}s, SOS={'set' if state.sos_number else 'NOT SET'})")

    accel_thread = threading.Thread(
        target=_read_mpu6050_loop, args=(state, running, on_event), daemon=True,
    )
    accel_thread.start()

    while running[0]:
        time.sleep(0.5)

    client.publish(TOPICS['crash_status'], json.dumps({
        'state': 'offline', 'ts': time.time(),
    }), retain=True)
    client.loop_stop()
    client.disconnect()
    log.info("Crash Detection stopped")


if __name__ == '__main__':
    main()

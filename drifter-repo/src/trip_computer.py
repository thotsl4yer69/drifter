#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Trip Computer
Subscribes to telemetry and computes per-trip distance, average and
instantaneous fuel economy (from MAF when available, AFR=14.7 stoich),
running cost in GBP, and trip duration. Publishes stats every second and
a richer summary on session end.
UNCAGED TECHNOLOGY — EST 1991
"""

import json
import logging
import signal
import time
from pathlib import Path
from typing import Optional

import paho.mqtt.client as mqtt

from config import (
    MQTT_HOST, MQTT_PORT, TOPICS,
    DRIFTER_DIR, TRIP_FUEL_PRICE_GBP_PER_L, TRIP_FUEL_TANK_LITRES,
    TRIP_AVG_CONSUMPTION_L_PER_100KM, TRIP_SESSION_GAP_MIN,
    FUEL_TYPE,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [TRIP] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

CONFIG_PATH = DRIFTER_DIR / "trip.yaml"

# AFR for stoichiometric petrol (mass air / mass fuel). Petrol density 0.745 kg/L.
AFR_STOICH = 14.7
PETROL_DENSITY_KG_PER_L = 0.745
# Diesel uses different stoich/density
DIESEL_AFR = 14.5
DIESEL_DENSITY_KG_PER_L = 0.832


def _density() -> float:
    return DIESEL_DENSITY_KG_PER_L if FUEL_TYPE == 'diesel' else PETROL_DENSITY_KG_PER_L


def _afr() -> float:
    return DIESEL_AFR if FUEL_TYPE == 'diesel' else AFR_STOICH


class TripState:
    def __init__(self, fuel_price: float, tank_l: float, avg_l_per_100: float) -> None:
        self.start_ts: Optional[float] = None
        self.last_ts: Optional[float] = None
        self.distance_km: float = 0.0
        self.fuel_l: float = 0.0
        self.last_speed_kph: float = 0.0
        self.last_maf_gps: Optional[float] = None
        self.fuel_price = fuel_price
        self.tank_l = tank_l
        self.avg_l_per_100km = avg_l_per_100
        self.idle_since: Optional[float] = None
        self.events: list = []

    def reset(self) -> None:
        log.info(f"Trip reset (was {self.distance_km:.1f} km, {self.fuel_l:.2f} L)")
        self.__init__(self.fuel_price, self.tank_l, self.avg_l_per_100km)

    def tick(self, ts: float, speed_kph: Optional[float], maf_gps: Optional[float]) -> None:
        if self.start_ts is None:
            self.start_ts = ts
            self.last_ts = ts
        dt = max(0.0, ts - (self.last_ts or ts))
        self.last_ts = ts

        if speed_kph is not None:
            self.last_speed_kph = float(speed_kph)
            km = (float(speed_kph) / 3600.0) * dt
            if km > 0 and km < 1.0:  # sanity cap
                self.distance_km += km

        if maf_gps is not None:
            self.last_maf_gps = float(maf_gps)
            # Fuel mass flow = MAF / AFR (g/s). Convert g/s -> L/s -> L
            fuel_g = float(maf_gps) / _afr() * dt
            litres = fuel_g / 1000.0 / _density()
            if 0 < litres < 0.1:
                self.fuel_l += litres

        # Idle detection
        if speed_kph is not None and float(speed_kph) <= 1.0:
            if self.idle_since is None:
                self.idle_since = ts
            elif ts - self.idle_since > TRIP_SESSION_GAP_MIN * 60:
                self.events.append({'event': 'long_idle', 'ts': ts})
        else:
            self.idle_since = None

    def to_dict(self) -> dict:
        elapsed = (self.last_ts - self.start_ts) if self.start_ts else 0.0
        cur_l_per_100 = None
        if self.last_maf_gps is not None and self.last_speed_kph > 1.0:
            litres_per_hour = self.last_maf_gps / _afr() * 3600.0 / 1000.0 / _density()
            cur_l_per_100 = round(litres_per_hour / max(self.last_speed_kph, 0.1) * 100.0, 2)
        avg_l_per_100 = None
        if self.distance_km > 0.1:
            avg_l_per_100 = round(self.fuel_l / self.distance_km * 100.0, 2)
        cost_gbp = round(self.fuel_l * self.fuel_price, 2)
        return {
            'duration_s': round(elapsed, 1),
            'distance_km': round(self.distance_km, 3),
            'fuel_l': round(self.fuel_l, 3),
            'avg_l_per_100km': avg_l_per_100 or self.avg_l_per_100km,
            'cur_l_per_100km': cur_l_per_100,
            'cost_gbp': cost_gbp,
            'fuel_price_per_l': self.fuel_price,
            'speed_kph': round(self.last_speed_kph, 1),
            'ts': time.time(),
        }


def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        import yaml
        return yaml.safe_load(CONFIG_PATH.read_text()) or {}
    except Exception as e:
        log.warning(f"trip.yaml load failed: {e}")
        return {}


def main() -> None:
    log.info("DRIFTER Trip Computer starting...")
    cfg = _load_config()
    state = TripState(
        fuel_price=float(cfg.get('fuel_price_gbp_per_l', TRIP_FUEL_PRICE_GBP_PER_L)),
        tank_l=float(cfg.get('tank_litres', TRIP_FUEL_TANK_LITRES)),
        avg_l_per_100=float(cfg.get('avg_consumption_l_per_100km',
                                    TRIP_AVG_CONSUMPTION_L_PER_100KM)),
    )

    running = True

    def _handle_signal(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    client = mqtt.Client(client_id="drifter-trip")

    def on_message(_c, _u, msg) -> None:
        try:
            data = json.loads(msg.payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        topic = msg.topic
        if topic == TOPICS['snapshot'] and isinstance(data, dict):
            state.tick(
                data.get('ts', time.time()),
                data.get('speed'),
                data.get('maf'),
            )
        elif topic == TOPICS['drive_session'] and isinstance(data, dict):
            if data.get('event') == 'start':
                state.reset()
            elif data.get('event') == 'end':
                client.publish(TOPICS['trip_stats'], json.dumps(state.to_dict()),
                               retain=True)
                client.publish(TOPICS['trip_event'], json.dumps({
                    'event': 'session_end',
                    'session_id': data.get('session_id'),
                    'final': state.to_dict(),
                    'ts': time.time(),
                }))

    client.on_message = on_message

    connected = False
    while not connected and running:
        try:
            client.connect(MQTT_HOST, MQTT_PORT, 60)
            connected = True
        except Exception as e:
            log.warning(f"Waiting for MQTT broker... ({e})")
            time.sleep(3)

    if not running:
        return

    client.subscribe([
        (TOPICS['snapshot'], 0),
        (TOPICS['drive_session'], 0),
    ])
    client.loop_start()
    log.info(f"Trip Computer LIVE — fuel £{state.fuel_price}/L, tank {state.tank_l}L")

    while running:
        snap = state.to_dict()
        client.publish(TOPICS['trip_stats'], json.dumps(snap))
        client.publish(TOPICS['trip_fuel'], json.dumps({
            'fuel_l': snap['fuel_l'],
            'cur_l_per_100km': snap['cur_l_per_100km'],
            'avg_l_per_100km': snap['avg_l_per_100km'],
            'ts': snap['ts'],
        }))
        client.publish(TOPICS['trip_cost'], json.dumps({
            'cost_gbp': snap['cost_gbp'],
            'fuel_price_per_l': snap['fuel_price_per_l'],
            'ts': snap['ts'],
        }))
        time.sleep(1)

    client.loop_stop()
    client.disconnect()
    log.info("Trip Computer stopped")


if __name__ == '__main__':
    main()

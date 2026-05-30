#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Vehicle Identification
Auto-detects the connected vehicle by VIN (OBD-II Mode 09, PID 02), looks up
the matching vehicles/<VIN>.yaml profile, and falls back to AI generation
via llm_client_v2 when no profile exists. Publishes the active profile so
downstream modules (alerts, trip computer, KB) can re-target.
UNCAGED TECHNOLOGY — EST 1991
"""

import json
import logging
import signal
import time
from pathlib import Path
from typing import Optional

import paho.mqtt.client as mqtt

import llm_client_v2
from config import (
    MQTT_HOST, MQTT_PORT, TOPICS,
    VEHICLES_DIR, VEHICLE_PROFILE_FILE, VEHICLE_DEFAULTS,
    VIN_DETECT_RETRIES,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [VEHICLEID] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

VIN_RE_CHARS = set("ABCDEFGHJKLMNPRSTUVWXYZ0123456789")

PROFILE_SYSTEM = (
    "You are a vehicle data specialist. Given a VIN, decode it and produce a "
    "concise vehicle profile suitable for a Raspberry Pi diagnostic system. "
    "Respond with valid JSON only — no prose, no markdown."
)
PROFILE_USER = (
    "VIN: {vin}\n\n"
    "Required JSON schema:\n"
    "{{\n"
    '  "make": str, "model": str, "year": int, "engine": str, '
    '"fuel_type": "petrol|diesel|hybrid|ev", "tank_litres": float, '
    '"avg_consumption_l_per_100km": float, "tire_size": str, '
    '"tire_pressure_front": float, "tire_pressure_rear": float, '
    '"known_issues": [str], "drivetrain": str, "transmission": str\n'
    "}}\n"
    "Use realistic factory specs. Pressures in PSI. Tank in litres."
)


def _valid_vin(s: str) -> bool:
    if not s or len(s) != 17:
        return False
    return all(c in VIN_RE_CHARS for c in s.upper())


def detect_vin_from_obd(retries: int = VIN_DETECT_RETRIES) -> Optional[str]:
    """Query VIN via python-can. Returns None if no CAN bus or no response."""
    try:
        import can
        from config import CAN_BITRATE, OBD_REQUEST_ID
    except ImportError:
        log.warning("python-can not available — cannot read VIN over CAN")
        return None

    for attempt in range(retries):
        try:
            bus = can.Bus(interface='socketcan', channel='can0', bitrate=CAN_BITRATE)
        except Exception as e:
            log.debug(f"CAN open attempt {attempt}: {e}")
            time.sleep(1)
            continue
        try:
            req = can.Message(
                arbitration_id=OBD_REQUEST_ID,
                data=[0x02, 0x09, 0x02, 0, 0, 0, 0, 0],
                is_extended_id=False,
            )
            bus.send(req)
            chunks: dict[int, bytes] = {}
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                msg = bus.recv(timeout=0.2)
                if msg is None:
                    continue
                if not (0x7E8 <= msg.arbitration_id <= 0x7EF):
                    continue
                data = bytes(msg.data)
                if len(data) < 3:
                    continue
                # ISO-TP first frame
                if data[0] & 0xF0 == 0x10:
                    chunks[0] = data[5:]
                # Consecutive frames
                elif data[0] & 0xF0 == 0x20:
                    idx = data[0] & 0x0F
                    chunks[idx] = data[1:]
                # Single frame
                elif data[0] & 0xF0 == 0x00 and len(data) >= 7 and data[2] == 0x09:
                    chunks[0] = data[5:]
            if chunks:
                raw = b''.join(chunks[k] for k in sorted(chunks))
                vin = raw.decode('ascii', errors='replace').strip('\x00 ').strip()
                if _valid_vin(vin):
                    log.info(f"VIN detected: {vin}")
                    return vin
        finally:
            try:
                bus.shutdown()
            except Exception:
                pass
    return None


def load_profile(vin: str) -> Optional[dict]:
    """Load vehicles/<vin>.yaml or vehicles/<vin>.json if present."""
    if not vin:
        return None
    for suffix in (".yaml", ".yml", ".json"):
        path = VEHICLES_DIR / f"{vin}{suffix}"
        if not path.exists():
            continue
        try:
            text = path.read_text()
            if suffix == ".json":
                return json.loads(text)
            import yaml
            return yaml.safe_load(text)
        except Exception as e:
            log.warning(f"Profile {path.name} failed to load: {e}")
    return None


def generate_profile(vin: str) -> Optional[dict]:
    """Ask the LLM cascade to decode a VIN. Cached on disk afterwards."""
    log.info(f"No local profile for {vin} — asking AI to decode")
    try:
        result = llm_client_v2.query_json(
            PROFILE_USER.format(vin=vin), PROFILE_SYSTEM, max_tokens=400,
        )
    except Exception as e:
        log.warning(f"AI profile generation failed: {e}")
        return None
    if result.get('parse_error') or not result.get('json'):
        log.warning("AI profile did not parse as JSON")
        return None
    profile = result['json']
    profile['vin'] = vin
    profile['source'] = 'ai_generated'
    # Persist for next boot
    try:
        VEHICLES_DIR.mkdir(parents=True, exist_ok=True)
        out = VEHICLES_DIR / f"{vin}.json"
        out.write_text(json.dumps(profile, indent=2))
        log.info(f"Cached AI profile to {out}")
    except Exception as e:
        log.warning(f"Could not persist AI profile: {e}")
    return profile


def write_active_profile(profile: dict) -> None:
    try:
        VEHICLE_PROFILE_FILE.parent.mkdir(parents=True, exist_ok=True)
        VEHICLE_PROFILE_FILE.write_text(json.dumps(profile, indent=2))
    except Exception as e:
        log.warning(f"Could not write active profile: {e}")


def resolve_profile(vin: Optional[str]) -> dict:
    """Try local YAML, then AI, finally fall back to defaults."""
    base = dict(VEHICLE_DEFAULTS)
    if vin:
        loaded = load_profile(vin) or generate_profile(vin)
        if loaded:
            base.update({k: v for k, v in loaded.items() if v is not None})
            base['vin'] = vin
            base['source'] = loaded.get('source', 'local')
            return base
    base['vin'] = vin or 'unknown'
    base['source'] = 'defaults'
    return base


def _publish(client: mqtt.Client, vin: Optional[str], profile: dict) -> None:
    client.publish(TOPICS['vehicle_id'], json.dumps({
        'vin': vin, 'ts': time.time(),
    }), retain=True)
    client.publish(TOPICS['vehicle_profile'], json.dumps({
        'profile': profile, 'ts': time.time(),
    }), retain=True)


def main() -> None:
    log.info("DRIFTER Vehicle Identifier starting...")

    running = True

    def _handle_signal(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    client = mqtt.Client(client_id="drifter-vehicleid")

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

    client.loop_start()

    vin = detect_vin_from_obd()
    profile = resolve_profile(vin)
    write_active_profile(profile)
    _publish(client, vin, profile)
    log.info(f"Active vehicle: {profile.get('make')} {profile.get('model')} "
             f"({profile.get('year')}) — source={profile.get('source')}")

    # Re-publish periodically so late subscribers see it
    while running:
        time.sleep(30)
        _publish(client, vin, profile)

    client.loop_stop()
    client.disconnect()
    log.info("Vehicle Identifier stopped")


if __name__ == '__main__':
    main()

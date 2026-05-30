#!/usr/bin/env python3
"""
MZ1312 DRIFTER — RealDash TCP Bridge
Bridges MQTT telemetry to RealDash via TCP CAN frame protocol.
RealDash connects to this bridge over Wi-Fi (10.42.0.1:35000).

Protocol: RealDash 0x44 CAN frames over TCP.
Each frame: [0x44, 0x33, 0x22, 0x11] + [frame_id 4 bytes LE] + [data 8 bytes]

UNCAGED TECHNOLOGY — EST 1991
"""

import json
import time
import signal
import struct
import socket
import threading
import logging
import paho.mqtt.client as mqtt

from config import MQTT_HOST, MQTT_PORT, REALDASH_TCP_PORT, LEVEL_NAMES, TOPICS, make_mqtt_client

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [REALDASH] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

# ── State ──
# All values start as None — None means "never received", 0 is a real value.
latest = {
    'rpm': None, 'coolant': None, 'stft1': None, 'stft2': None,
    'speed': None, 'throttle': None, 'load': None, 'voltage': None,
    'ltft1': None, 'ltft2': None, 'iat': None, 'maf': None,
    'o2_b1s1': None, 'o2_b2s1': None, 'timing': None, 'baro': None,
    'fuel_lvl': None, 'run_time': None,
    'alert_level': None,
    'tpms_fl_psi': None, 'tpms_fr_psi': None, 'tpms_rl_psi': None, 'tpms_rr_psi': None,
    'tpms_fl_temp': None, 'tpms_fr_temp': None, 'tpms_rl_temp': None, 'tpms_rr_temp': None,
}

# Last known good values — held across gaps so RealDash display stays stable.
_last_known = {}

# Set to True once at least one real CAN message has arrived.
# No frames are sent to RealDash until this is True.
_has_real_data = False

alert_message = ""
clients = []
clients_lock = threading.Lock()
running = True

# RealDash frame header: 0x44, 0x33, 0x22, 0x11
REALDASH_HEADER = bytes([0x44, 0x33, 0x22, 0x11])


def _get(key):
    """Return latest value for key, falling back to last known. Returns None if never seen."""
    v = latest.get(key)
    if v is not None:
        return v
    return _last_known.get(key)


def build_frame(frame_id, data_bytes):
    """Build a RealDash 0x44 CAN frame.
    Format: header(4) + frame_id(4 LE) + data(8)
    """
    frame = REALDASH_HEADER
    frame += struct.pack('<I', frame_id)
    # Pad data to 8 bytes
    data_bytes = data_bytes[:8]
    data_bytes += bytes(8 - len(data_bytes))
    frame += data_bytes
    return frame


def pack_engine_frame():
    """Frame 0x110: RPM, Coolant, STFT1, STFT2.
    Returns None if none of the sensors have ever reported.
    """
    rpm_v = _get('rpm')
    coolant_v = _get('coolant')
    stft1_v = _get('stft1')
    stft2_v = _get('stft2')

    if all(v is None for v in (rpm_v, coolant_v, stft1_v, stft2_v)):
        return None

    rpm = int((rpm_v or 0) * 4)           # RealDash expects raw OBD value
    coolant = int(((coolant_v or 0) + 40) * 10)   # Scale for precision
    stft1 = int(((stft1_v or 0) + 100) * 100)     # Offset and scale (±100% → 0-20000)
    stft2 = int(((stft2_v or 0) + 100) * 100)

    data = struct.pack('>HhHH',
                       max(0, min(65535, rpm)),
                       max(-32768, min(32767, coolant)),
                       max(0, min(65535, stft1)),
                       max(0, min(65535, stft2)))
    return build_frame(0x110, data)


def pack_vehicle_frame():
    """Frame 0x120: Speed, Throttle, Load, Voltage.
    Returns None if none of the sensors have ever reported.
    """
    speed_v = _get('speed')
    throttle_v = _get('throttle')
    load_v = _get('load')
    voltage_v = _get('voltage')

    if all(v is None for v in (speed_v, throttle_v, load_v, voltage_v)):
        return None

    speed = int(speed_v or 0)
    throttle = int((throttle_v or 0) * 10)
    load = int((load_v or 0) * 10)
    voltage = int((voltage_v or 0) * 100)

    data = struct.pack('>HHHH',
                       max(0, min(65535, speed)),
                       max(0, min(65535, throttle)),
                       max(0, min(65535, load)),
                       max(0, min(65535, voltage)))
    return build_frame(0x120, data)


def pack_extended_frame():
    """Frame 0x130: LTFT1, LTFT2, IAT, MAF.
    Returns None if none of the sensors have ever reported.
    """
    ltft1_v = _get('ltft1')
    ltft2_v = _get('ltft2')
    iat_v = _get('iat')
    maf_v = _get('maf')

    if all(v is None for v in (ltft1_v, ltft2_v, iat_v, maf_v)):
        return None

    ltft1 = int(((ltft1_v or 0) + 100) * 100)
    ltft2 = int(((ltft2_v or 0) + 100) * 100)
    iat = int(((iat_v or 0) + 40) * 10)
    maf = int((maf_v or 0) * 100)

    data = struct.pack('>HHHH',
                       max(0, min(65535, ltft1)),
                       max(0, min(65535, ltft2)),
                       max(0, min(65535, iat)),
                       max(0, min(65535, maf)))
    return build_frame(0x130, data)


def pack_alert_frame():
    """Frame 0x300: Alert level (1 byte).
    Returns None if alert_level has never been received.
    """
    level_v = _get('alert_level')
    if level_v is None:
        return None
    data = struct.pack('B', level_v)
    return build_frame(0x300, data)


def pack_alert_text_frame():
    """Frame 0x200: Alert text (up to 63 bytes + null terminator)."""
    text = alert_message[:63].encode('latin-1', errors='replace')
    text += b'\x00'  # Null terminator
    # RealDash text frame uses 0x44 header but with full 64 bytes
    frame = REALDASH_HEADER
    frame += struct.pack('<I', 0x200)
    frame += text
    frame += bytes(64 - len(text))  # Pad to 64
    return frame


def pack_tpms_frame():
    """Frame 0x140: TPMS — FL PSI, FR PSI, RL PSI, RR PSI (scaled ×10).
    Returns None if no TPMS pressure has ever been received.
    """
    fl_v = _get('tpms_fl_psi')
    fr_v = _get('tpms_fr_psi')
    rl_v = _get('tpms_rl_psi')
    rr_v = _get('tpms_rr_psi')

    if all(v is None for v in (fl_v, fr_v, rl_v, rr_v)):
        return None

    fl = int((fl_v or 0) * 10)
    fr = int((fr_v or 0) * 10)
    rl = int((rl_v or 0) * 10)
    rr = int((rr_v or 0) * 10)

    data = struct.pack('>HHHH',
                       max(0, min(65535, fl)),
                       max(0, min(65535, fr)),
                       max(0, min(65535, rl)),
                       max(0, min(65535, rr)))
    return build_frame(0x140, data)


def pack_extra_engine_frame():
    """Frame 0x160: O2 B1S1, O2 B2S1, Timing Advance, Barometric Pressure.
    Returns None if none of the sensors have ever reported.
    """
    o2_b1_v = _get('o2_b1s1')
    o2_b2_v = _get('o2_b2s1')
    timing_v = _get('timing')
    baro_v = _get('baro')

    if all(v is None for v in (o2_b1_v, o2_b2_v, timing_v, baro_v)):
        return None

    o2_b1 = int((o2_b1_v or 0) * 10000)        # 0-1.275V → 0-12750
    o2_b2 = int((o2_b2_v or 0) * 10000)
    timing = int(((timing_v or 0) + 64) * 100)  # -64 to +64° → 0-12800
    baro = int((baro_v or 0) * 10)              # kPa × 10

    data = struct.pack('>HHHH',
                       max(0, min(65535, o2_b1)),
                       max(0, min(65535, o2_b2)),
                       max(0, min(65535, timing)),
                       max(0, min(65535, baro)))
    return build_frame(0x160, data)


def pack_vehicle_extra_frame():
    """Frame 0x170: Fuel Level, Engine Run Time.
    Returns None if neither sensor has ever reported.
    """
    fuel_v = _get('fuel_lvl')
    run_time_v = _get('run_time')

    if fuel_v is None and run_time_v is None:
        return None

    fuel = int((fuel_v or 0) * 100)        # 0-100% → 0-10000
    run_time = int(run_time_v or 0)         # seconds

    data = struct.pack('>HH',
                       max(0, min(65535, fuel)),
                       max(0, min(65535, run_time)))
    return build_frame(0x170, data)


def pack_tpms_temp_frame():
    """Frame 0x150: TPMS temps — FL, FR, RL, RR (°C × 10, offset +40).
    Returns None if no TPMS temp has ever been received.
    """
    fl_v = _get('tpms_fl_temp')
    fr_v = _get('tpms_fr_temp')
    rl_v = _get('tpms_rl_temp')
    rr_v = _get('tpms_rr_temp')

    if all(v is None for v in (fl_v, fr_v, rl_v, rr_v)):
        return None

    fl = int(((fl_v or 0) + 40) * 10)
    fr = int(((fr_v or 0) + 40) * 10)
    rl = int(((rl_v or 0) + 40) * 10)
    rr = int(((rr_v or 0) + 40) * 10)

    data = struct.pack('>HHHH',
                       max(0, min(65535, fl)),
                       max(0, min(65535, fr)),
                       max(0, min(65535, rl)),
                       max(0, min(65535, rr)))
    return build_frame(0x150, data)


def on_mqtt_message(client, userdata, msg):
    """Update latest values from MQTT."""
    global alert_message, _has_real_data
    try:
        data = json.loads(msg.payload)
        topic = msg.topic

        def _update(key, value):
            """Store value in latest and _last_known, mark real data arrived."""
            global _has_real_data
            if value is not None:
                latest[key] = value
                _last_known[key] = value
                _has_real_data = True

        if topic.endswith('/rpm'):
            _update('rpm', data.get('value'))
        elif topic.endswith('/coolant'):
            _update('coolant', data.get('value'))
        elif topic.endswith('/stft1'):
            _update('stft1', data.get('value'))
        elif topic.endswith('/stft2'):
            _update('stft2', data.get('value'))
        elif topic.endswith('/ltft1'):
            _update('ltft1', data.get('value'))
        elif topic.endswith('/ltft2'):
            _update('ltft2', data.get('value'))
        elif topic.endswith('/load'):
            _update('load', data.get('value'))
        elif topic.endswith('/speed'):
            _update('speed', data.get('value'))
        elif topic.endswith('/throttle'):
            _update('throttle', data.get('value'))
        elif topic.endswith('/voltage'):
            _update('voltage', data.get('value'))
        elif topic.endswith('/iat'):
            _update('iat', data.get('value'))
        elif topic.endswith('/maf'):
            _update('maf', data.get('value'))
        elif topic.endswith('/o2_b1s1'):
            _update('o2_b1s1', data.get('value'))
        elif topic.endswith('/o2_b2s1'):
            _update('o2_b2s1', data.get('value'))
        elif topic.endswith('/timing'):
            _update('timing', data.get('value'))
        elif topic.endswith('/baro'):
            _update('baro', data.get('value'))
        elif topic.endswith('/fuel_lvl'):
            _update('fuel_lvl', data.get('value'))
        elif topic.endswith('/run_time'):
            _update('run_time', data.get('value'))
        elif topic.endswith('/alert/level'):
            level = data.get('level')
            if level is not None:
                latest['alert_level'] = level
                _last_known['alert_level'] = level
                _has_real_data = True
        elif topic.endswith('/alert/message'):
            alert_message = data.get('message', '')
        # TPMS
        elif '/rf/tpms/' in topic:
            pos = topic.split('/')[-1]  # fl, fr, rl, rr
            if pos in ('fl', 'fr', 'rl', 'rr'):
                psi = data.get('pressure_psi')
                temp = data.get('temp_c')
                _update(f'tpms_{pos}_psi', psi)
                _update(f'tpms_{pos}_temp', temp)

    except (json.JSONDecodeError, KeyError):
        pass


def handle_client(conn, addr):
    """Send CAN frames to a connected RealDash client at ~20Hz."""
    log.info(f"RealDash client connected: {addr}")

    with clients_lock:
        clients.append(conn)

    try:
        text_frame_counter = 0
        while running:
            try:
                # Don't send anything until at least one real sensor reading has arrived.
                if not _has_real_data:
                    time.sleep(0.05)
                    continue

                # Build all frames, skipping any that have no data yet.
                frame_builders = [
                    pack_engine_frame,
                    pack_vehicle_frame,
                    pack_extended_frame,
                    pack_extra_engine_frame,
                    pack_vehicle_extra_frame,
                    pack_alert_frame,
                    pack_tpms_frame,
                    pack_tpms_temp_frame,
                ]
                frames = b''
                for builder in frame_builders:
                    result = builder()
                    if result is not None:
                        frames += result

                if frames:
                    conn.sendall(frames)

                # Send text frame less frequently (every ~500ms = every 10th iteration)
                text_frame_counter += 1
                if alert_message and text_frame_counter >= 10:
                    conn.sendall(pack_alert_text_frame())
                    text_frame_counter = 0

                time.sleep(0.05)  # 20 Hz

            except (BrokenPipeError, ConnectionResetError, OSError):
                break
    finally:
        with clients_lock:
            if conn in clients:
                clients.remove(conn)
        try:
            conn.close()
        except Exception:
            pass
        log.info(f"RealDash client disconnected: {addr}")


def tcp_server():
    """TCP server accepting RealDash connections."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.settimeout(2.0)
    server.bind(('0.0.0.0', REALDASH_TCP_PORT))
    server.listen(3)
    log.info(f"RealDash TCP server listening on port {REALDASH_TCP_PORT}")

    while running:
        try:
            conn, addr = server.accept()
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            t = threading.Thread(target=handle_client, args=(conn, addr), daemon=True)
            t.start()
        except socket.timeout:
            continue
        except OSError:
            if running:
                log.error("TCP server error")
            break

    server.close()


def main():
    global running

    def _handle_signal(sig, frame):
        global running
        running = False

    log.info("DRIFTER RealDash Bridge starting...")

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    # ── MQTT ──
    mqtt_client = make_mqtt_client("drifter-realdash")
    mqtt_client.on_message = on_mqtt_message

    connected = False
    while not connected and running:
        try:
            mqtt_client.connect(MQTT_HOST, MQTT_PORT, 60)
            connected = True
        except Exception as e:
            log.warning(f"Waiting for MQTT broker... ({e})")
            time.sleep(3)

    mqtt_client.subscribe("drifter/engine/#")
    mqtt_client.subscribe("drifter/vehicle/#")
    mqtt_client.subscribe("drifter/power/#")
    mqtt_client.subscribe("drifter/alert/#")
    mqtt_client.subscribe("drifter/rf/tpms/#")
    mqtt_client.loop_start()

    # ── TCP Server ──
    server_thread = threading.Thread(target=tcp_server, daemon=True)
    server_thread.start()

    log.info("RealDash Bridge is LIVE")
    log.info(f"  TCP: 0.0.0.0:{REALDASH_TCP_PORT}")
    log.info(f"  MQTT: Also available at {MQTT_HOST}:{MQTT_PORT}")

    while running:
        time.sleep(1)

    # Cleanup
    with clients_lock:
        for c in clients:
            try:
                c.close()
            except Exception:
                pass

    mqtt_client.loop_stop()
    mqtt_client.disconnect()
    log.info("RealDash Bridge stopped")


if __name__ == '__main__':
    main()

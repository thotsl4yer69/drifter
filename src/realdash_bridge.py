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

from config import MQTT_HOST, MQTT_PORT, REALDASH_TCP_PORT, LEVEL_NAMES, TOPICS

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [REALDASH] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

# ── State ──
latest = {
    'rpm': 0, 'coolant': 0, 'stft1': 0, 'stft2': 0,
    'speed': 0, 'throttle': 0, 'load': 0, 'voltage': 0,
    'ltft1': 0, 'ltft2': 0, 'iat': 0, 'maf': 0,
    'alert_level': 0,
    'tpms_fl_psi': 0, 'tpms_fr_psi': 0, 'tpms_rl_psi': 0, 'tpms_rr_psi': 0,
    'tpms_fl_temp': 0, 'tpms_fr_temp': 0, 'tpms_rl_temp': 0, 'tpms_rr_temp': 0,
}
alert_message = ""
clients = []
clients_lock = threading.Lock()

# RealDash frame header: 0x44, 0x33, 0x22, 0x11
REALDASH_HEADER = bytes([0x44, 0x33, 0x22, 0x11])


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
    """Frame 0x110: RPM, Coolant, STFT1, STFT2."""
    rpm = int(latest['rpm'] * 4)  # RealDash expects raw OBD value
    coolant = int((latest['coolant'] + 40) * 10)  # Scale for precision
    stft1 = int((latest['stft1'] + 100) * 100)    # Offset and scale (±100% → 0-20000)
    stft2 = int((latest['stft2'] + 100) * 100)

    data = struct.pack('>HhHH',
                       max(0, min(65535, rpm)),
                       max(-32768, min(32767, coolant)),
                       max(0, min(65535, stft1)),
                       max(0, min(65535, stft2)))
    return build_frame(0x110, data)


def pack_vehicle_frame():
    """Frame 0x120: Speed, Throttle, Load, Voltage."""
    speed = int(latest['speed'])
    throttle = int(latest['throttle'] * 10)
    load = int(latest['load'] * 10)
    voltage = int(latest['voltage'] * 100)

    data = struct.pack('>HHHH',
                       max(0, min(65535, speed)),
                       max(0, min(65535, throttle)),
                       max(0, min(65535, load)),
                       max(0, min(65535, voltage)))
    return build_frame(0x120, data)


def pack_extended_frame():
    """Frame 0x130: LTFT1, LTFT2, IAT, MAF."""
    ltft1 = int((latest['ltft1'] + 100) * 100)
    ltft2 = int((latest['ltft2'] + 100) * 100)
    iat = int((latest['iat'] + 40) * 10)
    maf = int(latest['maf'] * 100)

    data = struct.pack('>HHHH',
                       max(0, min(65535, ltft1)),
                       max(0, min(65535, ltft2)),
                       max(0, min(65535, iat)),
                       max(0, min(65535, maf)))
    return build_frame(0x130, data)


def pack_alert_frame():
    """Frame 0x300: Alert level (1 byte)."""
    data = struct.pack('B', latest['alert_level'])
    return build_frame(0x300, data)


def pack_alert_text_frame():
    """Frame 0x200: Alert text (up to 63 bytes + null terminator)."""
    text = alert_message[:63].encode('ascii', errors='replace')
    text += b'\x00'  # Null terminator
    # RealDash text frame uses 0x44 header but with full 64 bytes
    frame = REALDASH_HEADER
    frame += struct.pack('<I', 0x200)
    frame += text
    frame += bytes(64 - len(text))  # Pad to 64
    return frame


def pack_tpms_frame():
    """Frame 0x140: TPMS — FL PSI, FR PSI, RL PSI, RR PSI (scaled ×10)."""
    fl = int(latest['tpms_fl_psi'] * 10)
    fr = int(latest['tpms_fr_psi'] * 10)
    rl = int(latest['tpms_rl_psi'] * 10)
    rr = int(latest['tpms_rr_psi'] * 10)

    data = struct.pack('>HHHH',
                       max(0, min(65535, fl)),
                       max(0, min(65535, fr)),
                       max(0, min(65535, rl)),
                       max(0, min(65535, rr)))
    return build_frame(0x140, data)


def pack_tpms_temp_frame():
    """Frame 0x150: TPMS temps — FL, FR, RL, RR (°C × 10, offset +40)."""
    fl = int((latest['tpms_fl_temp'] + 40) * 10)
    fr = int((latest['tpms_fr_temp'] + 40) * 10)
    rl = int((latest['tpms_rl_temp'] + 40) * 10)
    rr = int((latest['tpms_rr_temp'] + 40) * 10)

    data = struct.pack('>HHHH',
                       max(0, min(65535, fl)),
                       max(0, min(65535, fr)),
                       max(0, min(65535, rl)),
                       max(0, min(65535, rr)))
    return build_frame(0x150, data)


def on_mqtt_message(client, userdata, msg):
    """Update latest values from MQTT."""
    global alert_message
    try:
        data = json.loads(msg.payload)
        topic = msg.topic

        if topic.endswith('/rpm'):
            latest['rpm'] = data.get('value', 0)
        elif topic.endswith('/coolant'):
            latest['coolant'] = data.get('value', 0)
        elif topic.endswith('/stft1'):
            latest['stft1'] = data.get('value', 0)
        elif topic.endswith('/stft2'):
            latest['stft2'] = data.get('value', 0)
        elif topic.endswith('/ltft1'):
            latest['ltft1'] = data.get('value', 0)
        elif topic.endswith('/ltft2'):
            latest['ltft2'] = data.get('value', 0)
        elif topic.endswith('/load'):
            latest['load'] = data.get('value', 0)
        elif topic.endswith('/speed'):
            latest['speed'] = data.get('value', 0)
        elif topic.endswith('/throttle'):
            latest['throttle'] = data.get('value', 0)
        elif topic.endswith('/voltage'):
            latest['voltage'] = data.get('value', 0)
        elif topic.endswith('/iat'):
            latest['iat'] = data.get('value', 0)
        elif topic.endswith('/maf'):
            latest['maf'] = data.get('value', 0)
        elif topic.endswith('/alert/level'):
            latest['alert_level'] = data.get('level', 0)
        elif topic.endswith('/alert/message'):
            alert_message = data.get('message', '')
        # TPMS
        elif '/rf/tpms/' in topic:
            pos = topic.split('/')[-1]  # fl, fr, rl, rr
            if pos in ('fl', 'fr', 'rl', 'rr'):
                latest[f'tpms_{pos}_psi'] = data.get('pressure_psi', 0)
                latest[f'tpms_{pos}_temp'] = data.get('temp_c', 0)

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
                # Send all frames
                frames = (
                    pack_engine_frame() +
                    pack_vehicle_frame() +
                    pack_extended_frame() +
                    pack_alert_frame() +
                    pack_tpms_frame() +
                    pack_tpms_temp_frame()
                )
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
    running = True

    def _handle_signal(sig, frame):
        nonlocal running
        running = False

    log.info("DRIFTER RealDash Bridge starting...")

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    # ── MQTT ──
    mqtt_client = mqtt.Client(client_id="drifter-realdash")
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

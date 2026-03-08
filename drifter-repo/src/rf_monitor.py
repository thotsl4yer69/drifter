#!/usr/bin/env python3
"""
MZ1312 DRIFTER — RF Monitor
RTL-SDR daemon: TPMS decoding, RF spectrum scanning, emergency band monitoring.
Uses rtl_433 for 433 MHz signal decoding and rtl_power for spectrum sweeps.

UNCAGED TECHNOLOGY — EST 1991
"""

import json
import time
import signal
import subprocess
import logging
import threading
from collections import deque
from pathlib import Path
import paho.mqtt.client as mqtt

from config import (
    MQTT_HOST, MQTT_PORT, TOPICS,
    RTL433_BIN, TPMS_SENSOR_FILE, TPMS_POSITIONS,
    TPMS_LEARN_TIMEOUT, TPMS_STALE_TIMEOUT,
    SPECTRUM_SCAN_INTERVAL, SPECTRUM_FREQ_START, SPECTRUM_FREQ_END,
    EMERGENCY_SCAN_INTERVAL, EMERGENCY_SCAN_DWELL, EMERGENCY_BANDS,
    THRESHOLDS,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [RF] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

running = True


# ═══════════════════════════════════════════════════════════════════
#  TPMS State
# ═══════════════════════════════════════════════════════════════════

class TPMSState:
    """Track tire pressure/temperature for 4 positions."""

    def __init__(self):
        # Sensor ID → position mapping (loaded from file)
        self.sensor_map = {}       # {sensor_id_str: 'fl'|'fr'|'rl'|'rr'}
        self.tires = {}            # {'fl': {pressure, temp, sensor_id, ts}}
        self.pressure_history = {pos: deque(maxlen=60) for pos in TPMS_POSITIONS}
        self.learn_mode = False
        self.learn_start = 0
        self.learned_ids = []      # IDs seen during learn mode
        self._load_sensors()

    def _load_sensors(self):
        """Load sensor ID → position mapping from file."""
        if TPMS_SENSOR_FILE.exists():
            try:
                with open(TPMS_SENSOR_FILE) as f:
                    data = json.load(f)
                self.sensor_map = data.get('sensors', {})
                log.info(f"Loaded TPMS sensors: {len(self.sensor_map)} mapped")
                for sid, pos in self.sensor_map.items():
                    log.info(f"  {pos.upper()}: sensor {sid}")
            except Exception as e:
                log.warning(f"Could not load TPMS sensors: {e}")

    def save_sensors(self):
        """Persist sensor mapping to file."""
        try:
            data = {
                'sensors': self.sensor_map,
                'saved': time.strftime('%Y-%m-%d %H:%M:%S'),
            }
            with open(TPMS_SENSOR_FILE, 'w') as f:
                json.dump(data, f, indent=2)
            log.info("TPMS sensor mapping saved")
        except Exception as e:
            log.warning(f"Could not save TPMS sensors: {e}")

    def start_learn(self):
        """Enter learn mode — capture sensor IDs for mapping."""
        self.learn_mode = True
        self.learn_start = time.time()
        self.learned_ids = []
        log.info("TPMS LEARN MODE — drive slowly, sensors will be captured")

    def stop_learn(self):
        """Exit learn mode."""
        self.learn_mode = False
        log.info(f"TPMS learn stopped. Captured {len(self.learned_ids)} sensor IDs")

    def update(self, sensor_id, pressure_psi, temp_c):
        """Process a TPMS reading from rtl_433."""
        sid = str(sensor_id)
        now = time.time()

        # Learn mode — collect IDs
        if self.learn_mode:
            if sid not in self.learned_ids:
                self.learned_ids.append(sid)
                log.info(f"TPMS LEARN: new sensor {sid} "
                         f"(#{len(self.learned_ids)}, {pressure_psi:.1f} PSI, {temp_c:.0f}°C)")
            if now - self.learn_start > TPMS_LEARN_TIMEOUT:
                self.stop_learn()
            return None

        # Normal mode — map to position
        position = self.sensor_map.get(sid)
        if position is None:
            # Unknown sensor — log but don't track
            return None

        reading = {
            'position': position,
            'pressure_psi': round(pressure_psi, 1),
            'temp_c': round(temp_c, 1),
            'sensor_id': sid,
            'ts': now,
        }
        self.tires[position] = reading
        self.pressure_history[position].append((now, pressure_psi))
        return reading

    def get_snapshot(self):
        """Get current state of all 4 tires."""
        snap = {}
        now = time.time()
        for pos in TPMS_POSITIONS:
            if pos in self.tires:
                t = self.tires[pos]
                stale = (now - t['ts']) > TPMS_STALE_TIMEOUT
                snap[pos] = {**t, 'stale': stale}
            else:
                snap[pos] = {'position': pos, 'pressure_psi': None,
                             'temp_c': None, 'stale': True}
        return snap

    def get_pressure_drop(self, position, window_sec=300):
        """Calculate PSI drop over window (for rapid loss detection)."""
        history = self.pressure_history.get(position)
        if not history or len(history) < 2:
            return 0.0
        now = time.time()
        cutoff = now - window_sec
        readings = [(t, p) for t, p in history if t >= cutoff]
        if len(readings) < 2:
            return 0.0
        return readings[0][1] - readings[-1][1]  # positive = losing pressure


tpms = TPMSState()


# ═══════════════════════════════════════════════════════════════════
#  rtl_433 Signal Processor
# ═══════════════════════════════════════════════════════════════════

def check_rtl_sdr():
    """Verify RTL-SDR device is available."""
    try:
        result = subprocess.run(
            ['rtl_test', '-t'],
            capture_output=True, text=True, timeout=10
        )
        if 'Found' in result.stderr or 'Found' in result.stdout:
            log.info("RTL-SDR device detected")
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    log.warning("RTL-SDR device not found — RF features disabled")
    return False


def check_rtl_433():
    """Verify rtl_433 is installed."""
    try:
        result = subprocess.run(
            [RTL433_BIN, '-V'],
            capture_output=True, text=True, timeout=5
        )
        return result.returncode == 0
    except FileNotFoundError:
        # Try without full path
        try:
            result = subprocess.run(
                ['rtl_433', '-V'],
                capture_output=True, text=True, timeout=5
            )
            return result.returncode == 0
        except FileNotFoundError:
            return False


def run_rtl_433(mqtt_client, stop_event, proc_ref=None):
    """Run rtl_433 as a subprocess with JSON output and process signals."""
    rtl_433_cmd = RTL433_BIN if Path(RTL433_BIN).exists() else 'rtl_433'

    cmd = [
        rtl_433_cmd,
        '-f', '433920000',     # 433.92 MHz (EU/UK ISM band)
        '-F', 'json',          # JSON output
        '-M', 'time:unix',     # Include unix timestamp
        '-M', 'protocol',      # Include protocol info
        '-M', 'level',         # Include signal level
        '-C', 'customary',     # Customary units (PSI, °F → we convert)
        '-Y', 'autolevel',     # Automatic gain level
    ]

    log.info(f"Starting rtl_433: {' '.join(cmd)}")
    signal_count = 0
    tpms_count = 0

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1
        )
        if proc_ref is not None:
            proc_ref[0] = proc

        while not stop_event.is_set():
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    log.warning("rtl_433 process exited")
                    break
                continue

            line = line.strip()
            if not line:
                continue

            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue

            signal_count += 1
            model = data.get('model', 'unknown')

            # ── TPMS Signals ──
            if is_tpms_signal(data):
                tpms_count += 1
                process_tpms(data, mqtt_client)

            # ── Publish all decoded signals ──
            signal_data = {
                'model': model,
                'protocol': data.get('protocol', ''),
                'id': data.get('id', ''),
                'rssi': data.get('rssi', data.get('snr', '')),
                'ts': data.get('time', time.time()),
                'raw': data,
            }
            mqtt_client.publish(TOPICS['rf_signal'], json.dumps(signal_data))

            if signal_count % 50 == 0:
                log.info(f"Signals decoded: {signal_count} total, {tpms_count} TPMS")

    except FileNotFoundError:
        log.error(f"rtl_433 not found at {rtl_433_cmd}")
    except Exception as e:
        log.error(f"rtl_433 error: {e}")
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


def is_tpms_signal(data):
    """Check if a decoded signal is from a TPMS sensor."""
    model = data.get('model', '').lower()
    tpms_keywords = ['tpms', 'tire', 'tyre', 'pressure_psi', 'pressure_kpa']
    # Check model name
    if any(kw in model for kw in tpms_keywords):
        return True
    # Check for pressure fields (rtl_433 TPMS decoders use various field names)
    if 'pressure_PSI' in data or 'pressure_kPa' in data or 'pressure_bar' in data:
        return True
    return False


def process_tpms(data, mqtt_client):
    """Extract TPMS data and update state."""
    sensor_id = data.get('id', data.get('code', ''))

    # Extract pressure (normalize to PSI)
    pressure_psi = None
    if 'pressure_PSI' in data:
        pressure_psi = float(data['pressure_PSI'])
    elif 'pressure_kPa' in data:
        pressure_psi = float(data['pressure_kPa']) * 0.145038
    elif 'pressure_bar' in data:
        pressure_psi = float(data['pressure_bar']) * 14.5038

    # Extract temperature (normalize to °C)
    temp_c = None
    if 'temperature_C' in data:
        temp_c = float(data['temperature_C'])
    elif 'temperature_F' in data:
        temp_c = (float(data['temperature_F']) - 32) * 5 / 9

    if pressure_psi is None:
        return

    if temp_c is None:
        temp_c = 0.0  # Some sensors don't report temperature

    reading = tpms.update(sensor_id, pressure_psi, temp_c)
    if reading is None:
        return

    pos = reading['position']
    topic_key = f'tpms_{pos}'
    if topic_key in TOPICS:
        mqtt_client.publish(TOPICS[topic_key], json.dumps(reading), retain=True)

    # Publish snapshot every update
    snapshot = tpms.get_snapshot()
    snapshot['ts'] = time.time()
    mqtt_client.publish(TOPICS['tpms_snapshot'], json.dumps(snapshot), retain=True)


# ═══════════════════════════════════════════════════════════════════
#  Spectrum Scanner
# ═══════════════════════════════════════════════════════════════════

def run_spectrum_scan(mqtt_client):
    """Run a broad spectrum sweep using rtl_power."""
    log.info("Starting spectrum scan...")
    try:
        # rtl_power outputs CSV: date, time, Hz_low, Hz_high, Hz_step, samples, dB values
        cmd = [
            'rtl_power',
            '-f', f'{SPECTRUM_FREQ_START}M:{SPECTRUM_FREQ_END}M:1M',
            '-g', '40',       # Gain
            '-i', '1',        # Integration interval
            '-1',             # Single sweep
        ]

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120
        )

        if result.returncode != 0:
            log.warning(f"Spectrum scan failed: {result.stderr[:200]}")
            return

        # Parse CSV output into frequency → power map
        bands = {}
        strongest = {'freq': 0, 'power': -999}

        for line in result.stdout.strip().split('\n'):
            if not line or line.startswith('#'):
                continue
            parts = line.split(',')
            if len(parts) < 7:
                continue
            try:
                freq_low = float(parts[2])
                freq_step = float(parts[4])
                db_values = [float(x.strip()) for x in parts[6:] if x.strip()]
                for i, db in enumerate(db_values):
                    freq_mhz = (freq_low + i * freq_step) / 1e6
                    band_name = classify_band(freq_mhz)
                    if band_name:
                        if band_name not in bands or db > bands[band_name]['peak_db']:
                            bands[band_name] = {
                                'freq_mhz': round(freq_mhz, 2),
                                'peak_db': round(db, 1),
                            }
                    if db > strongest['power']:
                        strongest = {'freq': round(freq_mhz, 2), 'power': round(db, 1)}
            except (ValueError, IndexError):
                continue

        scan_result = {
            'bands': bands,
            'strongest_signal': strongest,
            'scan_range_mhz': f'{SPECTRUM_FREQ_START}-{SPECTRUM_FREQ_END}',
            'ts': time.time(),
        }

        mqtt_client.publish(TOPICS['rf_spectrum'], json.dumps(scan_result), retain=True)
        log.info(f"Spectrum scan complete — strongest signal at "
                 f"{strongest['freq']} MHz ({strongest['power']} dB)")

    except FileNotFoundError:
        log.warning("rtl_power not found — spectrum scanning disabled")
    except subprocess.TimeoutExpired:
        log.warning("Spectrum scan timed out")
    except Exception as e:
        log.error(f"Spectrum scan error: {e}")


def classify_band(freq_mhz):
    """Classify a frequency into a named band."""
    bands = [
        (87.5, 108, 'FM-Broadcast'),
        (118, 137, 'Airband'),
        (144, 148, 'Amateur-2m'),
        (150, 174, 'Marine-VHF'),
        (380, 400, 'TETRA'),
        (406, 430, 'UHF-Utility'),
        (430, 440, 'Amateur-70cm'),
        (440, 470, 'PMR-UHF'),
        (433, 434, 'ISM-433'),
        (470, 790, 'UHF-TV'),
        (860, 960, 'Cellular-GSM'),
        (1090, 1091, 'ADS-B'),
        (1575, 1576, 'GPS-L1'),
    ]
    for low, high, name in bands:
        if low <= freq_mhz <= high:
            return name
    return None


# ═══════════════════════════════════════════════════════════════════
#  Emergency Band Scanner
# ═══════════════════════════════════════════════════════════════════

def run_emergency_scan(mqtt_client):
    """Quick scan of known emergency/utility frequencies for activity."""
    results = []

    for band in EMERGENCY_BANDS:
        freq_hz = int(band['freq_mhz'] * 1e6)
        try:
            # Use rtl_power for a narrow scan around the frequency
            cmd = [
                'rtl_power',
                '-f', f'{freq_hz - 50000}:{freq_hz + 50000}:5000',
                '-g', '40',
                '-i', '1',
                '-1',
            ]
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=EMERGENCY_SCAN_DWELL + 5
            )

            peak_db = -999
            for line in result.stdout.strip().split('\n'):
                if not line or line.startswith('#'):
                    continue
                parts = line.split(',')
                try:
                    db_values = [float(x.strip()) for x in parts[6:] if x.strip()]
                    if db_values:
                        peak_db = max(peak_db, max(db_values))
                except (ValueError, IndexError):
                    continue

            # Activity threshold: signal above noise floor (-30 dB is strong)
            active = peak_db > -40
            results.append({
                'name': band['name'],
                'freq_mhz': band['freq_mhz'],
                'desc': band['desc'],
                'peak_db': round(peak_db, 1) if peak_db > -999 else None,
                'active': active,
            })

            if active:
                log.info(f"RF ACTIVITY: {band['name']} ({band['freq_mhz']} MHz) "
                         f"— {peak_db:.1f} dB — {band['desc']}")

        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            results.append({
                'name': band['name'],
                'freq_mhz': band['freq_mhz'],
                'desc': band['desc'],
                'peak_db': None,
                'active': False,
                'error': str(e),
            })

    scan_data = {
        'bands': results,
        'active_count': sum(1 for r in results if r.get('active')),
        'ts': time.time(),
    }
    mqtt_client.publish(TOPICS['rf_emergency'], json.dumps(scan_data), retain=True)


# ═══════════════════════════════════════════════════════════════════
#  MQTT Command Handler
# ═══════════════════════════════════════════════════════════════════

def on_message(client, userdata, msg):
    """Handle incoming MQTT commands for RF module."""
    try:
        data = json.loads(msg.payload)
        command = data.get('command', '')

        if command == 'tpms_learn_start':
            tpms.start_learn()
            client.publish(TOPICS['rf_status'], json.dumps({
                'mode': 'tpms_learn',
                'message': 'TPMS learn mode active — drive slowly',
                'ts': time.time(),
            }))

        elif command == 'tpms_learn_stop':
            tpms.stop_learn()
            client.publish(TOPICS['rf_status'], json.dumps({
                'mode': 'normal',
                'message': f'Learn stopped. IDs: {tpms.learned_ids}',
                'learned_ids': tpms.learned_ids,
                'ts': time.time(),
            }))

        elif command == 'tpms_assign':
            # Assign sensor IDs to positions: {sensor_id: 'fl', ...}
            assignments = data.get('assignments', {})
            for sid, pos in assignments.items():
                if pos in TPMS_POSITIONS:
                    tpms.sensor_map[str(sid)] = pos
            tpms.save_sensors()
            client.publish(TOPICS['rf_status'], json.dumps({
                'mode': 'normal',
                'message': f'TPMS sensors assigned: {assignments}',
                'ts': time.time(),
            }))

        elif command == 'tpms_auto_assign':
            # Auto-assign learned IDs in order: fl, fr, rl, rr
            if len(tpms.learned_ids) == 4:
                tpms.sensor_map = {}
                for i, sid in enumerate(tpms.learned_ids):
                    tpms.sensor_map[str(sid)] = TPMS_POSITIONS[i]
                tpms.save_sensors()
                log.info(f"TPMS auto-assigned: {tpms.sensor_map}")
                client.publish(TOPICS['rf_status'], json.dumps({
                    'mode': 'normal',
                    'message': f'Auto-assigned 4 sensors: {tpms.sensor_map}',
                    'ts': time.time(),
                }))
            else:
                client.publish(TOPICS['rf_status'], json.dumps({
                    'mode': 'error',
                    'message': f'Need exactly 4 learned IDs, have {len(tpms.learned_ids)}',
                    'ts': time.time(),
                }))

    except (json.JSONDecodeError, KeyError, TypeError) as e:
        log.warning(f"Bad RF command: {e}")


# ═══════════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════════

def main():
    global running

    log.info("DRIFTER RF Monitor starting...")

    def _handle_signal(sig, frame):
        global running
        running = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    # ── Check hardware ──
    has_sdr = check_rtl_sdr()
    has_rtl433 = check_rtl_433()

    if not has_sdr:
        log.error("No RTL-SDR device found. Plug in the dongle and restart.")
        # Keep running to accept MQTT commands (e.g., for when device is plugged in)
        # But won't start rtl_433

    if not has_rtl433:
        log.error("rtl_433 not installed. Run: sudo apt install rtl-433")

    # ── MQTT ──
    mqtt_client = mqtt.Client(client_id="drifter-rf")
    mqtt_client.on_message = on_message

    connected = False
    while not connected and running:
        try:
            mqtt_client.connect(MQTT_HOST, MQTT_PORT, 60)
            connected = True
        except Exception as e:
            log.warning(f"Waiting for MQTT broker... ({e})")
            time.sleep(3)

    mqtt_client.subscribe(TOPICS['rf_command'])
    mqtt_client.loop_start()

    # Publish status
    mqtt_client.publish(TOPICS['rf_status'], json.dumps({
        'state': 'online',
        'sdr_detected': has_sdr,
        'rtl433_installed': has_rtl433,
        'tpms_sensors': len(tpms.sensor_map),
        'ts': time.time(),
    }), retain=True)

    # ── Start rtl_433 listener ──
    stop_event = threading.Event()
    rtl_thread = None
    rtl_proc = [None]  # Mutable so inner thread can store the process

    def start_rtl_433():
        """Start rtl_433 listener thread."""
        nonlocal rtl_thread
        stop_event.clear()
        rtl_thread = threading.Thread(
            target=run_rtl_433, args=(mqtt_client, stop_event, rtl_proc), daemon=True
        )
        rtl_thread.start()

    def pause_rtl_433():
        """Stop rtl_433 temporarily for a scan. Returns True if it was running."""
        nonlocal rtl_thread
        if rtl_thread and rtl_thread.is_alive():
            stop_event.set()
            # Kill the rtl_433 subprocess directly for fast release
            if rtl_proc[0] and rtl_proc[0].poll() is None:
                try:
                    rtl_proc[0].terminate()
                    rtl_proc[0].wait(timeout=3)
                except Exception:
                    try:
                        rtl_proc[0].kill()
                    except Exception:
                        pass
            rtl_thread.join(timeout=5)
            rtl_thread = None
            time.sleep(0.5)  # Let SDR hardware release
            return True
        return False

    def resume_rtl_433():
        """Restart rtl_433 after a scan."""
        if has_sdr and has_rtl433:
            start_rtl_433()
            log.debug("rtl_433 resumed after scan")

    if has_sdr and has_rtl433:
        start_rtl_433()
        log.info("rtl_433 listener started — decoding 433 MHz signals")

    # ── Main loop: periodic scans ──
    last_spectrum = 0
    last_emergency = 0
    last_tpms_snapshot = 0

    log.info("RF Monitor is LIVE")
    if tpms.sensor_map:
        log.info(f"TPMS tracking {len(tpms.sensor_map)} sensors")
    else:
        log.info("No TPMS sensors configured — use MQTT command to learn/assign")

    while running:
        now = time.time()

        # Periodic TPMS snapshot (even when no new data — shows staleness)
        if now - last_tpms_snapshot >= 30 and tpms.sensor_map:
            snapshot = tpms.get_snapshot()
            snapshot['ts'] = now
            mqtt_client.publish(TOPICS['tpms_snapshot'],
                                json.dumps(snapshot), retain=True)
            last_tpms_snapshot = now

        # Spectrum scan (pauses rtl_433 briefly via time-division)
        if has_sdr and now - last_spectrum >= SPECTRUM_SCAN_INTERVAL:
            was_running = pause_rtl_433()
            try:
                run_spectrum_scan(mqtt_client)
            finally:
                if was_running:
                    resume_rtl_433()
            last_spectrum = now

        # Emergency band scan (pauses rtl_433 briefly)
        if has_sdr and now - last_emergency >= EMERGENCY_SCAN_INTERVAL:
            was_running = pause_rtl_433()
            try:
                run_emergency_scan(mqtt_client)
            finally:
                if was_running:
                    resume_rtl_433()
            last_emergency = now

        time.sleep(1)

    # ── Cleanup ──
    log.info("Shutting down RF Monitor...")
    stop_event.set()
    if rtl_thread and rtl_thread.is_alive():
        rtl_thread.join(timeout=5)

    mqtt_client.publish(TOPICS['rf_status'], json.dumps({
        'state': 'offline',
        'ts': time.time(),
    }), retain=True)
    mqtt_client.loop_stop()
    mqtt_client.disconnect()
    log.info("RF Monitor stopped")


if __name__ == '__main__':
    main()

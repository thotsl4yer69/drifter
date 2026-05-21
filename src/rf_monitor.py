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
    ADSB_SCAN_INTERVAL, ADSB_SCAN_DURATION, ADSB_JSON_DIR, DUMP1090_BIN,
    THRESHOLDS, make_mqtt_client,)
from hw_probe import probe_rtl_sdr, publish_hw_state

SDR_RESCAN_INTERVAL = 30  # seconds — poll for SDR plug/unplug

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [RF] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

running = True

# Cooperative SDR hand-off with peer services (rfaudio).
# `held_external` gates this module's own periodic scans while a peer
# holds the SDR; `scan_proc` lets MQTT pause interrupt an in-flight scan.
_rtl_control: dict = {'pause': None, 'resume': None,
                      'held_external': False, 'scan_proc': None}

# Cooperative scheduler interrupt + dongle lock.
# `_interrupt` wakes the main loop so a force command can yield the dongle.
# `_dongle_lock` serializes ad-hoc rtl_power sweeps against the scheduler.
# `_force_pending` signals the main loop to skip its remaining sleep slice.
_interrupt = threading.Event()
_dongle_lock = threading.Lock()
TPMS_HARVEST_PROGRESS_INTERVAL = 5.0


def _kill_active_scan() -> None:
    proc = _rtl_control.get('scan_proc')
    if proc is None:
        return
    if proc.poll() is not None:
        return
    # SIGINT first — rtl_power/rtl_433 flush and close the USB handle
    # cleanly on SIGINT. SIGKILL leaves the kernel gs_usb stack wedged
    # with "device busy" until rmmod/replug.
    try:
        proc.send_signal(signal.SIGINT)
        try:
            proc.communicate(timeout=3)
            return
        except subprocess.TimeoutExpired:
            pass
    except Exception:
        pass
    try:
        proc.terminate()
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except Exception:
            pass
    except Exception:
        pass


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
#  TPMS Per-Sensor Harvest + Per-Corner Assignment
# ═══════════════════════════════════════════════════════════════════

TPMS_ASSIGNMENTS_PATH = Path('/opt/drifter/state/tpms_assignments.json')


class TPMSHarvest:
    """Collect raw TPMS hits (deduped by sensor ID) for the corner-pair wizard."""

    def __init__(self):
        self.active = False
        self.start_ts = 0.0
        # {sensor_id: {samples: int, last_pressure_psi, last_temp_c,
        #              last_rssi, last_ts, first_ts}}
        self.sensors = {}

    def start(self):
        self.active = True
        self.start_ts = time.time()
        self.sensors = {}
        log.info("TPMS HARVEST started — collecting all TPMS hits")

    def stop(self):
        self.active = False
        log.info(f"TPMS HARVEST stopped — {len(self.sensors)} unique sensor IDs")

    def record(self, sensor_id, pressure_psi, temp_c, rssi):
        if not self.active:
            return
        sid = str(sensor_id)
        now = time.time()
        entry = self.sensors.get(sid)
        if entry is None:
            entry = {
                'samples': 0,
                'first_ts': now,
                'last_pressure_psi': None,
                'last_temp_c': None,
                'last_rssi': None,
                'last_ts': now,
            }
            self.sensors[sid] = entry
        entry['samples'] += 1
        entry['last_pressure_psi'] = (round(pressure_psi, 1)
                                       if pressure_psi is not None else None)
        entry['last_temp_c'] = (round(temp_c, 1)
                                 if temp_c is not None else None)
        entry['last_rssi'] = rssi
        entry['last_ts'] = now

    def snapshot(self):
        return {
            'active': self.active,
            'start_ts': self.start_ts,
            'ids_seen': sorted(self.sensors.keys()),
            'samples_per_id': {sid: e['samples']
                               for sid, e in self.sensors.items()},
            'sensors': {sid: dict(e) for sid, e in self.sensors.items()},
            'ts': time.time(),
        }


tpms_harvest = TPMSHarvest()


# ═══════════════════════════════════════════════════════════════════
#  TPMS Delta Capture (corner-pair wizard)
# ═══════════════════════════════════════════════════════════════════

# Pressure delta (kPa) above which a sensor's drop from baseline is
# treated as a candidate match for the corner under test. 5 kPa matches
# the existing "deflate threshold" used in the operator workflow.
TPMS_DELTA_THRESHOLD_KPA = 5.0
TPMS_DELTA_WINDOW_S = 30
TPMS_DELTA_PROGRESS_INTERVAL = 2.0


class TPMSDeltaCapture:
    """Bounded window that flags TPMS sensors whose pressure drops below baseline.

    Used by the cockpit wizard: the operator presses "Start FL", then
    physically deflates the front-left tire. While this window is open
    we listen for every TPMS hit and rank sensors by negative delta from
    the operator-supplied baseline_kpa. The candidate with the largest
    negative delta is the best match — but we never auto-assign;
    tpms_assign_corner remains the confirm step.
    """

    def __init__(self):
        self.active = False
        self.corner = ''          # 'FL'|'FR'|'RL'|'RR'
        self.baseline_kpa = 0.0
        self.start_ts = 0.0
        # {sensor_id: {current_kpa, delta_kpa, rssi, samples, last_ts}}
        self.candidates = {}

    def start(self, corner: str, baseline_kpa: float):
        self.active = True
        self.corner = corner
        self.baseline_kpa = float(baseline_kpa)
        self.start_ts = time.time()
        self.candidates = {}
        log.info(
            "TPMS DELTA CAPTURE started — corner=%s baseline=%.1fkPa",
            corner, baseline_kpa,
        )

    def stop(self):
        self.active = False
        log.info(
            "TPMS DELTA CAPTURE stopped — %d candidate(s)",
            len(self.candidates),
        )

    def record(self, sensor_id, pressure_psi, rssi):
        """Feed a TPMS hit into the window. pressure_psi may be None."""
        if not self.active or pressure_psi is None:
            return
        # Convert PSI → kPa to match baseline units the operator entered.
        current_kpa = float(pressure_psi) / 0.145038
        delta_kpa = current_kpa - self.baseline_kpa
        sid = str(sensor_id)
        now = time.time()
        entry = self.candidates.get(sid)
        if entry is None:
            entry = {
                'sensor_id': sid,
                'current_kpa': round(current_kpa, 1),
                'delta_kpa': round(delta_kpa, 1),
                'rssi': rssi,
                'samples': 0,
                'last_ts': now,
            }
            self.candidates[sid] = entry
        entry['current_kpa'] = round(current_kpa, 1)
        entry['delta_kpa'] = round(delta_kpa, 1)
        entry['rssi'] = rssi
        entry['samples'] += 1
        entry['last_ts'] = now

    def elapsed_s(self) -> int:
        if not self.active:
            return 0
        return int(time.time() - self.start_ts)

    def remaining_s(self) -> int:
        if not self.active:
            return 0
        return max(0, TPMS_DELTA_WINDOW_S - self.elapsed_s())

    def is_expired(self) -> bool:
        return self.active and self.elapsed_s() >= TPMS_DELTA_WINDOW_S

    def best_match(self):
        """Sensor with the largest negative delta past the threshold, else None."""
        flagged = [c for c in self.candidates.values()
                   if c['delta_kpa'] <= -TPMS_DELTA_THRESHOLD_KPA]
        if not flagged:
            return None
        return min(flagged, key=lambda c: c['delta_kpa'])

    def snapshot(self) -> dict:
        # Stable sort: most-negative delta first so the cockpit can render
        # candidates ranked without re-sorting on every progress publish.
        ranked = sorted(
            (dict(c) for c in self.candidates.values()),
            key=lambda c: c['delta_kpa'],
        )
        return {
            'active': self.active,
            'corner': self.corner,
            'baseline_kpa': round(self.baseline_kpa, 1),
            'elapsed_s': self.elapsed_s(),
            'remaining_s': self.remaining_s(),
            'candidates': ranked,
            'ts': time.time(),
        }


tpms_delta = TPMSDeltaCapture()


def load_tpms_assignments():
    """Read /opt/drifter/state/tpms_assignments.json. Returns dict or {}."""
    try:
        if TPMS_ASSIGNMENTS_PATH.exists():
            return json.loads(TPMS_ASSIGNMENTS_PATH.read_text())
    except (OSError, json.JSONDecodeError) as e:
        log.warning(f"TPMS assignments read failed: {e}")
    return {}


def save_tpms_assignments(data):
    """Atomic-write the per-corner assignment file."""
    try:
        TPMS_ASSIGNMENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = TPMS_ASSIGNMENTS_PATH.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(TPMS_ASSIGNMENTS_PATH)
        return True
    except OSError as e:
        log.warning(f"TPMS assignments save failed: {e}")
        return False


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

    # Harvest hits (deduped by ID, with RSSI) for the corner-pair wizard.
    rssi = data.get('rssi', data.get('snr'))
    tpms_harvest.record(sensor_id, pressure_psi, temp_c, rssi)
    # Delta-capture wizard (cockpit-driven). Feed every hit; the window
    # filter lives inside TPMSDeltaCapture.record so we don't gate twice.
    tpms_delta.record(sensor_id, pressure_psi, rssi)

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

        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        _rtl_control['scan_proc'] = proc
        try:
            stdout, stderr = proc.communicate(timeout=120)
        finally:
            _rtl_control['scan_proc'] = None

        if proc.returncode != 0:
            if _rtl_control.get('held_external'):
                log.info("Spectrum scan interrupted by peer")
            else:
                log.warning(f"Spectrum scan failed: {stderr[:200]}")
            return

        result = type('R', (), {'stdout': stdout, 'stderr': stderr,
                                'returncode': proc.returncode})

        # Parse CSV output into frequency → power map
        bands = {}
        strongest = {'freq': 0, 'power': -999}
        stdout_lines = result.stdout.strip().split('\n')

        for line in stdout_lines:
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

        scan_range_mhz = f'{SPECTRUM_FREQ_START}-{SPECTRUM_FREQ_END}'
        scan_result = {
            'bands': bands,
            'strongest_signal': strongest,
            'scan_range_mhz': scan_range_mhz,
            'ts': time.time(),
        }

        # Not retained: spectrum sweeps are transient and re-run on a
        # schedule. A retained scan can imply current activity at bands
        # that have since gone quiet.
        mqtt_client.publish(TOPICS['rf_spectrum'], json.dumps(scan_result))
        # Downsampled summary on a separate topic — what the cockpit WS
        # subscribes to. 1742 raw bins → ≤256 grouped bins per push.
        _publish_spectrum_summary(mqtt_client, stdout_lines, scan_range_mhz)
        log.info(f"Spectrum scan complete — strongest signal at "
                 f"{strongest['freq']} MHz ({strongest['power']} dB)")

    except FileNotFoundError:
        log.warning("rtl_power not found — spectrum scanning disabled")
    except subprocess.TimeoutExpired:
        log.warning("Spectrum scan timed out")
    except Exception as e:
        log.error(f"Spectrum scan error: {e}")


# Maximum bins published on drifter/rf/spectrum/summary. The full sweep
# (24M–1766M @ 1M step → 1742 bins) was being shipped to every WS client
# on every refresh — a 30× saving with negligible visual loss.
SPECTRUM_SUMMARY_MAX_BINS = 256


def downsample_spectrum(bins, max_bins: int = SPECTRUM_SUMMARY_MAX_BINS):
    """Reduce a [{freq_hz, db}] series to at most max_bins groups.

    Each output bin reports {freq_hz, level_db_min, level_db_max,
    level_db_mean} for its underlying range. The freq_hz of the output
    bin is the freq_hz of its first input bin (group start), so the
    cockpit can axis-label without round-trip metadata.

    Input shape: list of dicts with 'freq_hz' (number) and 'db' (number).
    Bins with a non-finite db are skipped — they would NaN-poison the mean.
    """
    if not bins:
        return []
    clean = [b for b in bins
             if isinstance(b, dict)
             and b.get('db') is not None
             and isinstance(b.get('freq_hz'), (int, float))]
    n = len(clean)
    if n == 0:
        return []
    if n <= max_bins:
        # Already small enough — emit one group per input bin.
        out = []
        for b in clean:
            db = float(b['db'])
            out.append({
                'freq_hz': float(b['freq_hz']),
                'level_db_min': round(db, 1),
                'level_db_max': round(db, 1),
                'level_db_mean': round(db, 1),
            })
        return out
    # Use ceiling division so we never produce MORE than max_bins groups.
    group = (n + max_bins - 1) // max_bins
    out = []
    for i in range(0, n, group):
        chunk = clean[i:i + group]
        dbs = [float(b['db']) for b in chunk]
        out.append({
            'freq_hz': float(chunk[0]['freq_hz']),
            'level_db_min': round(min(dbs), 1),
            'level_db_max': round(max(dbs), 1),
            'level_db_mean': round(sum(dbs) / len(dbs), 1),
        })
    return out


def _build_spectrum_bins(stdout_lines):
    """Parse rtl_power CSV stdout into a flat [{freq_hz, db}] series."""
    bins = []
    for line in stdout_lines:
        if not line or line.startswith('#'):
            continue
        parts = line.split(',')
        if len(parts) < 7:
            continue
        try:
            freq_low = float(parts[2])
            freq_step = float(parts[4])
            db_values = [float(x.strip()) for x in parts[6:] if x.strip()]
        except (ValueError, IndexError):
            continue
        for i, db in enumerate(db_values):
            bins.append({'freq_hz': freq_low + i * freq_step, 'db': db})
    return bins


# Latest spectrum summary — cached so /api/rf/spectrum/summary can serve
# without round-tripping MQTT. Updated by both run_spectrum_scan and
# _force_spectrum when they publish a new sweep.
_latest_spectrum_summary: dict = {}


def _publish_spectrum_summary(client, stdout_lines, scan_range_mhz, forced=False):
    """Build + publish the downsampled summary alongside the full sweep."""
    try:
        bins = _build_spectrum_bins(stdout_lines)
        summary_bins = downsample_spectrum(bins)
        summary = {
            'bins': summary_bins,
            'bin_count': len(summary_bins),
            'source_bin_count': len(bins),
            'scan_range_mhz': scan_range_mhz,
            'forced': forced,
            'ts': time.time(),
        }
        client.publish('drifter/rf/spectrum/summary', json.dumps(summary))
        # Update the module-level cache for the REST handler.
        _latest_spectrum_summary.clear()
        _latest_spectrum_summary.update(summary)
    except Exception as e:
        log.warning(f"spectrum summary publish failed: {e}")


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
        if _rtl_control.get('held_external'):
            break
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
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            _rtl_control['scan_proc'] = proc
            try:
                stdout, stderr = proc.communicate(
                    timeout=EMERGENCY_SCAN_DWELL + 5
                )
            finally:
                _rtl_control['scan_proc'] = None
            result = type('R', (), {'stdout': stdout, 'stderr': stderr,
                                    'returncode': proc.returncode})
            if proc.returncode != 0 and _rtl_control.get('held_external'):
                break

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
    # Not retained: emergency-band activity is a moment-in-time signal.
    # A retained scan from minutes ago should not flag an ongoing event.
    mqtt_client.publish(TOPICS['rf_emergency'], json.dumps(scan_data))


# ═══════════════════════════════════════════════════════════════════
#  ADS-B Aircraft Scanner
# ═══════════════════════════════════════════════════════════════════

def check_dump1090() -> bool:
    """Check if dump1090 is available."""
    try:
        subprocess.run(
            [DUMP1090_BIN, '--help'],
            capture_output=True, timeout=5
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def run_adsb_scan(mqtt_client):
    """
    Scan for ADS-B aircraft using dump1090.
    Runs for ADSB_SCAN_DURATION seconds, reads aircraft.json, publishes results.
    Caller is responsible for pausing/resuming rtl_433 around this call.
    """
    log.info("Starting ADS-B scan...")
    ADSB_JSON_DIR.mkdir(parents=True, exist_ok=True)
    aircraft_file = ADSB_JSON_DIR / 'aircraft.json'

    proc = None
    try:
        # readsb (Kali's dump1090 fork) needs --device-type rtlsdr before
        # the SDR-specific --device flag; the rest of the flags are
        # compatible with dump1090's CLI.
        cmd = [
            DUMP1090_BIN,
            '--device-type', 'rtlsdr',
            '--device', '0',
            '--no-interactive',
            '--quiet',
            '--write-json', str(ADSB_JSON_DIR),
            '--write-json-every', '1',
        ]
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        _rtl_control['scan_proc'] = proc

        # Break early if a peer service takes the SDR.
        for _ in range(int(ADSB_SCAN_DURATION)):
            if _rtl_control.get('held_external') or proc.poll() is not None:
                break
            time.sleep(1)

    except FileNotFoundError:
        log.warning(f"dump1090 not found — ADS-B scanning disabled. "
                    "Install with: sudo apt install dump1090-fa")
        return
    except Exception as e:
        log.warning(f"ADS-B scan error: {e}")
        return
    finally:
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        _rtl_control['scan_proc'] = None

    # Read results
    try:
        if not aircraft_file.exists():
            log.info("ADS-B scan complete — no aircraft data written")
            return

        data = json.loads(aircraft_file.read_text())
        aircraft = data.get('aircraft', [])

        # Filter to aircraft seen recently (within the scan window)
        visible = [
            a for a in aircraft
            if a.get('seen', 999) < ADSB_SCAN_DURATION
        ]

        result = {
            'aircraft': visible,
            'count': len(visible),
            'messages': data.get('messages', 0),
            'scan_duration_s': ADSB_SCAN_DURATION,
            'ts': time.time(),
        }

        # Not retained: ADS-B aircraft positions are transient. Even with
        # the per-aircraft 'seen' field, a retained scan from a previous
        # location/time will surface as "current overhead" to a fresh
        # subscriber. Matches the feeds.py policy on aircraft snapshots.
        mqtt_client.publish(TOPICS['rf_adsb'], json.dumps(result))

        if visible:
            callsigns = [a.get('flight', a.get('hex', '?')).strip()
                         for a in visible[:5]]
            log.info(f"ADS-B: {len(visible)} aircraft — {', '.join(callsigns)}")
        else:
            log.info("ADS-B scan complete — no aircraft detected")

    except Exception as e:
        log.warning(f"ADS-B result parse error: {e}")


# ═══════════════════════════════════════════════════════════════════
#  Force Spectrum Scan
# ═══════════════════════════════════════════════════════════════════

def _force_spectrum(client, params):
    """Interrupt the scheduler and run an out-of-band rtl_power sweep.

    Acquires _dongle_lock, kills any active scan, runs a full sweep,
    publishes the result to drifter/rf/spectrum, and releases the lock.
    """
    log.info("force spectrum sweep — yielding dongle to ad-hoc sweep")
    # Wake the scheduler so it stops sleeping and gives up its dongle slot.
    _interrupt.set()

    if not _dongle_lock.acquire(timeout=5.0):
        log.warning("force_spectrum: could not acquire dongle lock in 5s")
        client.publish(TOPICS['rf_error'] if 'rf_error' in TOPICS else
                       'drifter/rf/error', json.dumps({
            'command': 'force_spectrum',
            'error': 'dongle locked — try again',
            'ts': time.time(),
        }))
        return

    proc = None
    try:
        # Make the scheduler-side rtl_433 release the SDR cleanly.
        _kill_active_scan()
        pause_fn = _rtl_control.get('pause')
        if callable(pause_fn):
            try:
                pause_fn()
            except Exception as e:
                log.debug(f"force_spectrum pause hook error: {e}")

        cmd = [
            'rtl_power',
            '-f', '24M:1766M:1M',
            '-i', '2',
            '-g', '40',
            '-1',
        ]
        log.info(f"force_spectrum: {' '.join(cmd)}")
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )
        _rtl_control['scan_proc'] = proc

        stdout_lines = []
        # Stream stdout line-by-line. Each line is a CSV bin row from
        # rtl_power; partial publishes let the UI show progress.
        try:
            for line in proc.stdout:
                if line is None:
                    break
                line = line.rstrip('\n')
                if not line:
                    continue
                stdout_lines.append(line)
                try:
                    client.publish('drifter/rf/spectrum/partial', line)
                except Exception:
                    pass
            proc.wait(timeout=120)
        except subprocess.TimeoutExpired:
            log.warning("force_spectrum: rtl_power timed out — killing")
            try:
                proc.send_signal(signal.SIGINT)
                proc.communicate(timeout=3)
            except Exception:
                try:
                    proc.terminate()
                except Exception:
                    pass

        # Parse the assembled stdout into the same shape run_spectrum_scan
        # publishes, so dashboard subscribers see one canonical schema.
        bands = {}
        strongest = {'freq': 0, 'power': -999}
        for line in stdout_lines:
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
                        if (band_name not in bands
                                or db > bands[band_name]['peak_db']):
                            bands[band_name] = {
                                'freq_mhz': round(freq_mhz, 2),
                                'peak_db': round(db, 1),
                            }
                    if db > strongest['power']:
                        strongest = {'freq': round(freq_mhz, 2),
                                     'power': round(db, 1)}
            except (ValueError, IndexError):
                continue

        scan_result = {
            'bands': bands,
            'strongest_signal': strongest,
            'scan_range_mhz': '24-1766',
            'forced': True,
            'ts': time.time(),
        }
        client.publish(TOPICS['rf_spectrum'], json.dumps(scan_result))
        # Same downsampled summary the periodic sweep publishes.
        _publish_spectrum_summary(client, stdout_lines, '24-1766', forced=True)
        log.info(
            "force_spectrum done — strongest %.2f MHz @ %.1f dB",
            strongest['freq'], strongest['power'],
        )

    except FileNotFoundError:
        log.warning("force_spectrum: rtl_power not installed")
        client.publish('drifter/rf/error', json.dumps({
            'command': 'force_spectrum',
            'error': 'rtl_power not installed',
            'ts': time.time(),
        }))
    except Exception as e:
        log.error(f"force_spectrum error: {e}")
        client.publish('drifter/rf/error', json.dumps({
            'command': 'force_spectrum',
            'error': str(e),
            'ts': time.time(),
        }))
    finally:
        _rtl_control['scan_proc'] = None
        # Re-arm the scheduler — clear the interrupt so the loop sleeps again.
        _interrupt.clear()
        _dongle_lock.release()
        resume_fn = _rtl_control.get('resume')
        if callable(resume_fn) and not _rtl_control.get('held_external'):
            try:
                resume_fn()
            except Exception as e:
                log.debug(f"force_spectrum resume hook error: {e}")


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

        elif command == 'pause_rtl_433':
            # Flag first so the next scan tick is gated; then interrupt the
            # current scan and stop rtl_433.
            _rtl_control['held_external'] = True
            _kill_active_scan()
            fn = _rtl_control.get('pause')
            if callable(fn):
                was_running = fn()
                log.info("rtl_433 paused via MQTT (was_running=%s)", was_running)

        elif command == 'resume_rtl_433':
            _rtl_control['held_external'] = False
            fn = _rtl_control.get('resume')
            if callable(fn):
                fn()
                log.info("rtl_433 resumed via MQTT")

        elif command == 'force_spectrum':
            # Run in its own thread so the MQTT network thread (paho callback)
            # returns immediately — the sweep takes 60–90s.
            threading.Thread(
                target=_force_spectrum, args=(client, data),
                daemon=True, name='force-spectrum',
            ).start()

        elif command == 'tpms_harvest_start':
            tpms_harvest.start()
            client.publish(TOPICS['rf_status'], json.dumps({
                'mode': 'tpms_harvest',
                'message': 'TPMS harvest active — collecting all hits',
                'ts': time.time(),
            }))

        elif command == 'tpms_harvest_stop':
            tpms_harvest.stop()
            final = tpms_harvest.snapshot()
            client.publish('drifter/rf/tpms/harvest', json.dumps(final))
            client.publish(TOPICS['rf_status'], json.dumps({
                'mode': 'normal',
                'message': f'TPMS harvest stopped. {len(final["ids_seen"])} IDs.',
                'ts': time.time(),
            }))

        elif command == 'tpms_assign_corner':
            sid = str(data.get('sensor_id', '')).strip()
            corner = str(data.get('corner', '')).lower().strip()
            corner_alias = {'fl': 'fl', 'fr': 'fr', 'rl': 'rl', 'rr': 'rr'}
            mapped = corner_alias.get(corner)
            if not sid or not mapped:
                client.publish(TOPICS['rf_status'], json.dumps({
                    'mode': 'error',
                    'message': 'tpms_assign_corner needs sensor_id and corner FL|FR|RL|RR',
                    'ts': time.time(),
                }))
            else:
                assignments = load_tpms_assignments()
                # Remove any previous corner this sensor occupied.
                for k, v in list(assignments.items()):
                    if v == sid:
                        del assignments[k]
                assignments[mapped.upper()] = sid
                save_tpms_assignments(assignments)
                # Keep the runtime sensor_map in sync so live readings
                # land on the right position immediately.
                for k, v in list(tpms.sensor_map.items()):
                    if v == mapped:
                        del tpms.sensor_map[k]
                tpms.sensor_map[sid] = mapped
                tpms.save_sensors()
                client.publish(TOPICS['rf_status'], json.dumps({
                    'mode': 'normal',
                    'message': f'Assigned {sid} -> {mapped.upper()}',
                    'assignments': assignments,
                    'ts': time.time(),
                }))

        elif command == 'tpms_delta_capture':
            # Cockpit wizard step: operator deflates the named corner
            # by 5+ kPa within a 30s window. We listen for TPMS hits
            # and rank sensors by negative delta from baseline_kpa.
            # Final summary publishes at window close; no auto-assign.
            corner = str(data.get('corner', '')).upper().strip()
            try:
                baseline_kpa = float(data.get('baseline_kpa'))
            except (TypeError, ValueError):
                baseline_kpa = None
            if (corner not in {'FL', 'FR', 'RL', 'RR'}
                    or baseline_kpa is None
                    or not (50.0 <= baseline_kpa <= 500.0)):
                client.publish(TOPICS['rf_status'], json.dumps({
                    'mode': 'error',
                    'message': ('tpms_delta_capture needs corner FL|FR|RL|RR '
                                'and baseline_kpa in [50, 500]'),
                    'ts': time.time(),
                }))
            else:
                tpms_delta.start(corner, baseline_kpa)
                # Immediate progress publish so the cockpit can confirm
                # the window opened without waiting on the 2s tick.
                client.publish('drifter/rf/tpms/delta',
                               json.dumps(tpms_delta.snapshot()))
                client.publish(TOPICS['rf_status'], json.dumps({
                    'mode': 'tpms_delta',
                    'message': (f'Delta capture {corner} '
                                f'baseline={baseline_kpa:.1f}kPa — '
                                f'{TPMS_DELTA_WINDOW_S}s window open'),
                    'ts': time.time(),
                }))

        elif command == 'tpms_clear_assignments':
            save_tpms_assignments({})
            tpms.sensor_map = {}
            tpms.save_sensors()
            client.publish(TOPICS['rf_status'], json.dumps({
                'mode': 'normal',
                'message': 'TPMS assignments cleared',
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
    has_dump1090 = check_dump1090()

    if not has_sdr:
        log.error("No RTL-SDR device found. Plug in the dongle and restart.")
        # Keep running to accept MQTT commands (e.g., for when device is plugged in)
        # But won't start rtl_433

    if not has_rtl433:
        log.error("rtl_433 not installed. Run: sudo apt install rtl-433")

    # ── MQTT ──
    mqtt_client = make_mqtt_client("drifter-rf")
    mqtt_client.on_message = on_message

    connected = False
    while not connected and running:
        try:
            mqtt_client.connect(MQTT_HOST, MQTT_PORT, 60)
            connected = True
        except Exception as e:
            log.warning(f"Waiting for MQTT broker... ({e})")
            time.sleep(3)

    if has_dump1090:
        log.info("dump1090 detected — ADS-B aircraft tracking enabled")
    else:
        log.info("dump1090 not found — ADS-B disabled (install dump1090-fa)")

    # Publish status
    mqtt_client.publish(TOPICS['rf_status'], json.dumps({
        'state': 'online',
        'sdr_detected': has_sdr,
        'rtl433_installed': has_rtl433,
        'dump1090_installed': has_dump1090,
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
        # Kill the subprocess independently of thread state — it can outlive
        # the reader thread (EOF on stdout doesn't release the USB handle).
        nonlocal rtl_thread
        was_active = False
        # Kill the rtl_433 subprocess if it's alive — even if rtl_thread is
        # already dead. Without this, an orphaned rtl_433 keeps the SDR.
        proc = rtl_proc[0]
        if proc and proc.poll() is None:
            was_active = True
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        if rtl_thread and rtl_thread.is_alive():
            was_active = True
            stop_event.set()
            rtl_thread.join(timeout=5)
        rtl_thread = None
        rtl_proc[0] = None
        if was_active:
            time.sleep(0.5)  # Let SDR hardware release
        return was_active

    def resume_rtl_433():
        # Suppress while peer holds SDR — otherwise scan-finally races
        # the peer and spawns an orphan rtl_433 outside proc_ref tracking.
        if _rtl_control.get('held_external'):
            return
        if rtl_thread and rtl_thread.is_alive():
            return
        if has_sdr and has_rtl433:
            start_rtl_433()

    if has_sdr and has_rtl433:
        start_rtl_433()
        log.info("rtl_433 listener started — decoding 433 MHz signals")

    # Expose pause/resume to MQTT-side callers (rfaudio) — see _rtl_control.
    # Must be set BEFORE loop_start() so an early MQTT command can't slip
    # through the on_message handler with a None lookup.
    _rtl_control['pause'] = pause_rtl_433
    _rtl_control['resume'] = resume_rtl_433

    mqtt_client.subscribe(TOPICS['rf_command'])
    mqtt_client.loop_start()

    # Publish initial drifter/hw/rtl_sdr snapshot so the dashboard knows
    # the current state immediately (lsusb-based probe — non-invasive).
    publish_hw_state(mqtt_client, 'rtl_sdr', probe_rtl_sdr())

    # ── Main loop: periodic scans ──
    last_spectrum = 0
    last_emergency = 0
    last_tpms_snapshot = 0
    last_adsb = 0
    last_sdr_rescan = time.time()
    last_harvest_progress = 0.0
    last_delta_progress = 0.0

    # Boot-time: hydrate the runtime sensor_map from the on-disk per-corner
    # assignments so a fresh service restart still surfaces paired wheels.
    boot_assignments = load_tpms_assignments()
    if boot_assignments:
        # Reverse {FL: sid} → {sid: fl}. Mirrors sensor_map convention.
        for corner, sid in boot_assignments.items():
            tpms.sensor_map[str(sid)] = corner.lower()
        log.info(f"Restored {len(boot_assignments)} TPMS corner assignments")

    log.info("RF Monitor is LIVE")
    if tpms.sensor_map:
        log.info(f"TPMS tracking {len(tpms.sensor_map)} sensors")
    else:
        log.info("No TPMS sensors configured — use MQTT command to learn/assign")

    while running:
        now = time.time()

        # ── SDR hot-plug rescan ──
        # When the SDR is plugged in mid-flight, detect it and start rtl_433
        # without requiring `systemctl restart drifter-rf`. When it disappears,
        # stop the worker cleanly so we don't spin on a dead handle.
        if now - last_sdr_rescan >= SDR_RESCAN_INTERVAL:
            last_sdr_rescan = now
            probe = probe_rtl_sdr()
            present_now = probe['connected']
            if present_now and not has_sdr:
                # Plugged in — confirm with rtl_test (invasive but no thread holding it)
                if check_rtl_sdr():
                    has_sdr = True
                    log.info("RTL-SDR detected via rescan — starting rtl_433")
                    if has_rtl433:
                        start_rtl_433()
                    mqtt_client.publish(TOPICS['rf_status'], json.dumps({
                        'state': 'online', 'sdr_detected': True,
                        'rtl433_installed': has_rtl433,
                        'dump1090_installed': has_dump1090,
                        'tpms_sensors': len(tpms.sensor_map),
                        'ts': now,
                    }), retain=True)
                    publish_hw_state(mqtt_client, 'rtl_sdr', probe)
            elif not present_now and has_sdr:
                log.warning("RTL-SDR unplugged — stopping rtl_433")
                pause_rtl_433()
                has_sdr = False
                publish_hw_state(mqtt_client, 'rtl_sdr', probe)

        # Periodic TPMS snapshot (even when no new data — shows staleness)
        if now - last_tpms_snapshot >= 30 and tpms.sensor_map:
            snapshot = tpms.get_snapshot()
            snapshot['ts'] = now
            mqtt_client.publish(TOPICS['tpms_snapshot'],
                                json.dumps(snapshot), retain=True)
            last_tpms_snapshot = now

        # Skip periodic SDR scans while a peer service holds the device.
        sdr_available = has_sdr and not _rtl_control.get('held_external')

        # Spectrum scan (pauses rtl_433 briefly via time-division)
        if sdr_available and now - last_spectrum >= SPECTRUM_SCAN_INTERVAL:
            was_running = pause_rtl_433()
            try:
                run_spectrum_scan(mqtt_client)
            finally:
                if was_running:
                    resume_rtl_433()
            last_spectrum = now

        # Emergency band scan (pauses rtl_433 briefly)
        if sdr_available and now - last_emergency >= EMERGENCY_SCAN_INTERVAL:
            was_running = pause_rtl_433()
            try:
                run_emergency_scan(mqtt_client)
            finally:
                if was_running:
                    resume_rtl_433()
            last_emergency = now

        # ADS-B aircraft scan (pauses rtl_433 for ADSB_SCAN_DURATION seconds)
        if sdr_available and has_dump1090 and now - last_adsb >= ADSB_SCAN_INTERVAL:
            was_running = pause_rtl_433()
            try:
                run_adsb_scan(mqtt_client)
            finally:
                if was_running:
                    resume_rtl_433()
            last_adsb = now

        # TPMS harvest progress publish — every 5s while harvest is active.
        if tpms_harvest.active and (
                now - last_harvest_progress >= TPMS_HARVEST_PROGRESS_INTERVAL):
            mqtt_client.publish('drifter/rf/tpms/harvest',
                                 json.dumps(tpms_harvest.snapshot()))
            last_harvest_progress = now

        # TPMS delta-capture wizard — progress every 2s, final summary
        # when the 30s window expires. The summary flags the best-match
        # sensor (largest negative delta past 5 kPa) for the cockpit
        # to surface as a confirm prompt; tpms_assign_corner remains
        # the authoritative writer.
        if tpms_delta.active:
            if now - last_delta_progress >= TPMS_DELTA_PROGRESS_INTERVAL:
                mqtt_client.publish('drifter/rf/tpms/delta',
                                    json.dumps(tpms_delta.snapshot()))
                last_delta_progress = now
            if tpms_delta.is_expired():
                snap = tpms_delta.snapshot()
                best = tpms_delta.best_match()
                snap['final'] = True
                snap['active'] = False
                snap['best_match'] = best
                tpms_delta.stop()
                mqtt_client.publish('drifter/rf/tpms/delta', json.dumps(snap))

        # Interruptible sleep so force_spectrum can yield the dongle quickly.
        # _interrupt is set by force_spectrum (and any future ad-hoc command);
        # if wait returns True we yield immediately so the lock can be taken.
        if _interrupt.wait(timeout=1):
            # Don't clear here — the force_spectrum finally-block owns the reset.
            pass

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

#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Auto-Calibration
Learns baseline fuel trims, idle RPM, and voltage after warm-up.
Run once after install, then periodically to track sensor drift.

Usage:
  python3 calibrate.py             # Interactive calibration
  python3 calibrate.py --auto      # Non-interactive (runs for 5 min after warm-up)
  python3 calibrate.py --status    # Show current calibration

UNCAGED TECHNOLOGY — EST 1991
"""

import json
import time
import signal
import logging
import argparse
from collections import deque
from pathlib import Path
import paho.mqtt.client as mqtt

from config import (
    MQTT_HOST, MQTT_PORT, CALIBRATION_FILE, CALIBRATION_DEFAULTS,
    LEVEL_NAMES, TOPICS
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [CALIBRATE] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

# ── Calibration Parameters ──
WARMUP_COOLANT_MIN = 85       # °C — engine must be warm
IDLE_RPM_MAX = 1000           # Must be at idle
SAMPLE_DURATION = 300         # 5 minutes of samples
MIN_SAMPLES = 200             # At least this many readings per metric

CYAN = "\033[0;36m"
GREEN = "\033[0;32m"
AMBER = "\033[0;33m"
RED = "\033[0;31m"
NC = "\033[0m"


class CalibrationCollector:
    """Collects telemetry samples for calibration."""

    def __init__(self):
        self.rpm = deque(maxlen=2000)
        self.coolant = deque(maxlen=2000)
        self.stft1 = deque(maxlen=2000)
        self.stft2 = deque(maxlen=2000)
        self.ltft1 = deque(maxlen=2000)
        self.ltft2 = deque(maxlen=2000)
        self.voltage = deque(maxlen=2000)
        self.start_time = None
        self.idle_samples = 0

    def on_message(self, client, userdata, msg):
        """Ingest MQTT telemetry."""
        try:
            data = json.loads(msg.payload)
            value = data.get('value')
            if value is None:
                return

            topic = msg.topic
            if topic.endswith('/rpm'):
                self.rpm.append(value)
                if value < IDLE_RPM_MAX:
                    self.idle_samples += 1
            elif topic.endswith('/coolant'):
                self.coolant.append(value)
            elif topic.endswith('/stft1'):
                self.stft1.append(value)
            elif topic.endswith('/stft2'):
                self.stft2.append(value)
            elif topic.endswith('/ltft1'):
                self.ltft1.append(value)
            elif topic.endswith('/ltft2'):
                self.ltft2.append(value)
            elif topic.endswith('/voltage'):
                self.voltage.append(value)

        except (json.JSONDecodeError, KeyError):
            pass

    def is_warm(self):
        """Check if engine is at operating temperature."""
        if not self.coolant:
            return False
        return self.coolant[-1] >= WARMUP_COOLANT_MIN

    def is_idle(self):
        """Check if engine is at idle."""
        if not self.rpm:
            return False
        return self.rpm[-1] < IDLE_RPM_MAX

    def has_enough_samples(self):
        """Check if we have enough data for calibration."""
        return (
            len(self.stft1) >= MIN_SAMPLES and
            len(self.stft2) >= MIN_SAMPLES and
            len(self.voltage) >= MIN_SAMPLES and
            self.idle_samples >= MIN_SAMPLES
        )

    def compute_calibration(self):
        """Compute baseline values from collected idle samples."""
        # Filter to only idle samples (use last portion where engine was stable)
        # Take the middle 80% to reject outliers
        def trimmed_mean(data, trim=0.1):
            if not data:
                return 0.0
            sorted_data = sorted(data)
            n = len(sorted_data)
            low = int(n * trim)
            high = int(n * (1 - trim))
            if high <= low:
                return sum(sorted_data) / n
            trimmed = sorted_data[low:high]
            return sum(trimmed) / len(trimmed)

        cal = dict(CALIBRATION_DEFAULTS)
        cal['stft1_baseline'] = round(trimmed_mean(self.stft1), 2)
        cal['stft2_baseline'] = round(trimmed_mean(self.stft2), 2)
        cal['ltft1_baseline'] = round(trimmed_mean(self.ltft1), 2) if self.ltft1 else 0.0
        cal['ltft2_baseline'] = round(trimmed_mean(self.ltft2), 2) if self.ltft2 else 0.0
        cal['idle_rpm_baseline'] = round(trimmed_mean(self.rpm), 0)
        cal['voltage_baseline'] = round(trimmed_mean(self.voltage), 2)
        cal['coolant_normal'] = round(trimmed_mean(self.coolant), 1)
        cal['calibrated'] = True
        cal['calibration_date'] = time.strftime("%Y-%m-%d %H:%M:%S")
        cal['samples_collected'] = {
            'stft1': len(self.stft1),
            'stft2': len(self.stft2),
            'ltft1': len(self.ltft1),
            'ltft2': len(self.ltft2),
            'rpm': len(self.rpm),
            'voltage': len(self.voltage),
            'idle_only': self.idle_samples,
        }
        return cal


def load_calibration():
    """Load existing calibration or return defaults."""
    if CALIBRATION_FILE.exists():
        try:
            with open(CALIBRATION_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return dict(CALIBRATION_DEFAULTS)


def save_calibration(cal):
    """Write calibration to disk."""
    CALIBRATION_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CALIBRATION_FILE, 'w') as f:
        json.dump(cal, f, indent=2)
    log.info(f"Calibration saved to {CALIBRATION_FILE}")


def print_calibration(cal):
    """Pretty-print calibration data."""
    print(f"\n{CYAN}  DRIFTER CALIBRATION{NC}")
    print(f"  {'─' * 40}")

    if cal.get('calibrated'):
        print(f"  {GREEN}Calibrated: {cal['calibration_date']}{NC}")
    else:
        print(f"  {AMBER}Not calibrated — run: sudo systemctl start drifter-calibrate{NC}")
        return

    print(f"\n  Fuel Trim Baselines:")
    print(f"    STFT Bank 1:  {cal['stft1_baseline']:+.2f}%")
    print(f"    STFT Bank 2:  {cal['stft2_baseline']:+.2f}%")
    print(f"    LTFT Bank 1:  {cal['ltft1_baseline']:+.2f}%")
    print(f"    LTFT Bank 2:  {cal['ltft2_baseline']:+.2f}%")
    print(f"\n  Engine Baselines:")
    print(f"    Idle RPM:     {cal['idle_rpm_baseline']:.0f}")
    print(f"    Voltage:      {cal['voltage_baseline']:.2f}V")
    print(f"    Coolant:      {cal['coolant_normal']:.1f}°C")

    samples = cal.get('samples_collected', {})
    if samples:
        print(f"\n  Samples: {samples.get('idle_only', '?')} idle readings")
    print()


def run_calibration(auto=False):
    """Run the calibration routine."""
    collector = CalibrationCollector()
    running = True

    def _handle_signal(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    # Connect to MQTT
    client = mqtt.Client(client_id="drifter-calibrate")
    client.on_message = collector.on_message

    try:
        client.connect(MQTT_HOST, MQTT_PORT, 60)
    except Exception as e:
        log.error(f"Cannot connect to MQTT: {e}")
        log.error("Is the CAN bridge running? Start with: sudo systemctl start drifter-canbridge")
        return False

    client.subscribe("drifter/engine/#")
    client.subscribe("drifter/power/#")
    client.subscribe("drifter/vehicle/#")
    client.loop_start()

    if not auto:
        print(f"\n{CYAN}  DRIFTER AUTO-CALIBRATION{NC}")
        print(f"  {'─' * 40}")
        print(f"  Requirements:")
        print(f"    • Engine running and warmed up (coolant ≥{WARMUP_COOLANT_MIN}°C)")
        print(f"    • Car in PARK or NEUTRAL")
        print(f"    • A/C OFF, no electrical loads")
        print(f"    • Let it idle for ~5 minutes")
        print(f"\n  Press Ctrl+C to cancel.\n")

    # Phase 1: Wait for warm-up
    log.info("Waiting for engine warm-up...")
    while running and not collector.is_warm():
        if collector.coolant:
            temp = collector.coolant[-1]
            if not auto:
                print(f"\r  Coolant: {temp:.0f}°C / {WARMUP_COOLANT_MIN}°C needed  ", end='', flush=True)
        time.sleep(2)

    if not running:
        client.loop_stop()
        client.disconnect()
        return False

    if not auto:
        print(f"\n  {GREEN}Engine warm. Collecting idle data...{NC}\n")
    log.info("Engine warm — collecting calibration data")

    # Phase 2: Collect idle samples
    collector.start_time = time.monotonic()
    while running:
        elapsed = time.monotonic() - collector.start_time

        if not auto:
            rpm_str = f"{collector.rpm[-1]:.0f}" if collector.rpm else "—"
            stft1_str = f"{collector.stft1[-1]:+.1f}" if collector.stft1 else "—"
            stft2_str = f"{collector.stft2[-1]:+.1f}" if collector.stft2 else "—"
            print(f"\r  [{elapsed:.0f}s] RPM: {rpm_str}  STFT1: {stft1_str}%  "
                  f"STFT2: {stft2_str}%  Samples: {collector.idle_samples}  ",
                  end='', flush=True)

        if elapsed >= SAMPLE_DURATION and collector.has_enough_samples():
            break

        if elapsed >= SAMPLE_DURATION * 2:
            log.warning("Calibration timeout — not enough idle samples")
            if not auto:
                print(f"\n  {AMBER}Timeout. Need {MIN_SAMPLES} idle samples, "
                      f"got {collector.idle_samples}.{NC}")
            break

        time.sleep(1)

    client.loop_stop()
    client.disconnect()

    if not collector.has_enough_samples():
        log.error("Insufficient data for calibration")
        return False

    # Compute and save
    cal = collector.compute_calibration()
    save_calibration(cal)

    # Publish calibration event
    try:
        pub = mqtt.Client(client_id="drifter-cal-pub")
        pub.connect(MQTT_HOST, MQTT_PORT, 10)
        pub.publish(TOPICS['calibration'], json.dumps(cal), retain=True)
        pub.disconnect()
    except Exception:
        pass

    if not auto:
        print(f"\n\n  {GREEN}CALIBRATION COMPLETE{NC}")
        print_calibration(cal)
    else:
        log.info("Calibration complete")
        log.info(f"  STFT1 baseline: {cal['stft1_baseline']:+.2f}%")
        log.info(f"  STFT2 baseline: {cal['stft2_baseline']:+.2f}%")
        log.info(f"  Idle RPM: {cal['idle_rpm_baseline']:.0f}")
        log.info(f"  Voltage: {cal['voltage_baseline']:.2f}V")

    return True


def main():
    parser = argparse.ArgumentParser(description="DRIFTER Auto-Calibration")
    parser.add_argument("--auto", action="store_true",
                        help="Non-interactive mode (for systemd)")
    parser.add_argument("--status", action="store_true",
                        help="Show current calibration")
    args = parser.parse_args()

    if args.status:
        cal = load_calibration()
        print_calibration(cal)
        return

    success = run_calibration(auto=args.auto)
    exit(0 if success else 1)


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Dashcam Recorder
Continuous segmented recording (default 60s segments) using ffmpeg.
Rotates oldest segments when disk usage exceeds DASHCAM_MAX_GB. Reacts
to crash/sentry events by tagging segments and publishing pointers.
UNCAGED TECHNOLOGY — EST 1991
"""

import json
import logging
import os
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

import paho.mqtt.client as mqtt

from config import (
    MQTT_HOST, MQTT_PORT, TOPICS,
    DASHCAM_DIR, DASHCAM_SEGMENT_SECONDS, DASHCAM_MAX_GB,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [DASHCAM] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

DEFAULT_VIDEO_DEV = "/dev/video0"


def _disk_used_bytes(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for f in path.glob("*.mp4"):
        try:
            total += f.stat().st_size
        except OSError:
            continue
    return total


def _prune(path: Path, max_bytes: int) -> None:
    files = sorted(path.glob("*.mp4"), key=lambda p: p.stat().st_mtime)
    used = _disk_used_bytes(path)
    while files and used > max_bytes:
        victim = files.pop(0)
        try:
            sz = victim.stat().st_size
            victim.unlink()
            used -= sz
            log.info(f"Pruned {victim.name}")
        except OSError as e:
            log.debug(f"prune {victim}: {e}")
            break


def _ffmpeg_running() -> bool:
    return shutil.which("ffmpeg") is not None


def _start_ffmpeg(device: str, out_pattern: str) -> Optional[subprocess.Popen]:
    if not _ffmpeg_running():
        log.warning("ffmpeg not installed — dashcam disabled")
        return None
    cmd = [
        "ffmpeg", "-y",
        "-f", "v4l2", "-i", device,
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26",
        "-pix_fmt", "yuv420p",
        "-f", "segment",
        "-segment_time", str(DASHCAM_SEGMENT_SECONDS),
        "-reset_timestamps", "1",
        "-strftime", "1",
        out_pattern,
    ]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log.info(f"ffmpeg started ({device})")
        return proc
    except Exception as e:
        log.warning(f"ffmpeg start failed: {e}")
        return None


def _tag_latest_segment(reason: str) -> Optional[Path]:
    files = sorted(DASHCAM_DIR.glob("*.mp4"), key=lambda p: p.stat().st_mtime)
    if not files:
        return None
    target = files[-1]
    tag_path = target.with_suffix('.tag.json')
    try:
        tag_path.write_text(json.dumps({
            'reason': reason,
            'segment': target.name,
            'ts': time.time(),
        }))
    except Exception as e:
        log.warning(f"tag write failed: {e}")
    return target


def main() -> None:
    log.info("DRIFTER Dashcam starting...")
    DASHCAM_DIR.mkdir(parents=True, exist_ok=True)
    device = os.environ.get('DRIFTER_DASHCAM_DEV', DEFAULT_VIDEO_DEV)
    out_pattern = str(DASHCAM_DIR / "seg_%Y%m%d-%H%M%S.mp4")
    proc = _start_ffmpeg(device, out_pattern)

    running = True

    def _handle_signal(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    client = mqtt.Client(client_id="drifter-dashcam")

    def on_message(_c, _u, msg) -> None:
        try:
            data = json.loads(msg.payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        if msg.topic == TOPICS['crash_event'] and isinstance(data, dict) and data.get('active'):
            target = _tag_latest_segment(f"crash:{data.get('reason', 'unknown')}")
            if target:
                client.publish(TOPICS['dashcam_clip'], json.dumps({
                    'reason': 'crash',
                    'segment': target.name,
                    'path': str(target),
                    'ts': time.time(),
                }))
        elif msg.topic == TOPICS['sentry_clip'] and isinstance(data, dict):
            target = _tag_latest_segment(f"sentry:{data.get('reason', 'bump')}")
            if target:
                client.publish(TOPICS['dashcam_clip'], json.dumps({
                    'reason': 'sentry',
                    'segment': target.name,
                    'path': str(target),
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

    client.subscribe([(TOPICS['crash_event'], 0), (TOPICS['sentry_clip'], 0)])
    client.loop_start()
    client.publish(TOPICS['dashcam_status'], json.dumps({
        'state': 'recording' if proc else 'offline',
        'device': device,
        'segment_seconds': DASHCAM_SEGMENT_SECONDS,
        'ts': time.time(),
    }), retain=True)
    log.info(f"Dashcam LIVE (segments={DASHCAM_SEGMENT_SECONDS}s, max={DASHCAM_MAX_GB}GB)")

    max_bytes = DASHCAM_MAX_GB * (1024 ** 3)
    while running:
        _prune(DASHCAM_DIR, max_bytes)
        if proc and proc.poll() is not None:
            log.warning("ffmpeg exited, restarting in 5s")
            time.sleep(5)
            proc = _start_ffmpeg(device, out_pattern)
            client.publish(TOPICS['dashcam_status'], json.dumps({
                'state': 'recording' if proc else 'offline',
                'restart_ts': time.time(),
            }), retain=True)
        time.sleep(10)

    if proc and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

    client.publish(TOPICS['dashcam_status'], json.dumps({
        'state': 'offline', 'ts': time.time(),
    }), retain=True)
    client.loop_stop()
    client.disconnect()
    log.info("Dashcam stopped")


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""MZ1312 DRIFTER — rfaudio
On-demand RTL-SDR → speaker bridge for emergency-services audio.

Listens on drifter/rfaudio/command and pipes demodulated audio from
rtl_fm through aplay to the operator's USB speaker.

Cooperates with drifter-rf (rtl_433) for the shared RTL-SDR device:
publishes {command: pause_rtl_433} on start, {command: resume_rtl_433}
on stop.

Commands (drifter/rfaudio/command, JSON):
  {"action": "start", "freq_mhz": 476.525, "mode": "nfm", "gain": 0}
  {"action": "stop"}
  {"action": "scan"}        # cycle through EMERGENCY_AUDIO_BANDS, 8s each
  {"action": "test_tone"}   # 1s 1kHz sine via speaker-test — proves the
                            #   aplay path without needing the SDR
  {"action": "list_bands"}  # publishes EMERGENCY_AUDIO_BANDS to status

Status (drifter/rfaudio/status, retained):
  {"state": "idle"|"playing"|"scanning", "freq_mhz": float, "mode": str, "ts": float}

The "start" action refuses if hw_probe reports no SDR — fail fast with a
clear error rather than letting rtl_fm spawn and immediately die.

UNCAGED TECHNOLOGY — EST 1991
"""
from __future__ import annotations

import json
import logging
import signal
import subprocess
import threading
import time
from typing import Optional

from config import (
    MQTT_HOST, MQTT_PORT, TOPICS, make_mqtt_client,
    EMERGENCY_AUDIO_BANDS,
    RFAUDIO_DEFAULT_FREQ_MHZ, RFAUDIO_DEFAULT_MODE, RFAUDIO_DEFAULT_GAIN,
    RFAUDIO_SAMPLE_RATE, RFAUDIO_OUTPUT_RATE, RFAUDIO_APLAY_DEVICE,
    RFAUDIO_PAUSE_WAIT_SEC,
    RFAUDIO_OPEN_RETRIES, RFAUDIO_OPEN_RETRY_BACKOFF_SEC,
)
from hw_probe import probe_rtl_sdr, publish_hw_state

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [RFAUDIO] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger(__name__)

SCAN_DWELL_SEC = 8.0
HW_RESCAN_INTERVAL = 30.0


class AudioStream:
    """rtl_fm | aplay subprocess pair. One stream active at a time."""

    def __init__(self) -> None:
        self._rtl: Optional[subprocess.Popen] = None
        self._aplay: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self.freq_mhz: Optional[float] = None
        self.mode: Optional[str] = None

    def start(self, freq_mhz: float, mode: str, gain: float) -> bool:
        # Retry usb_claim_interface -6: drifter-rf may still be releasing the SDR.
        with self._lock:
            if self._rtl is not None:
                self._stop_locked()
            for attempt in range(1, RFAUDIO_OPEN_RETRIES + 1):
                if not self._spawn_locked(freq_mhz, mode, gain):
                    return False
                time.sleep(0.7)
                if self._rtl is not None and self._rtl.poll() is None:
                    return True
                err = self._read_rtl_stderr_locked()
                self._stop_locked()
                if 'usb_claim_interface' not in err and 'Failed to open' not in err:
                    log.warning("rtl_fm died early: %r", err[:200])
                    return False
                log.info("rtl_fm busy (attempt %d/%d)", attempt, RFAUDIO_OPEN_RETRIES)
                time.sleep(RFAUDIO_OPEN_RETRY_BACKOFF_SEC)
            log.warning("rtl_fm could not claim SDR after %d attempts",
                        RFAUDIO_OPEN_RETRIES)
            return False

    def _spawn_locked(self, freq_mhz: float, mode: str, gain: float) -> bool:
        freq_hz = int(freq_mhz * 1_000_000)
        gain_arg = ['-g', str(gain)] if gain and gain > 0 else []  # 0 = auto
        rtl_cmd = [
            'rtl_fm',
            '-f', str(freq_hz),
            '-M', mode,
            '-s', str(RFAUDIO_SAMPLE_RATE),
            '-r', str(RFAUDIO_OUTPUT_RATE),
            *gain_arg,
        ]
        aplay_cmd = [
            'aplay',
            '-q',
            '-f', 'S16_LE',
            '-r', str(RFAUDIO_OUTPUT_RATE),
            '-c', '1',
            '-D', RFAUDIO_APLAY_DEVICE,
        ]
        log.info("rtl_fm %.3f MHz %s gain=%s → aplay %s",
                 freq_mhz, mode, gain or 'auto', RFAUDIO_APLAY_DEVICE)
        try:
            self._rtl = subprocess.Popen(
                rtl_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            self._aplay = subprocess.Popen(
                aplay_cmd, stdin=self._rtl.stdout, stderr=subprocess.PIPE,
            )
            # Detach the pipe end in the parent so rtl_fm exits cleanly
            # when aplay is terminated.
            if self._rtl.stdout:
                self._rtl.stdout.close()
        except FileNotFoundError as e:
            log.error("required binary missing: %s", e)
            self._rtl = self._aplay = None
            return False
        self.freq_mhz, self.mode = freq_mhz, mode
        return True

    def _read_rtl_stderr_locked(self) -> str:
        try:
            if self._rtl and self._rtl.stderr:
                return self._rtl.stderr.read().decode(errors='replace')
        except Exception:
            pass
        return ''

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def drain_stderr(self) -> tuple[str, str]:
        # Used by the supervisor loop to log why a stream died.
        rtl_err = self._read_rtl_stderr_locked()[:500]
        aplay_err = ''
        try:
            if self._aplay and self._aplay.stderr:
                aplay_err = self._aplay.stderr.read().decode(errors='replace')[:500]
        except Exception:
            pass
        return rtl_err, aplay_err

    def _stop_locked(self) -> None:
        for proc in (self._aplay, self._rtl):
            if proc is None or proc.poll() is not None:
                continue
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except Exception:
                    pass
        self._rtl = self._aplay = None
        self.freq_mhz = self.mode = None

    def is_running(self) -> bool:
        return self._rtl is not None and self._rtl.poll() is None


_stream = AudioStream()
_state = 'idle'  # 'idle' | 'playing' | 'scanning'
_scan_thread: Optional[threading.Thread] = None
_scan_stop = threading.Event()


def _publish_status(client) -> None:
    payload = {
        'state': _state,
        'freq_mhz': _stream.freq_mhz,
        'mode': _stream.mode,
        'ts': time.time(),
    }
    client.publish(TOPICS['rfaudio_status'], json.dumps(payload), retain=True, qos=1)


def _pause_rtl_433(client) -> None:
    # Runs on a worker thread so paho's loop can flush this before we sleep.
    client.publish(TOPICS['rf_command'], json.dumps({
        'command': 'pause_rtl_433', 'ts': time.time(),
    }), qos=0)
    time.sleep(RFAUDIO_PAUSE_WAIT_SEC)


def _resume_rtl_433(client) -> None:
    client.publish(TOPICS['rf_command'], json.dumps({
        'command': 'resume_rtl_433', 'ts': time.time(),
    }), qos=1)


def _stop_scan() -> None:
    global _scan_thread
    if _scan_thread is not None:
        _scan_stop.set()
        _scan_thread.join(timeout=SCAN_DWELL_SEC + 2)
        _scan_thread = None
    _scan_stop.clear()


def _start_scan(client) -> None:
    """Cycle through EMERGENCY_AUDIO_BANDS, dwelling SCAN_DWELL_SEC on each."""
    global _scan_thread, _state

    def worker():
        global _state
        for band in EMERGENCY_AUDIO_BANDS:
            if _scan_stop.is_set():
                break
            _stream.start(band['freq_mhz'], band['mode'], RFAUDIO_DEFAULT_GAIN)
            _state = 'scanning'
            _publish_status(client)
            log.info("scan: %s @ %.3f MHz", band['name'], band['freq_mhz'])
            if _scan_stop.wait(SCAN_DWELL_SEC):
                break
        _stream.stop()
        _state = 'idle'
        _publish_status(client)

    _stop_scan()
    _scan_thread = threading.Thread(target=worker, name='rfaudio-scan', daemon=True)
    _scan_thread.start()


def _publish_error(client, message: str) -> None:
    """Publish an error to rfaudio_status without changing _state."""
    client.publish(TOPICS['rfaudio_status'], json.dumps({
        'state': _state,
        'freq_mhz': _stream.freq_mhz,
        'mode': _stream.mode,
        'error': message,
        'ts': time.time(),
    }), retain=True, qos=1)
    log.warning(message)


def _play_test_tone(client) -> None:
    """Play a 1-second 1kHz tone through aplay to verify the speaker path.
    Useful when no SDR is plugged in but the operator wants to confirm
    audio is wired correctly before connecting the dongle."""
    log.info("test_tone: speaker-test 1kHz / 1s → %s", RFAUDIO_APLAY_DEVICE)
    try:
        subprocess.run(
            ['speaker-test', '-t', 'sine', '-f', '1000', '-l', '1',
             '-D', RFAUDIO_APLAY_DEVICE],
            check=False, capture_output=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        _publish_error(client, f"test_tone failed: {e}")


def _publish_bands(client) -> None:
    """Publish the configured EMERGENCY_AUDIO_BANDS so the dashboard can
    populate a band picker without re-importing config."""
    client.publish(TOPICS['rfaudio_status'], json.dumps({
        'state': _state,
        'freq_mhz': _stream.freq_mhz,
        'mode': _stream.mode,
        'bands': EMERGENCY_AUDIO_BANDS,
        'ts': time.time(),
    }), retain=True, qos=1)


def _handle_command(client, payload: dict) -> None:
    """Dispatch an rfaudio command. Quiet on bad input — log + status, never crash."""
    global _state
    action = payload.get('action', '').lower()

    if action == 'start':
        # Fail fast when the SDR isn't physically present, so we don't
        # spawn rtl_fm just to watch it die a second later.
        probe = probe_rtl_sdr()
        if not probe['connected']:
            _publish_error(client, f"start refused: {probe['detail']} — {probe['action']}")
            return
        _stop_scan()
        freq = float(payload.get('freq_mhz', RFAUDIO_DEFAULT_FREQ_MHZ))
        mode = str(payload.get('mode', RFAUDIO_DEFAULT_MODE)).lower()
        gain = float(payload.get('gain', RFAUDIO_DEFAULT_GAIN))
        _pause_rtl_433(client)
        if _stream.start(freq, mode, gain):
            _state = 'playing'
        else:
            _state = 'idle'
            _resume_rtl_433(client)
        _publish_status(client)
        return

    if action == 'stop':
        _stop_scan()
        _stream.stop()
        _state = 'idle'
        _publish_status(client)
        _resume_rtl_433(client)
        return

    if action == 'scan':
        probe = probe_rtl_sdr()
        if not probe['connected']:
            _publish_error(client, f"scan refused: {probe['detail']} — {probe['action']}")
            return
        _pause_rtl_433(client)
        _start_scan(client)
        return

    if action == 'test_tone':
        _play_test_tone(client)
        return

    if action == 'list_bands':
        _publish_bands(client)
        return

    log.warning("unknown action: %r", action)


def on_message(client, userdata, msg) -> None:  # noqa: ARG001 — paho callback shape
    try:
        payload = json.loads(msg.payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        log.warning("bad payload on %s", msg.topic)
        return
    if not isinstance(payload, dict):
        log.warning("payload not a JSON object")
        return
    # Worker thread: paho's loop must stay free to flush our own publishes
    # (e.g. pause_rtl_433) while _handle_command is in its retry chain.
    threading.Thread(
        target=_handle_command, args=(client, payload),
        name='rfaudio-cmd', daemon=True,
    ).start()


def main() -> int:
    running = True

    def _on_signal(_sig, _frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    log.info("DRIFTER rfaudio starting — default %.3f MHz %s",
             RFAUDIO_DEFAULT_FREQ_MHZ, RFAUDIO_DEFAULT_MODE)

    client = make_mqtt_client('drifter-rfaudio')
    client.on_message = on_message

    while running:
        try:
            client.connect(MQTT_HOST, MQTT_PORT, 60)
            break
        except (ConnectionRefusedError, OSError) as e:
            log.warning("MQTT not yet: %s — retrying in 3s", e)
            time.sleep(3)

    client.subscribe(TOPICS['rfaudio_command'])
    client.loop_start()
    _publish_status(client)
    publish_hw_state(client, 'rtl_sdr', probe_rtl_sdr())

    last_hw_tick = time.time()
    while running:
        now = time.time()
        # Hot-plug visibility: republish drifter/hw/rtl_sdr periodically so
        # the dashboard reflects mid-session SDR plug/unplug.
        if now - last_hw_tick >= HW_RESCAN_INTERVAL:
            last_hw_tick = now
            publish_hw_state(client, 'rtl_sdr', probe_rtl_sdr())
        # Bail-out: if the operator started playback but rtl_fm exited
        # (e.g. SDR was unplugged), reflect that in status without
        # waiting for them to explicitly stop.
        global _state
        if _state == 'playing' and not _stream.is_running():
            rtl_err, aplay_err = _stream.drain_stderr()
            log.warning("stream died unexpectedly — returning to idle. "
                        "rtl_fm stderr=%r aplay stderr=%r", rtl_err, aplay_err)
            _state = 'idle'
            _publish_status(client)
            _resume_rtl_433(client)
        time.sleep(1)

    log.info("rfaudio shutting down")
    _stop_scan()
    _stream.stop()
    _resume_rtl_433(client)
    client.loop_stop()
    client.disconnect()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

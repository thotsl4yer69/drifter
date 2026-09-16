#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Boot Sequence Manager

A one-shot orchestrator that runs early at boot and paints progress on the
3.5" SPI LCD so the operator can see the node come alive without an HDMI
monitor. It does NOT keep the screen — once the spine is up it hands fb1
over to drifter-lcd (which is ordered After= this unit).

Sequence:
  1. Init the LCD → "DRIFTER BOOTING…" splash
  2. Wait for the network (IP on wlan0 / drifter-autoconnect)
  3. Confirm the MQTT broker is accepting connections
  4. Wait for the core services inside ONE bounded readiness window
  5. Ready/degraded → retain drifter/boot/status, exit 0

Any failure is shown on the LCD and published, but the unit still exits 0 —
boot must never wedge on a missing dongle. The core-service stage is bounded as
one window rather than 20 seconds per service, keeping worst-case hand-off well
inside the systemd unit timeout.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import subprocess
import time

from config import (
    BOOT_CORE_SERVICES,
    BOOT_MQTT_WAIT_SEC,
    BOOT_NETWORK_WAIT_SEC,
    LCD_THEME,
    MQTT_HOST,
    MQTT_PORT,
    TOPICS,
    make_mqtt_client,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [BOOT] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger(__name__)

BOOT_CORE_WAIT_SEC = max(5.0, float(os.getenv("BOOT_CORE_WAIT_SEC", "45")))
BOOT_SERVICE_POLL_SEC = max(0.1, float(os.getenv("BOOT_SERVICE_POLL_SEC", "1")))
BOOT_HANDOFF_DELAY_SEC = max(0.0, float(os.getenv("BOOT_HANDOFF_DELAY_SEC", "1.5")))

try:
    import lcd_dashboard as lcd  # type: ignore
    _LCD_OK = True
except Exception:  # pragma: no cover
    lcd = None  # type: ignore
    _LCD_OK = False


class BootScreen:
    """Thin splash renderer over lcd_dashboard's Framebuffer."""

    def __init__(self):
        self.ok = False
        self.fb = None
        self.fonts = None
        self.lines: list[tuple[str, str]] = []
        if not _LCD_OK or not (lcd._PIL_OK and lcd._NUMPY_OK):
            return
        try:
            fb = lcd.Framebuffer()
            if not fb.available():
                return
            self.fb = fb
            self.fonts = lcd.load_fonts()
            self.ok = True
        except Exception as exc:  # pragma: no cover
            log.warning("boot LCD init failed: %s", exc)

    def add(self, text: str, level: str = 'fg') -> None:
        self.lines.append((text, level))
        self.lines = self.lines[-12:]
        self._paint()

    def _paint(self) -> None:
        if not self.ok:
            return
        th = LCD_THEME
        try:
            img = lcd.Image.new('RGB', (self.fb.width, self.fb.height), th['bg'])
            draw = lcd.ImageDraw.Draw(img)
            draw.rectangle([0, 0, self.fb.width, 46], fill=th['header_bg'])
            draw.text((10, 8), "DRIFTER", font=self.fonts['lg'], fill=th['accent'])
            draw.text(
                (10, 38), "MZ1312 UNCAGED TECHNOLOGY",
                font=self.fonts['sm'], fill=th['dim'],
            )
            y = 58
            for text, level in self.lines:
                draw.text((10, y), text, font=self.fonts['sm'], fill=th.get(level, th['fg']))
                y += 20
            self.fb.show(img)
        except Exception as exc:  # pragma: no cover
            log.warning("boot splash paint failed: %s", exc)


def _systemctl_active(service: str) -> bool:
    if not shutil.which('systemctl'):
        return False
    try:
        result = subprocess.run(
            ['systemctl', 'is-active', service],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        return result.stdout.strip() == 'active'
    except Exception:
        return False


def _have_ip() -> bool:
    try:
        result = subprocess.run(
            ['ip', '-4', '-brief', 'addr'],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
        for line in result.stdout.splitlines():
            fields = line.split()
            if fields and fields[0] != 'lo' and '/' in fields[-1]:
                return True
    except Exception:
        pass
    return False


def _mqtt_reachable() -> bool:
    try:
        with socket.create_connection((MQTT_HOST, MQTT_PORT), timeout=2):
            return True
    except OSError:
        return False


def _publish(client, stage: str, detail: str, ok: bool) -> None:
    if client is None:
        return
    try:
        client.publish(
            TOPICS['boot_status'],
            json.dumps({'stage': stage, 'detail': detail, 'ok': ok, 'ts': time.time()}),
            qos=1,
            retain=True,
        )
    except Exception:
        pass


def _wait_for(predicate, timeout: float, poll: float = 1.0) -> bool:
    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(poll)
    return bool(predicate())


def _wait_for_core_services(
    services: list[str] | tuple[str, ...],
    timeout: float = BOOT_CORE_WAIT_SEC,
    poll: float = BOOT_SERVICE_POLL_SEC,
) -> dict[str, bool]:
    """Resolve core-service readiness within one shared deadline."""
    ordered = list(services)
    pending = set(ordered)
    ready: dict[str, bool] = {service: False for service in ordered}
    deadline = time.monotonic() + max(0.0, timeout)

    while pending:
        for service in list(pending):
            if _systemctl_active(service):
                ready[service] = True
                pending.remove(service)
        if not pending or time.monotonic() >= deadline:
            break
        time.sleep(poll)

    # One final observation at the deadline avoids classifying a service that
    # became active during the last sleep as failed.
    for service in list(pending):
        if _systemctl_active(service):
            ready[service] = True
    return ready


def _connect_boot_client():
    client = make_mqtt_client("drifter-bootmgr")
    try:
        client.connect(MQTT_HOST, MQTT_PORT, 15)
        client.loop_start()
        return client
    except Exception as exc:
        log.warning("boot MQTT client connect failed: %s", exc)
        try:
            client.disconnect()
        except Exception:
            pass
        return None


def main() -> int:
    log.info("DRIFTER boot manager starting...")
    screen = BootScreen()
    screen.add("Booting…", 'accent')

    client = _connect_boot_client() if _mqtt_reachable() else None

    screen.add("Network: waiting…", 'warn')
    _publish(client, 'network', 'waiting for IP', False)
    if _wait_for(_have_ip, BOOT_NETWORK_WAIT_SEC):
        screen.add("Network: up", 'ok')
        _publish(client, 'network', 'ip acquired', True)
    else:
        screen.add("Network: no IP (AP fallback?)", 'crit')
        _publish(client, 'network', 'no ip within timeout', False)

    screen.add("MQTT: starting…", 'warn')
    if _wait_for(_mqtt_reachable, BOOT_MQTT_WAIT_SEC):
        screen.add("MQTT: connected", 'ok')
        if client is None:
            client = _connect_boot_client()
        _publish(client, 'mqtt', 'broker reachable', True)
    else:
        screen.add("MQTT: DOWN", 'crit')
        _publish(client, 'mqtt', 'broker unreachable', False)

    states = _wait_for_core_services(BOOT_CORE_SERVICES)
    all_ok = all(states.values()) if states else True
    for service in BOOT_CORE_SERVICES:
        short = service.replace('drifter-', '')
        if states.get(service):
            screen.add(f"{short}: ok", 'ok')
            _publish(client, 'service', f'{service} active', True)
        else:
            screen.add(f"{short}: FAILED", 'crit')
            _publish(client, 'service', f'{service} not active', False)
            log.warning("core service %s not active at boot", service)

    if all_ok:
        screen.add("Ready → dashboard", 'ok')
        _publish(client, 'ready', 'all core services up', True)
    else:
        failed = [service for service, ok in states.items() if not ok]
        screen.add("Degraded → dashboard", 'warn')
        _publish(client, 'ready', f"core services down: {','.join(failed)}", False)
    log.info("boot sequence complete; handing LCD to drifter-lcd")

    time.sleep(BOOT_HANDOFF_DELAY_SEC)
    if client:
        try:
            client.loop_stop()
            client.disconnect()
        except Exception:
            pass
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

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
  4. Wait for the core services (BOOT_CORE_SERVICES) in dependency order
  5. Ready → publish drifter/boot/status, exit 0 (drifter-lcd takes the screen)

Any failure is shown on the LCD and published, but the unit still exits 0 —
boot must never wedge on a missing dongle.

UNCAGED TECHNOLOGY — EST 1991
"""
from __future__ import annotations

import json
import logging
import shutil
import socket
import subprocess
import time
from pathlib import Path

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

# LCD bits are optional — boot still proceeds (and publishes) without a panel.
try:
    import lcd_dashboard as lcd  # type: ignore
    _LCD_OK = True
except Exception:  # pragma: no cover
    lcd = None  # type: ignore
    _LCD_OK = False


class BootScreen:
    """Thin splash renderer over lcd_dashboard's Framebuffer. No-op when the
    panel/PIL/numpy is unavailable so boot never blocks on the display."""

    def __init__(self):
        self.ok = False
        self.fb = None
        self.fonts = None
        self.lines: list[tuple[str, str]] = []  # (text, level) — level in ok/warn/crit/fg
        if not _LCD_OK or not (lcd._PIL_OK and lcd._NUMPY_OK):
            return
        try:
            fb = lcd.Framebuffer()
            if not fb.available():
                return
            self.fb = fb
            self.fonts = lcd.load_fonts()
            self.ok = True
        except Exception as e:  # pragma: no cover
            log.warning(f"boot LCD init failed: {e}")

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
            d = lcd.ImageDraw.Draw(img)
            d.rectangle([0, 0, self.fb.width, 46], fill=th['header_bg'])
            d.text((10, 8), "DRIFTER", font=self.fonts['lg'], fill=th['accent'])
            d.text((10, 38), "MZ1312 UNCAGED TECHNOLOGY",
                   font=self.fonts['sm'], fill=th['dim'])
            y = 58
            for text, level in self.lines:
                d.text((10, y), text, font=self.fonts['sm'], fill=th.get(level, th['fg']))
                y += 20
            self.fb.show(img)
        except Exception as e:  # pragma: no cover
            log.warning(f"boot splash paint failed: {e}")


def _systemctl_active(svc: str) -> bool:
    if not shutil.which('systemctl'):
        return False
    try:
        r = subprocess.run(['systemctl', 'is-active', svc],
                           capture_output=True, text=True, timeout=3)
        return r.stdout.strip() == 'active'
    except Exception:
        return False


def _have_ip() -> bool:
    try:
        r = subprocess.run(['ip', '-4', '-brief', 'addr'],
                           capture_output=True, text=True, timeout=4)
        for line in r.stdout.splitlines():
            f = line.split()
            if f and f[0] != 'lo' and '/' in f[-1]:
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
        client.publish(TOPICS['boot_status'], json.dumps({
            'stage': stage, 'detail': detail, 'ok': ok, 'ts': time.time(),
        }))
    except Exception:
        pass


def _wait_for(predicate, timeout: float, poll: float = 1.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(poll)
    return False


def main() -> int:
    log.info("DRIFTER boot manager starting...")
    screen = BootScreen()
    screen.add("Booting…", 'accent')

    # MQTT status channel — best-effort (broker may not be up yet).
    client = None
    if _mqtt_reachable():
        client = make_mqtt_client("drifter-bootmgr")
        try:
            client.connect(MQTT_HOST, MQTT_PORT, 15)
            client.loop_start()
        except OSError:
            client = None

    # 1. Network
    screen.add("Network: waiting…", 'warn')
    _publish(client, 'network', 'waiting for IP', False)
    if _wait_for(_have_ip, BOOT_NETWORK_WAIT_SEC):
        screen.add("Network: up", 'ok')
        _publish(client, 'network', 'ip acquired', True)
    else:
        screen.add("Network: no IP (AP fallback?)", 'crit')
        _publish(client, 'network', 'no ip within timeout', False)

    # 2. MQTT broker
    screen.add("MQTT: starting…", 'warn')
    if _wait_for(_mqtt_reachable, BOOT_MQTT_WAIT_SEC):
        screen.add("MQTT: connected", 'ok')
        _publish(client, 'mqtt', 'broker reachable', True)
        if client is None and _mqtt_reachable():
            # Broker came up after our first check — connect now for publishes.
            client = make_mqtt_client("drifter-bootmgr")
            try:
                client.connect(MQTT_HOST, MQTT_PORT, 15)
                client.loop_start()
            except OSError:
                client = None
    else:
        screen.add("MQTT: DOWN", 'crit')
        _publish(client, 'mqtt', 'broker unreachable', False)

    # 3. Core services in dependency order
    all_ok = True
    for svc in BOOT_CORE_SERVICES:
        short = svc.replace('drifter-', '')
        up = _wait_for(lambda s=svc: _systemctl_active(s), 20, poll=1.0)
        if up:
            screen.add(f"{short}: ok", 'ok')
            _publish(client, 'service', f'{svc} active', True)
        else:
            all_ok = False
            screen.add(f"{short}: FAILED", 'crit')
            _publish(client, 'service', f'{svc} not active', False)
            log.warning(f"core service {svc} not active at boot")

    # 4. Ready — hand the LCD over to drifter-lcd
    if all_ok:
        screen.add("Ready → dashboard", 'ok')
        _publish(client, 'ready', 'all core services up', True)
    else:
        screen.add("Degraded → dashboard", 'warn')
        _publish(client, 'ready', 'some core services down', False)
    log.info("boot sequence complete; handing LCD to drifter-lcd")

    time.sleep(1.5)  # let the operator read the final splash line
    if client:
        client.loop_stop()
        client.disconnect()
    return 0  # never wedge boot


if __name__ == '__main__':
    raise SystemExit(main())

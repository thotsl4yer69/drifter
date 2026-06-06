#!/usr/bin/env python3
"""
MZ1312 DRIFTER — In-Car LCD Dashboard

A framebuffer-rendered triage console for a 3.5" SPI TFT (480x320), e.g.
Waveshare 3.5A / piscreen. Runs DIRECTLY on the Linux framebuffer in CLI
mode — NO X11/desktop required. The operator can see node state and
troubleshoot at the car without dragging an HDMI monitor out.

This OWNS /dev/fb1 with its own menu UI; it is distinct from
drifter-fbmirror, which mirrors fb0→fb1. Run only one of the two.

Screens (cycle with the GPIO buttons):
  status        system + network + MQTT + services + GPS at a glance
  services      every drifter-* unit, colour-coded, scrollable
  network       Wi-Fi/IP detail + internet ping + hotspot auto-connect state
  diagnostics   last journalctl error lines + recent drifter/alerts/*
  vehicle       RPM / speed / coolant / battery / DTCs (when OBD connected)

Buttons (active-low, internal pull-up, BCM):
  LCD_BTN_PREV    previous screen
  LCD_BTN_NEXT    next screen
  LCD_BTN_ACTION  refresh now / scroll long lists

UNCAGED TECHNOLOGY — EST 1991
"""
from __future__ import annotations

import logging
import shutil
import signal
import socket
import subprocess
import time
from pathlib import Path

from config import (
    DEFAULT_MODE,
    LCD_BTN_ACTION,
    LCD_BTN_DEBOUNCE_MS,
    LCD_BTN_NEXT,
    LCD_BTN_PREV,
    LCD_DIAG_LOG_LINES,
    LCD_FB_DEVICE,
    LCD_FONT_CANDIDATES,
    LCD_HEIGHT,
    LCD_REFRESH_HZ,
    LCD_ROTATE,
    LCD_SCREENS,
    LCD_THEME,
    LCD_VEHICLE_REFRESH_HZ,
    LCD_WIDTH,
    MODE_STATE_PATH,
    MODES,
    MQTT_HOST,
    MQTT_PORT,
    PING_HOST,
    PING_TIMEOUT_SEC,
    SERVICES,
    TOPICS,
    make_mqtt_client,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [LCD] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger(__name__)

# ── Optional deps — guarded so the module imports on a dev box / CI for the
#    pure-helper unit tests. Rendering needs PIL + numpy; buttons need RPi.GPIO.
try:
    from PIL import Image, ImageDraw, ImageFont  # type: ignore
    _PIL_OK = True
except ImportError:  # pragma: no cover - exercised only off-Pi
    Image = ImageDraw = ImageFont = None  # type: ignore
    _PIL_OK = False

try:
    import numpy as np  # type: ignore
    _NUMPY_OK = True
except ImportError:  # pragma: no cover
    np = None  # type: ignore
    _NUMPY_OK = False

try:
    import psutil  # type: ignore
    _PSUTIL_OK = True
except ImportError:  # pragma: no cover
    psutil = None  # type: ignore
    _PSUTIL_OK = False


# ════════════════════════════════════════════════════════════════════
#  Pure helpers (unit-tested off-Pi)
# ════════════════════════════════════════════════════════════════════

def fmt_uptime(seconds: float) -> str:
    """Human uptime: '3d 04h', '4h 12m', '7m 03s'."""
    s = int(max(0, seconds))
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if d:
        return f"{d}d {h:02d}h"
    if h:
        return f"{h}h {m:02d}m"
    return f"{m}m {s:02d}s"


def fmt_bytes(n: float) -> str:
    """1536 -> '1.5K', 1.2e9 -> '1.1G'."""
    n = float(n)
    for unit in ('B', 'K', 'M', 'G', 'T'):
        if abs(n) < 1024.0 or unit == 'T':
            return f"{n:.0f}{unit}" if unit == 'B' else f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}P"


def level_color(value: float, warn: float, crit: float, theme: dict,
                higher_is_worse: bool = True) -> tuple:
    """Pick ok/warn/crit colour for a numeric value against thresholds."""
    if higher_is_worse:
        if value >= crit:
            return theme['crit']
        if value >= warn:
            return theme['warn']
    else:
        if value <= crit:
            return theme['crit']
        if value <= warn:
            return theme['warn']
    return theme['ok']


def service_state_color(state: str, theme: dict) -> tuple:
    """Map a systemctl is-active string to a theme colour."""
    if state == 'active':
        return theme['ok']
    if state in ('activating', 'reloading', 'deactivating'):
        return theme['warn']
    return theme['crit']


def signal_quality(dbm: float | None) -> tuple[int, str]:
    """Map RSSI dBm to (bars 0-4, label). None -> (0, 'n/a')."""
    if dbm is None:
        return 0, 'n/a'
    if dbm >= -55:
        return 4, 'excellent'
    if dbm >= -67:
        return 3, 'good'
    if dbm >= -75:
        return 2, 'fair'
    if dbm >= -85:
        return 1, 'weak'
    return 0, 'poor'


def active_mode() -> str:
    try:
        return Path(MODE_STATE_PATH).read_text(encoding='utf-8').strip() or DEFAULT_MODE
    except OSError:
        return DEFAULT_MODE


# ════════════════════════════════════════════════════════════════════
#  Framebuffer
# ════════════════════════════════════════════════════════════════════

class Framebuffer:
    """Minimal direct-to-/dev/fbN writer. Reads geometry from sysfs and
    converts a PIL RGB image to the panel's native pixel format.

    Supports 16bpp (RGB565, the common SPI-TFT format) and 32bpp (BGRA).
    """

    def __init__(self, device: str = LCD_FB_DEVICE):
        self.device = device
        self.width = LCD_WIDTH
        self.height = LCD_HEIGHT
        self.bpp = 16
        self.stride = self.width * 2
        self._read_geometry()

    def _sysfs(self, leaf: str) -> str:
        fb = Path(self.device).name  # 'fb1'
        try:
            return Path(f"/sys/class/graphics/{fb}/{leaf}").read_text().strip()
        except OSError:
            return ''

    def _read_geometry(self) -> None:
        size = self._sysfs('virtual_size')  # "480,320"
        if ',' in size:
            try:
                w, h = size.split(',', 1)
                self.width, self.height = int(w), int(h)
            except ValueError:
                pass
        bpp = self._sysfs('bits_per_pixel')
        if bpp.isdigit():
            self.bpp = int(bpp)
        stride = self._sysfs('stride')
        self.stride = int(stride) if stride.isdigit() else self.width * (self.bpp // 8)
        log.info(f"Framebuffer {self.device}: {self.width}x{self.height} "
                 f"{self.bpp}bpp stride={self.stride}")

    def available(self) -> bool:
        return Path(self.device).exists()

    def _to_bytes(self, img) -> bytes:
        """Convert a PIL RGB image (already sized W x H) to raw fb bytes."""
        if not _NUMPY_OK:  # pragma: no cover - numpy ships in the venv
            raise RuntimeError("numpy required for framebuffer conversion")
        arr = np.asarray(img.convert('RGB'), dtype=np.uint16)  # H x W x 3
        if self.bpp == 16:
            r = (arr[:, :, 0] >> 3) << 11
            g = (arr[:, :, 1] >> 2) << 5
            b = (arr[:, :, 2] >> 3)
            rgb565 = (r | g | b).astype('<u2')  # little-endian
            return rgb565.tobytes()
        # 32bpp BGRA (alpha opaque)
        rgb = np.asarray(img.convert('RGB'), dtype=np.uint8)
        h, w, _ = rgb.shape
        bgra = np.empty((h, w, 4), dtype=np.uint8)
        bgra[:, :, 0] = rgb[:, :, 2]
        bgra[:, :, 1] = rgb[:, :, 1]
        bgra[:, :, 2] = rgb[:, :, 0]
        bgra[:, :, 3] = 255
        return bgra.tobytes()

    def show(self, img) -> None:
        """Blit a PIL image to the panel. Pads rows to stride when needed."""
        if img.size != (self.width, self.height):
            img = img.resize((self.width, self.height))
        raw = self._to_bytes(img)
        row_bytes = self.width * (self.bpp // 8)
        with open(self.device, 'wb') as fb:
            if self.stride == row_bytes:
                fb.write(raw)
            else:  # padded stride — write row by row
                pad = b'\x00' * (self.stride - row_bytes)
                for y in range(self.height):
                    fb.write(raw[y * row_bytes:(y + 1) * row_bytes])
                    fb.write(pad)


def load_fonts() -> dict:
    """Load a monospace font family at the sizes the screens use."""
    path = next((p for p in LCD_FONT_CANDIDATES if Path(p).exists()), None)
    sizes = {'sm': 14, 'md': 18, 'lg': 26, 'xl': 40}
    fonts: dict = {}
    for name, size in sizes.items():
        try:
            fonts[name] = ImageFont.truetype(path, size) if path else ImageFont.load_default()
        except Exception:  # pragma: no cover - defensive
            fonts[name] = ImageFont.load_default()
    return fonts


# ════════════════════════════════════════════════════════════════════
#  Data collection — cached so the render loop stays responsive
# ════════════════════════════════════════════════════════════════════

def _run(cmd: list[str], timeout: float = 3.0) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout
    except Exception:
        return ''


def collect_system() -> dict:
    """hostname, uptime, CPU temp, RAM%, disk% — cheap, refreshed every tick."""
    out = {'hostname': socket.gethostname(), 'uptime': 0.0,
           'cpu_temp': None, 'cpu_pct': None, 'ram_pct': None, 'disk_pct': None}
    try:
        out['uptime'] = time.time() - psutil.boot_time() if _PSUTIL_OK else _uptime_proc()
    except Exception:
        out['uptime'] = _uptime_proc()
    # CPU temp via thermal zone (works without psutil sensors on Pi).
    try:
        milli = Path('/sys/class/thermal/thermal_zone0/temp').read_text().strip()
        out['cpu_temp'] = round(int(milli) / 1000.0, 1)
    except (OSError, ValueError):
        pass
    if _PSUTIL_OK:
        try:
            out['cpu_pct'] = psutil.cpu_percent(interval=None)
            out['ram_pct'] = psutil.virtual_memory().percent
            out['disk_pct'] = psutil.disk_usage('/').percent
        except Exception:
            pass
    return out


def _uptime_proc() -> float:
    try:
        return float(Path('/proc/uptime').read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return 0.0


def collect_network() -> dict:
    """SSID/IP/signal/gateway/DNS + internet reachability via nmcli/ip/ping."""
    out = {'ssid': None, 'bssid': None, 'channel': None, 'signal_dbm': None,
           'ip': None, 'gateway': None, 'dns': [], 'internet': False,
           'mode': None}
    if shutil.which('nmcli'):
        # Active Wi-Fi: SSID, signal%, channel, bssid, mode.
        wifi = _run(['nmcli', '-t', '-f',
                     'ACTIVE,SSID,SIGNAL,CHAN,BSSID,MODE', 'dev', 'wifi'])
        for line in wifi.splitlines():
            # nmcli escapes ':' in BSSID with '\:' — split carefully.
            parts = line.replace('\\:', '%%').split(':')
            if parts and parts[0] == 'yes':
                out['ssid'] = parts[1] or None
                try:
                    pct = int(parts[2])
                    # nmcli reports 0-100%; map back to a rough dBm scale.
                    out['signal_dbm'] = round(pct / 2 - 100)
                except (ValueError, IndexError):
                    pass
                out['channel'] = parts[3] if len(parts) > 3 else None
                out['bssid'] = parts[4].replace('%%', ':') if len(parts) > 4 else None
                out['mode'] = parts[5] if len(parts) > 5 else None
                break
    # IP + gateway via iproute2.
    route = _run(['ip', 'route', 'show', 'default'])
    for tok in route.split():
        if tok not in ('default', 'via', 'dev', 'proto', 'metric'):
            if out['gateway'] is None and tok.count('.') == 3:
                out['gateway'] = tok
                break
    addr = _run(['ip', '-4', '-brief', 'addr'])
    for line in addr.splitlines():
        f = line.split()
        if len(f) >= 3 and f[0] not in ('lo',) and '/' in f[-1]:
            out['ip'] = f[-1].split('/')[0]
            if f[0].startswith('wl') or f[0] == 'wlan0':
                break
    # DNS — resolv.conf is the lowest-common-denominator source.
    try:
        for line in Path('/etc/resolv.conf').read_text().splitlines():
            if line.startswith('nameserver'):
                out['dns'].append(line.split()[1])
    except OSError:
        pass
    out['internet'] = ping_ok()
    return out


def ping_ok(host: str = PING_HOST, timeout: int = PING_TIMEOUT_SEC) -> bool:
    """One ICMP echo. Linux ping: -c1 -W<sec>."""
    if not shutil.which('ping'):
        return False
    try:
        r = subprocess.run(['ping', '-c', '1', '-W', str(timeout), host],
                           capture_output=True, timeout=timeout + 2)
    except (subprocess.TimeoutExpired, OSError):
        # A stuck ping must not bubble out of collect_network and drop the
        # whole LCD frame — every other subprocess here is guarded too.
        return False
    return r.returncode == 0


def collect_services() -> dict:
    """systemctl is-active for every drifter unit + expected-in-mode set."""
    states: dict[str, str] = {}
    if shutil.which('systemctl'):
        for svc in SERVICES:
            states[svc] = (_run(['systemctl', 'is-active', svc], timeout=2).strip()
                           or 'unknown')
    mode = active_mode()
    expected = MODES.get(mode, set(SERVICES))
    running = sum(1 for v in states.values() if v == 'active')
    exp_running = sum(1 for s, v in states.items() if s in expected and v == 'active')
    return {'states': states, 'mode': mode, 'expected': expected,
            'running': running, 'expected_count': len(expected),
            'expected_running': exp_running, 'total': len(states)}


def collect_journal_errors(lines: int = LCD_DIAG_LOG_LINES) -> list[str]:
    """Recent priority<=err journal lines (system-wide — drifter services log
    to the journal, so an err here is almost always one of ours)."""
    if not shutil.which('journalctl'):
        return ['journalctl unavailable']
    out = _run(['journalctl', '-p', 'err', '-n', str(lines), '--no-pager',
                '-o', 'short'], timeout=4)
    rows = [ln.strip() for ln in out.splitlines() if ln.strip()]
    return rows[-lines:] if rows else ['no recent errors']


# ════════════════════════════════════════════════════════════════════
#  MQTT cache — live telemetry / alerts / network status / GPS
# ════════════════════════════════════════════════════════════════════

class MqttCache:
    """Subscribes to the topics the LCD renders and caches latest values."""

    def __init__(self):
        self.connected = False
        self.msg_count = 0
        self._rate_anchor = (time.time(), 0)
        self.vehicle: dict = {}      # rpm/speed/coolant/voltage
        self.dtcs: list = []
        self.last_alert: dict | None = None
        self.network: dict | None = None  # from auto_connect
        self.gps: dict | None = None
        self._client = None

    def msg_rate(self) -> float:
        now = time.time()
        t0, c0 = self._rate_anchor
        dt = now - t0
        if dt < 1.0:
            return 0.0
        rate = (self.msg_count - c0) / dt
        self._rate_anchor = (now, self.msg_count)
        return round(rate, 1)

    def start(self) -> None:
        import json
        client = make_mqtt_client("drifter-lcd")

        def on_connect(c, u, flags, rc, props=None):
            self.connected = True
            for key in ('rpm', 'speed', 'coolant', 'voltage', 'dtc',
                        'alert_message', 'alert_level', 'network_status', 'gps_fix'):
                topic = TOPICS.get(key)
                if topic:
                    c.subscribe(topic, 0)

        def on_disconnect(c, u, *a):
            self.connected = False

        def on_message(c, u, msg):
            self.msg_count += 1
            try:
                data = json.loads(msg.payload)
            except (ValueError, UnicodeDecodeError):
                return
            t = msg.topic
            if t == TOPICS.get('rpm'):
                self.vehicle['rpm'] = _val(data)
            elif t == TOPICS.get('speed'):
                self.vehicle['speed'] = _val(data)
            elif t == TOPICS.get('coolant'):
                self.vehicle['coolant'] = _val(data)
            elif t == TOPICS.get('voltage'):
                self.vehicle['voltage'] = _val(data)
            elif t == TOPICS.get('dtc'):
                codes = data.get('codes') if isinstance(data, dict) else data
                if isinstance(codes, list):
                    self.dtcs = codes
            elif t == TOPICS.get('alert_message'):
                self.last_alert = data if isinstance(data, dict) else {'message': str(data)}
            elif t == TOPICS.get('network_status'):
                self.network = data if isinstance(data, dict) else None
            elif t == TOPICS.get('gps_fix'):
                self.gps = data if isinstance(data, dict) else None

        client.on_connect = on_connect
        client.on_disconnect = on_disconnect
        client.on_message = on_message
        try:
            client.connect(MQTT_HOST, MQTT_PORT, 30)
            client.loop_start()
            self._client = client
        except OSError as e:
            log.warning(f"MQTT connect failed ({e}); telemetry screens limited")

    def publish_status(self, screen: str, fb_ok: bool) -> None:
        if not self._client:
            return
        import json
        try:
            self._client.publish(TOPICS['lcd_status'], json.dumps(
                {'screen': screen, 'fb': fb_ok, 'ts': time.time()}))
        except Exception:
            pass

    def stop(self) -> None:
        if self._client:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:
                pass


def _val(data):
    """Pull the numeric value out of a {'value':..} or bare-number payload."""
    if isinstance(data, dict):
        return data.get('value')
    return data


# ════════════════════════════════════════════════════════════════════
#  Screen rendering
# ════════════════════════════════════════════════════════════════════

class Renderer:
    def __init__(self, fonts: dict, theme: dict = LCD_THEME,
                 width: int = LCD_WIDTH, height: int = LCD_HEIGHT):
        self.fonts = fonts
        self.theme = theme
        self.width = width
        self.height = height

    def _canvas(self):
        img = Image.new('RGB', (self.width, self.height), self.theme['bg'])
        return img, ImageDraw.Draw(img)

    def _header(self, d, title: str, subtitle: str = '') -> int:
        th = self.theme
        d.rectangle([0, 0, self.width, 30], fill=th['header_bg'])
        d.text((8, 6), f"DRIFTER · {title}", font=self.fonts['md'], fill=th['accent'])
        if subtitle:
            w = d.textlength(subtitle, font=self.fonts['sm'])
            d.text((self.width - w - 8, 9), subtitle, font=self.fonts['sm'], fill=th['dim'])
        d.line([0, 31, self.width, 31], fill=th['panel'])
        return 40  # first content y

    def _row(self, d, y: int, label: str, value: str, color=None) -> int:
        th = self.theme
        d.text((10, y), label, font=self.fonts['sm'], fill=th['dim'])
        d.text((150, y), value, font=self.fonts['sm'], fill=color or th['fg'])
        return y + 22

    # ── status ──────────────────────────────────────────────────────
    def status(self, data: dict):
        th = self.theme
        img, d = self._canvas()
        sys_ = data['system']
        net = data['network']
        svc = data['services']
        mq = data['mqtt']
        y = self._header(d, 'STATUS', time.strftime('%H:%M:%S'))

        y = self._row(d, y, 'HOST', sys_.get('hostname') or '?')
        y = self._row(d, y, 'UPTIME', fmt_uptime(sys_.get('uptime') or 0))
        ct = sys_.get('cpu_temp')
        y = self._row(d, y, 'CPU TEMP', f"{ct:.0f}°C" if ct is not None else 'n/a',
                      level_color(ct or 0, 70, 80, th) if ct is not None else th['dim'])
        ram = sys_.get('ram_pct')
        y = self._row(d, y, 'RAM', f"{ram:.0f}%" if ram is not None else 'n/a',
                      level_color(ram or 0, 80, 92, th) if ram is not None else th['dim'])
        disk = sys_.get('disk_pct')
        y = self._row(d, y, 'DISK', f"{disk:.0f}%" if disk is not None else 'n/a',
                      level_color(disk or 0, 85, 95, th) if disk is not None else th['dim'])

        d.line([0, y + 2, self.width, y + 2], fill=th['panel']); y += 8
        ssid = net.get('ssid') or '—'
        bars, _ = signal_quality(net.get('signal_dbm'))
        y = self._row(d, y, 'WIFI', f"{ssid} {'|' * bars}{'.' * (4 - bars)}",
                      th['ok'] if net.get('ssid') else th['warn'])
        y = self._row(d, y, 'IP', net.get('ip') or '—',
                      th['fg'] if net.get('ip') else th['warn'])
        y = self._row(d, y, 'INTERNET', 'online' if net.get('internet') else 'OFFLINE',
                      th['ok'] if net.get('internet') else th['crit'])

        d.line([0, y + 2, self.width, y + 2], fill=th['panel']); y += 8
        y = self._row(d, y, 'MQTT', f"up · {mq['rate']:.0f} msg/s" if mq['connected'] else 'DOWN',
                      th['ok'] if mq['connected'] else th['crit'])
        svc_color = (th['ok'] if svc['expected_running'] == svc['expected_count']
                     else th['warn'] if svc['expected_running'] else th['crit'])
        y = self._row(d, y, 'SERVICES',
                      f"{svc['expected_running']}/{svc['expected_count']} ({svc['mode']})",
                      svc_color)
        gps = data.get('gps')
        if gps and gps.get('lat') is not None:
            y = self._row(d, y, 'GPS', f"{gps['lat']:.4f},{gps['lon']:.4f}", th['ok'])
        else:
            y = self._row(d, y, 'GPS', 'No GPS', th['dim'])

        alert = mq.get('last_alert')
        if alert:
            msg = (alert.get('message') or '')[:46]
            d.rectangle([0, self.height - 22, self.width, self.height], fill=th['header_bg'])
            d.text((8, self.height - 19), f"! {msg}", font=self.fonts['sm'], fill=th['warn'])
        return img

    # ── services ────────────────────────────────────────────────────
    def services(self, data: dict, scroll: int = 0):
        th = self.theme
        img, d = self._canvas()
        svc = data['services']
        y0 = self._header(d, 'SERVICES',
                          f"{svc['running']}/{svc['total']} up")
        states = svc['states']
        expected = svc['expected']
        names = sorted(states)
        per_page = (self.height - y0 - 4) // 16
        view = names[scroll:scroll + per_page]
        y = y0
        for name in view:
            state = states[name]
            in_mode = name in expected
            color = service_state_color(state, th)
            if not in_mode and state != 'active':
                color = th['dim']  # out-of-mode inactivity is expected
            d.ellipse([10, y + 4, 18, y + 12], fill=color)
            short = name.replace('drifter-', '')
            d.text((26, y), short, font=self.fonts['sm'],
                   fill=th['fg'] if in_mode else th['dim'])
            w = d.textlength(state, font=self.fonts['sm'])
            d.text((self.width - w - 8, y), state, font=self.fonts['sm'], fill=color)
            y += 16
        if scroll + per_page < len(names):
            d.text((self.width - 14, self.height - 16), '▼',
                   font=self.fonts['sm'], fill=th['accent'])
        return img

    # ── network ─────────────────────────────────────────────────────
    def network(self, data: dict):
        th = self.theme
        img, d = self._canvas()
        net = data['network']
        auto = data.get('autoconnect')
        y = self._header(d, 'NETWORK')
        bars, label = signal_quality(net.get('signal_dbm'))
        y = self._row(d, y, 'SSID', net.get('ssid') or '—',
                      th['ok'] if net.get('ssid') else th['warn'])
        y = self._row(d, y, 'BSSID', net.get('bssid') or '—')
        y = self._row(d, y, 'CHANNEL', str(net.get('channel') or '—'))
        dbm = net.get('signal_dbm')
        y = self._row(d, y, 'SIGNAL',
                      f"{dbm} dBm  {label}" if dbm is not None else 'n/a',
                      level_color(bars, 2, 1, th, higher_is_worse=False))
        d.line([0, y + 2, self.width, y + 2], fill=th['panel']); y += 8
        y = self._row(d, y, 'IP', net.get('ip') or '—',
                      th['fg'] if net.get('ip') else th['crit'])
        y = self._row(d, y, 'GATEWAY', net.get('gateway') or '—')
        y = self._row(d, y, 'DNS', ', '.join(net.get('dns') or []) or '—')
        y = self._row(d, y, 'INTERNET', 'online' if net.get('internet') else 'OFFLINE',
                      th['ok'] if net.get('internet') else th['crit'])
        d.line([0, y + 2, self.width, y + 2], fill=th['panel']); y += 8
        if auto:
            state = auto.get('state', '?')
            color = (th['ok'] if state in ('connected', 'client')
                     else th['warn'] if 'fallback' in state or state == 'ap'
                     else th['crit'] if state in ('searching', 'offline') else th['fg'])
            y = self._row(d, y, 'AUTOCONNECT', state, color)
            if auto.get('ap_fallback'):
                y = self._row(d, y, 'AP MODE', 'active (SSH in)', th['warn'])
        else:
            y = self._row(d, y, 'AUTOCONNECT', 'no status yet', th['dim'])
        return img

    # ── diagnostics ─────────────────────────────────────────────────
    def diagnostics(self, data: dict):
        th = self.theme
        img, d = self._canvas()
        y = self._header(d, 'DIAGNOSTICS', 'journal -p err')
        for line in data.get('errors', [])[-9:]:
            d.text((6, y), line[:62], font=self.fonts['sm'], fill=th['crit'])
            y += 15
            if y > self.height - 30:
                break
        alert = data['mqtt'].get('last_alert')
        d.rectangle([0, self.height - 24, self.width, self.height], fill=th['header_bg'])
        if alert:
            msg = (alert.get('message') or '')[:54]
            d.text((6, self.height - 20), f"alert: {msg}",
                   font=self.fonts['sm'], fill=th['warn'])
        else:
            d.text((6, self.height - 20), 'no recent drifter/alert',
                   font=self.fonts['sm'], fill=th['dim'])
        return img

    # ── vehicle ─────────────────────────────────────────────────────
    def vehicle(self, data: dict):
        th = self.theme
        img, d = self._canvas()
        v = data['mqtt']['vehicle']
        dtcs = data['mqtt']['dtcs']
        connected = bool(v)
        y = self._header(d, 'VEHICLE', 'OBD' if connected else 'no OBD')
        if not connected:
            d.text((self.width // 2 - 70, self.height // 2 - 20), 'No OBD data',
                   font=self.fonts['lg'], fill=th['dim'])
            return img

        def gauge(x, y0, label, value, unit, color):
            d.text((x, y0), label, font=self.fonts['sm'], fill=th['dim'])
            txt = f"{value:.0f}" if isinstance(value, (int, float)) else '—'
            d.text((x, y0 + 16), txt, font=self.fonts['xl'], fill=color)
            d.text((x + d.textlength(txt, font=self.fonts['xl']) + 4, y0 + 34),
                   unit, font=self.fonts['sm'], fill=th['dim'])

        rpm = v.get('rpm')
        spd = v.get('speed')
        cool = v.get('coolant')
        volt = v.get('voltage')
        gauge(16, 44, 'RPM', rpm if rpm is not None else '—', '',
              level_color(rpm or 0, 5500, 6500, th))
        gauge(self.width // 2 + 8, 44, 'SPEED', spd if spd is not None else '—', 'km/h', th['fg'])
        gauge(16, 130, 'COOLANT', cool if cool is not None else '—', '°C',
              level_color(cool or 0, 104, 108, th))
        gauge(self.width // 2 + 8, 130, 'BATT',
              volt if volt is not None else '—', 'V',
              level_color(volt or 14, 13.2, 12.0, th, higher_is_worse=False))
        d.line([0, 214, self.width, 214], fill=th['panel'])
        if dtcs:
            d.text((10, 222), f"DTC: {', '.join(str(c) for c in dtcs[:6])}",
                   font=self.fonts['md'], fill=th['crit'])
        else:
            d.text((10, 222), 'DTC: none', font=self.fonts['md'], fill=th['ok'])
        return img


# ════════════════════════════════════════════════════════════════════
#  Buttons
# ════════════════════════════════════════════════════════════════════

class Buttons:
    """Active-low GPIO buttons with software debounce. No-op if GPIO is
    unavailable (dev box / no Pi header)."""

    def __init__(self):
        self.ok = False
        self._last = {}
        self._GPIO = None
        try:
            import RPi.GPIO as GPIO  # type: ignore
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            for pin in (LCD_BTN_PREV, LCD_BTN_NEXT, LCD_BTN_ACTION):
                GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
                self._last[pin] = 0.0
            self._GPIO = GPIO
            self.ok = True
            log.info(f"Buttons ready: prev={LCD_BTN_PREV} next={LCD_BTN_NEXT} "
                     f"action={LCD_BTN_ACTION}")
        except Exception as e:  # pragma: no cover
            log.warning(f"GPIO buttons unavailable ({e}); LCD will auto-cycle/MQTT-only")

    def pressed(self) -> str | None:
        """Return 'prev'|'next'|'action' on a fresh debounced press, else None."""
        if not self.ok:
            return None
        now = time.time()
        debounce = LCD_BTN_DEBOUNCE_MS / 1000.0
        for pin, name in ((LCD_BTN_PREV, 'prev'), (LCD_BTN_NEXT, 'next'),
                          (LCD_BTN_ACTION, 'action')):
            if self._GPIO.input(pin) == self._GPIO.LOW:
                if now - self._last[pin] > debounce:
                    self._last[pin] = now
                    return name
        return None

    def cleanup(self) -> None:
        if self._GPIO:
            try:
                self._GPIO.cleanup()
            except Exception:
                pass


# ════════════════════════════════════════════════════════════════════
#  Main loop
# ════════════════════════════════════════════════════════════════════

class CachedCollectors:
    """Wraps the expensive collectors with per-call TTLs so a 4Hz vehicle
    refresh doesn't shell out to systemctl/nmcli on every frame."""

    def __init__(self):
        self._cache: dict = {}
        self._ts: dict = {}

    def get(self, key: str, fn, ttl: float):
        now = time.time()
        if key not in self._cache or (now - self._ts.get(key, 0)) >= ttl:
            self._cache[key] = fn()
            self._ts[key] = now
        return self._cache[key]


def build_frame_data(cache: MqttCache, cc: CachedCollectors) -> dict:
    return {
        'system': cc.get('system', collect_system, 1.0),
        'network': cc.get('network', collect_network, 5.0),
        'services': cc.get('services', collect_services, 4.0),
        'errors': cc.get('errors', collect_journal_errors, 8.0),
        'autoconnect': cache.network,
        'gps': cache.gps,
        'mqtt': {
            'connected': cache.connected,
            'rate': cache.msg_rate(),
            'vehicle': cache.vehicle,
            'dtcs': cache.dtcs,
            'last_alert': cache.last_alert,
        },
    }


def render_screen(renderer: Renderer, screen: str, data: dict, scroll: int):
    if screen == 'status':
        return renderer.status(data)
    if screen == 'services':
        return renderer.services(data, scroll)
    if screen == 'network':
        return renderer.network(data)
    if screen == 'diagnostics':
        return renderer.diagnostics(data)
    if screen == 'vehicle':
        return renderer.vehicle(data)
    return renderer.status(data)


def main() -> None:
    log.info("DRIFTER LCD dashboard starting...")
    if not (_PIL_OK and _NUMPY_OK):
        log.error("PIL + numpy required for the LCD dashboard — install Pillow/numpy. "
                  "Exiting.")
        return

    fb = Framebuffer()
    if not fb.available():
        log.error(f"{LCD_FB_DEVICE} not present — SPI LCD not wired. "
                  "Run scripts/setup-lcd.sh and reboot. Exiting (hw-pending).")
        return

    fonts = load_fonts()
    renderer = Renderer(fonts, width=fb.width, height=fb.height)
    cache = MqttCache()
    cache.start()
    cc = CachedCollectors()
    buttons = Buttons()

    screens = list(LCD_SCREENS)
    idx = 0
    scroll = 0
    running = True

    def _stop(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    log.info(f"LCD live on {fb.device} — {len(screens)} screens")
    last_render = 0.0
    while running:
        # Buttons are polled fast for responsiveness; rendering is throttled.
        btn = buttons.pressed()
        if btn == 'next':
            idx = (idx + 1) % len(screens); scroll = 0
        elif btn == 'prev':
            idx = (idx - 1) % len(screens); scroll = 0
        elif btn == 'action':
            if screens[idx] == 'services':
                scroll += 6
            cc._ts.clear()  # force a fresh collect

        screen = screens[idx]
        hz = LCD_VEHICLE_REFRESH_HZ if screen == 'vehicle' else LCD_REFRESH_HZ
        now = time.time()
        if btn or (now - last_render) >= (1.0 / max(hz, 0.1)):
            data = build_frame_data(cache, cc)
            try:
                img = render_screen(renderer, screen, data, scroll)
                if LCD_ROTATE:
                    img = img.rotate(-LCD_ROTATE, expand=False)
                fb.show(img)
                cache.publish_status(screen, True)
            except Exception as e:  # pragma: no cover - keep the loop alive
                log.warning(f"render/blit failed on '{screen}': {e}")
            last_render = now
        time.sleep(0.05)

    log.info("LCD dashboard shutting down...")
    cache.stop()
    buttons.cleanup()
    log.info("LCD dashboard stopped")


if __name__ == '__main__':
    main()

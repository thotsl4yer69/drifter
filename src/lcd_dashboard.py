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

import glob
import logging
import os
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
    LCD_FB_WAIT_SEC,
    LCD_FONT_CANDIDATES,
    LCD_HEIGHT,
    LCD_OBD_STALE_S,
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
    XTYPE_DTC_LOOKUP,
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


def decode_dtc(code: str) -> tuple[str, str]:
    """Return (label, severity) for a DTC code from XTYPE_DTC_LOOKUP — turns a
    raw 'P0301' into 'P0301 Cylinder 1 Misfire' so the fault is readable at the
    wheel. Unknown codes fall back to (code, 'AMBER')."""
    norm = str(code).strip().upper()
    info = XTYPE_DTC_LOOKUP.get(norm) if norm else None
    if info:
        return (f"{norm} {info.get('desc', '')}".strip(), info.get('severity', 'AMBER'))
    return (norm, 'AMBER')


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

def resolve_fb_device(preferred: str = LCD_FB_DEVICE,
                      sysfs_root: str = "/sys/class/graphics",
                      name_hints: tuple = (
                          "ili9486", "ili9341", "ili9488", "st7789",
                          "hx8357", "fb_ili", "tft")) -> str | None:
    """Resolve the SPI panel's /dev/fbN, resilient to fbN renumbering.

    The fbtft panel and the vc4 DRM/HDMI plane race at boot, so /dev/fb1 is not
    guaranteed to be the SPI TFT. Prefer the framebuffer whose sysfs 'name'
    matches a known SPI-TFT driver (e.g. 'fb_ili9486') and explicitly skip the
    DRM plane ('vc4drmfb'). Only if no panel is matched by name do we accept the
    configured `preferred` path, and only when it actually exists. Returns None
    when no usable panel framebuffer is present yet (caller waits / retries)."""
    for name_path in sorted(glob.glob(f"{sysfs_root}/fb*/name")):
        try:
            name = Path(name_path).read_text().strip().lower()
        except OSError:
            continue
        if "vc4" in name or "drm" in name:
            continue  # HDMI/DRM plane — never the SPI dash panel
        if any(hint in name for hint in name_hints):
            fb = os.path.basename(os.path.dirname(name_path))  # 'fb1'
            return f"/dev/{fb}"
    if os.path.exists(preferred):
        return preferred
    return None


def wait_for_fb(timeout: float = LCD_FB_WAIT_SEC, interval: float = 2.0,
                **kwargs) -> str | None:
    """Poll resolve_fb_device until the SPI panel appears or `timeout` elapses.

    The panel registers ~12s into boot (later under a crank brownout); waiting
    here — rather than exiting on the first miss — is what keeps the dash alive
    on a cold car start."""
    dev = resolve_fb_device(**kwargs)
    deadline = time.time() + max(0.0, timeout)
    while dev is None and time.time() < deadline:
        log.info("SPI panel framebuffer not present yet — waiting...")
        time.sleep(max(0.1, interval))
        dev = resolve_fb_device(**kwargs)
    return dev


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
    """Load the monospace family at the sizes the v4 screens use, plus a bold
    cut for the hero gauge numbers."""
    reg = next((p for p in LCD_FONT_CANDIDATES
                if 'Bold' not in p and Path(p).exists()), None)
    bold = next((p for p in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
        "/opt/drifter/fonts/DejaVuSansMono-Bold.ttf", reg)
        if p and Path(p).exists()), reg)
    sizes = {'xs': 11, 'sm': 13, 'md': 16, 'lg': 22, 'xl': 30, 'huge': 44}
    fonts: dict = {}
    for name, size in sizes.items():
        try:
            fonts[name] = ImageFont.truetype(reg, size) if reg else ImageFont.load_default()
        except Exception:  # pragma: no cover - defensive
            fonts[name] = ImageFont.load_default()
    for name, size in {'blg': 22, 'bxl': 30, 'bhuge': 44}.items():
        try:
            fonts[name] = ImageFont.truetype(bold, size) if bold else fonts['lg']
        except Exception:  # pragma: no cover
            fonts[name] = fonts.get(name[1:], ImageFont.load_default())
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
        self.vehicle_ts: float = 0.0  # last vehicle telemetry arrival (staleness)
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
                self.vehicle_ts = time.time()
            elif t == TOPICS.get('speed'):
                self.vehicle['speed'] = _val(data)
                self.vehicle_ts = time.time()
            elif t == TOPICS.get('coolant'):
                self.vehicle['coolant'] = _val(data)
                self.vehicle_ts = time.time()
            elif t == TOPICS.get('voltage'):
                self.vehicle['voltage'] = _val(data)
                self.vehicle_ts = time.time()
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

    # ── v4 "uncaged" primitives ─────────────────────────────────────
    def _rrect(self, d, box, radius, **kw):
        try:
            d.rounded_rectangle(box, radius=radius, **kw)
        except (AttributeError, TypeError):  # pragma: no cover - old Pillow
            d.rectangle(box, **{k: v for k, v in kw.items() if k in ('fill', 'outline')})

    def _tw(self, d, text, font, tracking=0.0):
        return sum(d.textlength(ch, font=font) + tracking for ch in text)

    def _track(self, d, xy, text, font, fill, tracking=1.0):
        """Letter-spaced text — the Major-Mono-Display stencil feel."""
        x, y = xy
        for ch in text:
            d.text((x, y), ch, font=font, fill=fill)
            x += d.textlength(ch, font=font) + tracking
        return x

    def _label(self, d, xy, text, color=None, tracking=1.4):
        return self._track(d, xy, text.lower(), self.fonts['xs'],
                           color or self.theme['dim'], tracking)

    def _brackets(self, d, box, color, ln=8):
        x0, y0, x1, y1 = box
        for cx, cy, dx, dy in ((x0, y0, 1, 1), (x1, y0, -1, 1),
                               (x0, y1, 1, -1), (x1, y1, -1, -1)):
            d.line([cx, cy, cx + dx * ln, cy], fill=color)
            d.line([cx, cy, cx, cy + dy * ln], fill=color)

    def _dot(self, d, x, y, color, r=3):
        d.ellipse([x - r, y - r, x + r, y + r], fill=color)

    def _bars(self, d, x, y, bars, n=4):
        th = self.theme
        for i in range(n):
            h = 4 + i * 3
            col = th['teal'] if i < bars else th['fg_deep']
            d.rectangle([x + i * 8, y + (13 - h), x + i * 8 + 5, y + 13], fill=col)

    def _pill(self, d, x, y, text, color):
        tw = self._tw(d, text.upper(), self.fonts['xs'], 0.8)
        self._rrect(d, [x, y, x + tw + 16, y + 16], 8, outline=self.theme['edge'])
        self._track(d, (x + 8, y + 3), text.upper(), self.fonts['xs'], color, 0.8)

    def _panel(self, d, box, label=None, meta=None, bracket=False, live=False) -> int:
        """Glass tile: rounded dark fill + edge stroke (+brackets/label/live).
        Returns the y at which body content should start."""
        th = self.theme
        x0, y0, x1, y1 = box
        self._rrect(d, box, 8, fill=th['panel'], outline=th['edge'])
        if bracket:
            self._brackets(d, (x0 + 4, y0 + 4, x1 - 4, y1 - 4), th['amber_dim'])
        if label:
            self._label(d, (x0 + 11, y0 + 7), label)
        if meta:
            mw = d.textlength(meta.upper(), font=self.fonts['xs'])
            d.text((x1 - mw - (24 if live else 11), y0 + 8), meta.upper(),
                   font=self.fonts['xs'], fill=th['fg_deep'])
        if live:
            self._dot(d, x1 - 13, y0 + 11, th['teal'])
        return (y0 + 26) if label else (y0 + 9)

    def _kv(self, d, x, y, w, label, value, color=None, vfont=None) -> int:
        """stencil label (left, dim) → mono value (right-aligned)."""
        th = self.theme
        self._label(d, (x, y + 1), label)
        vf = vfont or self.fonts['sm']
        vw = d.textlength(value, font=vf)
        d.text((x + w - vw, y), value, font=vf, fill=color or th['fg'])
        return y + 20

    def _honest(self, d, box, kind, label, hint=None):
        """Centered honest-state card (brief §2.4) — drawn with primitives so
        it never depends on exotic font glyphs. no-hw=ring+slash, acquiring=arc."""
        th = self.theme
        x0, y0, x1, y1 = box
        col = {'no-hw': th['fg_dim'], 'acquiring': th['cyan'],
               'no-key': th['fg_dim'], 'conn-err': th['crit']}.get(kind, th['fg_dim'])
        cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
        gy, r = cy - 24, 12
        if kind == 'acquiring':
            d.arc([cx - r, gy - r, cx + r, gy + r], 35, 315, fill=col, width=2)
        else:
            d.ellipse([cx - r, gy - r, cx + r, gy + r], outline=col, width=2)
            d.line([cx - r * 0.66, gy + r * 0.66, cx + r * 0.66, gy - r * 0.66],
                   fill=col, width=2)
        lw = self._tw(d, label.upper(), self.fonts['sm'], 1.0)
        self._track(d, (cx - lw / 2, cy), label.upper(), self.fonts['sm'], col, 1.0)
        if hint:
            hw = d.textlength(hint, font=self.fonts['xs'])
            d.text((cx - hw / 2, cy + 18), hint, font=self.fonts['xs'], fill=th['fg_deep'])

    def _brand(self, d, screen: str) -> int:
        """Top brand bar — amber 'm' badge + mz1312·drifter stencil + screen + clock."""
        th = self.theme
        W = self.width
        self._rrect(d, [8, 6, 30, 28], 6, outline=th['amber'])
        d.text((14, 7), 'm', font=self.fonts['md'], fill=th['amber'])
        self._track(d, (38, 7), 'mz1312 · drifter', self.fonts['sm'], th['fg'], 1.2)
        self._track(d, (38, 20), 'uncaged tech / est 1991', self.fonts['xs'], th['fg_dim'], 0.5)
        clock = time.strftime('%H:%M:%S')
        cw = d.textlength(clock, font=self.fonts['sm'])
        d.text((W - cw - 8, 7), clock, font=self.fonts['sm'], fill=th['dim'])
        sw = self._tw(d, screen.lower(), self.fonts['sm'], 1.2)
        self._track(d, (W - cw - sw - 22, 7), screen.lower(), self.fonts['sm'], th['amber'], 1.2)
        d.line([0, 32, W, 32], fill=th['amber_dim'])
        d.line([W // 3, 32, 2 * W // 3, 32], fill=th['amber'])
        return 40

    # ── status ──────────────────────────────────────────────────────
    def status(self, data: dict):
        th = self.theme
        img, d = self._canvas()
        sys_ = data['system']
        net = data['network']
        svc = data['services']
        mq = data['mqtt']
        self._brand(d, 'status')

        # SYSTEM panel
        y = self._panel(d, (8, 40, 233, 184), label='system', bracket=True, live=True)
        iw, ix = 233 - 8 - 22, 8 + 11
        y = self._kv(d, ix, y, iw, 'host', sys_.get('hostname') or '?')
        y = self._kv(d, ix, y, iw, 'uptime', fmt_uptime(sys_.get('uptime') or 0))
        ct = sys_.get('cpu_temp')
        y = self._kv(d, ix, y, iw, 'cpu temp', f"{ct:.0f}°C" if ct is not None else 'n/a',
                     level_color(ct or 0, 70, 80, th) if ct is not None else th['fg_dim'])
        ram = sys_.get('ram_pct')
        y = self._kv(d, ix, y, iw, 'ram', f"{ram:.0f}%" if ram is not None else 'n/a',
                     level_color(ram or 0, 80, 92, th) if ram is not None else th['fg_dim'])
        disk = sys_.get('disk_pct')
        self._kv(d, ix, y, iw, 'disk', f"{disk:.0f}%" if disk is not None else 'n/a',
                 level_color(disk or 0, 85, 95, th) if disk is not None else th['fg_dim'])

        # LINK panel
        y = self._panel(d, (247, 40, 472, 184), label='link', meta='wifi·mqtt', bracket=True)
        iw, ix = 472 - 247 - 22, 247 + 11
        bars, _ = signal_quality(net.get('signal_dbm'))
        y = self._kv(d, ix, y, iw, 'wi-fi', (net.get('ssid') or '—')[:14],
                     th['teal'] if net.get('ssid') else th['warn'])
        self._label(d, (ix, y + 1), 'signal')
        self._bars(d, ix + iw - 35, y + 2, bars)
        y += 20
        y = self._kv(d, ix, y, iw, 'ip', net.get('ip') or '—',
                     th['fg'] if net.get('ip') else th['warn'])
        y = self._kv(d, ix, y, iw, 'internet', 'online' if net.get('internet') else 'OFFLINE',
                     th['teal'] if net.get('internet') else th['crit'])
        self._kv(d, ix, y, iw, 'mqtt', f"{mq['rate']:.0f} msg/s" if mq['connected'] else 'down',
                 th['teal'] if mq['connected'] else th['crit'])

        # NODE panel (services count + mode + gps)
        self._panel(d, (8, 192, 472, 300), label='node', meta=f"mode {svc['mode']}")
        sc = (th['teal'] if svc['expected_running'] == svc['expected_count']
              else th['warn'] if svc['expected_running'] else th['crit'])
        self._label(d, (22, 216), 'services')
        d.text((22, 230), f"{svc['expected_running']}/{svc['expected_count']}",
               font=self.fonts['bxl'], fill=sc)
        self._pill(d, 22, 272, f"mode · {svc['mode']}",
                   th['amber'] if svc['mode'] != 'diag' else th['dim'])
        gps = data.get('gps')
        self._label(d, (250, 216), 'gps')
        if gps and gps.get('lat') is not None:
            d.text((250, 230), f"{gps['lat']:.4f}", font=self.fonts['lg'], fill=th['teal'])
            d.text((250, 254), f"{gps['lon']:.4f}", font=self.fonts['lg'], fill=th['teal'])
        else:
            self._track(d, (250, 234), 'NO FIX', self.fonts['md'], th['fg_dim'], 1.0)
            d.text((250, 258), 'awaiting gps — never faked', font=self.fonts['xs'], fill=th['fg_deep'])

        alert = mq.get('last_alert')
        if alert:
            msg = (alert.get('message') or '')[:52]
            d.rectangle([0, self.height - 18, self.width, self.height], fill=th['header_bg'])
            self._dot(d, 9, self.height - 9, th['warn'])
            d.text((18, self.height - 16), msg, font=self.fonts['xs'], fill=th['warn'])
        return img

    # ── services ────────────────────────────────────────────────────
    def services(self, data: dict, scroll: int = 0):
        th = self.theme
        img, d = self._canvas()
        svc = data['services']
        self._brand(d, 'services')
        states = svc['states']
        expected = svc['expected']
        self._panel(d, (8, 40, 472, 300), label='units · watchdog',
                    meta=f"{svc['running']}/{svc['total']} up · {svc['mode']}")
        names = sorted(states)
        per_col, colw, y0 = 11, 228, 70
        view = names[scroll:scroll + per_col * 2]
        for i, name in enumerate(view):
            col, row = divmod(i, per_col)
            x = 20 + col * colw
            yy = y0 + row * 20
            state = states[name]
            in_mode = name in expected
            color = service_state_color(state, th)
            if not in_mode and state != 'active':
                color = th['fg_deep']  # out-of-mode inactivity is expected
            self._dot(d, x + 3, yy + 7, color)
            short = name.replace('drifter-', '')[:15]
            d.text((x + 14, yy), short, font=self.fonts['sm'],
                   fill=th['fg'] if in_mode else th['fg_dim'])
            stt = state[:6]
            sw = d.textlength(stt, font=self.fonts['xs'])
            d.text((x + colw - sw - 16, yy + 2), stt, font=self.fonts['xs'], fill=color)
        # legend + scroll hint
        self._dot(d, 22, 290, th['teal']); d.text((30, 285), 'ok', font=self.fonts['xs'], fill=th['fg_dim'])
        self._dot(d, 70, 290, th['warn']); d.text((78, 285), 'starting', font=self.fonts['xs'], fill=th['fg_dim'])
        self._dot(d, 150, 290, th['crit']); d.text((158, 285), 'down', font=self.fonts['xs'], fill=th['fg_dim'])
        if scroll + per_col * 2 < len(names):
            self._track(d, (self.width - 92, 285), 'action ▸ more', self.fonts['xs'], th['amber'], 0.5)
        return img

    # ── network ─────────────────────────────────────────────────────
    def network(self, data: dict):
        th = self.theme
        img, d = self._canvas()
        net = data['network']
        auto = data.get('autoconnect')
        self._brand(d, 'network')
        bars, label = signal_quality(net.get('signal_dbm'))
        dbm = net.get('signal_dbm')

        # WI-FI panel
        y = self._panel(d, (8, 40, 472, 150), label='wi-fi', meta='client',
                        bracket=True, live=bool(net.get('ssid')))
        iw, ix = 472 - 16 - 22, 8 + 11
        y = self._kv(d, ix, y, iw, 'ssid', net.get('ssid') or '—',
                     th['teal'] if net.get('ssid') else th['warn'])
        y = self._kv(d, ix, y, iw, 'bssid · ch', f"{net.get('bssid') or '—'}  ch{net.get('channel') or '?'}")
        self._label(d, (ix, y + 1), 'signal')
        self._bars(d, ix + 70, y + 2, bars)
        sigtxt = f"{dbm} dBm · {label}" if dbm is not None else 'n/a'
        sw = d.textlength(sigtxt, font=self.fonts['sm'])
        d.text((472 - 11 - sw, y), sigtxt, font=self.fonts['sm'],
               fill=level_color(bars, 2, 1, th, higher_is_worse=False))

        # IP panel
        y = self._panel(d, (8, 158, 472, 246), label='ip · routing')
        y = self._kv(d, ix, y, iw, 'address', net.get('ip') or '—',
                     th['fg'] if net.get('ip') else th['crit'])
        y = self._kv(d, ix, y, iw, 'gateway', net.get('gateway') or '—')
        y = self._kv(d, ix, y, iw, 'internet', 'online' if net.get('internet') else 'OFFLINE',
                     th['teal'] if net.get('internet') else th['crit'])

        # RADIO panel (one wi-fi, explicit owner)
        self._panel(d, (8, 254, 472, 308), label='radio · one wi-fi owner')
        if auto:
            state = auto.get('state', '?')
            ap = auto.get('ap_fallback') or state == 'ap'
            owner = 'ap · tether (MZ1312_DRIFTER)' if ap else f'client · {state}'
            color = (th['teal'] if state in ('connected', 'client') and not ap
                     else th['warn'] if ap or 'fallback' in str(state)
                     else th['crit'] if state in ('searching', 'offline') else th['fg'])
            self._pill(d, 22, 276, 'AP' if ap else 'CLIENT', color)
            d.text((78, 278), owner, font=self.fonts['sm'], fill=color)
        else:
            self._track(d, (22, 278), 'NO STATUS YET', self.fonts['sm'], th['fg_dim'], 1.0)
        return img

    # ── diagnostics ─────────────────────────────────────────────────
    def diagnostics(self, data: dict):
        th = self.theme
        img, d = self._canvas()
        self._brand(d, 'diagnostics')
        errors = [e for e in data.get('errors', []) if e]
        clean = (not errors) or (len(errors) == 1 and errors[0] in
                                 ('no recent errors', 'journalctl unavailable'))
        y = self._panel(d, (8, 40, 472, 270), label='journal · priority err',
                        meta='-p err', live=clean)
        if clean:
            self._honest(d, (8, 40, 472, 270), 'no-key', 'no recent errors',
                         'journal -p err is clear')
        else:
            yy = y + 2
            for line in errors[-10:]:
                self._dot(d, 15, yy + 7, th['crit'], r=2)
                d.text((24, yy), line[:60], font=self.fonts['xs'], fill=th['crit'])
                yy += 16
                if yy > 262:
                    break
        # alert footer
        alert = data['mqtt'].get('last_alert')
        self._panel(d, (8, 276, 472, 308), label='last alert')
        if alert:
            msg = (alert.get('message') or '')[:48]
            self._dot(d, 100, 292, th['warn'])
            d.text((110, 285), msg, font=self.fonts['sm'], fill=th['warn'])
        else:
            self._track(d, (100, 286), 'NONE · BUS QUIET', self.fonts['sm'], th['fg_dim'], 1.0)
        return img

    # ── vehicle (hero gauges) ───────────────────────────────────────
    def _gauge(self, d, box, label, meta, value, unit, color, frac, stale):
        th = self.theme
        x0, y0, x1, y1 = box
        self._panel(d, box, label=label, meta=('stale' if stale else meta),
                    bracket=True, live=not stale)
        num = f"{value:.0f}" if isinstance(value, (int, float)) else '—'
        col = th['fg_dim'] if stale else color
        d.text((x0 + 14, y0 + 26), num, font=self.fonts['bhuge'], fill=col)
        nw = d.textlength(num, font=self.fonts['bhuge'])
        if unit:
            d.text((x0 + 14 + nw + 6, y0 + 56), unit, font=self.fonts['sm'], fill=th['fg_dim'])
        # tape bar
        bx0, bx1, by = x0 + 14, x1 - 14, y1 - 13
        d.rectangle([bx0, by, bx1, by + 4], fill=th['bg1'])
        fw = (bx1 - bx0) * max(0.0, min(1.0, frac))
        d.rectangle([bx0, by, bx0 + fw, by + 4], fill=col)

    def vehicle(self, data: dict):
        th = self.theme
        img, d = self._canvas()
        v = data['mqtt']['vehicle']
        dtcs = data['mqtt']['dtcs']
        v_ts = data['mqtt'].get('vehicle_ts', 0.0)
        connected = bool(v)
        stale = bool(connected and v_ts and (time.time() - v_ts) > LCD_OBD_STALE_S)
        self._brand(d, 'vehicle')
        if not connected:
            # Honest no-data state — never show fake zeros at the wheel (§2.4).
            self._panel(d, (8, 40, 472, 308))
            self._honest(d, (8, 40, 472, 308), 'no-hw', 'ecu not connected',
                         'can0 idle — plug in obd-ii')
            return img

        rpm, spd = v.get('rpm'), v.get('speed')
        cool, volt = v.get('coolant'), v.get('voltage')
        self._gauge(d, (8, 40, 233, 168), 'rpm · crank', 'can·50ms',
                    rpm if rpm is not None else '—', 'rpm',
                    level_color(rpm or 0, 5500, 6500, th), (rpm or 0) / 7000.0, stale)
        self._gauge(d, (247, 40, 472, 168), 'speed', 'km/h',
                    spd if spd is not None else '—', 'km/h', th['cyan'],
                    (spd or 0) / 160.0, stale)
        self._gauge(d, (8, 176, 233, 288), 'coolant', '°C',
                    cool if cool is not None else '—', '°C',
                    level_color(cool or 0, 104, 108, th), ((cool or 40) - 40) / 80.0, stale)
        self._gauge(d, (247, 176, 472, 288), 'battery', 'V',
                    volt if volt is not None else '—', 'V',
                    level_color(volt or 14, 13.2, 12.0, th, higher_is_worse=False),
                    ((volt or 11) - 11) / 4.0, stale)

        # DTC strip
        self._panel(d, (8, 292, 472, 316))
        if dtcs:
            label, sev = decode_dtc(str(dtcs[0]))
            color = th['crit'] if sev == 'RED' else th['warn']
            more = f"  +{len(dtcs) - 1}" if len(dtcs) > 1 else ''
            self._dot(d, 18, 304, color)
            d.text((28, 297), f"{label}{more}"[:58], font=self.fonts['sm'], fill=color)
        else:
            self._dot(d, 18, 304, th['teal'])
            self._track(d, (28, 298), 'DTC · NONE', self.fonts['sm'], th['teal'], 1.0)
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
            'vehicle_ts': cache.vehicle_ts,
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

    dev = wait_for_fb()
    if dev is None:
        log.error("No SPI panel framebuffer appeared after %ss — panel not wired "
                  "or the fbtft driver failed to probe. Exiting non-zero so "
                  "systemd (Restart=on-failure) retries, instead of a clean exit "
                  "that leaves the dash blank for the whole drive.", LCD_FB_WAIT_SEC)
        raise SystemExit(1)
    fb = Framebuffer(dev)
    log.info("Resolved SPI panel framebuffer: %s", dev)

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

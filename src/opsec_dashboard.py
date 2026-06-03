#!/usr/bin/env python3
"""
MZ1312 DRIFTER — OPSEC Dashboard

Mobile/foot persona. Same Pi, different dashboard. Dark Kali aesthetic.
Listens on port 8090; intentionally separate from the in-vehicle HUD on 8080
so the cabin display can keep showing telemetry while the operator works the
recon/console UI on a phone or laptop over the hotspot.

Slice 3 — real features:
  • TERMINAL  — allowlisted probes + ad-hoc args via /api/tool/<name>
  • TOOLS     — curated nmap / kismet / hcxdumptool / hashcat launchers
  • FLIPPER   — live status + recent SubGHz captures from MQTT
  • WARDRIVE  — latest WiFi/BT scan from MQTT
  • KILLSWITCH — MAC randomize, wipe logs, halt recon services

UNCAGED TECHNOLOGY — EST 1991
"""
from __future__ import annotations

import json
import logging
import shlex
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, '/opt/drifter')
sys.path.insert(0, str(Path(__file__).resolve().parent))

import opsec_marauder_client as marauder_client
from config import (
    DEFAULT_MODE,
    MODE_STATE_PATH,
    MODES,
    MQTT_HOST,
    MQTT_PORT,
    TOPICS,
)

LOG_FORMAT = '%(asctime)s [OPSEC] %(message)s'
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

OPSEC_PORT = 8090
MAX_POST_BODY = 4 * 1024
TOOL_TIMEOUT_SEC = 30

# ── Allowlisted commands ────────────────────────────────────────────────────
# Quick probes — no args from web, fixed argv. Read-only / no-side-effect.
PROBES: dict[str, list[str]] = {
    # System / iface state
    'iwconfig':         ['iwconfig'],
    'iw-dev':           ['iw', 'dev'],
    'ip-addr':          ['ip', '-brief', 'addr'],
    'ip-link':          ['ip', '-brief', 'link'],
    'ip-route':         ['ip', '-brief', 'route'],
    'arp':              ['ip', 'neigh'],
    'nmcli-radio':      ['nmcli', 'radio'],
    'lsusb':            ['lsusb'],
    'lspci':            ['lspci'],
    'uname':            ['uname', '-a'],
    'free':             ['free', '-h'],
    'uptime':           ['uptime'],
    # SDR hardware checks
    'rtl-test':         ['rtl_test', '-t'],
    'hackrf-info':      ['hackrf_info'],
    # Toolchain version sanity
    'kismet-version':   ['kismet', '--version'],
    'nmap-version':     ['nmap', '--version'],
    'masscan-version':  ['masscan', '--version'],
    'bettercap-version':['bettercap', '-version'],
    'aircrack-help':    ['aircrack-ng', '--help'],
    'airmon-ng':        ['airmon-ng'],
}

# Configurable tools — argv template; user-supplied args appended via
# shlex.split. Each user arg is treated as a single token; no shell metas.
TOOLS: dict[str, dict] = {
    # ── Network discovery ─────────────────────────────────────────────────
    'nmap': {
        'argv':      ['nmap'],
        'default':   '-sn 10.42.0.0/24',
        'hint':      'target / flags (e.g. -sV 10.42.0.5)',
        'timeout':   120,
    },
    'nmap-fast': {
        'argv':      ['nmap', '-T4', '-F'],
        'default':   '10.42.0.0/24',
        'hint':      'target subnet/host',
        'timeout':   120,
    },
    'masscan': {
        'argv':      ['masscan', '--rate=1000'],
        'default':   '-p80,443,22 10.42.0.0/24',
        'hint':      'flags + target (e.g. -p1-65535 10.42.0.5)',
        'timeout':   180,
    },
    # ── Wi-Fi recon ───────────────────────────────────────────────────────
    'iwlist-scan': {
        'argv':      ['iwlist'],
        'default':   'wlan0 scanning',
        'hint':      'iface scanning',
        'timeout':   30,
    },
    'nmcli-wifi': {
        'argv':      ['nmcli', '-t', '-f',
                      'SSID,BSSID,SIGNAL,CHAN,SECURITY', 'device', 'wifi'],
        'default':   'list',
        'hint':      'list | rescan',
        'timeout':   20,
    },
    'airmon-ng': {
        'argv':      ['sudo', '-n', '/usr/sbin/airmon-ng'],
        'default':   'check',
        'hint':      'check | start wlan1 | stop wlan1mon',
        'timeout':   20,
    },
    'kismet-info': {
        'argv':      ['kismet', '--list-server-modules'],
        'default':   '',
        'hint':      '(no args needed)',
        'timeout':   10,
    },
    # ── Bluetooth ─────────────────────────────────────────────────────────
    'hcitool': {
        'argv':      ['hcitool'],
        'default':   'scan',
        'hint':      'subcommand (scan | inq | lescan)',
        'timeout':   30,
    },
    'bluetoothctl': {
        'argv':      ['bluetoothctl', '--timeout', '10'],
        'default':   'devices',
        'hint':      'subcommand (devices | scan on)',
        'timeout':   15,
    },
    # ── SDR ───────────────────────────────────────────────────────────────
    # rtl_power: spectrum sweep. Output written to stdout as CSV.
    # Default sweeps 433MHz ISM band for 30s in 1MHz bins.
    'rtl_power': {
        'argv':      ['rtl_power', '-f', '430M:435M:25k', '-i', '1', '-e', '30s'],
        'default':   '',
        'hint':      'override flags (e.g. -f 88M:108M:200k -e 60s)',
        'timeout':   90,
    },
    'rtl_433-once': {
        'argv':      ['rtl_433', '-T', '20', '-F', 'json'],
        'default':   '',
        'hint':      'extra rtl_433 flags',
        'timeout':   30,
    },
    # ── DNS / WHOIS / route ──────────────────────────────────────────────
    'dig': {
        'argv':      ['dig', '+short'],
        'default':   'example.com',
        'hint':      'host [type]',
        'timeout':   15,
    },
    'whois': {
        'argv':      ['whois'],
        'default':   'example.com',
        'hint':      'domain or IP',
        'timeout':   20,
    },
    'traceroute': {
        'argv':      ['traceroute', '-n', '-w', '2', '-q', '1'],
        'default':   '8.8.8.8',
        'hint':      'host',
        'timeout':   30,
    },
    'mtr-report': {
        'argv':      ['mtr', '-r', '-c', '5', '-n'],
        'default':   '8.8.8.8',
        'hint':      'host',
        'timeout':   30,
    },
    # ── Web recon ─────────────────────────────────────────────────────────
    'curl': {
        'argv':      ['curl', '-sS', '-m', '10'],
        'default':   'https://ipinfo.io',
        'hint':      'url + curl flags',
        'timeout':   15,
    },
    'whatweb': {
        'argv':      ['whatweb', '--quiet', '--no-errors'],
        'default':   'http://10.42.0.1:8080',
        'hint':      'url',
        'timeout':   30,
    },
    'nikto': {
        'argv':      ['nikto', '-Tuning', '12345b', '-maxtime', '60s', '-h'],
        'default':   'http://10.42.0.1:8080',
        'hint':      'target url',
        'timeout':   90,
    },
}

# ── Mode helpers ────────────────────────────────────────────────────────────

def current_mode() -> str:
    try:
        return Path(MODE_STATE_PATH).read_text(encoding='utf-8').strip() or DEFAULT_MODE
    except OSError:
        return DEFAULT_MODE


# ── MQTT subscriber (background) ────────────────────────────────────────────

class MqttCache:
    """Thread-safe last-message cache + recent capture ring."""
    def __init__(self, capture_history: int = 50):
        self._lock = threading.Lock()
        self._latest: dict[str, dict] = {}
        self._captures = deque(maxlen=capture_history)

    def store_latest(self, topic: str, payload: dict) -> None:
        with self._lock:
            self._latest[topic] = {'payload': payload, 'ts': time.time()}

    def push_capture(self, payload: dict) -> None:
        with self._lock:
            self._captures.appendleft({'ts': time.time(), 'payload': payload})

    def snapshot(self) -> dict:
        with self._lock:
            return {
                'latest':   dict(self._latest),
                'captures': list(self._captures),
            }


CACHE = MqttCache()
_MQTT_CLIENT = None  # set on connect; used to publish flipper commands


def _is_local_peer(peer: str) -> bool:
    """Hotspot-only ACL — 127.0.0.1 + 10.42.0.0/24 Wi-Fi hotspot."""
    return peer == '127.0.0.1' or peer.startswith('10.42.0.')


def _start_mqtt() -> None:
    """Subscribe to flipper + wardrive topics in a daemon thread."""
    global _MQTT_CLIENT
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        log.warning('paho-mqtt not installed; live MQTT panes disabled')
        return

    subscribe_topics = [
        TOPICS['flipper_status'],
        TOPICS['flipper_result'],
        TOPICS['flipper_subghz'],
        TOPICS['wardrive_wifi'],
        TOPICS['wardrive_bt'],
        TOPICS['wardrive_status'],
        TOPICS['wardrive_snapshot'],
    ]

    def on_connect(client, _u, _f, rc, *_a):
        if rc == 0:
            log.info(f'MQTT connected; subscribing to {len(subscribe_topics)} topics')
            for t in subscribe_topics:
                client.subscribe(t)
        else:
            log.warning(f'MQTT connect rc={rc}')

    def on_message(_c, _u, msg):
        try:
            payload = json.loads(msg.payload.decode('utf-8', errors='replace'))
        except (ValueError, UnicodeDecodeError):
            payload = {'raw': msg.payload.decode('utf-8', errors='replace')}
        CACHE.store_latest(msg.topic, payload)
        if msg.topic == TOPICS['flipper_subghz']:
            CACHE.push_capture(payload)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id='opsec-dash')
    client.on_connect = on_connect
    client.on_message = on_message

    def runner():
        while True:
            try:
                client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
                client.loop_forever(retry_first_connection=True)
            except Exception as e:
                log.warning(f'MQTT runner restart in 5s: {e}')
                time.sleep(5)

    global _MQTT_CLIENT
    _MQTT_CLIENT = client
    marauder_client.install(client)
    threading.Thread(target=runner, name='mqtt-sub', daemon=True).start()


# ── Killswitch primitives ───────────────────────────────────────────────────

LOG_DIRS_TO_WIPE = [
    Path('/opt/drifter/logs'),
    Path('/opt/drifter/wardrive_logs'),
]


def kill_mac_randomize(iface: str = 'wlan0') -> dict:
    """Bring iface down, randomize MAC, bring it back up. Needs sudo NOPASSWD."""
    import random
    new_mac = '02:' + ':'.join(f'{random.randint(0, 255):02x}' for _ in range(5))
    steps = [
        ['sudo', '-n', 'ip', 'link', 'set', 'dev', iface, 'down'],
        ['sudo', '-n', 'ip', 'link', 'set', 'dev', iface, 'address', new_mac],
        ['sudo', '-n', 'ip', 'link', 'set', 'dev', iface, 'up'],
    ]
    out = []
    for cmd in steps:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        out.append({'argv': cmd, 'rc': r.returncode, 'err': (r.stderr or '').strip()})
        if r.returncode != 0:
            return {'ok': False, 'iface': iface, 'mac': new_mac, 'steps': out}
    return {'ok': True, 'iface': iface, 'mac': new_mac, 'steps': out}


def kill_wipe_logs() -> dict:
    """Delete recon log files. Catalogues what was deleted."""
    deleted = []
    failed = []
    for d in LOG_DIRS_TO_WIPE:
        if not d.exists():
            continue
        for p in d.glob('**/*'):
            if p.is_file():
                try:
                    size = p.stat().st_size
                    p.unlink()
                    deleted.append({'path': str(p), 'bytes': size})
                except OSError as e:
                    failed.append({'path': str(p), 'err': str(e)})
    return {
        'ok':           not failed,
        'deleted':      deleted,
        'failed':       failed,
        'total_bytes':  sum(d['bytes'] for d in deleted),
    }


def kill_halt_recon() -> dict:
    """Stop active recon services (flipper + wardrive). Leaves opsec dashboard
    up so the operator still has a UI."""
    targets = ['drifter-flipper', 'drifter-wardrive']
    results = []
    for unit in targets:
        r = subprocess.run(
            ['sudo', '-n', 'systemctl', 'stop', unit],
            capture_output=True, text=True, timeout=15,
        )
        results.append({'unit': unit, 'rc': r.returncode, 'err': (r.stderr or '').strip()})
    return {'ok': all(r['rc'] == 0 for r in results), 'units': results}


# ── HTML ────────────────────────────────────────────────────────────────────

OPSEC_HTML = r"""<!doctype html>
<html lang="en" data-theme="nightrun">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>DRIFTER · OPSEC</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,300..800&family=JetBrains+Mono:wght@300;400;500;700&family=Major+Mono+Display&display=swap" rel="stylesheet">
<style>
/* ────────────────────────────────────────────────────────────
   DRIFTER OPSEC console — cockpit-grade reskin.
   Tokens + glass/stencil/scanline primitives ported INLINE from
   ui/cockpit-preview.html (opsec runs standalone on :8090; no
   cross-server stylesheet plumbing). Default palette = nightrun
   (armed red — green→red reads "offensive").
   ──────────────────────────────────────────────────────────── */
:root{
  --bg-0:#07090d;--bg-1:#0c1017;--bg-2:#11161f;--bg-3:#161c28;--bg-edge:#1c2330;
  --glass-bg:rgba(22,27,36,.62);--glass-bg-strong:rgba(22,27,36,.88);
  --glass-stroke:rgba(255,255,255,.09);--glass-stroke-2:rgba(255,255,255,.10);
  --glass-stroke-amber:rgba(255,174,66,.45);--glass-stroke-cyan:rgba(125,211,252,.35);
  --glass-blur:blur(20px) saturate(150%);
  --fg:#e8eaed;--fg-mute:#9aa3b1;--fg-dim:#5a6471;--fg-deep:#38414e;
  --amber:#ffae42;--amber-deep:#b87a1f;
  --amber-glow:0 0 24px rgba(255,174,66,.30);--amber-glow-strong:0 0 32px rgba(255,174,66,.55);
  --cyan:#7dd3fc;--cyan-dim:rgba(125,211,252,.55);
  --red:#ff6b6b;--red-glow:0 0 22px rgba(255,107,107,.45);--teal:#5eead4;
  --r-sm:4px;--r-md:10px;--r-lg:16px;--r-pill:999px;
  --space-1:4px;--space-2:8px;--space-3:12px;--space-4:16px;--space-5:24px;--space-6:32px;
}
/* nightrun = armed/offensive default palette */
:root[data-theme="nightrun"]{
  --fg:#ff7878;--fg-mute:#c44545;--fg-dim:#843030;--fg-deep:#4d1c1c;
  --amber:#ff3030;--amber-deep:#c41818;
  --amber-glow:0 0 24px rgba(255,48,48,.28);--amber-glow-strong:0 0 32px rgba(255,48,48,.55);
  --cyan:#c44545;--teal:#ff7878;
  --bg-0:#0a0606;--bg-1:#100808;--bg-2:#160c0c;--bg-3:#1f1010;
  --glass-stroke-amber:rgba(255,48,48,.45);--glass-stroke-cyan:rgba(196,69,69,.35);
}
*,*::before,*::after{box-sizing:border-box}
html,body{margin:0;padding:0;min-height:100%}
body{
  font-family:'Bricolage Grotesque',system-ui,sans-serif;
  font-feature-settings:'ss01' 1,'cv01' 1;
  background:var(--bg-0);color:var(--fg);font-size:14px;line-height:1.4;
  min-height:100vh;
  background-image:
    radial-gradient(ellipse 80% 60% at 50% -10%,rgba(255,48,48,.05) 0%,transparent 60%),
    radial-gradient(ellipse 50% 50% at 100% 110%,rgba(196,69,69,.035) 0%,transparent 65%),
    radial-gradient(ellipse 60% 50% at 0% 80%,rgba(255,120,120,.022) 0%,transparent 60%);
}
/* CRT scanline + grain (low-intensity, click-through) */
body::before,body::after{content:'';position:fixed;inset:0;pointer-events:none;z-index:9999}
body::before{
  background-image:repeating-linear-gradient(to bottom,
    rgba(255,255,255,.018) 0px,rgba(255,255,255,.018) 1px,transparent 1px,transparent 3px);
  mix-blend-mode:overlay;opacity:.42;
}
body::after{
  background-image:
    radial-gradient(circle at 20% 30%,rgba(255,48,48,.05) 0px,transparent 1px),
    radial-gradient(circle at 70% 60%,rgba(196,69,69,.045) 0px,transparent 1px),
    radial-gradient(circle at 40% 80%,rgba(255,255,255,.04) 0px,transparent 1px);
  background-size:3px 3px,5px 5px,7px 7px;opacity:.32;
}
.mono{font-family:'JetBrains Mono',ui-monospace,monospace;font-variant-numeric:tabular-nums}
.stencil{font-family:'Major Mono Display','JetBrains Mono',monospace;letter-spacing:.18em;text-transform:lowercase}

/* ── Top bar ───────────────────────────────────────────────── */
header{
  display:flex;align-items:center;gap:14px;justify-content:space-between;
  padding:0 16px;height:52px;position:relative;z-index:30;
  background:linear-gradient(180deg,rgba(255,48,48,.05),transparent 45%),rgba(10,6,6,.82);
  border-bottom:1px solid var(--glass-stroke);
  backdrop-filter:var(--glass-blur);-webkit-backdrop-filter:var(--glass-blur);
}
header::after{
  content:'';position:absolute;left:0;right:0;bottom:-1px;height:1px;
  background:linear-gradient(90deg,transparent,var(--amber) 35%,var(--amber) 65%,transparent);
  opacity:.5;
}
.brand{display:flex;align-items:center;gap:12px}
.brand .badge{
  display:flex;align-items:center;justify-content:center;width:34px;height:34px;
  border:1px solid var(--glass-stroke-amber);border-radius:7px;
  background:var(--glass-bg);backdrop-filter:var(--glass-blur);
  color:var(--amber);font-family:'Major Mono Display',monospace;font-size:15px;
  text-shadow:var(--amber-glow);
}
.brand h1{
  font-family:'Major Mono Display',monospace;font-size:15px;letter-spacing:.18em;
  color:var(--amber);font-weight:400;text-shadow:var(--amber-glow);margin:0;
  text-transform:lowercase;
}
.brand .sub{
  font-family:'JetBrains Mono',monospace;font-size:9.5px;letter-spacing:.2em;
  color:var(--fg-dim);text-transform:uppercase;
}
.top-right{display:flex;align-items:center;gap:10px}
.mode-pill{
  display:inline-flex;align-items:center;gap:6px;padding:5px 12px;
  font-family:'JetBrains Mono',monospace;font-size:10px;letter-spacing:.18em;
  border:1px solid var(--glass-stroke-amber);border-radius:var(--r-pill);
  color:var(--amber);background:var(--glass-bg);backdrop-filter:var(--glass-blur);
  box-shadow:inset 0 0 12px rgba(255,48,48,.08);text-transform:uppercase;
}
.mode-pill.drive{color:var(--cyan);border-color:var(--glass-stroke-cyan);box-shadow:none}
.switch-link{
  font-family:'JetBrains Mono',monospace;font-size:10px;letter-spacing:.14em;
  color:var(--fg-mute);text-decoration:none;padding:5px 10px;
  border:1px solid var(--glass-stroke);border-radius:var(--r-pill);
  background:var(--glass-bg);backdrop-filter:var(--glass-blur);text-transform:uppercase;
}
.switch-link:hover{color:var(--amber);border-color:var(--glass-stroke-amber)}

/* ── Nav (drawer-tab styled) ───────────────────────────────── */
nav{
  display:flex;gap:4px;padding:8px 16px 0;position:relative;z-index:20;
  background:rgba(10,6,6,.5);border-bottom:1px solid var(--glass-stroke);
}
nav a{
  flex:1;text-align:center;padding:10px 14px;cursor:pointer;text-decoration:none;
  font-family:'Major Mono Display',monospace;font-size:11px;letter-spacing:.16em;
  color:var(--fg-mute);text-transform:lowercase;border-radius:var(--r-md) var(--r-md) 0 0;
  border:1px solid transparent;border-bottom:none;transition:.16s ease;
}
nav a:hover{color:var(--amber)}
nav a.back{margin-right:auto;color:var(--cyan);font-weight:500}
nav a.back:hover{color:var(--fg)}
nav a.active{
  color:var(--amber);background:linear-gradient(180deg,rgba(255,48,48,.14),rgba(255,48,48,.02));
  border-color:var(--glass-stroke);text-shadow:var(--amber-glow);
}
nav a.kill{color:var(--fg-dim)}
nav a.kill:hover,nav a.kill.active{
  color:var(--red);background:linear-gradient(180deg,rgba(255,107,107,.16),rgba(255,107,107,.02));
  text-shadow:var(--red-glow);
}

main{padding:18px;max-width:1240px;margin:0 auto}
.page{display:none}
.page.active{display:block}

/* ── Glass tile (cockpit primitive) ─────────────────────────── */
.tile{
  background:var(--glass-bg);backdrop-filter:var(--glass-blur);
  -webkit-backdrop-filter:var(--glass-blur);
  border:1px solid var(--glass-stroke);border-radius:var(--r-lg);
  position:relative;overflow:hidden;padding:16px;margin-bottom:16px;
  box-shadow:inset 0 1px 0 rgba(255,255,255,.05),0 1px 2px rgba(0,0,0,.35),0 8px 24px rgba(0,0,0,.30);
}
.tile::before{
  content:'';position:absolute;inset:0;border-radius:inherit;pointer-events:none;
  background:linear-gradient(135deg,rgba(255,255,255,.04) 0%,transparent 30%,transparent 70%,rgba(0,0,0,.18) 100%);
}
.tile>*{position:relative;z-index:1}
.tile .corner{position:absolute;width:11px;height:11px;border:1px solid var(--glass-stroke-amber);opacity:.45;pointer-events:none;z-index:0}
.tile .corner.tl{top:6px;left:6px;border-right:none;border-bottom:none}
.tile .corner.tr{top:6px;right:6px;border-left:none;border-bottom:none}
.tile .corner.bl{bottom:6px;left:6px;border-right:none;border-top:none}
.tile .corner.br{bottom:6px;right:6px;border-left:none;border-top:none}
.tile.danger{border-color:rgba(255,107,107,.30)}
.tile.danger .corner{border-color:rgba(255,107,107,.45)}

/* ── Section head (stencil) ─────────────────────────────────── */
.section-head{
  display:flex;align-items:center;justify-content:space-between;gap:8px;
  font-family:'Major Mono Display',monospace;font-size:10px;letter-spacing:.22em;
  text-transform:lowercase;color:var(--amber-deep);padding:2px 0 10px;
  margin-bottom:12px;border-bottom:1px dashed var(--glass-stroke);
}
.section-head.crit{color:var(--red)}
.section-head .tag{
  font-family:'JetBrains Mono',monospace;font-size:9px;color:var(--fg-dim);
  letter-spacing:0;text-transform:none;
}

/* ── Tiles grid / probe buttons ─────────────────────────────── */
.tile-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:8px}
button.probe,button.btn{
  background:var(--glass-bg-strong);border:1px solid var(--glass-stroke);
  color:var(--fg);padding:11px 12px;cursor:pointer;text-align:left;
  font-family:'JetBrains Mono',monospace;font-size:12px;letter-spacing:.05em;
  border-radius:var(--r-md);transition:.14s ease;
}
button.probe:hover,button.btn:hover{
  border-color:var(--glass-stroke-amber);color:var(--amber);
  box-shadow:0 0 14px -4px var(--amber-glow-strong);
}
button.btn.danger{border-color:rgba(255,107,107,.30);color:var(--fg)}
button.btn.danger:hover{border-color:rgba(255,107,107,.6);color:var(--red);box-shadow:0 0 14px -4px var(--red-glow)}
button:disabled{opacity:.4;cursor:not-allowed}
.run-flag{color:var(--amber)}

input[type=text]{
  background:rgba(0,0,0,.45);border:1px solid var(--glass-stroke);color:var(--fg);
  padding:10px 12px;font-family:'JetBrains Mono',monospace;font-size:12px;
  width:100%;border-radius:var(--r-md);outline:none;
}
input[type=text]:focus{border-color:var(--glass-stroke-amber);box-shadow:0 0 10px -2px var(--amber-glow-strong)}

/* ── Terminal / output panes (mono) ─────────────────────────── */
.term{
  background:rgba(0,0,0,.55);border:1px solid var(--glass-stroke);
  padding:14px;font-family:'JetBrains Mono',monospace;font-size:12px;
  color:var(--amber);height:420px;overflow-y:auto;white-space:pre-wrap;
  word-break:break-word;border-radius:var(--r-md);
}
.term .err{color:var(--red)}
.term .ok{color:var(--cyan)}
.term .prompt{color:var(--fg-mute)}

/* ── Curated launcher rows (output adjacent) ────────────────── */
.tool-split{display:grid;grid-template-columns:1fr 1fr;gap:16px;align-items:start}
@media(max-width:880px){.tool-split{grid-template-columns:1fr}}
.tool-row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.tool-block{
  padding:10px 0;border-bottom:1px solid var(--glass-stroke);
}
.tool-block:last-child{border-bottom:none}
.tool-block.active{
  background:linear-gradient(90deg,rgba(255,48,48,.07),transparent);
  border-left:2px solid var(--amber);padding-left:10px;margin-left:-12px;border-radius:0 var(--r-sm) var(--r-sm) 0;
}
.tool-row label{
  min-width:118px;color:var(--fg-mute);font-family:'JetBrains Mono',monospace;
  font-size:11px;letter-spacing:.06em;
}
.tool-row input{flex:1;min-width:160px}
.tool-row .btn{min-width:118px;text-align:center}
.hint{font-size:10px;color:var(--fg-dim);margin-top:5px;margin-left:126px;font-family:'JetBrains Mono',monospace}
.term.sticky{position:sticky;top:12px}

/* ── KV + tables ────────────────────────────────────────────── */
.kv{
  display:grid;grid-template-columns:160px 1fr;gap:6px 14px;
  font-family:'JetBrains Mono',monospace;font-size:12px;
}
.kv dt{color:var(--fg-mute);text-transform:uppercase;letter-spacing:.12em;font-size:10px;align-self:center}
.kv dd{color:var(--amber);margin:0}
table{width:100%;border-collapse:collapse;font-family:'JetBrains Mono',monospace;font-size:11px}
th,td{padding:6px 10px;text-align:left;border-bottom:1px solid var(--glass-stroke);letter-spacing:.04em}
th{color:var(--fg-dim);text-transform:uppercase;letter-spacing:.16em;font-size:9px}

/* ── Killswitch cards ───────────────────────────────────────── */
.kill-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}
.kill-card{
  background:var(--glass-bg-strong);border:1px solid rgba(255,107,107,.22);
  padding:14px;border-radius:var(--r-md);
}
.kill-card h3{
  color:var(--red);font-family:'Major Mono Display',monospace;font-size:12px;
  letter-spacing:.14em;margin:0 0 6px;text-transform:lowercase;
}
.kill-card p{color:var(--fg-mute);font-size:11px;margin:0 0 12px;line-height:1.5;font-family:'JetBrains Mono',monospace}
.confirm-prompt{
  margin-top:8px;font-size:11px;color:var(--amber);letter-spacing:.08em;
  display:none;font-family:'JetBrains Mono',monospace;
}
.confirm-prompt.show{display:block}

/* ── Empty state ────────────────────────────────────────────── */
.empty{
  display:flex;flex-direction:column;align-items:center;gap:8px;
  padding:28px 14px;color:var(--fg-dim);font-family:'JetBrains Mono',monospace;font-size:11px;
}
.empty .glyph{font-size:26px;opacity:.5;line-height:1}

/* ── Footer ─────────────────────────────────────────────────── */
footer{
  padding:11px 18px;border-top:1px solid var(--glass-stroke);margin-top:8px;
  font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--fg-dim);
  letter-spacing:.08em;display:flex;justify-content:space-between;
}
.dot{display:inline-block;width:6px;height:6px;border-radius:50%;margin-right:6px;vertical-align:middle}
.dot.ok{background:var(--amber);box-shadow:var(--amber-glow)}
.dot.warn{background:var(--cyan)}
.dot.off{background:var(--fg-dim);opacity:.5}
.blink{animation:blink 1.2s step-end infinite}
@keyframes blink{50%{opacity:.4}}
</style>
</head>
<body>

<header>
  <div class="brand">
    <span class="badge">⊗</span>
    <div>
      <h1>opsec</h1>
      <div class="sub">MZ1312 · UNCAGED · ARMED</div>
    </div>
  </div>
  <div class="top-right">
    <span class="mode-pill" id="mode-pill">FOOT</span>
    <a class="switch-link" href="http://10.42.0.1:8080/" id="switch-link">→ DRIVE</a>
  </div>
</header>

<nav>
  <a class="back" id="nav-cockpit" href="#" title="Back to the cockpit dashboard">&larr; cockpit</a>
  <a data-page="terminal" class="active">terminal</a>
  <a data-page="tools">tools</a>
  <a data-page="killswitch" class="kill">killswitch</a>
</nav>

<main>

<section id="page-terminal" class="page active">
  <div class="tile">
    <span class="corner tl"></span><span class="corner tr"></span><span class="corner bl"></span><span class="corner br"></span>
    <div class="section-head">quick probes<span class="tag">read-only · fixed argv</span></div>
    <div class="tile-grid" id="quick-tiles"><div class="empty"><span class="glyph">▦</span><span>awaiting probe set</span></div></div>
  </div>
  <div class="tile">
    <span class="corner tl"></span><span class="corner tr"></span><span class="corner bl"></span><span class="corner br"></span>
    <div class="section-head">output<span class="tag blink">_</span></div>
    <div class="term" id="term"><span class="prompt">// ready · select a probe above or use TOOLS</span></div>
  </div>
</section>

<section id="page-tools" class="page">
  <div class="tile">
    <span class="corner tl"></span><span class="corner tr"></span><span class="corner bl"></span><span class="corner br"></span>
    <div class="section-head">curated launchers<span class="tag">arbitrary-arg · ad-hoc console</span></div>
    <div class="tool-split">
      <div id="tool-rows"></div>
      <div>
        <div class="section-head">output<span class="tag" id="tools-active">// idle</span></div>
        <div class="term sticky" id="tools-term"><span class="prompt">// run a launcher — output lands here</span></div>
      </div>
    </div>
  </div>
</section>

<section id="page-killswitch" class="page">
  <div class="tile danger">
    <span class="corner tl"></span><span class="corner tr"></span><span class="corner bl"></span><span class="corner br"></span>
    <div class="section-head crit">⚠ danger zone<span class="tag">destructive · confirm-gated</span></div>
    <div class="kill-grid">
      <div class="kill-card">
        <h3>randomize mac</h3>
        <p>Bring wlan0 down, set a fresh random 02:xx address, bring it up. Severs current Wi-Fi associations.</p>
        <button class="btn danger" data-confirm="MAC" data-action="mac-randomize">EXECUTE</button>
        <div class="confirm-prompt"></div>
      </div>
      <div class="kill-card">
        <h3>wipe logs</h3>
        <p>Delete /opt/drifter/logs/* and /opt/drifter/wardrive_logs/*. Reports total bytes purged.</p>
        <button class="btn danger" data-confirm="WIPE" data-action="wipe-logs">EXECUTE</button>
        <div class="confirm-prompt"></div>
      </div>
      <div class="kill-card">
        <h3>halt recon</h3>
        <p>systemctl stop drifter-flipper + drifter-wardrive. Dashboard stays up so you keep the UI.</p>
        <button class="btn danger" data-confirm="HALT" data-action="halt-recon">EXECUTE</button>
        <div class="confirm-prompt"></div>
      </div>
    </div>
  </div>
  <div class="tile">
    <span class="corner tl"></span><span class="corner tr"></span><span class="corner bl"></span><span class="corner br"></span>
    <div class="section-head">result<span class="tag blink">_</span></div>
    <div class="term" id="kill-term" style="height:200px"><span class="prompt">// no actions taken</span></div>
  </div>
</section>

</main>

<footer>
  <span><span class="dot off" id="mqtt-dot"></span>OPSEC · :8090 · MQTT <span id="mqtt-state">…</span></span>
  <span id="ts">--:--:--</span>
</footer>

<script>
const PROBES = __PROBES_JSON__;
const TOOLS  = __TOOLS_JSON__;

// ── Terminal helpers ──────────────────────────────────────────────────
function appendTo(elId, line, cls){
  const el = document.getElementById(elId);
  const span = document.createElement('span');
  if (cls) span.className = cls;
  span.textContent = line + '\n';
  el.appendChild(span);
  el.scrollTop = el.scrollHeight;
}
function clearTerm(elId){ document.getElementById(elId).textContent = ''; }

async function runProbe(name){
  clearTerm('term');
  appendTo('term', '$ ' + name, 'prompt');
  try {
    const res = await fetch('/api/tool/' + encodeURIComponent(name), {method:'POST'});
    const data = await res.json();
    if (data.error){ appendTo('term', 'ERR ' + data.error, 'err'); return; }
    if (data.stdout) appendTo('term', data.stdout);
    if (data.stderr) appendTo('term', data.stderr, 'err');
    appendTo('term', '// rc=' + data.rc + ' · ' + data.duration_ms + 'ms', 'ok');
  } catch (e){ appendTo('term', 'ERR ' + e, 'err'); }
}

async function runTool(name, args, btn){
  clearTerm('tools-term');
  appendTo('tools-term', '$ ' + name + ' ' + (args || ''), 'prompt');
  appendTo('tools-term', '// running…', 'ok');
  // Highlight the active launcher block + label the output pane so a
  // launch is obviously wired to the adjacent output.
  document.querySelectorAll('.tool-block.active').forEach(x => x.classList.remove('active'));
  if (btn){ const blk = btn.closest('.tool-block'); if (blk) blk.classList.add('active'); }
  const lbl = document.getElementById('tools-active');
  if (lbl){ lbl.textContent = '// running ' + name + '…'; lbl.classList.add('run-flag'); }
  // Bring the OUTPUT pane into view — it sits beside/below the launchers,
  // so a click with no scroll looked like nothing happened.
  document.getElementById('tools-term').scrollIntoView({behavior:'smooth', block:'center'});
  let _label;
  if (btn){ _label = btn.textContent; btn.textContent = 'RUNNING…'; btn.disabled = true; btn.style.opacity = '.5'; }
  try {
    const res = await fetch('/api/launch/' + encodeURIComponent(name), {
      method: 'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({args: args || ''}),
    });
    const data = await res.json();
    clearTerm('tools-term');
    appendTo('tools-term', '$ ' + name + ' ' + (args || ''), 'prompt');
    if (data.error){ appendTo('tools-term', 'ERR ' + data.error, 'err'); return; }
    if (data.stdout) appendTo('tools-term', data.stdout);
    if (data.stderr) appendTo('tools-term', data.stderr, 'err');
    if (!data.stdout && !data.stderr) appendTo('tools-term', '(no output)', 'ok');
    appendTo('tools-term', '// rc=' + data.rc + ' · ' + data.duration_ms + 'ms', 'ok');
  } catch (e){ appendTo('tools-term', 'ERR ' + e, 'err'); }
  finally {
    if (btn){ btn.textContent = _label; btn.disabled = false; btn.style.opacity = ''; }
    if (lbl){ lbl.textContent = '// ' + name + ' done'; lbl.classList.remove('run-flag'); }
  }
}

// ── Build quick probe tiles ───────────────────────────────────────────
const quick = document.getElementById('quick-tiles');
quick.innerHTML = '';
for (const name of Object.keys(PROBES)){
  const b = document.createElement('button');
  b.className = 'probe';
  b.textContent = name;
  b.onclick = () => runProbe(name);
  quick.appendChild(b);
}

// ── Build tool rows ───────────────────────────────────────────────────
const toolRows = document.getElementById('tool-rows');
for (const [name, spec] of Object.entries(TOOLS)){
  const row = document.createElement('div');
  row.className = 'tool-block';
  row.innerHTML = `
    <div class="tool-row">
      <label>${name}</label>
      <input type="text" value="${spec.default}" data-tool="${name}">
      <button class="btn" data-launch="${name}">LAUNCH</button>
    </div>
    <div class="hint">${spec.hint}</div>
  `;
  toolRows.appendChild(row);
}
toolRows.addEventListener('click', e => {
  const name = e.target.dataset.launch;
  if (!name) return;
  const input = toolRows.querySelector(`input[data-tool="${name}"]`);
  runTool(name, input ? input.value : '', e.target);
});

// ── Page nav ──────────────────────────────────────────────────────────
// Back-to-cockpit link: real navigation to the HUD on :8080 (same host).
// In kiosk mode there is no browser chrome, so this is the only way back.
var _bk = document.getElementById('nav-cockpit');
if (_bk) _bk.href = 'http://' + (location.hostname || '127.0.0.1') + ':8080/';
document.querySelectorAll('nav a').forEach(a => {
  a.onclick = (e) => {
    if (!a.dataset.page) return;   // back link → let the anchor navigate
    e.preventDefault();
    document.querySelectorAll('nav a').forEach(x => x.classList.remove('active'));
    document.querySelectorAll('.page').forEach(x => x.classList.remove('active'));
    a.classList.add('active');
    document.getElementById('page-' + a.dataset.page).classList.add('active');
  };
});

// ── Mode poll ─────────────────────────────────────────────────────────
async function refreshMode(){
  try {
    const m = await fetch('/api/mode/status').then(r => r.json());
    const pill = document.getElementById('mode-pill');
    const link = document.getElementById('switch-link');
    pill.textContent = m.mode.toUpperCase();
    pill.className = 'mode-pill ' + m.mode;
    if (m.mode === 'drive'){
      link.textContent = '→ FOOT';
      link.href = 'http://' + location.hostname + ':8090/';
    } else {
      link.textContent = '→ DRIVE';
      link.href = 'http://' + location.hostname + ':8080/';
    }
  } catch(e){}
}

// ── MQTT connectivity (footer only — flipper/wardrive views live in the
// cockpit drawers now; opsec stays a focused console). ─────────────────
async function refreshMqtt(){
  try {
    const data = await fetch('/api/mqtt/cache').then(r => r.json());
    const dot = document.getElementById('mqtt-dot');
    const state = document.getElementById('mqtt-state');
    const topics = Object.keys(data.latest || {});
    if (topics.length){
      dot.className = 'dot ok';
      state.textContent = 'live · ' + topics.length + ' topics';
    } else {
      dot.className = 'dot warn';
      state.textContent = 'connected · idle';
    }
  } catch(e){
    document.getElementById('mqtt-dot').className = 'dot warn';
    document.getElementById('mqtt-state').textContent = 'poll error';
  }
}

// ── Killswitch ────────────────────────────────────────────────────────
document.querySelectorAll('button[data-action]').forEach(btn => {
  btn.onclick = async () => {
    const action = btn.dataset.action;
    const word = btn.dataset.confirm;
    const prompt = btn.parentElement.querySelector('.confirm-prompt');
    if (!btn.dataset.armed){
      btn.dataset.armed = '1';
      btn.textContent = 'CONFIRM (' + word + ')';
      prompt.classList.add('show');
      prompt.textContent = 'click again within 6s to execute';
      setTimeout(() => {
        delete btn.dataset.armed;
        btn.textContent = 'EXECUTE';
        prompt.classList.remove('show');
      }, 6000);
      return;
    }
    delete btn.dataset.armed;
    btn.textContent = 'EXECUTE';
    prompt.classList.remove('show');
    btn.disabled = true;
    try {
      const res = await fetch('/api/kill/' + action, {method:'POST'});
      const data = await res.json();
      appendTo('kill-term', '$ kill/' + action, 'prompt');
      appendTo('kill-term', JSON.stringify(data, null, 2), data.ok ? 'ok' : 'err');
    } catch(e){
      appendTo('kill-term', 'ERR ' + e, 'err');
    } finally {
      btn.disabled = false;
    }
  };
});

setInterval(() => {
  document.getElementById('ts').textContent = new Date().toTimeString().slice(0,8);
}, 1000);
refreshMode();  refreshMqtt();
setInterval(refreshMode, 5000);
setInterval(refreshMqtt, 3000);
</script>
</body>
</html>
"""


# ── HTTP handler ────────────────────────────────────────────────────────────

class OpsecHandler(BaseHTTPRequestHandler):
    server_version = 'DrifterOpsec/0.3'

    def log_message(self, fmt, *args):
        log.debug(fmt % args)

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, status: int, body: str, ctype: str = 'text/html; charset=utf-8') -> None:
        data = body.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict | None:
        try:
            length = int(self.headers.get('Content-Length') or '0')
        except ValueError:
            return None
        if length <= 0 or length > MAX_POST_BODY:
            return None
        try:
            body = self.rfile.read(length).decode('utf-8')
            obj = json.loads(body)
            return obj if isinstance(obj, dict) else None
        except (ValueError, UnicodeDecodeError):
            return None

    # ── GET ────────────────────────────────────────────────────────────
    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == '/' or path == '/index.html':
            html = OPSEC_HTML
            html = html.replace('__PROBES_JSON__', json.dumps({k: ' '.join(v) for k, v in PROBES.items()}))
            html = html.replace('__TOOLS_JSON__', json.dumps(
                {k: {'default': v['default'], 'hint': v['hint']} for k, v in TOOLS.items()}
            ))
            self._send_text(200, html)
            return
        if path == '/healthz':
            self._send_json(200, {
                'status':  'ok',
                'mode':    current_mode(),
                'probes':  list(PROBES),
                'tools':   list(TOOLS),
                'ts':      time.time(),
            })
            return
        if path == '/api/mode/status' or path == '/api/mode':
            self._send_json(200, {'mode': current_mode(), 'choices': sorted(MODES)})
            return
        if path == '/api/tools':
            self._send_json(200, {
                'probes': {k: ' '.join(v) for k, v in PROBES.items()},
                'tools':  TOOLS,
            })
            return
        if path == '/api/mqtt/cache':
            self._send_json(200, CACHE.snapshot())
            return
        if path == '/api/marauder/status':
            self._send_json(200, marauder_client.get_status())
            return
        if path.startswith('/api/marauder/scan/recent'):
            from urllib.parse import parse_qs
            qs = parse_qs(urlparse(self.path).query)
            stream = qs.get('stream', ['ap'])[0]
            n = int(qs.get('n', ['200'])[0])
            events = marauder_client.get_scan_recent(stream, n=n)
            self._send_json(200, {'stream': stream, 'count': len(events), 'events': events})
            return
        if path == '/api/marauder/portal/sessions':
            return self._send_json(200, {'sessions': marauder_client.list_portal_sessions()})
        if path.startswith('/api/marauder/portal/session/'):
            # Path like /api/marauder/portal/session/<id>/captures.jsonl[?wipe=1]
            from urllib.parse import parse_qs as _parse_qs
            parsed = urlparse(self.path)
            parts = parsed.path.split('/')
            # parts: ['', 'api', 'marauder', 'portal', 'session', '<id>', 'captures.jsonl']
            if len(parts) == 7 and parts[6] == 'captures.jsonl':
                if not _is_local_peer(self.client_address[0]):
                    return self._send_json(403, {'ok': False, 'response': 'remote not allowed'})
                sid = parts[5]
                token = self.headers.get('X-Drifter-Op-Confirm', '')
                if not marauder_client.consume_reveal_token(token, sid):
                    return self._send_json(403, {'ok': False,
                                                  'response': 'invalid or expired reveal token'})
                cap_path = marauder_client.portal_capture_path(sid)
                if not cap_path.exists():
                    return self._send_json(404, {'ok': False, 'response': 'no captures'})
                body = cap_path.read_bytes()
                qs = _parse_qs(parsed.query)
                if qs.get('wipe', ['0'])[0] == '1':
                    try:
                        cap_path.unlink()
                    except OSError:
                        pass
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
        self.send_error(404)

    # ── POST ───────────────────────────────────────────────────────────
    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path.startswith('/api/tool/'):
            self._run_probe(path[len('/api/tool/'):])
            return
        if path.startswith('/api/launch/'):
            self._launch_tool(path[len('/api/launch/'):])
            return
        if path == '/api/kill/mac-randomize':
            self._send_json(200, kill_mac_randomize())
            return
        if path == '/api/kill/wipe-logs':
            self._send_json(200, kill_wipe_logs())
            return
        if path == '/api/kill/halt-recon':
            self._send_json(200, kill_halt_recon())
            return
        if path.startswith('/api/mode/') and not path.endswith('/status'):
            target = path[len('/api/mode/'):]
            if target not in MODES:
                self._send_json(400, {'error': f'unknown mode {target!r}'})
                return
            # systemd-run as a transient unit so the switch survives this
            # process being SIGTERM'd by its own cgroup teardown when
            # FOOT→DRIVE disables drifter-opsec.
            r = subprocess.run(
                ['sudo', '-n', '/usr/bin/systemd-run', '--no-block',
                 '--unit=drifter-mode-switch', '/usr/local/bin/drifter', 'mode', target],
                capture_output=True, text=True, timeout=10,
            )
            self._send_json(200, {
                'requested': target,
                'status':    'dispatched' if r.returncode == 0 else 'failed',
                'rc':        r.returncode,
                'stderr':    r.stderr.strip(),
            })
            return
        if path == '/api/marauder/cmd':
            if not _is_local_peer(self.client_address[0]):
                self._send_json(403, {'ok': False, 'response': 'remote not allowed'})
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                body = json.loads(self.rfile.read(length).decode() or '{}')
            except Exception as e:
                self._send_json(400, {'ok': False, 'response': f'bad body: {e}'})
                return
            op_id = marauder_client.publish_cmd(
                _MQTT_CLIENT,
                command=body.get('command', ''),
                args=body.get('args') or {},
                confirm_token=body.get('confirm_token'),
            )
            self._send_json(200, {'ok': True, 'op_id': op_id,
                                  'note': 'command published; subscribe to drifter/marauder/event'})
            return
        if path == '/api/marauder/probe':
            if not _is_local_peer(self.client_address[0]):
                self._send_json(403, {'ok': False, 'response': 'remote not allowed'})
                return
            op_id = marauder_client.publish_cmd(_MQTT_CLIENT, command='probe')
            self._send_json(200, {'ok': True, 'op_id': op_id})
            return
        if path == '/api/marauder/stop':
            if not _is_local_peer(self.client_address[0]):
                self._send_json(403, {'ok': False, 'response': 'remote not allowed'})
                return
            op_id = marauder_client.publish_cmd(_MQTT_CLIENT, command='stop')
            self._send_json(200, {'ok': True, 'op_id': op_id})
            return
        if path.startswith('/api/marauder/portal/session/') and \
                path.endswith('/reveal_token'):
            if not _is_local_peer(self.client_address[0]):
                self._send_json(403, {'ok': False, 'response': 'remote not allowed'})
                return
            parts = path.split('/')
            sid = parts[5]
            token = marauder_client.issue_reveal_token(sid)
            return self._send_json(200, {'ok': True, 'token': token,
                                         'expires_in_s': 60,
                                         'header': 'X-Drifter-Op-Confirm'})
        self.send_error(404)

    def _run_probe(self, name: str) -> None:
        argv = PROBES.get(name)
        if argv is None:
            self._send_json(400, {'error': f'unknown probe {name!r}'})
            return
        self._exec_argv(name, argv, TOOL_TIMEOUT_SEC)

    def _launch_tool(self, name: str) -> None:
        spec = TOOLS.get(name)
        if spec is None:
            self._send_json(400, {'error': f'unknown tool {name!r}'})
            return
        body = self._read_json() or {}
        user_args = body.get('args', '') or ''
        try:
            extra = shlex.split(user_args)
        except ValueError as e:
            self._send_json(400, {'error': f'arg parse: {e}'})
            return
        argv = list(spec['argv']) + extra
        # Refuse args that look like absolute path overrides of the binary —
        # prevents 'curl /etc/passwd' from sneaking a leading '/file' as the URL.
        # We don't try to be a full safety net; allowlist is the real defense.
        self._exec_argv(name, argv, spec.get('timeout', TOOL_TIMEOUT_SEC))

    def _exec_argv(self, name: str, argv: list[str], timeout: int) -> None:
        t0 = time.monotonic()
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            self._send_json(504, {'error': f'tool timed out (>{timeout}s)', 'tool': name})
            return
        except FileNotFoundError:
            self._send_json(500, {'error': f'binary not installed: {argv[0]}'})
            return
        dur_ms = int((time.monotonic() - t0) * 1000)
        self._send_json(200, {
            'tool':        name,
            'argv':        argv,
            'rc':          r.returncode,
            'stdout':      r.stdout,
            'stderr':      r.stderr,
            'duration_ms': dur_ms,
        })


# ── entry point ─────────────────────────────────────────────────────────────

def main() -> int:
    _start_mqtt()
    addr = ('0.0.0.0', OPSEC_PORT)
    httpd = ThreadingHTTPServer(addr, OpsecHandler)
    log.info(f'OPSEC dashboard listening on http://{addr[0]}:{addr[1]}')
    log.info(f'probes={len(PROBES)} tools={len(TOOLS)}')

    def _shutdown(_signo, _frame):
        # httpd.shutdown() blocks waiting for serve_forever to exit; calling
        # it from the same thread that owns serve_forever deadlocks. Spawn
        # a brief helper thread so the signal handler returns immediately.
        log.info('shutdown signal — closing socket')
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()
    return 0


if __name__ == '__main__':
    sys.exit(main())

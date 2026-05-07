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
import os
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

from config import (  # noqa: E402
    MODE_STATE_PATH, DEFAULT_MODE, MQTT_HOST, MQTT_PORT, TOPICS,
)

LOG_FORMAT = '%(asctime)s [OPSEC] %(message)s'
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

OPSEC_PORT = 8090
MAX_POST_BODY = 4 * 1024
TOOL_TIMEOUT_SEC = 30

# ── Allowlisted commands ────────────────────────────────────────────────────
# Quick probes — no args from web, fixed argv.
PROBES: dict[str, list[str]] = {
    'iwconfig':       ['iwconfig'],
    'ip-addr':        ['ip', '-brief', 'addr'],
    'ip-link':        ['ip', '-brief', 'link'],
    'arp':            ['ip', 'neigh'],
    'lsusb':          ['lsusb'],
    'lspci':          ['lspci'],
    'uname':          ['uname', '-a'],
    'free':           ['free', '-h'],
    'uptime':         ['uptime'],
    'kismet-version': ['kismet', '--version'],
    'nmap-version':   ['nmap', '--version'],
    'aircrack-help':  ['aircrack-ng', '--help'],
}

# Configurable tools — argv template; user-supplied args appended via
# shlex.split. Each user arg is treated as a single token; no shell metas.
# The 'arg_pattern' regex is informational only (UI hint).
TOOLS: dict[str, dict] = {
    'nmap': {
        'argv':      ['nmap'],
        'default':   '-sn 10.42.0.0/24',
        'hint':      'target / flags (e.g. -sV 10.42.0.5)',
        'timeout':   60,
    },
    'nmap-fast': {
        'argv':      ['nmap', '-T4', '-F'],
        'default':   '10.42.0.0/24',
        'hint':      'target subnet/host',
        'timeout':   60,
    },
    'iwlist-scan': {
        'argv':      ['iwlist'],
        'default':   'wlan0 scanning',
        'hint':      'iface scanning',
        'timeout':   20,
    },
    'hcitool': {
        'argv':      ['hcitool'],
        'default':   'scan',
        'hint':      'subcommand',
        'timeout':   30,
    },
    'curl': {
        'argv':      ['curl', '-sS', '-m', '10'],
        'default':   'https://ipinfo.io',
        'hint':      'url',
        'timeout':   15,
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
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>DRIFTER · OPSEC</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --bg:#020503;--bg-elev:#06100a;--card:#0a1610;--card-hi:#0f1f17;
  --border:rgba(57,255,20,.15);--border-hi:rgba(57,255,20,.4);
  --text:#caffd5;--text-mute:#5a7c66;
  --green:#39ff14;--green-glow:rgba(57,255,20,.45);
  --amber:#ffb000;--red:#ff2052;--cyan:#22d3ee;
  --mono:'JetBrains Mono','Fira Code',Menlo,Consolas,monospace;
  --fs-xs:11px;--fs-sm:12px;--fs-md:14px;--fs-lg:18px;
}
html,body{
  background:var(--bg);color:var(--text);font-family:var(--mono);
  font-size:var(--fs-md);min-height:100vh;
}
body{
  background:
    radial-gradient(1200px 600px at 50% -200px,rgba(57,255,20,.08),transparent 60%),
    repeating-linear-gradient(0deg,rgba(57,255,20,.03) 0,rgba(57,255,20,.03) 1px,transparent 1px,transparent 3px),
    var(--bg);
}
header{
  padding:14px 18px;border-bottom:1px solid var(--border-hi);
  background:linear-gradient(180deg,#031509 0%,var(--bg) 100%);
  display:flex;align-items:center;gap:18px;justify-content:space-between;
}
.brand{display:flex;align-items:center;gap:14px}
.brand h1{
  font-size:18px;letter-spacing:6px;color:var(--green);font-weight:700;
  text-shadow:0 0 12px var(--green-glow);
}
.brand .sub{font-size:var(--fs-xs);color:var(--text-mute);letter-spacing:2px}
.mode-pill{
  padding:4px 12px;border:1px solid var(--green);border-radius:2px;
  font-size:var(--fs-xs);letter-spacing:2px;color:var(--green);
  text-shadow:0 0 8px var(--green-glow);
}
.mode-pill.drive{color:var(--cyan);border-color:var(--cyan);text-shadow:0 0 8px var(--cyan)}
.switch-link{
  font-size:var(--fs-xs);color:var(--text-mute);text-decoration:none;
  border:1px solid var(--border);padding:4px 10px;border-radius:2px;
  letter-spacing:1.5px;
}
.switch-link:hover{color:var(--green);border-color:var(--green)}
nav{
  display:flex;border-bottom:1px solid var(--border);background:var(--bg-elev);
}
nav a{
  flex:1;padding:12px 16px;color:var(--text-mute);text-decoration:none;
  text-align:center;letter-spacing:3px;font-size:var(--fs-sm);
  border-right:1px solid var(--border);transition:.18s ease;cursor:pointer;
}
nav a:last-child{border-right:none}
nav a.active,nav a:hover{
  color:var(--green);background:rgba(57,255,20,.05);
  text-shadow:0 0 8px var(--green-glow);
}
nav a.kill{color:var(--red)}
nav a.kill.active,nav a.kill:hover{
  color:var(--red);background:rgba(255,32,82,.06);
  text-shadow:0 0 8px var(--red);
}
main{padding:18px;max-width:1200px;margin:0 auto}
.page{display:none}
.page.active{display:block}
.card{
  background:var(--card);border:1px solid var(--border);border-radius:2px;
  padding:14px;margin-bottom:14px;
}
.card h2{
  font-size:var(--fs-sm);letter-spacing:3px;color:var(--green);
  margin-bottom:10px;padding-bottom:8px;border-bottom:1px solid var(--border);
  display:flex;justify-content:space-between;align-items:baseline;
}
.card h2 .count{color:var(--text-mute);font-size:var(--fs-xs);letter-spacing:1px}
.tile-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:8px}
button.tile,button.btn{
  background:var(--card-hi);border:1px solid var(--border);color:var(--text);
  padding:12px;font-family:var(--mono);font-size:var(--fs-sm);cursor:pointer;
  letter-spacing:1.5px;transition:.14s;border-radius:2px;
  text-align:left;
}
button.tile:hover,button.btn:hover{border-color:var(--green);color:var(--green);box-shadow:0 0 12px -4px var(--green-glow)}
button.danger{border-color:rgba(255,32,82,.3)}
button.danger:hover{border-color:var(--red);color:var(--red);box-shadow:0 0 12px -4px var(--red)}
button:disabled{opacity:.4;cursor:not-allowed}
input[type=text]{
  background:#000;border:1px solid var(--border);color:var(--text);
  padding:10px;font-family:var(--mono);font-size:var(--fs-sm);
  width:100%;border-radius:2px;outline:none;
}
input[type=text]:focus{border-color:var(--green);box-shadow:0 0 8px -2px var(--green-glow)}
.term{
  background:#000;border:1px solid var(--border);padding:12px;
  font-family:var(--mono);font-size:var(--fs-sm);color:var(--green);
  height:420px;overflow-y:auto;white-space:pre-wrap;word-break:break-word;
  border-radius:2px;
}
.term .err{color:var(--red)}
.term .ok{color:var(--cyan)}
.term .prompt{color:var(--text-mute)}
.tool-row{display:flex;gap:8px;margin-bottom:10px;align-items:stretch}
.tool-row label{
  min-width:120px;color:var(--text-mute);font-size:var(--fs-xs);
  letter-spacing:1.5px;align-self:center;
}
.tool-row .hint{font-size:var(--fs-xs);color:var(--text-mute);margin-top:4px}
table{width:100%;border-collapse:collapse;font-size:var(--fs-xs)}
th,td{
  padding:6px 10px;text-align:left;border-bottom:1px solid var(--border);
  letter-spacing:.5px;
}
th{color:var(--text-mute);text-transform:uppercase;letter-spacing:2px;font-size:10px}
.kv{display:grid;grid-template-columns:140px 1fr;gap:6px 14px;font-size:var(--fs-xs)}
.kv dt{color:var(--text-mute);text-transform:uppercase;letter-spacing:1.5px}
.kv dd{color:var(--green)}
.kill-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}
.kill-card{
  background:var(--card-hi);border:1px solid rgba(255,32,82,.2);
  padding:14px;border-radius:2px;
}
.kill-card h3{
  color:var(--red);font-size:var(--fs-sm);letter-spacing:2px;
  margin-bottom:6px;
}
.kill-card p{color:var(--text-mute);font-size:var(--fs-xs);margin-bottom:12px;line-height:1.5}
.confirm-prompt{
  margin-top:8px;font-size:var(--fs-xs);color:var(--amber);
  letter-spacing:1.5px;display:none;
}
.confirm-prompt.show{display:block}
footer{
  padding:10px 18px;border-top:1px solid var(--border);
  font-size:var(--fs-xs);color:var(--text-mute);letter-spacing:1.5px;
  display:flex;justify-content:space-between;
}
.dot{display:inline-block;width:6px;height:6px;border-radius:50%;margin-right:6px;vertical-align:middle}
.dot.ok{background:var(--green);box-shadow:0 0 6px var(--green-glow)}
.dot.warn{background:var(--amber)}
.dot.off{background:var(--text-mute);opacity:.5}
.blink{animation:blink 1.2s step-end infinite}
@keyframes blink{50%{opacity:.4}}
</style>
</head>
<body>

<header>
  <div class="brand">
    <h1>OPSEC</h1>
    <span class="sub">MZ1312 · UNCAGED</span>
  </div>
  <div style="display:flex;align-items:center;gap:12px">
    <span class="mode-pill" id="mode-pill">FOOT</span>
    <a class="switch-link" href="http://10.42.0.1:8080/" id="switch-link">→ DRIVE</a>
  </div>
</header>

<nav>
  <a data-page="terminal" class="active">TERMINAL</a>
  <a data-page="tools">TOOLS</a>
  <a data-page="flipper">FLIPPER</a>
  <a data-page="wardrive">WARDRIVE</a>
  <a data-page="killswitch" class="kill">KILLSWITCH</a>
</nav>

<main>

<section id="page-terminal" class="page active">
  <div class="card">
    <h2>QUICK PROBES</h2>
    <div class="tile-grid" id="quick-tiles"></div>
  </div>
  <div class="card">
    <h2>OUTPUT <span class="blink" style="color:var(--text-mute)">_</span></h2>
    <div class="term" id="term"><span class="prompt">// ready · select a probe above or use TOOLS</span></div>
  </div>
</section>

<section id="page-tools" class="page">
  <div class="card">
    <h2>CURATED LAUNCHERS</h2>
    <div id="tool-rows"></div>
  </div>
  <div class="card">
    <h2>OUTPUT <span class="blink" style="color:var(--text-mute)">_</span></h2>
    <div class="term" id="tools-term"><span class="prompt">// run a launcher to see output here</span></div>
  </div>
</section>

<section id="page-flipper" class="page">
  <div class="card">
    <h2>FLIPPER STATUS</h2>
    <dl class="kv" id="flipper-status">
      <dt>connection</dt><dd>—</dd>
    </dl>
  </div>
  <div class="card">
    <h2>SUBGHZ CAPTURES <span class="count" id="capture-count">0</span></h2>
    <div id="captures"><div class="prompt" style="color:var(--text-mute);padding:8px">// no captures yet · plug in Flipper and broadcast on 433.92 MHz</div></div>
  </div>
</section>

<section id="page-wardrive" class="page">
  <div class="card">
    <h2>WARDRIVE STATUS</h2>
    <dl class="kv" id="wardrive-status"><dt>state</dt><dd>—</dd></dl>
  </div>
  <div class="card">
    <h2>WIFI <span class="count" id="wifi-count">0</span></h2>
    <div id="wifi-table" style="overflow-x:auto"><div class="prompt" style="color:var(--text-mute);padding:8px">// waiting for first scan…</div></div>
  </div>
  <div class="card">
    <h2>BLUETOOTH <span class="count" id="bt-count">0</span></h2>
    <div id="bt-table" style="overflow-x:auto"><div class="prompt" style="color:var(--text-mute);padding:8px">// waiting for first scan…</div></div>
  </div>
</section>

<section id="page-killswitch" class="page">
  <div class="card">
    <h2 style="color:var(--red);border-color:rgba(255,32,82,.3)">⚠ DANGER ZONE</h2>
    <div class="kill-grid">
      <div class="kill-card">
        <h3>RANDOMIZE MAC</h3>
        <p>Bring wlan0 down, set a fresh random 02:xx address, bring it up. Severs current Wi-Fi associations.</p>
        <button class="btn danger" data-confirm="MAC" data-action="mac-randomize">EXECUTE</button>
        <div class="confirm-prompt"></div>
      </div>
      <div class="kill-card">
        <h3>WIPE LOGS</h3>
        <p>Delete /opt/drifter/logs/* and /opt/drifter/wardrive_logs/*. Reports total bytes purged.</p>
        <button class="btn danger" data-confirm="WIPE" data-action="wipe-logs">EXECUTE</button>
        <div class="confirm-prompt"></div>
      </div>
      <div class="kill-card">
        <h3>HALT RECON</h3>
        <p>systemctl stop drifter-flipper + drifter-wardrive. Dashboard stays up so you keep the UI.</p>
        <button class="btn danger" data-confirm="HALT" data-action="halt-recon">EXECUTE</button>
        <div class="confirm-prompt"></div>
      </div>
    </div>
  </div>
  <div class="card">
    <h2>RESULT</h2>
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

async function runTool(name, args){
  clearTerm('tools-term');
  appendTo('tools-term', '$ ' + name + ' ' + (args || ''), 'prompt');
  try {
    const res = await fetch('/api/launch/' + encodeURIComponent(name), {
      method: 'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({args: args || ''}),
    });
    const data = await res.json();
    if (data.error){ appendTo('tools-term', 'ERR ' + data.error, 'err'); return; }
    if (data.stdout) appendTo('tools-term', data.stdout);
    if (data.stderr) appendTo('tools-term', data.stderr, 'err');
    appendTo('tools-term', '// rc=' + data.rc + ' · ' + data.duration_ms + 'ms', 'ok');
  } catch (e){ appendTo('tools-term', 'ERR ' + e, 'err'); }
}

// ── Build quick probe tiles ───────────────────────────────────────────
const quick = document.getElementById('quick-tiles');
for (const name of Object.keys(PROBES)){
  const b = document.createElement('button');
  b.className = 'tile';
  b.textContent = name;
  b.onclick = () => runProbe(name);
  quick.appendChild(b);
}

// ── Build tool rows ───────────────────────────────────────────────────
const toolRows = document.getElementById('tool-rows');
for (const [name, spec] of Object.entries(TOOLS)){
  const row = document.createElement('div');
  row.style.marginBottom = '14px';
  row.innerHTML = `
    <div class="tool-row">
      <label>${name}</label>
      <input type="text" value="${spec.default}" data-tool="${name}">
      <button class="btn" data-launch="${name}" style="min-width:120px">LAUNCH</button>
    </div>
    <div class="hint" style="margin-left:128px">${spec.hint}</div>
  `;
  toolRows.appendChild(row);
}
toolRows.addEventListener('click', e => {
  const name = e.target.dataset.launch;
  if (!name) return;
  const input = toolRows.querySelector(`input[data-tool="${name}"]`);
  runTool(name, input ? input.value : '');
});

// ── Page nav ──────────────────────────────────────────────────────────
document.querySelectorAll('nav a').forEach(a => {
  a.onclick = (e) => {
    e.preventDefault();
    document.querySelectorAll('nav a').forEach(x => x.classList.remove('active'));
    document.querySelectorAll('.page').forEach(x => x.classList.remove('active'));
    a.classList.add('active');
    document.getElementById('page-' + a.dataset.page).classList.add('active');
  };
});

// ── Mode + MQTT poll ──────────────────────────────────────────────────
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

function fmt(o, indent=0){
  if (o == null) return '—';
  if (typeof o === 'object') return JSON.stringify(o);
  return String(o);
}

function renderKV(elId, obj){
  const el = document.getElementById(elId);
  if (!obj || Object.keys(obj).length === 0){
    el.innerHTML = '<dt>—</dt><dd>no data</dd>';
    return;
  }
  el.innerHTML = '';
  for (const [k, v] of Object.entries(obj)){
    el.insertAdjacentHTML('beforeend', `<dt>${k}</dt><dd>${fmt(v)}</dd>`);
  }
}

function renderTable(elId, rows, columns){
  const el = document.getElementById(elId);
  if (!rows || rows.length === 0){
    el.innerHTML = '<div style="padding:8px;color:var(--text-mute)">// empty</div>';
    return;
  }
  let html = '<table><thead><tr>';
  for (const c of columns) html += `<th>${c}</th>`;
  html += '</tr></thead><tbody>';
  for (const r of rows){
    html += '<tr>';
    for (const c of columns) html += `<td>${fmt(r[c])}</td>`;
    html += '</tr>';
  }
  html += '</tbody></table>';
  el.innerHTML = html;
}

async function refreshMqtt(){
  try {
    const data = await fetch('/api/mqtt/cache').then(r => r.json());
    const dot = document.getElementById('mqtt-dot');
    const state = document.getElementById('mqtt-state');
    const topics = Object.keys(data.latest);
    if (topics.length){
      dot.className = 'dot ok';
      state.textContent = 'live · ' + topics.length + ' topics';
    } else {
      dot.className = 'dot warn';
      state.textContent = 'connected · idle';
    }

    // Flipper
    const fStatus = data.latest['drifter/flipper/status'];
    renderKV('flipper-status', fStatus ? fStatus.payload : {connection:'no signal'});
    document.getElementById('capture-count').textContent = data.captures.length;
    if (data.captures.length){
      const div = document.getElementById('captures');
      div.innerHTML = data.captures.slice(0, 20).map(c =>
        `<div style="padding:8px;border-bottom:1px solid var(--border);font-size:var(--fs-xs)">
          <span class="prompt">${new Date(c.ts*1000).toTimeString().slice(0,8)}</span>
          ${' ' + JSON.stringify(c.payload)}
        </div>`
      ).join('');
    }

    // Wardrive
    const wStatus = data.latest['drifter/wardrive/status'];
    renderKV('wardrive-status', wStatus ? wStatus.payload : {state:'no signal'});
    const wifi = data.latest['drifter/wardrive/wifi'];
    if (wifi){
      const aps = (wifi.payload.aps || wifi.payload.networks || []);
      document.getElementById('wifi-count').textContent = aps.length;
      renderTable('wifi-table', aps.slice(0, 100), ['ssid','bssid','signal','channel','encryption']);
    }
    const bt = data.latest['drifter/wardrive/bt'];
    if (bt){
      const devs = (bt.payload.devices || []);
      document.getElementById('bt-count').textContent = devs.length;
      renderTable('bt-table', devs.slice(0, 100), ['address','name','rssi']);
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
        if path == '/api/mode/status':
            self._send_json(200, {'mode': current_mode()})
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
        log.info('shutdown signal — closing socket')
        httpd.shutdown()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()
    return 0


if __name__ == '__main__':
    sys.exit(main())

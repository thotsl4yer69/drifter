#!/usr/bin/env python3
"""
MZ1312 DRIFTER — OPSEC Dashboard

Mobile/foot persona. Same Pi, different dashboard. Dark Kali aesthetic.
Listens on port 8090; intentionally separate from the in-vehicle HUD on 8080
so the cabin display can keep showing telemetry while the operator works the
recon/console UI on a phone or laptop over the hotspot.

Slice 2 = skeleton. Pages render, /api/tool/* runs an allowlisted command and
streams output via Server-Sent Events. Slice 3 will add the real Flipper /
wardrive / killswitch panes.

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
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, '/opt/drifter')
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import MODE_STATE_PATH, DEFAULT_MODE  # noqa: E402

LOG_FORMAT = '%(asctime)s [OPSEC] %(message)s'
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

OPSEC_PORT = 8090

# Allowlisted tools — `tool name → argv template`. Web requests pick a tool
# by name; user-supplied args are appended via shlex.split with NO shell.
# Keeps the surface small and auditable. Slice 3 will expand the catalog.
TOOLS: dict[str, list[str]] = {
    'iwconfig':       ['iwconfig'],
    'ip-addr':        ['ip', '-brief', 'addr'],
    'ip-link':        ['ip', '-brief', 'link'],
    'arp':            ['ip', 'neigh'],
    'lsusb':          ['lsusb'],
    'lspci':          ['lspci'],
    'uname':          ['uname', '-a'],
    'free':           ['free', '-h'],
    'uptime':         ['uptime'],
    'mac-current':    ['ip', 'link', 'show'],
    'kismet-version': ['kismet', '--version'],
    'nmap-version':   ['nmap', '--version'],
    'aircrack-version': ['aircrack-ng', '--help'],
}


def current_mode() -> str:
    try:
        return Path(MODE_STATE_PATH).read_text(encoding='utf-8').strip() or DEFAULT_MODE
    except OSError:
        return DEFAULT_MODE


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
  border-right:1px solid var(--border);transition:.18s ease;
}
nav a:last-child{border-right:none}
nav a.active,nav a:hover{
  color:var(--green);background:rgba(57,255,20,.05);
  text-shadow:0 0 8px var(--green-glow);
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
}
.tile-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:8px}
button.tile{
  background:var(--card-hi);border:1px solid var(--border);color:var(--text);
  padding:12px;font-family:var(--mono);font-size:var(--fs-sm);cursor:pointer;
  letter-spacing:1.5px;transition:.14s;border-radius:2px;
  text-align:left;
}
button.tile:hover{border-color:var(--green);color:var(--green);box-shadow:0 0 12px -4px var(--green-glow)}
button.tile.danger:hover{border-color:var(--red);color:var(--red);box-shadow:0 0 12px -4px var(--red)}
.term{
  background:#000;border:1px solid var(--border);padding:12px;
  font-family:var(--mono);font-size:var(--fs-sm);color:var(--green);
  height:420px;overflow-y:auto;white-space:pre-wrap;word-break:break-word;
  border-radius:2px;
}
.term .err{color:var(--red)}
.term .ok{color:var(--cyan)}
.term .prompt{color:var(--text-mute)}
.placeholder{
  padding:32px;text-align:center;color:var(--text-mute);
  border:1px dashed var(--border);border-radius:2px;letter-spacing:2px;
}
footer{
  padding:10px 18px;border-top:1px solid var(--border);
  font-size:var(--fs-xs);color:var(--text-mute);letter-spacing:1.5px;
  display:flex;justify-content:space-between;
}
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
  <a data-page="killswitch">KILLSWITCH</a>
</nav>

<main>

<section id="page-terminal" class="page active">
  <div class="card">
    <h2>QUICK PROBES</h2>
    <div class="tile-grid" id="quick-tiles"></div>
  </div>
  <div class="card">
    <h2>OUTPUT <span class="blink" style="color:var(--text-mute)">_</span></h2>
    <div class="term" id="term"><span class="prompt">// ready · select a probe above</span></div>
  </div>
</section>

<section id="page-tools" class="page">
  <div class="card">
    <h2>KALI TOOLS</h2>
    <div class="placeholder">slice 3 — curated launchers (nmap / aircrack-ng / kismet / hcxdumptool / hashcat)</div>
  </div>
</section>

<section id="page-flipper" class="page">
  <div class="card">
    <h2>FLIPPER ZERO</h2>
    <div class="placeholder">slice 3 — sub-GHz capture controls + log feed via flipper_bridge MQTT topic</div>
  </div>
</section>

<section id="page-wardrive" class="page">
  <div class="card">
    <h2>WARDRIVE</h2>
    <div class="placeholder">slice 3 — live AP/BT scan, GPS-tagged log, kml export</div>
  </div>
</section>

<section id="page-killswitch" class="page">
  <div class="card">
    <h2>OPSEC KILLSWITCH</h2>
    <div class="placeholder">slice 3 — drop hotspot, randomize MACs, wipe recent logs, halt recon services</div>
  </div>
</section>

</main>

<footer>
  <span>OPSEC · :8090</span>
  <span id="ts">--:--:--</span>
</footer>

<script>
const TOOLS = __TOOLS_JSON__;
const term = document.getElementById('term');
const quick = document.getElementById('quick-tiles');

function append(line, cls){
  const span = document.createElement('span');
  if (cls) span.className = cls;
  span.textContent = line + '\n';
  term.appendChild(span);
  term.scrollTop = term.scrollHeight;
}

function clearTerm(){ term.textContent = ''; }

async function runTool(name){
  clearTerm();
  append('$ ' + name, 'prompt');
  try {
    const res = await fetch('/api/tool/' + encodeURIComponent(name), {method:'POST'});
    const data = await res.json();
    if (data.error){
      append('ERR ' + data.error, 'err');
      return;
    }
    if (data.stdout) append(data.stdout);
    if (data.stderr) append(data.stderr, 'err');
    append('// rc=' + data.rc + ' · ' + data.duration_ms + 'ms', 'ok');
  } catch (e){
    append('ERR ' + e, 'err');
  }
}

for (const name of Object.keys(TOOLS)){
  const b = document.createElement('button');
  b.className = 'tile';
  b.textContent = name;
  b.onclick = () => runTool(name);
  quick.appendChild(b);
}

// Page nav
document.querySelectorAll('nav a').forEach(a => {
  a.onclick = (e) => {
    e.preventDefault();
    document.querySelectorAll('nav a').forEach(x => x.classList.remove('active'));
    document.querySelectorAll('.page').forEach(x => x.classList.remove('active'));
    a.classList.add('active');
    document.getElementById('page-' + a.dataset.page).classList.add('active');
  };
});

// Mode pill + switch link reflect /api/mode/status
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

setInterval(() => {
  document.getElementById('ts').textContent = new Date().toTimeString().slice(0,8);
}, 1000);
refreshMode();
setInterval(refreshMode, 5000);
</script>
</body>
</html>
"""


# ── HTTP handler ────────────────────────────────────────────────────────────

class OpsecHandler(BaseHTTPRequestHandler):
    server_version = 'DrifterOpsec/0.1'

    def log_message(self, fmt, *args):  # quiet stdlib noise
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

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == '/' or path == '/index.html':
            tools_json = json.dumps({k: ' '.join(v) for k, v in TOOLS.items()})
            self._send_text(200, OPSEC_HTML.replace('__TOOLS_JSON__', tools_json))
            return
        if path == '/healthz':
            self._send_json(200, {
                'status': 'ok',
                'mode':   current_mode(),
                'tools':  list(TOOLS),
                'ts':     time.time(),
            })
            return
        if path == '/api/mode/status':
            self._send_json(200, {'mode': current_mode()})
            return
        if path == '/api/tools':
            self._send_json(200, {'tools': {k: ' '.join(v) for k, v in TOOLS.items()}})
            return
        self.send_error(404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path.startswith('/api/tool/'):
            name = path[len('/api/tool/'):]
            self._run_tool(name)
            return
        self.send_error(404)

    def _run_tool(self, name: str) -> None:
        argv = TOOLS.get(name)
        if argv is None:
            self._send_json(400, {'error': f'unknown tool {name!r}'})
            return
        t0 = time.monotonic()
        try:
            r = subprocess.run(
                argv, capture_output=True, text=True, timeout=15,
            )
        except subprocess.TimeoutExpired:
            self._send_json(504, {'error': 'tool timed out (>15s)'})
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
    addr = ('0.0.0.0', OPSEC_PORT)
    httpd = ThreadingHTTPServer(addr, OpsecHandler)
    log.info(f'OPSEC dashboard listening on http://{addr[0]}:{addr[1]}')
    log.info(f'tools loaded: {sorted(TOOLS)}')

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

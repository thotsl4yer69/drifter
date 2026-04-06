#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Web Dashboard & Audio Bridge
Self-hosted live vehicle dashboard served over Wi-Fi hotspot.

Phone connects to MZ1312_DRIFTER Wi-Fi → opens browser → 10.13.12.1:8080
Live telemetry via WebSocket, zero apps needed.

Also serves TTS audio alerts as WAV over WebSocket for phone speaker output,
so your Jag speaks through the Pioneer via Android Auto / phone audio.

UNCAGED TECHNOLOGY — EST 1991
"""

import json
import time
import glob as globmod
import signal
import asyncio
import logging
import threading
import subprocess
from pathlib import Path
from http.server import HTTPServer, SimpleHTTPRequestHandler
import paho.mqtt.client as mqtt

try:
    import websockets
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False

from config import MQTT_HOST, MQTT_PORT, TOPICS, LEVEL_NAMES, DRIFTER_DIR
from mechanic import (
    search as mechanic_search, VEHICLE_SPECS, COMMON_PROBLEMS,
    SERVICE_SCHEDULE, EMERGENCY_PROCEDURES, TORQUE_SPECS, FUSE_REFERENCE,
    get_advice_for_alert,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [DASHBOARD] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

WEB_PORT = 8080
WS_PORT = 8081
AUDIO_WS_PORT = 8082

running = True
latest_state = {}
latest_report = {}
mqtt_client = None
ws_clients = set()
audio_ws_clients = set()
_ws_loop = None  # Set in main() — avoids deprecated asyncio.get_event_loop()


# ═══════════════════════════════════════════════════════════════════
#  Hardware Diagnostics
# ═══════════════════════════════════════════════════════════════════

def check_hardware():
    """Check what hardware is present and what services are running."""
    hw = {}

    # CAN adapter — check for socketcan interfaces and USB serial devices
    can_ifaces = []
    try:
        out = subprocess.run(['ip', '-brief', 'link', 'show', 'type', 'can'],
                             capture_output=True, text=True, timeout=3)
        for line in out.stdout.strip().splitlines():
            parts = line.split()
            if parts:
                can_ifaces.append({'name': parts[0], 'state': parts[1] if len(parts) > 1 else 'UNKNOWN'})
    except Exception:
        pass

    usb_serial = globmod.glob('/dev/ttyUSB*') + globmod.glob('/dev/ttyACM*')
    hw['can'] = {
        'interfaces': can_ifaces,
        'usb_serial': usb_serial,
        'ok': any(i['state'] == 'UP' for i in can_ifaces),
        'hint': 'Plug in USB2CAN adapter and start the car' if not can_ifaces and not usb_serial
                else 'CAN interface down — check adapter' if can_ifaces and not any(i['state'] == 'UP' for i in can_ifaces)
                else 'USB serial detected but no CAN interface — adapter may need setup' if usb_serial and not can_ifaces
                else '',
    }

    # RTL-SDR — check for USB device
    rtl_present = False
    try:
        out = subprocess.run(['lsusb'], capture_output=True, text=True, timeout=3)
        rtl_present = 'RTL2838' in out.stdout or 'RTL2832' in out.stdout or 'Realtek' in out.stdout.lower()
    except Exception:
        pass
    hw['rtl_sdr'] = {'ok': rtl_present, 'hint': 'No RTL-SDR dongle detected' if not rtl_present else ''}

    # Network
    networks = {}
    try:
        out = subprocess.run(['ip', '-brief', 'addr', 'show'], capture_output=True, text=True, timeout=3)
        for line in out.stdout.strip().splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[0] != 'lo':
                addrs = [p.split('/')[0] for p in parts[2:] if '.' in p]
                if addrs:
                    networks[parts[0]] = {'state': parts[1], 'addrs': addrs}
    except Exception:
        pass
    hw['network'] = networks

    # Services
    services_status = {}
    for svc in ['drifter-canbridge', 'drifter-alerts', 'drifter-dashboard',
                'drifter-watchdog', 'drifter-hotspot', 'drifter-rf',
                'drifter-wardrive', 'drifter-voice', 'drifter-realdash',
                'drifter-logger', 'drifter-homesync', 'drifter-anomaly',
                'drifter-analyst', 'drifter-voicein', 'drifter-fbmirror',
                'nanomq', 'mosquitto']:
        try:
            out = subprocess.run(['systemctl', 'is-active', svc],
                                 capture_output=True, text=True, timeout=3)
            services_status[svc] = out.stdout.strip()
        except Exception:
            services_status[svc] = 'unknown'
    hw['services'] = services_status

    # MQTT data flow — distinguish engine data from system/watchdog data
    last_update = latest_state.get('_last_update', 0)
    data_age = time.time() - last_update if last_update else None
    engine_keys = [k for k in latest_state if k.startswith('engine_') and not k.startswith('_')]
    all_keys = [k for k in latest_state if not k.startswith('_')]
    has_engine_data = bool(engine_keys) and data_age is not None and data_age < 60
    hw['mqtt'] = {
        'broker': services_status.get('mosquitto', 'unknown'),
        'last_data_age': round(data_age, 1) if data_age else None,
        'has_data': has_engine_data,
        'topics_seen': len(all_keys),
        'engine_topics': len(engine_keys),
    }

    # Overall readiness — need CAN + actual engine data
    hw['ready'] = hw['can']['ok'] and has_engine_data
    hw['summary'] = []
    if not hw['can']['ok']:
        if not usb_serial and not can_ifaces:
            hw['summary'].append({'item': 'USB2CAN', 'status': 'missing', 'detail': 'No adapter detected. Plug in USB2CAN.'})
        elif usb_serial and not can_ifaces:
            hw['summary'].append({'item': 'USB2CAN', 'status': 'setup', 'detail': f'Serial device {usb_serial[0]} found but CAN not configured.'})
        else:
            hw['summary'].append({'item': 'CAN Bus', 'status': 'down', 'detail': f'{can_ifaces[0]["name"]} is {can_ifaces[0]["state"]}. Start car / check wiring.'})
    else:
        hw['summary'].append({'item': 'CAN Bus', 'status': 'ok', 'detail': f'{can_ifaces[0]["name"]} UP'})

    if not has_engine_data:
        if hw['can']['ok']:
            hw['summary'].append({'item': 'OBD Data', 'status': 'waiting', 'detail': 'CAN is up but no engine data yet. Turn ignition on.'})
        else:
            hw['summary'].append({'item': 'OBD Data', 'status': 'waiting', 'detail': 'Waiting for CAN connection.'})
    else:
        hw['summary'].append({'item': 'OBD Data', 'status': 'ok', 'detail': f'{len(engine_keys)} engine params, {data_age:.0f}s ago'})

    if not hw['rtl_sdr']['ok']:
        hw['summary'].append({'item': 'RTL-SDR', 'status': 'missing', 'detail': 'No SDR dongle. TPMS/RF unavailable.'})
    else:
        hw['summary'].append({'item': 'RTL-SDR', 'status': 'ok', 'detail': 'SDR dongle detected'})

    svc_failed = [s for s, v in services_status.items() if v == 'failed']
    if svc_failed:
        hw['summary'].append({'item': 'Services', 'status': 'error', 'detail': f'Failed: {", ".join(svc_failed)}'})

    return hw


# ═══════════════════════════════════════════════════════════════════
#  MQTT → State Collector
# ═══════════════════════════════════════════════════════════════════

def on_message(client, userdata, msg):
    """Collect all MQTT data into latest_state."""
    global latest_report
    try:
        data = json.loads(msg.payload)
        topic = msg.topic

        if topic == 'drifter/analysis/report':
            latest_report = data
            log.info("New diagnostic report received")
            return

        key = topic.replace('drifter/', '').replace('/', '_')
        latest_state[key] = data
        latest_state['_last_update'] = time.time()

        # Broadcast to WebSocket clients
        ws_msg = json.dumps({'topic': topic, 'data': data, 'ts': time.time()})
        if _ws_loop is not None:
            _ws_loop.call_soon_threadsafe(
                _broadcast_sync, ws_msg
            )
    except (json.JSONDecodeError, RuntimeError):
        pass


def _broadcast_sync(msg):
    """Schedule broadcast to all WS clients."""
    for ws_queue in list(ws_clients):
        try:
            ws_queue.put_nowait(msg)
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════
#  TTS Audio → WebSocket (phone plays through its speaker / BT)
# ═══════════════════════════════════════════════════════════════════

PIPER_MODEL_PATH = DRIFTER_DIR / "piper-models" / "en_GB-alan-medium.onnx"
last_audio_text = ""
last_audio_time = 0


def generate_audio_wav(text):
    """Generate WAV bytes from text using piper or espeak-ng."""
    # Try piper first
    if PIPER_MODEL_PATH.exists():
        try:
            proc = subprocess.Popen(
                ['piper', '--model', str(PIPER_MODEL_PATH), '--output-raw'],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            raw, _ = proc.communicate(input=text.encode(), timeout=10)
            if raw:
                return _raw_to_wav(raw, rate=22050, channels=1, width=2)
        except Exception:
            pass

    # espeak-ng fallback — output to stdout as WAV
    try:
        proc = subprocess.run(
            ['espeak-ng', '-v', 'en-gb', '-s', '150', '-p', '40',
             '--stdout', text],
            capture_output=True, timeout=10
        )
        if proc.returncode == 0 and proc.stdout:
            return proc.stdout
    except Exception:
        pass

    return None


def _raw_to_wav(raw_data, rate=22050, channels=1, width=2):
    """Wrap raw PCM in a WAV header."""
    import struct
    data_size = len(raw_data)
    header = struct.pack('<4sI4s', b'RIFF', 36 + data_size, b'WAVE')
    fmt = struct.pack('<4sIHHIIHH', b'fmt ', 16, 1, channels,
                      rate, rate * channels * width, channels * width,
                      width * 8)
    data_header = struct.pack('<4sI', b'data', data_size)
    return header + fmt + data_header + raw_data


# ═══════════════════════════════════════════════════════════════════
#  HTML Dashboard (single-file, no dependencies)
# ═══════════════════════════════════════════════════════════════════

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<title>DRIFTER</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --bg:#0a0a0a;--card:#141414;--border:#222;
  --text:#e0e0e0;--dim:#666;--accent:#00bcd4;
  --ok:#4caf50;--info:#2196f3;--amber:#ff9800;--red:#f44336;
}
body{
  background:var(--bg);color:var(--text);
  font-family:'Courier New',monospace;
  overflow-x:hidden;-webkit-font-smoothing:antialiased;
}
.header{
  text-align:center;padding:12px 0 8px;
  border-bottom:1px solid var(--border);
  background:linear-gradient(180deg,#111 0%,#0a0a0a 100%);
  position:sticky;top:0;z-index:100;
}
.header h1{font-size:18px;letter-spacing:6px;color:var(--accent)}
.header .sub{font-size:10px;color:var(--dim);margin-top:2px}
.status-bar{
  display:flex;justify-content:space-between;align-items:center;
  padding:6px 16px;font-size:11px;
  border-bottom:1px solid var(--border);
}
.status-dot{width:8px;height:8px;border-radius:50%;display:inline-block;margin-right:6px}
.dot-ok{background:var(--ok)}.dot-warn{background:var(--amber)}.dot-off{background:#444}

/* Alert Banner */
.alert-banner{
  padding:10px 16px;font-size:13px;font-weight:bold;
  text-align:center;display:none;
  animation:pulse 2s infinite;
}
.alert-ok{background:#1b2e1b;color:var(--ok);display:block;animation:none}
.alert-info{background:#1a2733;color:var(--info);display:block;animation:none}
.alert-amber{background:#2e2a1a;color:var(--amber);display:block}
.alert-red{background:#2e1a1a;color:var(--red);display:block}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.7}}

/* Grid */
.grid{
  display:grid;
  grid-template-columns:repeat(auto-fit,minmax(140px,1fr));
  gap:8px;padding:10px;
}
.card{
  background:var(--card);border:1px solid var(--border);
  border-radius:8px;padding:10px;text-align:center;
  transition:border-color .3s;
}
.card.flash{border-color:var(--accent);transition:none}
.card .label{font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:1px}
.card .value{font-size:28px;font-weight:bold;margin:4px 0;font-variant-numeric:tabular-nums}
.card .unit{font-size:11px;color:var(--dim)}
.card .bar{
  height:3px;background:#222;border-radius:2px;margin-top:6px;overflow:hidden;
}
.card .bar-fill{height:100%;border-radius:2px;transition:width .3s}

/* Sizes */
.card.lg .value{font-size:42px}
.card.med .value{font-size:32px}

/* RPM zone markers */
.bar-zones{position:relative;height:3px;background:#222;border-radius:2px;margin-top:6px;overflow:hidden}
.bar-zone-ok{position:absolute;left:0;top:0;height:100%;width:78%;background:var(--ok)}
.bar-zone-warn{position:absolute;left:78%;top:0;height:100%;width:8%;background:var(--amber)}
.bar-zone-red{position:absolute;left:86%;top:0;height:100%;width:14%;background:var(--red)}
.bar-needle{position:absolute;top:-1px;width:2px;height:5px;background:#fff;border-radius:1px;transform:translateX(-50%);transition:left .2s}

/* Trim bar — centred at 0 */
.trim-bar-wrap{height:3px;background:#333;border-radius:2px;margin-top:6px;position:relative;overflow:hidden}
.trim-bar-center{position:absolute;left:50%;top:0;width:1px;height:100%;background:#555}
.trim-bar-fill{position:absolute;top:0;height:100%;border-radius:2px;transition:left .3s,width .3s}

/* TPMS */
.tpms-grid{
  display:grid;grid-template-columns:1fr 1fr;
  gap:6px;padding:0 10px;
}
.tpms-card{
  background:var(--card);border:1px solid var(--border);
  border-radius:8px;padding:8px 10px;
  display:flex;justify-content:space-between;align-items:center;
}
.tpms-card .pos{font-size:11px;color:var(--dim);font-weight:bold}
.tpms-card .psi{font-size:20px;font-weight:bold}
.tpms-card .temp{font-size:11px;color:var(--dim)}

/* Section Headers */
.section{
  padding:8px 16px 4px;font-size:11px;color:var(--dim);
  text-transform:uppercase;letter-spacing:2px;
  border-top:1px solid var(--border);margin-top:6px;
}

/* Alert Message */
.alert-msg{
  padding:8px 16px;font-size:12px;line-height:1.4;
  background:#111;margin:0 10px;border-radius:6px;
  min-height:36px;
}

/* DTC */
.dtc-list{
  padding:4px 16px 8px;font-size:12px;
}
.dtc-code{
  display:inline-block;background:#2e1a1a;color:var(--red);
  padding:2px 8px;border-radius:4px;margin:2px;font-weight:bold;
}
.dtc-pending{background:#2e2a1a;color:var(--amber)}

/* System */
.sys-row{
  display:flex;justify-content:space-between;
  padding:3px 16px;font-size:11px;
}
.sys-row .lbl{color:var(--dim)}

/* Quick-pick chips */
.chip{
  padding:6px 10px;background:#1a1a1a;border:1px solid #2a2a2a;
  border-radius:16px;color:var(--dim);font-family:inherit;font-size:11px;
  cursor:pointer;white-space:nowrap;transition:border-color .15s,color .15s;
}
.chip:active,.chip.active{border-color:var(--accent);color:var(--accent)}

/* Audio Toggle */
.audio-btn{
  position:fixed;bottom:16px;right:16px;z-index:200;
  width:48px;height:48px;border-radius:50%;border:2px solid var(--accent);
  background:var(--card);color:var(--accent);font-size:20px;
  cursor:pointer;display:flex;align-items:center;justify-content:center;
}
.audio-btn.active{background:var(--accent);color:#000}

/* Connection */
.disconnected{
  position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);
  background:rgba(0,0,0,.9);padding:30px;border-radius:12px;
  text-align:center;z-index:999;border:1px solid var(--red);
}
.disconnected h2{color:var(--red);margin-bottom:8px}
.disconnected p{color:var(--dim);font-size:12px}
.hidden{display:none!important}

/* Hardware Status */
.hw-overlay{
  position:fixed;top:0;left:0;right:0;bottom:0;
  background:var(--bg);z-index:500;
  display:flex;flex-direction:column;
  transition:opacity 0.4s;
}
.hw-overlay.fade-out{opacity:0;pointer-events:none}
.hw-header{text-align:center;padding:20px 0 12px}
.hw-header h2{font-size:16px;letter-spacing:4px;color:var(--accent)}
.hw-header .hw-sub{font-size:10px;color:var(--dim);margin-top:4px}
.hw-list{padding:0 16px;flex:1;overflow-y:auto}
.hw-item{
  display:flex;align-items:center;gap:12px;
  padding:12px;margin-bottom:8px;
  background:var(--card);border:1px solid var(--border);border-radius:8px;
}
.hw-dot{width:10px;height:10px;border-radius:50%;flex-shrink:0}
.hw-dot.ok{background:var(--ok);box-shadow:0 0 6px rgba(76,175,80,0.4)}
.hw-dot.missing{background:var(--red);box-shadow:0 0 6px rgba(244,67,54,0.4)}
.hw-dot.waiting{background:var(--amber);box-shadow:0 0 6px rgba(255,152,0,0.4);animation:pulse 1.5s infinite}
.hw-dot.setup{background:var(--info);box-shadow:0 0 6px rgba(33,150,243,0.4)}
.hw-dot.down{background:var(--red)}
.hw-dot.error{background:var(--red)}
.hw-info{flex:1}
.hw-name{font-size:13px;font-weight:bold;letter-spacing:1px}
.hw-detail{font-size:11px;color:var(--dim);margin-top:2px}
.hw-services{padding:8px 16px 16px;font-size:10px;color:var(--dim);text-align:center}
.hw-services span{margin:0 4px}
.hw-svc-ok{color:var(--ok)}
.hw-svc-fail{color:var(--red)}
.hw-svc-off{color:#555}
</style>
</head>
<body>

<div class="header">
  <h1>DRIFTER</h1>
  <div class="sub">2004 JAGUAR X-TYPE 2.5L V6 &mdash; MZ1312</div>
</div>

<div class="status-bar">
  <span><span class="status-dot dot-off" id="dot-conn"></span><span id="conn-text">CONNECTING</span></span>
  <span id="data-age">--</span>
</div>

<div class="alert-banner alert-ok" id="alert-banner">SYSTEMS NOMINAL</div>

<div class="section">ENGINE</div>
<div class="grid">
  <div class="card lg" id="c-rpm">
    <div class="label">RPM</div>
    <div class="value" id="v-rpm">--</div>
    <div class="bar-zones">
      <div class="bar-zone-ok"></div>
      <div class="bar-zone-warn"></div>
      <div class="bar-zone-red"></div>
      <div class="bar-needle" id="b-rpm" style="left:0"></div>
    </div>
  </div>
  <div class="card lg" id="c-speed">
    <div class="label">SPEED</div>
    <div class="value" id="v-speed">--</div>
    <div class="unit">km/h</div>
  </div>
  <div class="card med" id="c-coolant">
    <div class="label">COOLANT</div>
    <div class="value" id="v-coolant">--</div>
    <div class="unit">&deg;C &nbsp;<span style="font-size:9px;color:var(--dim)">normal 86-98</span></div>
    <div class="bar" style="position:relative">
      <div class="bar-fill" id="b-coolant" style="width:0;background:var(--ok)"></div>
      <!-- Normal range markers at 86°C (57.5%) and 98°C (72.5%) of 40-145°C span -->
      <div style="position:absolute;top:0;left:57.5%;width:1px;height:100%;background:#444"></div>
      <div style="position:absolute;top:0;left:72.5%;width:1px;height:100%;background:#444"></div>
    </div>
  </div>
  <div class="card med" id="c-voltage">
    <div class="label">VOLTAGE</div>
    <div class="value" id="v-voltage">--</div>
    <div class="unit">V</div>
  </div>
</div>

<div class="section">FUEL</div>
<div class="grid">
  <div class="card">
    <div class="label">STFT B1</div>
    <div class="value" id="v-stft1">--</div>
    <div class="unit">%</div>
    <div class="trim-bar-wrap"><div class="trim-bar-center"></div><div class="trim-bar-fill" id="tb-stft1"></div></div>
  </div>
  <div class="card">
    <div class="label">STFT B2</div>
    <div class="value" id="v-stft2">--</div>
    <div class="unit">%</div>
    <div class="trim-bar-wrap"><div class="trim-bar-center"></div><div class="trim-bar-fill" id="tb-stft2"></div></div>
  </div>
  <div class="card">
    <div class="label">LTFT B1</div>
    <div class="value" id="v-ltft1">--</div>
    <div class="unit">%</div>
    <div class="trim-bar-wrap"><div class="trim-bar-center"></div><div class="trim-bar-fill" id="tb-ltft1"></div></div>
  </div>
  <div class="card">
    <div class="label">LTFT B2</div>
    <div class="value" id="v-ltft2">--</div>
    <div class="unit">%</div>
    <div class="trim-bar-wrap"><div class="trim-bar-center"></div><div class="trim-bar-fill" id="tb-ltft2"></div></div>
  </div>
</div>

<div class="section">PERFORMANCE</div>
<div class="grid">
  <div class="card">
    <div class="label">LOAD</div>
    <div class="value" id="v-load">--</div>
    <div class="unit">%</div>
    <div class="bar"><div class="bar-fill" id="b-load" style="width:0;background:var(--accent)"></div></div>
  </div>
  <div class="card">
    <div class="label">THROTTLE</div>
    <div class="value" id="v-throttle">--</div>
    <div class="unit">%</div>
    <div class="bar"><div class="bar-fill" id="b-throttle" style="width:0;background:var(--accent)"></div></div>
  </div>
  <div class="card">
    <div class="label">IAT</div>
    <div class="value" id="v-iat">--</div>
    <div class="unit">&deg;C</div>
  </div>
  <div class="card">
    <div class="label">MAF</div>
    <div class="value" id="v-maf">--</div>
    <div class="unit">g/s</div>
  </div>
</div>

<div class="section">DIAGNOSTICS</div>
<div class="alert-msg" id="alert-msg">Waiting for data...</div>
<div class="dtc-list" id="dtc-list"></div>

<div class="section">TIRES</div>
<div class="tpms-grid">
  <div class="tpms-card" id="tpms-fl">
    <div><div class="pos">FL</div><div class="psi" id="v-tpms-fl-psi">--</div></div>
    <div class="temp" id="v-tpms-fl-temp">--</div>
  </div>
  <div class="tpms-card" id="tpms-fr">
    <div><div class="pos">FR</div><div class="psi" id="v-tpms-fr-psi">--</div></div>
    <div class="temp" id="v-tpms-fr-temp">--</div>
  </div>
  <div class="tpms-card" id="tpms-rl">
    <div><div class="pos">RL</div><div class="psi" id="v-tpms-rl-psi">--</div></div>
    <div class="temp" id="v-tpms-rl-temp">--</div>
  </div>
  <div class="tpms-card" id="tpms-rr">
    <div><div class="pos">RR</div><div class="psi" id="v-tpms-rr-psi">--</div></div>
    <div class="temp" id="v-tpms-rr-temp">--</div>
  </div>
</div>

<div class="section">SYSTEM</div>
<div id="sys-info">
  <div class="sys-row"><span class="lbl">CPU Temp</span><span id="v-cpu-temp">--</span></div>
  <div class="sys-row"><span class="lbl">Disk</span><span id="v-disk">--</span></div>
  <div class="sys-row"><span class="lbl">Memory</span><span id="v-mem">--</span></div>
  <div class="sys-row"><span class="lbl">Uptime</span><span id="v-uptime">--</span></div>
</div>

<div class="section">DIAGNOSIS</div>
<div id="diag-card" style="background:#111;border:1px solid #222;border-radius:6px;padding:12px;margin:4px 0 8px">
  <div id="diag-primary" style="font-size:15px;font-weight:bold;color:var(--ok)">No report yet — complete a drive to generate one</div>
  <div id="diag-evidence" style="font-size:12px;color:var(--dim);margin-top:4px"></div>
  <div id="diag-actions" style="margin-top:8px;font-size:12px"></div>
  <div id="diag-safety" style="color:var(--red);font-weight:bold;display:none;margin-top:6px">&#x26a0; SAFETY CRITICAL</div>
</div>
<button onclick="triggerAnalysis()" style="width:100%;padding:8px;background:#1a1a1a;border:1px solid #333;border-radius:4px;color:var(--text);font-size:12px;cursor:pointer;margin-bottom:4px">RUN ANALYSIS</button>
<details style="font-size:11px;color:var(--dim)">
  <summary style="cursor:pointer;padding:4px">Full report JSON</summary>
  <pre id="diag-json" style="font-size:10px;overflow-x:auto;color:var(--dim)"></pre>
</details>

<div class="section">RECENT DRIVES</div>
<div id="sessions-list" style="padding:6px 16px 2px;font-size:12px;color:var(--dim)">Loading...</div>

<div class="section">WARDRIVE</div>
<div id="wardrive-panel" style="padding:6px 10px 4px">
  <div style="display:flex;gap:8px;font-size:11px;color:var(--dim);margin-bottom:6px">
    <span>&#x1f4f6; Wi-Fi: <b id="wd-wifi-count" style="color:var(--text)">--</b></span>
    <span>&bull;</span>
    <span>&#x1f4f1; BT: <b id="wd-bt-count" style="color:var(--text)">--</b></span>
    <span style="margin-left:auto" id="wd-session-totals" style="color:var(--dim)"></span>
  </div>
  <div id="wd-networks" style="font-size:11px;color:var(--dim)">No scan yet</div>
</div>

<div class="section">ADS-B AIRCRAFT</div>
<div id="adsb-panel" style="padding:6px 10px 8px;font-size:11px;color:var(--dim)">
  No data yet — ADS-B scan runs every 5 min (requires dump1090)
</div>

<div class="section">ASK MECHANIC</div>
<div style="padding:6px 10px 10px">

  <!-- Quick-pick chips — one tap sends the question -->
  <div id="ask-chips" style="display:flex;flex-wrap:wrap;gap:5px;margin-bottom:8px">
    <button class="chip" onclick="chipAsk(this)">Safe to drive?</button>
    <button class="chip" onclick="chipAsk(this)">Explain my fuel trims</button>
    <button class="chip" onclick="chipAsk(this)">Why is coolant rising?</button>
    <button class="chip" onclick="chipAsk(this)">What do my DTCs mean?</button>
    <button class="chip" onclick="chipAsk(this)">Likely cause of alert?</button>
    <button class="chip" onclick="chipAsk(this)">Check engine light causes?</button>
    <button class="chip" onclick="chipAsk(this)">Next service items?</button>
    <button class="chip" onclick="chipAsk(this)">Thermostat OK?</button>
  </div>

  <!-- Text input row with mic button -->
  <div style="display:flex;gap:6px;margin-bottom:6px">
    <input id="ask-input" type="text"
      placeholder="Or type a question..."
      style="flex:1;padding:8px 10px;background:#1a1a1a;border:1px solid #333;border-radius:6px;
             color:var(--text);font-family:inherit;font-size:12px;outline:none">
    <button id="mic-btn" onclick="toggleMic()" title="Voice input"
      style="padding:8px 10px;background:#1a1a1a;border:1px solid #333;border-radius:6px;
             color:var(--dim);font-size:16px;cursor:pointer;line-height:1">&#x1f3a4;</button>
    <button id="ask-btn" onclick="askMechanic()"
      style="padding:8px 12px;background:#1a1a1a;border:1px solid #333;border-radius:6px;
             color:var(--accent);font-size:12px;cursor:pointer;white-space:nowrap">ASK</button>
  </div>

  <div id="ask-output"
    style="font-size:12px;color:var(--dim);line-height:1.5;white-space:pre-wrap;
           min-height:32px;padding:6px 2px">
    Tap a question above or use the mic &mdash; live telemetry is sent with every query.
  </div>
  <div id="ask-meta" style="font-size:9px;color:#444;text-align:right;padding:0 2px"></div>
</div>

<div style="height:80px"></div>

<a href="/mechanic" class="audio-btn" style="bottom:16px;left:16px;font-size:14px;text-decoration:none" title="Mechanic advisor">&#x1f527;</a>
<button class="audio-btn" id="audio-btn" title="Enable voice alerts on this device">&#x1f50a;</button>

<div class="disconnected hidden" id="dc-overlay">
  <h2>DISCONNECTED</h2>
  <p>Reconnecting to DRIFTER...</p>
</div>

<div class="hw-overlay" id="hw-overlay">
  <div class="hw-header">
    <h2>DRIFTER</h2>
    <div class="hw-sub">HARDWARE STATUS</div>
  </div>
  <div class="hw-list" id="hw-list"></div>
  <div class="hw-services" id="hw-services"></div>
</div>

<script>
const WS_URL = `ws://${location.hostname}:8081`;
const AUDIO_WS_URL = `ws://${location.hostname}:8082`;
let ws = null;
let audioWs = null;
let audioEnabled = false;
let audioCtx = null;
let lastDataTime = 0;
let hwOverlayDismissed = false;
let hwPollTimer = null;

// ── Hardware Status ──
function pollHardware(){
  fetch('/api/hardware').then(r=>r.json()).then(hw=>{
    const ol = document.getElementById('hw-overlay');
    if(!ol) return;
    if(hw.ready || hwOverlayDismissed){
      ol.classList.add('fade-out');
      if(hwPollTimer){clearInterval(hwPollTimer);hwPollTimer=null;}
      return;
    }
    ol.classList.remove('fade-out');
    // Render summary items
    const list = document.getElementById('hw-list');
    list.innerHTML = (hw.summary||[]).map(s=>{
      const dot = s.status;
      return `<div class="hw-item"><div class="hw-dot ${dot}"></div><div class="hw-info"><div class="hw-name">${s.item}</div><div class="hw-detail">${s.detail}</div></div></div>`;
    }).join('');
    // Render services
    const svcs = document.getElementById('hw-services');
    if(hw.services){
      svcs.innerHTML = Object.entries(hw.services).map(([k,v])=>{
        const cls = v==='active'?'hw-svc-ok':v==='failed'?'hw-svc-fail':'hw-svc-off';
        const name = k.replace('drifter-','');
        return `<span class="${cls}">${name}</span>`;
      }).join(' ');
    }
  }).catch(()=>{});
}
// Poll hardware every 5s until data arrives
pollHardware();
hwPollTimer = setInterval(pollHardware, 5000);

// ── Color helpers ──
function rpmColor(v){return v>6500?'var(--red)':v>5500?'var(--amber)':'var(--ok)'}
function coolantColor(v){return v>=108?'var(--red)':v>100?'var(--amber)':'var(--ok)'}
function voltColor(v){return v<12?'var(--red)':v<13.2?'var(--amber)':'var(--ok)'}
function trimColor(v){return Math.abs(v)>12?'var(--amber)':Math.abs(v)>8?'var(--info)':'var(--ok)'}
function iatColor(v){return v>65?'var(--amber)':v>50?'var(--info)':'var(--ok)'}
function psiColor(v){return v<20?'var(--red)':v<26?'var(--amber)':'var(--ok)'}

function setVal(id, val, color){
  const el=document.getElementById(id);
  if(!el)return;
  el.textContent=val;
  if(color)el.style.color=color;
}
function setBar(id, pct, color){
  const el=document.getElementById(id);
  if(!el)return;
  el.style.width=Math.min(100,Math.max(0,pct))+'%';
  if(color)el.style.background=color;
}
function setTrimBar(id, val, color){
  // val is fuel trim %, range clamped to ±25%. Bar grows left or right from centre.
  const el=document.getElementById(id);
  if(!el)return;
  const pct=Math.min(25,Math.max(-25,val));
  const halfW=Math.abs(pct)/25*50; // 0-50% of half-width
  if(pct>=0){el.style.left='50%';el.style.width=halfW+'%';}
  else{el.style.left=(50-halfW)+'%';el.style.width=halfW+'%';}
  if(color)el.style.background=color;
}
function flash(cardId){
  const el=document.getElementById(cardId);
  if(!el)return;
  el.classList.add('flash');
  setTimeout(()=>el.classList.remove('flash'),300);
}

// ── Process incoming MQTT data ──
function handleMessage(msg){
  const {topic, data} = msg;
  lastDataTime = Date.now();
  // Dismiss hardware overlay once real data flows
  if(!hwOverlayDismissed && topic.includes('/engine/')){
    hwOverlayDismissed = true;
    const ol = document.getElementById('hw-overlay');
    if(ol) ol.classList.add('fade-out');
  }
  const v = data.value;

  if(topic.endsWith('/rpm') && v!==undefined){
    setVal('v-rpm', Math.round(v), rpmColor(v));
    // Position needle along zone bar (0-7000 RPM = 0-100%)
    const needle=document.getElementById('b-rpm');
    if(needle) needle.style.left=Math.min(100,(v/7000)*100)+'%';
    flash('c-rpm');
  }
  else if(topic.endsWith('/coolant') && v!==undefined){
    setVal('v-coolant', Math.round(v), coolantColor(v));
    setBar('b-coolant', ((v-40)/80)*100, coolantColor(v));
    flash('c-coolant');
  }
  else if(topic.endsWith('/speed') && v!==undefined){
    setVal('v-speed', Math.round(v));
    flash('c-speed');
  }
  else if(topic.endsWith('/voltage') && v!==undefined){
    setVal('v-voltage', v.toFixed(1), voltColor(v));
    flash('c-voltage');
  }
  else if(topic.endsWith('/stft1') && v!==undefined){
    setVal('v-stft1', (v>=0?'+':'')+v.toFixed(1), trimColor(v));
    setTrimBar('tb-stft1', v, trimColor(v));
  }
  else if(topic.endsWith('/stft2') && v!==undefined){
    setVal('v-stft2', (v>=0?'+':'')+v.toFixed(1), trimColor(v));
    setTrimBar('tb-stft2', v, trimColor(v));
  }
  else if(topic.endsWith('/ltft1') && v!==undefined){
    setVal('v-ltft1', (v>=0?'+':'')+v.toFixed(1), trimColor(v));
    setTrimBar('tb-ltft1', v, trimColor(v));
  }
  else if(topic.endsWith('/ltft2') && v!==undefined){
    setVal('v-ltft2', (v>=0?'+':'')+v.toFixed(1), trimColor(v));
    setTrimBar('tb-ltft2', v, trimColor(v));
  }
  else if(topic.endsWith('/load') && v!==undefined){
    setVal('v-load', v.toFixed(0));
    setBar('b-load', v, 'var(--accent)');
  }
  else if(topic.endsWith('/throttle') && v!==undefined){
    setVal('v-throttle', v.toFixed(0));
    setBar('b-throttle', v, 'var(--accent)');
  }
  else if(topic.endsWith('/iat') && v!==undefined){
    setVal('v-iat', Math.round(v), iatColor(v));
  }
  else if(topic.endsWith('/maf') && v!==undefined){
    setVal('v-maf', v.toFixed(1));
  }
  // Alert level
  else if(topic.endsWith('/alert/level')){
    const lvl = data.level || 0;
    const banner = document.getElementById('alert-banner');
    const names = {0:'SYSTEMS NOMINAL',1:'INFO',2:'CAUTION',3:'ALERT'};
    const cls = {0:'alert-ok',1:'alert-info',2:'alert-amber',3:'alert-red'};
    banner.className = 'alert-banner ' + (cls[lvl]||'alert-ok');
    banner.dataset.level = lvl;
    // Only show level name if no message text is stored
    if(!banner.dataset.msg) banner.textContent = names[lvl] || 'OK';
  }
  // Alert message
  else if(topic.endsWith('/alert/message')){
    const el = document.getElementById('alert-msg');
    const lvl = data.level || 0;
    const colors = {0:'var(--ok)',1:'var(--info)',2:'var(--amber)',3:'var(--red)'};
    el.style.color = colors[lvl] || 'var(--text)';
    el.textContent = data.message || 'Systems nominal';
    // Mirror active alerts on the banner too
    const banner = document.getElementById('alert-banner');
    if(lvl > 0 && data.message){
      banner.dataset.msg = data.message;
      banner.textContent = data.message;
    } else {
      delete banner.dataset.msg;
      const names = {0:'SYSTEMS NOMINAL',1:'INFO',2:'CAUTION',3:'ALERT'};
      banner.textContent = names[lvl] || 'SYSTEMS NOMINAL';
    }
  }
  // DTCs
  else if(topic.endsWith('/dtc')){
    renderDtcs(data.stored||[], data.pending||[]);
  }
  // TPMS
  else if(topic.includes('/rf/tpms/') && !topic.endsWith('/snapshot')){
    const pos = topic.split('/').pop();
    if(['fl','fr','rl','rr'].includes(pos)){
      const psi = data.pressure_psi;
      const temp = data.temp_c;
      if(psi!==null&&psi!==undefined){
        setVal(`v-tpms-${pos}-psi`, psi.toFixed(0)+' PSI', psiColor(psi));
      }
      if(temp!==null&&temp!==undefined){
        setVal(`v-tpms-${pos}-temp`, temp.toFixed(0)+'\u00b0C');
      }
    }
  }
  // Wardrive
  else if(topic.includes('/wardrive/')){
    handleWardrive(topic, data);
  }
  // ADS-B
  else if(topic.endsWith('/rf/adsb')){
    handleAdsb(data);
  }
  // Watchdog / system
  else if(topic.endsWith('/system/watchdog')){
    const sys = data.system || {};
    if(sys.cpu_temp) setVal('v-cpu-temp', sys.cpu_temp.toFixed(0)+'\u00b0C');
    if(sys.disk_percent) setVal('v-disk', sys.disk_percent.toFixed(0)+'% ('+
      (sys.disk_free_gb||'?')+'GB free)');
    if(sys.memory_percent) setVal('v-mem', sys.memory_percent.toFixed(0)+'%');
    if(sys.uptime_seconds){
      const h = Math.floor(sys.uptime_seconds/3600);
      const m = Math.floor((sys.uptime_seconds%3600)/60);
      setVal('v-uptime', h+'h '+m+'m');
    }
  }
}

// ── WebSocket Connection ──
function connect(){
  ws = new WebSocket(WS_URL);
  ws.onopen = ()=>{
    document.getElementById('dc-overlay').classList.add('hidden');
    document.getElementById('dot-conn').className='status-dot dot-ok';
    document.getElementById('conn-text').textContent='LIVE';
  };
  ws.onmessage = (e)=>{
    try{handleMessage(JSON.parse(e.data))}catch(err){}
  };
  ws.onclose = ()=>{
    document.getElementById('dc-overlay').classList.remove('hidden');
    document.getElementById('dot-conn').className='status-dot dot-off';
    document.getElementById('conn-text').textContent='OFFLINE';
    setTimeout(connect, 2000);
  };
  ws.onerror = ()=>ws.close();
}

// ── Audio WebSocket ──
function connectAudio(){
  audioWs = new WebSocket(AUDIO_WS_URL);
  audioWs.binaryType = 'arraybuffer';
  audioWs.onmessage = (e)=>{
    if(!audioEnabled || !audioCtx) return;
    // Decode WAV and play
    audioCtx.decodeAudioData(e.data.slice(0)).then(buf=>{
      const src = audioCtx.createBufferSource();
      src.buffer = buf;
      src.connect(audioCtx.destination);
      src.start(0);
    }).catch(()=>{});
  };
  audioWs.onclose = ()=>setTimeout(connectAudio, 5000);
  audioWs.onerror = ()=>audioWs.close();
}

// ── Audio Toggle ──
document.getElementById('audio-btn').addEventListener('click', ()=>{
  audioEnabled = !audioEnabled;
  const btn = document.getElementById('audio-btn');
  btn.classList.toggle('active', audioEnabled);
  if(audioEnabled && !audioCtx){
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    connectAudio();
  }
});

// ── Data Age Timer ──
setInterval(()=>{
  const el = document.getElementById('data-age');
  if(!lastDataTime){el.textContent='NO DATA';return}
  const age = (Date.now()-lastDataTime)/1000;
  if(age<2) el.textContent='LIVE';
  else if(age<10) el.textContent=age.toFixed(0)+'s ago';
  else el.textContent='STALE ('+age.toFixed(0)+'s)';
  el.style.color = age<5?'var(--ok)':age<30?'var(--amber)':'var(--red)';
}, 1000);

// ── Diagnosis ──
function triggerAnalysis(){
  fetch('/api/analyse',{method:'POST'})
    .then(r=>r.json())
    .then(d=>{document.getElementById('diag-primary').textContent='Analysis triggered — check back in ~60s';})
    .catch(()=>{});
}
function loadReport(){
  fetch('/api/report').then(r=>r.json()).then(report=>{
    if(!report||!report.session_id) return;
    const ps=report.primary_suspect||{};
    const conf=ps.confidence!=null?` (${ps.confidence}%)`:'';
    document.getElementById('diag-primary').textContent=(ps.diagnosis||'Unknown')+conf;
    document.getElementById('diag-primary').style.color=report.safety_critical?'var(--red)':'var(--ok)';
    document.getElementById('diag-evidence').textContent=ps.evidence||'';
    const actions=(report.action_items||[]).map(a=>`• ${a}`).join('\n');
    document.getElementById('diag-actions').textContent=actions;
    document.getElementById('diag-safety').style.display=report.safety_critical?'':'none';
    document.getElementById('diag-json').textContent=JSON.stringify(report,null,2);
  }).catch(()=>{});
}
loadReport();
setInterval(loadReport,30000);

// ── Wardrive live updates ──
function handleWardrive(topic, data){
  if(topic.endsWith('/wardrive/wifi')){
    const nets=data.scan||[];
    document.getElementById('wd-wifi-count').textContent=nets.length;
    const tot=data.session_total||0;
    document.getElementById('wd-session-totals').textContent=
      `session: ${tot} unique SSIDs`;
    if(!nets.length){
      document.getElementById('wd-networks').textContent='No Wi-Fi networks in range';
      return;
    }
    const sorted=[...nets].sort((a,b)=>(b.signal_dbm||0)-(a.signal_dbm||0));
    document.getElementById('wd-networks').innerHTML=sorted.slice(0,8).map(n=>{
      const dbm=n.signal_dbm!=null?n.signal_dbm+'dBm':'';
      const sec=n.security?`<span style="color:#555;margin-left:4px">${esc(n.security)}</span>`:'';
      return `<div style="display:flex;justify-content:space-between;padding:2px 0;border-bottom:1px solid #1a1a1a">
        <span style="color:var(--text)">${esc(n.ssid||'<hidden>')}</span>
        <span style="color:var(--dim)">${esc(n.channel||'')}${dbm?'&ensp;'+dbm:''}${sec}</span>
      </div>`;
    }).join('');
  }
  else if(topic.endsWith('/wardrive/bt')){
    const devs=data.devices||[];
    document.getElementById('wd-bt-count').textContent=devs.length;
  }
}

// ── ADS-B live updates ──
function handleAdsb(data){
  const panel=document.getElementById('adsb-panel');
  const aircraft=data.aircraft||[];
  if(!aircraft.length){
    panel.textContent=`No aircraft detected (${data.count||0} in scan, ${data.messages||0} msgs)`;
    return;
  }
  panel.innerHTML=aircraft.slice(0,6).map(a=>{
    const cs=(a.flight||a.hex||'?').trim();
    const alt=a.altitude?Math.round(a.altitude).toLocaleString()+"ft":'--';
    const spd=a.speed?Math.round(a.speed)+"kt":'--';
    const rssi=a.rssi!=null?a.rssi.toFixed(0)+'dBFS':'';
    return `<div style="display:flex;justify-content:space-between;padding:2px 0;border-bottom:1px solid #1a1a1a">
      <span style="color:var(--accent);font-weight:bold">${esc(cs)}</span>
      <span style="color:var(--dim)">${alt}&ensp;${spd}${rssi?'&ensp;'+rssi:''}</span>
    </div>`;
  }).join('');
}

function esc(s){const d=document.createElement('div');d.textContent=String(s||'');return d.innerHTML;}

// ── DTC description enrichment ──
const dtcCache = {};
async function fetchDtcDesc(code){
  if(dtcCache[code]!==undefined) return dtcCache[code];
  try{
    const r=await fetch('/api/mechanic/dtc/'+code);
    const d=await r.json();
    dtcCache[code]=d.desc||'';
  }catch(e){dtcCache[code]='';}
  return dtcCache[code];
}
async function renderDtcs(stored, pending){
  const el=document.getElementById('dtc-list');
  if(!stored.length&&!pending.length){
    el.innerHTML='<span style="color:var(--ok);font-size:11px">No DTCs</span>';
    return;
  }
  const all=[...stored.map(c=>({c,p:false})),...pending.map(c=>({c,p:true}))];
  const descs=await Promise.all(all.map(({c})=>fetchDtcDesc(c)));
  el.innerHTML=all.map(({c,p},i)=>{
    const desc=descs[i]?`<span style="font-size:10px;color:var(--dim);display:block;margin-top:1px">${descs[i]}</span>`:'';
    return `<div style="margin:3px 0"><span class="dtc-code${p?' dtc-pending':''}">${c}</span>${desc}</div>`;
  }).join('');
}

// ── Recent Drives ──
function loadSessions(){
  fetch('/api/sessions').then(r=>r.json()).then(sessions=>{
    const el=document.getElementById('sessions-list');
    if(!sessions||!sessions.length){el.textContent='No sessions recorded yet';return;}
    el.innerHTML=sessions.slice(0,5).map(s=>{
      const d=new Date((s.start_ts||0)*1000);
      const dateStr=d.toLocaleDateString('en-GB',{day:'2-digit',month:'short',year:'2-digit'});
      const dur=Math.round((s.duration_seconds||0)/60);
      const dist=(s.distance_km||0).toFixed(1);
      const cool=s.max_coolant?Math.round(s.max_coolant)+'°C':'--';
      const volt=s.min_voltage?s.min_voltage.toFixed(1)+'V':'--';
      const alerts=s.alert_count||0;
      const alertBadge=alerts?`<span style="color:var(--amber);margin-left:6px">${alerts} alert${alerts>1?'s':''}</span>`:'';
      return `<div style="border-left:2px solid #2a2a2a;padding:5px 0 5px 10px;margin-bottom:6px">
        <div style="color:var(--text);font-size:11px">${dateStr}&ensp;<span style="color:var(--dim)">${dur}min &bull; ${dist}&thinsp;km</span>${alertBadge}</div>
        <div style="font-size:10px;color:var(--dim);margin-top:2px">Cool ${cool} &bull; ${volt}</div>
      </div>`;
    }).join('');
  }).catch(()=>{});
}
loadSessions();

// ── Ask Mechanic (LLM) ──
let queryBusy=false;

function _submitQuery(q){
  if(queryBusy||!q) return;
  queryBusy=true;
  const out=document.getElementById('ask-output');
  const meta=document.getElementById('ask-meta');
  const btn=document.getElementById('ask-btn');
  out.style.color='var(--dim)';
  out.innerHTML='<span style="animation:pulse 1.5s infinite">Thinking\u2026</span>';
  if(meta) meta.textContent='';
  btn.disabled=true;
  btn.textContent='\u2026';
  fetch('/api/query',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query:q})})
    .then(r=>r.json())
    .then(d=>{
      if(d.error){
        out.style.color='var(--red)';
        out.textContent='Error: '+d.error;
      } else {
        out.style.color='var(--text)';
        // Escape HTML before injecting text but allow our own line breaks
        const text = d.response || '';
        const escText = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#039;');
        out.innerHTML = escText.replace(/\n/g, '<br>');
        if(meta){
          const m=(d.model||'').split('/').pop();
          meta.textContent=m+(d.tokens?' \u00b7 '+d.tokens+' tok':'');
        }
      }
    })
    .catch(()=>{out.style.color='var(--red)';out.textContent='Request failed \u2014 is Ollama running?';})
    .finally(()=>{queryBusy=false;btn.disabled=false;btn.textContent='ASK';});
}

function askMechanic(){
  const q=document.getElementById('ask-input').value.trim();
  _submitQuery(q);
}

// Quick-pick chip — highlight it, fill the input, and submit immediately
function chipAsk(el){
  document.querySelectorAll('.chip').forEach(c=>c.classList.remove('active'));
  el.classList.add('active');
  const q=el.textContent;
  document.getElementById('ask-input').value=q;
  _submitQuery(q);
}

// Voice input via Web Speech API
let recognition=null;
function toggleMic(){
  const SpeechRecognition=window.SpeechRecognition||window.webkitSpeechRecognition;
  const btn=document.getElementById('mic-btn');
  if(!SpeechRecognition){
    document.getElementById('ask-output').textContent='Voice input not supported in this browser.';
    return;
  }
  if(recognition){
    recognition.stop();
    return;
  }
  recognition=new SpeechRecognition();
  recognition.lang='en-GB';
  recognition.interimResults=false;
  recognition.maxAlternatives=1;
  btn.style.color='var(--red)';
  btn.title='Listening... tap to cancel';
  recognition.onresult=e=>{
    const transcript=e.results[0][0].transcript;
    document.getElementById('ask-input').value=transcript;
    _submitQuery(transcript);
  };
  recognition.onerror=()=>{};
  recognition.onend=()=>{
    recognition=null;
    btn.style.color='var(--dim)';
    btn.title='Voice input';
  };
  recognition.start();
}

document.getElementById('ask-input').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();askMechanic();}});

// ── Start ──
connect();
</script>
</body>
</html>"""


# ═══════════════════════════════════════════════════════════════════
#  Mechanic Advisor Page (offline knowledge base)
# ═══════════════════════════════════════════════════════════════════

MECHANIC_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<meta name="apple-mobile-web-app-capable" content="yes">
<title>DRIFTER MECHANIC</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{--bg:#0a0a0a;--card:#141414;--border:#222;--text:#e0e0e0;--dim:#666;
--accent:#00bcd4;--ok:#4caf50;--info:#2196f3;--amber:#ff9800;--red:#f44336}
body{background:var(--bg);color:var(--text);font-family:'Courier New',monospace;
overflow-x:hidden;max-width:800px;margin:0 auto}

.header{text-align:center;padding:12px 0 8px;border-bottom:1px solid var(--border);
background:linear-gradient(180deg,#111,#0a0a0a);position:sticky;top:0;z-index:100}
.header h1{font-size:18px;letter-spacing:4px;color:var(--accent)}
.header .sub{font-size:10px;color:var(--dim);margin-top:2px}

.nav{display:flex;gap:0;border-bottom:1px solid var(--border);overflow-x:auto;
position:sticky;top:50px;z-index:99;background:var(--bg)}
.nav a{padding:10px 14px;font-size:11px;color:var(--dim);text-decoration:none;
white-space:nowrap;border-bottom:2px solid transparent;text-transform:uppercase;letter-spacing:1px}
.nav a.active{color:var(--accent);border-bottom-color:var(--accent)}
.nav a:hover{color:var(--text)}

.search-box{padding:12px;position:sticky;top:92px;z-index:98;background:var(--bg)}
.search-box input{width:100%;padding:10px 14px;background:#1a1a1a;border:1px solid var(--border);
border-radius:8px;color:var(--text);font-family:inherit;font-size:14px;outline:none}
.search-box input:focus{border-color:var(--accent)}
.search-box input::placeholder{color:#444}

.content{padding:10px}

.card{background:var(--card);border:1px solid var(--border);border-radius:8px;
padding:14px;margin-bottom:10px}
.card h3{color:var(--accent);font-size:14px;margin-bottom:8px}
.card h4{color:var(--amber);font-size:12px;margin:10px 0 4px;text-transform:uppercase;letter-spacing:1px}
.card p,.card li{font-size:12px;line-height:1.5;color:#ccc}
.card ul{margin-left:16px}
.card li{margin-bottom:3px}
.card .tag{display:inline-block;background:#1a2a2a;color:var(--accent);padding:2px 8px;
border-radius:4px;font-size:10px;margin:2px}
.card .severity-red{border-left:3px solid var(--red)}
.card .severity-amber{border-left:3px solid var(--amber)}
.card .severity-info{border-left:3px solid var(--info)}

.spec-table{width:100%;border-collapse:collapse;font-size:12px}
.spec-table td{padding:5px 8px;border-bottom:1px solid #1a1a1a}
.spec-table td:first-child{color:var(--dim);width:40%;text-transform:capitalize}
.spec-table td:last-child{color:var(--text)}

.step-num{display:inline-block;background:var(--accent);color:#000;width:20px;height:20px;
border-radius:50%;text-align:center;line-height:20px;font-size:11px;font-weight:bold;margin-right:8px;flex-shrink:0}
.step{display:flex;align-items:flex-start;margin-bottom:8px}
.step p{font-size:12px;line-height:1.5}

.section-title{padding:12px 4px 6px;font-size:12px;color:var(--dim);
text-transform:uppercase;letter-spacing:2px;border-top:1px solid var(--border);margin-top:8px}

.back-link{display:inline-block;padding:8px 16px;color:var(--accent);font-size:12px;text-decoration:none}

.training-content{white-space:pre-wrap;font-size:12px;line-height:1.6;color:#ccc}

.empty{text-align:center;padding:40px;color:var(--dim);font-size:13px}

#results-count{font-size:11px;color:var(--dim);padding:4px 14px}

.home-btn{position:fixed;bottom:16px;left:16px;z-index:200;width:44px;height:44px;
border-radius:50%;border:2px solid var(--accent);background:var(--card);color:var(--accent);
font-size:18px;cursor:pointer;display:flex;align-items:center;justify-content:center;text-decoration:none}
</style>
</head>
<body>

<div class="header">
  <h1>DRIFTER MECHANIC</h1>
  <div class="sub">2004 JAGUAR X-TYPE 2.5L V6 &mdash; OFFLINE ADVISOR</div>
</div>

<div class="nav" id="nav">
  <a href="#" data-tab="search" class="active">SEARCH</a>
  <a href="#" data-tab="problems">PROBLEMS</a>
  <a href="#" data-tab="specs">SPECS</a>
  <a href="#" data-tab="emergency">EMERGENCY</a>
  <a href="#" data-tab="service">SERVICE</a>
  <a href="#" data-tab="torque">TORQUE</a>
  <a href="#" data-tab="fuses">FUSES</a>
  <a href="#" data-tab="tsb">TSB</a>
  <a href="#" data-tab="training">TRAINING</a>
</div>

<div class="search-box" id="search-box">
  <input type="text" id="search-input" placeholder="Search: thermostat, P0171, coolant, misfire..."
         autocomplete="off" autofocus>
</div>
<div id="results-count"></div>

<div class="content" id="content">
  <div class="empty">Type a keyword to search the X-Type knowledge base.<br>
  Or tap a category above to browse.</div>
</div>

<a href="/" class="home-btn" title="Back to dashboard">&#x25C0;</a>

<script>
const API = '';
let currentTab = 'search';
let debounceTimer = null;

// ── Tab Navigation ──
document.getElementById('nav').addEventListener('click', (e) => {
  if (e.target.tagName !== 'A') return;
  e.preventDefault();
  const tab = e.target.dataset.tab;
  document.querySelectorAll('.nav a').forEach(a => a.classList.remove('active'));
  e.target.classList.add('active');
  currentTab = tab;
  document.getElementById('search-box').style.display = tab === 'search' ? '' : 'none';
  document.getElementById('results-count').textContent = '';
  loadTab(tab);
});

function loadTab(tab) {
  const c = document.getElementById('content');
  c.innerHTML = '<div class="empty">Loading...</div>';

  if (tab === 'search') {
    c.innerHTML = '<div class="empty">Type a keyword to search the X-Type knowledge base.</div>';
    return;
  }

  const endpoints = {
    problems: '/api/mechanic/problems',
    specs: '/api/mechanic/specs',
    emergency: '/api/mechanic/emergency',
    service: '/api/mechanic/service',
    torque: '/api/mechanic/torque',
    fuses: '/api/mechanic/fuses',
    tsb: '/api/mechanic/tsb',
    training: '/api/mechanic/training',
  };

  fetch(endpoints[tab]).then(r => r.json()).then(data => {
    if (tab === 'problems') renderProblems(data);
    else if (tab === 'specs') renderSpecs(data);
    else if (tab === 'emergency') renderEmergency(data);
    else if (tab === 'service') renderService(data);
    else if (tab === 'torque') renderTorque(data);
    else if (tab === 'fuses') renderFuses(data);
    else if (tab === 'tsb') renderTSB(data);
    else if (tab === 'training') renderTraining(data);
  }).catch(() => { c.innerHTML = '<div class="empty">Failed to load.</div>'; });
}

// ── Search ──
document.getElementById('search-input').addEventListener('input', (e) => {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => doSearch(e.target.value), 250);
});

function doSearch(q) {
  if (!q || q.length < 2) {
    document.getElementById('content').innerHTML =
      '<div class="empty">Type a keyword to search the X-Type knowledge base.</div>';
    document.getElementById('results-count').textContent = '';
    return;
  }
  fetch(`/api/mechanic/search?q=${encodeURIComponent(q)}`)
    .then(r => r.json())
    .then(data => {
      const c = document.getElementById('content');
      const rc = document.getElementById('results-count');
      if (!data.results || !data.results.length) {
        c.innerHTML = '<div class="empty">No results. Try different keywords.</div>';
        rc.textContent = '0 results';
        return;
      }
      rc.textContent = data.results.length + ' result(s)';
      c.innerHTML = data.results.map(r => renderResult(r)).join('');
    });
}

function renderResult(r) {
  if (r.type === 'problem') return renderProblemCard(r.data);
  if (r.type === 'emergency') return renderEmergencyCard(r.data);
  if (r.type === 'torque') return `<div class="card"><h3>${esc(r.title)}</h3></div>`;
  if (r.type === 'spec') return `<div class="card"><h3>${esc(r.title)}</h3><p>${esc(r.data.value)}</p></div>`;
  if (r.type === 'fuse') return `<div class="card"><h3>${esc(r.data.fuse)}: ${esc(r.data.description)}</h3><p>${esc(r.data.box)} — ${esc(r.data.location)}</p></div>`;
  if (r.type === 'tsb') return renderTSBCard(r.data);
  if (r.type === 'training') return renderTrainingCard(r.data);
  return `<div class="card"><h3>${esc(r.title)}</h3></div>`;
}

// ── Renderers ──
function renderProblemCard(p) {
  return `<div class="card">
    <h3>${esc(p.title)}</h3>
    <h4>Symptoms</h4><ul>${(p.symptoms||[]).map(s=>'<li>'+esc(s)+'</li>').join('')}</ul>
    <h4>Cause</h4><p>${esc(p.cause)}</p>
    <h4>Fix</h4><p>${esc(p.fix)}</p>
    ${p.parts?'<h4>Parts Needed</h4><ul>'+p.parts.map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul>':''}
    <h4>Difficulty</h4><p>${esc(p.difficulty||'')}</p>
    <h4>Estimated Cost</h4><p>${esc(p.cost||'')}</p>
  </div>`;
}
function renderProblems(data) {
  document.getElementById('content').innerHTML = data.map(p => renderProblemCard(p)).join('');
}

function renderSpecs(data) {
  let html = '';
  for (const [cat, specs] of Object.entries(data)) {
    html += `<div class="section-title">${esc(cat)}</div><div class="card"><table class="spec-table">`;
    for (const [k, v] of Object.entries(specs)) {
      html += `<tr><td>${esc(k.replace(/_/g,' '))}</td><td>${esc(v)}</td></tr>`;
    }
    html += '</table></div>';
  }
  document.getElementById('content').innerHTML = html;
}

function renderEmergencyCard(proc) {
  return `<div class="card severity-red">
    <h3>${esc(proc.title)}</h3>
    ${proc.steps.map((s,i)=>`<div class="step"><span class="step-num">${i+1}</span><p>${esc(s)}</p></div>`).join('')}
  </div>`;
}
function renderEmergency(data) {
  document.getElementById('content').innerHTML = data.map(p => renderEmergencyCard(p)).join('');
}

function renderService(data) {
  let html = data.map(s => `<div class="card">
    <h3>${esc(s.item)}</h3>
    <p style="color:var(--amber)">${esc(s.interval)}</p>
    <p style="margin-top:6px">${esc(s.details)}</p>
  </div>`).join('');
  document.getElementById('content').innerHTML = html;
}

function renderTorque(data) {
  let html = '<div class="card"><table class="spec-table">';
  for (const [part, torque] of Object.entries(data)) {
    html += `<tr><td>${esc(part)}</td><td style="color:var(--accent)">${esc(torque)}</td></tr>`;
  }
  html += '</table></div>';
  document.getElementById('content').innerHTML = html;
}

function renderFuses(data) {
  let html = '';
  for (const [box, info] of Object.entries(data)) {
    html += `<div class="section-title">${esc(box)}</div>`;
    html += `<div class="card"><p style="color:var(--amber);margin-bottom:8px">${esc(info.location)}</p>`;
    html += '<table class="spec-table">';
    for (const [fuse, desc] of Object.entries(info.key_fuses || {})) {
      html += `<tr><td>${esc(fuse)}</td><td>${esc(desc)}</td></tr>`;
    }
    html += '</table></div>';
  }
  document.getElementById('content').innerHTML = html;
}

function renderTSBCard(t) {
  return `<div class="card severity-amber">
    <h3>${esc(t.ref||'')}: ${esc(t.title)}</h3>
    <p>${esc(t.description)}</p>
    <h4>Action</h4><p>${esc(t.action)}</p>
    <p style="margin-top:6px;color:var(--dim)">Affected: ${esc(t.affected||'')}</p>
  </div>`;
}
function renderTSB(data) {
  if (!data || !data.length) {
    document.getElementById('content').innerHTML = '<div class="empty">No TSBs loaded.</div>';
    return;
  }
  document.getElementById('content').innerHTML = data.map(t => renderTSBCard(t)).join('');
}

function renderTrainingCard(t) {
  return `<div class="card">
    <h3>${esc(t.title)}</h3>
    <div class="training-content">${esc(t.content)}</div>
  </div>`;
}
function renderTraining(data) {
  if (!data || !data.length) {
    document.getElementById('content').innerHTML = '<div class="empty">No training modules loaded.</div>';
    return;
  }
  document.getElementById('content').innerHTML = data.map(t => renderTrainingCard(t)).join('');
}

function esc(s) {
  if (s === null || s === undefined) return '';
  const d = document.createElement('div');
  d.textContent = String(s);
  return d.innerHTML;
}
</script>
</body>
</html>"""


# ═══════════════════════════════════════════════════════════════════
#  HTTP Server (serves the dashboard HTML + mechanic advisor)
# ═══════════════════════════════════════════════════════════════════

class DashboardHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(self.path)

        if parsed.path in ('/', '/index.html'):
            self._serve_html(DASHBOARD_HTML)
        elif parsed.path == '/mechanic':
            self._serve_html(MECHANIC_HTML)
        elif parsed.path == '/api/state':
            self._serve_json(latest_state)
        elif parsed.path == '/api/hardware':
            self._serve_json(check_hardware())
        elif parsed.path == '/api/mechanic/search':
            q = parse_qs(parsed.query).get('q', [''])[0]
            results = mechanic_search(q)
            self._serve_json({'query': q, 'results': results})
        elif parsed.path == '/api/mechanic/specs':
            self._serve_json(VEHICLE_SPECS)
        elif parsed.path == '/api/mechanic/problems':
            self._serve_json(COMMON_PROBLEMS)
        elif parsed.path == '/api/mechanic/service':
            self._serve_json(SERVICE_SCHEDULE)
        elif parsed.path == '/api/mechanic/emergency':
            self._serve_json(EMERGENCY_PROCEDURES)
        elif parsed.path == '/api/mechanic/torque':
            self._serve_json(TORQUE_SPECS)
        elif parsed.path == '/api/mechanic/fuses':
            self._serve_json(FUSE_REFERENCE)
        elif parsed.path == '/api/mechanic/advice':
            msg = parse_qs(parsed.query).get('alert', [''])[0]
            advice = get_advice_for_alert(msg)
            self._serve_json({'alert': msg, 'advice': advice or []})
        elif parsed.path == '/api/mechanic/training':
            try:
                from mechanic import TRAINING_MODULES
                self._serve_json(TRAINING_MODULES)
            except ImportError:
                self._serve_json([])
        elif parsed.path == '/api/mechanic/tsb':
            try:
                from mechanic import TECHNICAL_BULLETINS
                self._serve_json(TECHNICAL_BULLETINS)
            except ImportError:
                self._serve_json([])
        elif parsed.path == '/api/sessions':
            try:
                import db as _db
                self._serve_json(_db.get_recent_sessions(10))
            except Exception:
                self._serve_json([])
        elif parsed.path == '/api/wardrive':
            wifi = latest_state.get('wardrive_wifi', {})
            bt = latest_state.get('wardrive_bt', {})
            adsb = latest_state.get('rf_adsb', {})
            self._serve_json({
                'wifi': wifi,
                'bluetooth': bt,
                'adsb': adsb,
            })
        elif parsed.path.startswith('/api/mechanic/dtc/'):
            code = parsed.path.split('/')[-1].upper()
            from config import XTYPE_DTC_LOOKUP
            info = XTYPE_DTC_LOOKUP.get(code, {})
            self._serve_json({'code': code, **info})
        elif parsed.path == '/api/report':
            self._serve_json(latest_report)
        elif parsed.path == '/api/reports':
            try:
                import sys as _sys
                if 'src' not in _sys.path:
                    _sys.path.insert(0, '/opt/drifter')
                import db as _db
                self._serve_json(_db.get_recent_reports(10))
            except Exception:
                self._serve_json([])
        elif parsed.path in ('/screen', '/screen.html'):
            screen_path = Path('/opt/drifter/screen_dash.html')
            if screen_path.exists():
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Cache-Control', 'no-cache')
                self.end_headers()
                self.wfile.write(screen_path.read_bytes())
            else:
                self.send_error(404, 'Screen dashboard not found')
        elif parsed.path == '/realdash.xml':
            xml_path = Path('/opt/drifter/drifter_channels.xml')
            if xml_path.exists():
                self.send_response(200)
                self.send_header('Content-Type', 'application/xml')
                self.send_header('Content-Disposition', 'attachment; filename="drifter_channels.xml"')
                self.end_headers()
                self.wfile.write(xml_path.read_bytes())
            else:
                self.send_error(404)
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == '/api/analyse':
            try:
                mqtt_client.publish('drifter/analysis/request', '{}')
            except Exception:
                pass
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"status": "triggered"}')
        elif self.path == '/api/query':
            try:
                length = int(self.headers.get('Content-Length', 0))
                body = json.loads(self.rfile.read(length))
                query = body.get('query', '').strip()
                if not query:
                    self.send_error(400, 'Missing query')
                    return

                # Build context: live telemetry + relevant KB entries
                from mechanic import search as kb_search
                context_parts = []

                # Telemetry snapshot
                telem_lines = []
                def _v(key):
                    d = latest_state.get(key, {})
                    return d.get('value') if isinstance(d, dict) else None
                rpm = _v('engine_rpm')
                cool = _v('engine_coolant')
                speed = _v('vehicle_speed')
                stft1 = _v('engine_stft1')
                stft2 = _v('engine_stft2')
                ltft1 = _v('engine_ltft1')
                ltft2 = _v('engine_ltft2')
                volt = _v('power_voltage')
                load = _v('engine_load')
                throttle = _v('vehicle_throttle')
                iat = _v('engine_iat')
                maf = _v('engine_maf')
                if rpm is not None:   telem_lines.append(f"RPM: {rpm:.0f}")
                if cool is not None:  telem_lines.append(f"Coolant: {cool:.1f}°C")
                if speed is not None: telem_lines.append(f"Speed: {speed:.0f} km/h")
                if stft1 is not None: telem_lines.append(f"STFT B1: {stft1:+.1f}%")
                if stft2 is not None: telem_lines.append(f"STFT B2: {stft2:+.1f}%")
                if ltft1 is not None: telem_lines.append(f"LTFT B1: {ltft1:+.1f}%")
                if ltft2 is not None: telem_lines.append(f"LTFT B2: {ltft2:+.1f}%")
                if volt is not None:  telem_lines.append(f"Battery: {volt:.1f}V")
                if load is not None:  telem_lines.append(f"Load: {load:.0f}%")
                if throttle is not None: telem_lines.append(f"Throttle: {throttle:.0f}%")
                if iat is not None:   telem_lines.append(f"IAT: {iat:.0f}°C")
                if maf is not None:   telem_lines.append(f"MAF: {maf:.1f} g/s")

                # DTCs
                dtc_data = latest_state.get('diag_dtc', {})
                stored_dtcs = dtc_data.get('stored', []) if isinstance(dtc_data, dict) else []
                pending_dtcs = dtc_data.get('pending', []) if isinstance(dtc_data, dict) else []
                if stored_dtcs:
                    telem_lines.append(f"Active DTCs: {', '.join(stored_dtcs)}")
                if pending_dtcs:
                    telem_lines.append(f"Pending DTCs: {', '.join(pending_dtcs)}")

                # Current alert
                alert_d = latest_state.get('alert_message', {})
                alert_msg = alert_d.get('message', '') if isinstance(alert_d, dict) else ''
                if alert_msg and alert_msg != 'Systems nominal':
                    telem_lines.append(f"Active alert: {alert_msg}")

                if telem_lines:
                    context_parts.append("CURRENT VEHICLE STATE:\n" + "\n".join(telem_lines))
                else:
                    context_parts.append("CURRENT VEHICLE STATE: No live telemetry — car may be off")

                # KB retrieval (up to 5 entries for better context)
                kb_results = kb_search(query)
                kb_lines = []
                for r in kb_results[:5]:
                    if r.get('type') == 'problem':
                        p = r['data']
                        kb_lines.append(
                            f"KNOWN ISSUE: {p['title']}\n"
                            f"Cause: {p.get('cause','')}\n"
                            f"Fix: {p.get('fix','')}\n"
                            f"Cost: {p.get('cost', 'Unknown')}"
                        )
                    elif r.get('type') == 'dtc':
                        d = r['data']
                        kb_lines.append(
                            f"DTC: {d.get('code','')} — {d.get('desc','')}\n"
                            f"Causes: {', '.join(d.get('causes', []))}"
                        )
                    elif r.get('type') == 'telemetry_guide':
                        kb_lines.append(f"GUIDE: {r.get('title', '')}")
                if kb_lines:
                    context_parts.append("RELEVANT KNOWLEDGE:\n" + "\n---\n".join(kb_lines))

                prompt = query
                if context_parts:
                    prompt += "\n\n---\n\n" + "\n\n".join(context_parts)

                import llm_client
                result = llm_client.query_chat(prompt)
                self._serve_json({
                    'response': result['text'],
                    'model': result['model'],
                    'tokens': result['tokens'],
                })
            except Exception as e:
                log.warning(f"Query error: {e}")
                self._serve_json({'error': str(e)})
        else:
            self.send_error(404)

    def _serve_html(self, html):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(html.encode())

    def _serve_json(self, data):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str).encode())

    def log_message(self, format, *args):
        pass  # Suppress HTTP logs


def run_http_server():
    server = HTTPServer(('0.0.0.0', WEB_PORT), DashboardHandler)
    server.timeout = 2
    log.info(f"HTTP dashboard on http://0.0.0.0:{WEB_PORT}")
    while running:
        server.handle_request()
    server.server_close()


# ═══════════════════════════════════════════════════════════════════
#  WebSocket Server (streams live MQTT to browser)
# ═══════════════════════════════════════════════════════════════════

async def ws_handler(websocket):
    """Handle a WebSocket client — stream MQTT data."""
    queue = asyncio.Queue(maxsize=200)
    ws_clients.add(queue)
    log.info(f"Dashboard client connected ({len(ws_clients)} total)")
    try:
        # Send current state snapshot
        for key, data in latest_state.items():
            if key.startswith('_'):
                continue
            topic = 'drifter/' + key.replace('_', '/')
            await websocket.send(json.dumps({
                'topic': topic, 'data': data, 'ts': time.time()
            }))

        while running:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=1.0)
                await websocket.send(msg)
            except asyncio.TimeoutError:
                # Send keepalive ping
                try:
                    await websocket.ping()
                except Exception:
                    break
    except websockets.ConnectionClosed:
        pass
    finally:
        ws_clients.discard(queue)
        log.info(f"Dashboard client disconnected ({len(ws_clients)} remaining)")


async def audio_ws_handler(websocket):
    """Handle audio WebSocket client — send TTS WAV when alerts fire."""
    audio_ws_clients.add(websocket)
    log.info(f"Audio client connected ({len(audio_ws_clients)} total)")
    try:
        while running:
            await asyncio.sleep(1)
    except websockets.ConnectionClosed:
        pass
    finally:
        audio_ws_clients.discard(websocket)


async def broadcast_audio(wav_bytes):
    """Send audio WAV to all connected audio clients."""
    if not wav_bytes or not audio_ws_clients:
        return
    dead = set()
    for ws in audio_ws_clients:
        try:
            await ws.send(wav_bytes)
        except Exception:
            dead.add(ws)
    audio_ws_clients.difference_update(dead)


# ═══════════════════════════════════════════════════════════════════
#  Alert → Audio Bridge
# ═══════════════════════════════════════════════════════════════════

def on_alert_message(client, userdata, msg):
    """When voice_alerts publishes WAV via MQTT, forward to audio WebSocket clients.
    Falls back to local TTS generation if the voice service didn't send WAV."""
    global last_audio_text, last_audio_time
    try:
        data = json.loads(msg.payload)

        # Route 1: WAV from voice_alerts.py via drifter/audio/wav
        if msg.topic == 'drifter/audio/wav':
            import base64
            wav_b64 = data.get('wav_b64')
            if wav_b64:
                wav_bytes = base64.b64decode(wav_b64)
                last_audio_text = data.get('text', '')
                last_audio_time = time.time()
                try:
                    if _ws_loop is not None:
                        _ws_loop.call_soon_threadsafe(
                            lambda w=wav_bytes: asyncio.ensure_future(broadcast_audio(w))
                        )
                except RuntimeError:
                    pass
            return

        # Route 2: Fallback — generate TTS locally if no WAV arrived
        level = data.get('level', 0)
        message = data.get('message', '')

        if not message or level < 2:
            return

        now = time.time()
        if message == last_audio_text and now - last_audio_time < 60:
            return
        if now - last_audio_time < 15:
            return

        prefix = "Critical alert. " if level >= 3 else "Warning. "
        text = prefix + message

        wav = generate_audio_wav(text)
        if wav:
            last_audio_text = message
            last_audio_time = now
            try:
                if _ws_loop is not None:
                    _ws_loop.call_soon_threadsafe(
                        lambda w=wav: asyncio.ensure_future(broadcast_audio(w))
                    )
            except RuntimeError:
                pass
    except Exception as e:
        log.warning(f"Audio alert error: {e}")


# ═══════════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════════

def main():
    global running

    log.info("DRIFTER Web Dashboard starting...")

    if not HAS_WEBSOCKETS:
        log.error("websockets package not installed. Run: pip install websockets")
        return

    def _handle_signal(sig, frame):
        global running
        running = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    # ── MQTT (telemetry ingest) ──
    global mqtt_client
    mqtt_client = mqtt.Client(client_id="drifter-dashboard")
    mqtt_client.on_message = on_message

    connected = False
    while not connected and running:
        try:
            mqtt_client.connect(MQTT_HOST, MQTT_PORT, 60)
            connected = True
        except Exception as e:
            log.warning(f"Waiting for MQTT broker... ({e})")
            time.sleep(3)

    mqtt_client.subscribe("drifter/#")
    mqtt_client.loop_start()

    # ── MQTT (alert audio) ──
    audio_mqtt = mqtt.Client(client_id="drifter-dashboard-audio")
    audio_mqtt.on_message = on_alert_message
    try:
        audio_mqtt.connect(MQTT_HOST, MQTT_PORT, 60)
        audio_mqtt.subscribe("drifter/alert/message")
        audio_mqtt.subscribe("drifter/audio/wav")
        audio_mqtt.loop_start()
    except Exception:
        pass

    # ── HTTP Server Thread ──
    http_thread = threading.Thread(target=run_http_server, daemon=True)
    http_thread.start()

    # ── Async Event Loop (WebSocket servers) ──
    global _ws_loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _ws_loop = loop  # Published for MQTT callbacks to schedule coroutines

    async def run_ws_servers():
        async with websockets.serve(ws_handler, '0.0.0.0', WS_PORT):
            log.info(f"WebSocket telemetry on ws://0.0.0.0:{WS_PORT}")
            async with websockets.serve(audio_ws_handler, '0.0.0.0', AUDIO_WS_PORT):
                log.info(f"WebSocket audio on ws://0.0.0.0:{AUDIO_WS_PORT}")
                log.info("")
                log.info("=== DRIFTER DASHBOARD LIVE ===")
                log.info(f"  Open on phone: http://10.13.12.1:{WEB_PORT}")
                log.info(f"  Local:         http://localhost:{WEB_PORT}")
                log.info(f"  RealDash TCP:  10.13.12.1:35000 (still available)")
                log.info("")
                while running:
                    await asyncio.sleep(0.5)

    try:
        loop.run_until_complete(run_ws_servers())
    except Exception as e:
        if running:
            log.error(f"WebSocket server error: {e}")

    # Cleanup
    mqtt_client.loop_stop()
    mqtt_client.disconnect()
    audio_mqtt.loop_stop()
    audio_mqtt.disconnect()
    log.info("Dashboard stopped")


if __name__ == '__main__':
    main()

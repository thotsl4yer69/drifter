"""HTML templates served by web_dashboard.

These are pure presentation — kept in their own module so web_dashboard.py
stays focused on server wiring.  Each template is a self-contained page
(no external CSS/JS dependencies) so the dashboard works on a phone
without any network besides the Pi's hotspot.
"""

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
  background:rgba(0,0,0,.95);padding:30px;border-radius:12px;
  text-align:center;z-index:999;border:1px solid var(--red);
  max-width:320px;
}
.disconnected h2{color:var(--red);margin-bottom:8px}
.disconnected p{color:var(--dim);font-size:12px;line-height:1.6}
.disconnected .retry-info{color:var(--amber);font-size:11px;margin-top:10px}
.hidden{display:none!important}

/* Toast Notifications */
.toast-container{
  position:fixed;bottom:70px;left:50%;transform:translateX(-50%);
  z-index:1000;display:flex;flex-direction:column-reverse;gap:6px;
  pointer-events:none;max-width:90%;
}
.toast{
  padding:10px 16px;border-radius:8px;font-size:12px;
  pointer-events:auto;animation:toast-in .3s ease;
  display:flex;align-items:center;gap:8px;
  box-shadow:0 4px 12px rgba(0,0,0,.5);
}
.toast.info{background:#1a2733;color:var(--info);border:1px solid #2196f355}
.toast.warn{background:#2e2a1a;color:var(--amber);border:1px solid #ff980055}
.toast.error{background:#2e1a1a;color:var(--red);border:1px solid #f4433655}
.toast.success{background:#1b2e1b;color:var(--ok);border:1px solid #4caf5055}
@keyframes toast-in{from{opacity:0;transform:translateY(20px)}to{opacity:1;transform:translateY(0)}}
@keyframes toast-out{from{opacity:1}to{opacity:0;transform:translateY(-10px)}}

/* Help Tooltip */
.help-icon{
  display:inline-block;width:14px;height:14px;border-radius:50%;
  background:#222;color:var(--dim);font-size:9px;text-align:center;
  line-height:14px;cursor:pointer;margin-left:4px;vertical-align:middle;
  border:1px solid #333;user-select:none;
}
.help-icon:hover{color:var(--accent);border-color:var(--accent)}
.help-tip{
  display:none;position:absolute;left:0;right:0;top:100%;z-index:50;
  background:#1a1a1a;border:1px solid var(--border);border-radius:6px;
  padding:8px 10px;font-size:11px;color:var(--dim);line-height:1.5;
  margin-top:4px;font-weight:normal;text-transform:none;letter-spacing:0;
}
.help-tip.show{display:block}

/* Alert expanded */
.alert-expand{
  background:#111;margin:0 10px;border-radius:0 0 6px 6px;
  padding:10px 16px;font-size:11px;line-height:1.5;
  border:1px solid #222;border-top:none;
  max-height:0;overflow:hidden;transition:max-height .3s ease,padding .3s;
}
.alert-expand.open{max-height:400px;padding:10px 16px}
.alert-expand .advice-text{color:var(--dim);margin-bottom:8px}
.alert-expand .alert-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:6px}
.alert-expand .alert-actions button{
  padding:4px 10px;background:#1a1a1a;border:1px solid #333;border-radius:4px;
  color:var(--accent);font-family:inherit;font-size:11px;cursor:pointer;
}

/* Alert History */
.alert-history{
  max-height:200px;overflow-y:auto;padding:4px 16px 8px;font-size:11px;
}
.alert-history-item{
  display:flex;justify-content:space-between;padding:4px 0;
  border-bottom:1px solid #1a1a1a;color:var(--dim);
}
.alert-history-item .ah-time{color:#555;white-space:nowrap;margin-left:8px}

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
  <div class="card" style="position:relative">
    <div class="label">STFT B1 <span class="help-icon" tabindex="0" onclick="toggleHelp(this)" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();toggleHelp(this)}" data-help="Short-Term Fuel Trim Bank 1: How much the ECU is adjusting fuel right now. Positive = adding fuel (lean). Negative = removing fuel (rich). Normal: &plusmn;5%.">?</span></div>
    <div class="value" id="v-stft1">--</div>
    <div class="unit">%</div>
    <div class="trim-bar-wrap"><div class="trim-bar-center"></div><div class="trim-bar-fill" id="tb-stft1"></div></div>
    <div class="help-tip"></div>
  </div>
  <div class="card" style="position:relative">
    <div class="label">STFT B2 <span class="help-icon" tabindex="0" onclick="toggleHelp(this)" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();toggleHelp(this)}" data-help="Short-Term Fuel Trim Bank 2: Same as B1 but for the other cylinder bank. Both banks high = shared vacuum leak. One bank high = bank-specific issue.">?</span></div>
    <div class="value" id="v-stft2">--</div>
    <div class="unit">%</div>
    <div class="trim-bar-wrap"><div class="trim-bar-center"></div><div class="trim-bar-fill" id="tb-stft2"></div></div>
    <div class="help-tip"></div>
  </div>
  <div class="card" style="position:relative">
    <div class="label">LTFT B1 <span class="help-icon" tabindex="0" onclick="toggleHelp(this)" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();toggleHelp(this)}" data-help="Long-Term Fuel Trim Bank 1: The ECU's learned fuel adjustment. High positive = sustained lean (vacuum leak, dirty MAF). Persists across restarts. Normal: &plusmn;5%.">?</span></div>
    <div class="value" id="v-ltft1">--</div>
    <div class="unit">%</div>
    <div class="trim-bar-wrap"><div class="trim-bar-center"></div><div class="trim-bar-fill" id="tb-ltft1"></div></div>
    <div class="help-tip"></div>
  </div>
  <div class="card" style="position:relative">
    <div class="label">LTFT B2 <span class="help-icon" tabindex="0" onclick="toggleHelp(this)" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();toggleHelp(this)}" data-help="Long-Term Fuel Trim Bank 2: Same as LTFT B1 but for the other cylinder bank. Compare both banks to isolate bank-specific issues.">?</span></div>
    <div class="value" id="v-ltft2">--</div>
    <div class="unit">%</div>
    <div class="trim-bar-wrap"><div class="trim-bar-center"></div><div class="trim-bar-fill" id="tb-ltft2"></div></div>
    <div class="help-tip"></div>
  </div>
</div>

<div class="section">PERFORMANCE</div>
<div class="grid">
  <div class="card" style="position:relative">
    <div class="label">LOAD <span class="help-icon" tabindex="0" onclick="toggleHelp(this)" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();toggleHelp(this)}" data-help="Engine Load: How hard the engine is working (0-100%). Idle ~15-25%. Cruising ~30-50%. Full throttle ~80-100%.">?</span></div>
    <div class="value" id="v-load">--</div>
    <div class="unit">%</div>
    <div class="bar"><div class="bar-fill" id="b-load" style="width:0;background:var(--accent)"></div></div>
    <div class="help-tip"></div>
  </div>
  <div class="card">
    <div class="label">THROTTLE</div>
    <div class="value" id="v-throttle">--</div>
    <div class="unit">%</div>
    <div class="bar"><div class="bar-fill" id="b-throttle" style="width:0;background:var(--accent)"></div></div>
  </div>
  <div class="card" style="position:relative">
    <div class="label">IAT <span class="help-icon" tabindex="0" onclick="toggleHelp(this)" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();toggleHelp(this)}" data-help="Intake Air Temperature: Air temp entering the engine. Normal: 20-45&deg;C. Above 50&deg;C = heat soak risk, reduced power. Above 65&deg;C = critical.">?</span></div>
    <div class="value" id="v-iat">--</div>
    <div class="unit">&deg;C</div>
    <div class="help-tip"></div>
  </div>
  <div class="card" style="position:relative">
    <div class="label">MAF <span class="help-icon" tabindex="0" onclick="toggleHelp(this)" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();toggleHelp(this)}" data-help="Mass Air Flow: Air entering the engine in grams/second. Idle: 2.5-6.0 g/s. Below 2.5 at warm idle = dirty/failing MAF sensor. Clean with electronics cleaner.">?</span></div>
    <div class="value" id="v-maf">--</div>
    <div class="unit">g/s</div>
    <div class="help-tip"></div>
  </div>
</div>

<div class="section">DIAGNOSTICS</div>
<div class="alert-msg" id="alert-msg" onclick="toggleAlertExpand()" style="cursor:pointer" title="Tap for details">Waiting for data...</div>
<div class="alert-expand" id="alert-expand">
  <div class="advice-text" id="alert-advice">Loading guidance...</div>
  <div class="alert-actions">
    <button onclick="askAboutAlert()">&#x1f527; Ask Mechanic</button>
    <button onclick="dismissAlert()">&#x23f8; Dismiss 10min</button>
    <button onclick="toggleAlertHistory()">&#x1f4dc; History</button>
  </div>
</div>
<div class="alert-history hidden" id="alert-history"></div>
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
    <button id="cancel-btn" onclick="cancelQuery()" class="hidden"
      style="padding:8px 12px;background:#2e1a1a;border:1px solid #f4433655;border-radius:6px;
             color:var(--red);font-size:12px;cursor:pointer;white-space:nowrap">CANCEL</button>
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
<a href="/settings" class="audio-btn" style="bottom:16px;left:64px;font-size:14px;text-decoration:none" title="Settings" aria-label="Settings">&#x2699;</a>
<button class="audio-btn" id="audio-btn" title="Enable voice alerts on this device">&#x1f50a;</button>

<div class="disconnected hidden" id="dc-overlay">
  <h2>DISCONNECTED</h2>
  <p>Connecting to vehicle&hellip;<br>Check that the MZ1312_DRIFTER hotspot is active and your phone is connected to it.</p>
  <div class="retry-info" id="dc-retry">Retrying in 2s&hellip;</div>
</div>

<div class="toast-container" id="toast-container"></div>

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
let wsRetryDelay = 2000;
const WS_RETRY_MAX = 16000;

// ── Toast Notification System ──
function showToast(message, type='info', duration=4000){
  const container = document.getElementById('toast-container');
  if(!container) return;
  const t = document.createElement('div');
  t.className = 'toast ' + type;
  t.textContent = message;
  container.appendChild(t);
  setTimeout(()=>{
    t.style.animation='toast-out .3s ease forwards';
    setTimeout(()=>t.remove(), 300);
  }, duration);
}

// ── Help Tooltip Toggle ──
function toggleHelp(icon){
  const card = icon.closest('.card');
  if(!card) return;
  const tip = card.querySelector('.help-tip');
  if(!tip) return;
  const isOpen = tip.classList.contains('show');
  // Close all other tips
  document.querySelectorAll('.help-tip.show').forEach(t=>t.classList.remove('show'));
  if(!isOpen){
    tip.textContent = icon.dataset.help || '';
    tip.classList.add('show');
  }
}
// Close tips on outside click
document.addEventListener('click', (e)=>{
  if(!e.target.classList.contains('help-icon')){
    document.querySelectorAll('.help-tip.show').forEach(t=>t.classList.remove('show'));
  }
});

// ── Alert Interaction ──
let alertHistory = [];
let currentAlertMsg = '';
let dismissedAlerts = {};

function toggleAlertExpand(){
  const el = document.getElementById('alert-expand');
  if(!el) return;
  const isOpen = el.classList.contains('open');
  if(isOpen){ el.classList.remove('open'); return; }
  el.classList.add('open');
  // Fetch advice for current alert
  if(currentAlertMsg && currentAlertMsg !== 'Systems nominal'){
    fetch('/api/mechanic/advice?alert='+encodeURIComponent(currentAlertMsg))
      .then(r=>r.json()).then(d=>{
        const advEl = document.getElementById('alert-advice');
        if(d.advice && d.advice.length){
          advEl.innerHTML = d.advice.map(a=>'<div style="margin-bottom:4px">&bull; '+esc(typeof a==='string'?a:a.text||JSON.stringify(a))+'</div>').join('');
        } else {
          advEl.textContent = 'No specific guidance available for this alert.';
        }
      }).catch(()=>{});
  }
}
function askAboutAlert(){
  if(!currentAlertMsg) return;
  document.getElementById('ask-input').value = 'Explain this alert and what I should do: ' + currentAlertMsg;
  document.getElementById('alert-expand').classList.remove('open');
  askMechanic();
  // Scroll to Ask Mechanic section
  document.getElementById('ask-input').scrollIntoView({behavior:'smooth',block:'center'});
}
function dismissAlert(){
  if(currentAlertMsg){
    dismissedAlerts[currentAlertMsg] = Date.now() + 600000; // 10 min
    // Persist to sessionStorage so it survives page refresh
    try{sessionStorage.setItem('drifter_dismissed',JSON.stringify(dismissedAlerts))}catch(e){}
    showToast('Alert dismissed for 10 minutes', 'info');
    document.getElementById('alert-expand').classList.remove('open');
  }
}
// Restore dismissed alerts from sessionStorage
try{
  const saved=sessionStorage.getItem('drifter_dismissed');
  if(saved){
    const parsed=JSON.parse(saved);
    const now=Date.now();
    for(const[k,v] of Object.entries(parsed)){
      if(v>now) dismissedAlerts[k]=v; // Only restore non-expired
    }
  }
}catch(e){}
function toggleAlertHistory(){
  const el = document.getElementById('alert-history');
  el.classList.toggle('hidden');
  if(!el.classList.contains('hidden')){
    el.innerHTML = alertHistory.length ?
      alertHistory.slice(-50).reverse().map(a=>{
        const t = new Date(a.ts).toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit',second:'2-digit'});
        const colors = {0:'var(--ok)',1:'var(--info)',2:'var(--amber)',3:'var(--red)'};
        return `<div class="alert-history-item"><span style="color:${colors[a.level]||'var(--dim)'}">${esc(a.message)}</span><span class="ah-time">${t}</span></div>`;
      }).join('') :
      '<div style="color:var(--dim);padding:8px;text-align:center">No alert history yet</div>';
  }
}

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
    const message = data.message || 'Systems nominal';
    const colors = {0:'var(--ok)',1:'var(--info)',2:'var(--amber)',3:'var(--red)'};
    // Check dismissed
    const now = Date.now();
    if(dismissedAlerts[message] && dismissedAlerts[message] > now){
      return; // Still dismissed
    }
    delete dismissedAlerts[message]; // Expired
    currentAlertMsg = message;
    el.style.color = colors[lvl] || 'var(--text)';
    el.textContent = message;
    // Track alert history
    if(lvl > 0 && message !== 'Systems nominal'){
      alertHistory.push({level:lvl, message:message, ts:now});
      if(alertHistory.length > 50) alertHistory.shift();
    }
    // Mirror active alerts on the banner too
    const banner = document.getElementById('alert-banner');
    if(lvl > 0 && message){
      banner.dataset.msg = message;
      banner.textContent = message;
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

// ── WebSocket Connection (exponential backoff) ──
function connect(){
  ws = new WebSocket(WS_URL);
  ws.onopen = ()=>{
    wsRetryDelay = 2000; // Reset backoff on success
    document.getElementById('dc-overlay').classList.add('hidden');
    document.getElementById('dot-conn').className='status-dot dot-ok';
    document.getElementById('conn-text').textContent='LIVE';
    showToast('Connected to DRIFTER', 'success', 2000);
  };
  ws.onmessage = (e)=>{
    try{handleMessage(JSON.parse(e.data))}catch(err){}
  };
  ws.onclose = ()=>{
    document.getElementById('dc-overlay').classList.remove('hidden');
    document.getElementById('dot-conn').className='status-dot dot-off';
    document.getElementById('conn-text').textContent='OFFLINE';
    const retryEl = document.getElementById('dc-retry');
    if(retryEl) retryEl.textContent = 'Retrying in '+(wsRetryDelay/1000)+'s\u2026';
    setTimeout(connect, wsRetryDelay);
    wsRetryDelay = Math.min(wsRetryDelay * 2, WS_RETRY_MAX); // Exponential backoff
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

// ── Ask Mechanic (LLM with Streaming) ──
let queryBusy=false;
let queryAbort=null;
let queryTimer=null;

function _submitQuery(q){
  if(queryBusy||!q) return;
  queryBusy=true;
  const out=document.getElementById('ask-output');
  const meta=document.getElementById('ask-meta');
  const btn=document.getElementById('ask-btn');
  const cancelBtn=document.getElementById('cancel-btn');
  out.style.color='var(--dim)';
  out.innerHTML='<span style="animation:pulse 1.5s infinite">\u25cf\u25cf\u25cf Thinking\u2026</span>';
  if(meta) meta.textContent='';
  btn.disabled=true;
  btn.classList.add('hidden');
  cancelBtn.classList.remove('hidden');

  // Elapsed time counter
  const startTime=Date.now();
  queryTimer=setInterval(()=>{
    const elapsed=((Date.now()-startTime)/1000).toFixed(0);
    if(meta) meta.textContent=elapsed+'s elapsed';
  }, 1000);

  // Try streaming first, fall back to non-streaming
  queryAbort = new AbortController();
  fetch('/api/query/stream',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({query:q}),
    signal:queryAbort.signal
  }).then(resp=>{
    if(!resp.ok) throw new Error('Stream unavailable');
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let fullText='';
    let model='';
    let tokens=0;
    out.textContent='';
    out.style.color='var(--text)';

    function readChunk(){
      return reader.read().then(({done, value})=>{
        if(done) return;
        const text = decoder.decode(value, {stream:true});
        const lines = text.split('\n');
        for(const line of lines){
          if(!line.startsWith('data: ')) continue;
          try{
            const d=JSON.parse(line.slice(6));
            if(d.error){
              out.style.color='var(--red)';
              out.textContent='Error: '+d.error;
              return;
            }
            if(d.token){
              fullText+=d.token;
              // Escape and render
              const escText=fullText.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
              out.innerHTML=escText.replace(/\n/g,'<br>');
            }
            if(d.done){
              model=d.model||'';
              tokens=d.tokens||0;
            }
          }catch(e){}
        }
        return readChunk();
      });
    }
    return readChunk().then(()=>{
      if(meta){
        const m=(model||'').split('/').pop();
        const elapsed=((Date.now()-startTime)/1000).toFixed(1);
        meta.textContent=(m?m+' \u00b7 ':'')+(tokens?tokens+' tok \u00b7 ':'')+elapsed+'s';
      }
    });
  }).catch(err=>{
    if(err.name==='AbortError'){
      out.style.color='var(--amber)';
      out.textContent='Query cancelled.';
      return;
    }
    // Fallback to non-streaming
    return fetch('/api/query',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query:q}),signal:queryAbort.signal})
      .then(r=>r.json())
      .then(d=>{
        if(d.error){out.style.color='var(--red)';out.textContent='Error: '+d.error;}
        else{
          out.style.color='var(--text)';
          const text=d.response||'';
          const escText=text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
          out.innerHTML=escText.replace(/\n/g,'<br>');
          if(meta){
            const m=(d.model||'').split('/').pop();
            const elapsed=((Date.now()-startTime)/1000).toFixed(1);
            meta.textContent=(m?m+' \u00b7 ':'')+(d.tokens?d.tokens+' tok \u00b7 ':'')+elapsed+'s';
          }
        }
      });
  }).catch(err=>{
    if(err.name!=='AbortError'){
      out.style.color='var(--red)';out.textContent='Request failed \u2014 is Ollama running?';
    }
  }).finally(()=>{
    queryBusy=false;queryAbort=null;
    btn.disabled=false;btn.classList.remove('hidden');btn.textContent='ASK';
    cancelBtn.classList.add('hidden');
    if(queryTimer){clearInterval(queryTimer);queryTimer=null;}
  });
}

function cancelQuery(){
  if(queryAbort) queryAbort.abort();
  showToast('Query cancelled', 'info', 2000);
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


SETTINGS_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<meta name="apple-mobile-web-app-capable" content="yes">
<title>DRIFTER SETTINGS</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{--bg:#0a0a0a;--card:#141414;--border:#222;--text:#e0e0e0;--dim:#666;
--accent:#00bcd4;--ok:#4caf50;--info:#2196f3;--amber:#ff9800;--red:#f44336}
body{background:var(--bg);color:var(--text);font-family:'Courier New',monospace;
overflow-x:hidden;-webkit-font-smoothing:antialiased;padding:16px;padding-bottom:80px}
h1{font-size:16px;letter-spacing:2px;color:var(--accent);margin-bottom:16px}
.section{background:var(--card);border:1px solid var(--border);border-radius:6px;
padding:14px;margin-bottom:14px}
.section h2{font-size:13px;color:var(--accent);letter-spacing:1px;margin-bottom:12px;
border-bottom:1px solid var(--border);padding-bottom:6px}
.field{display:flex;flex-wrap:wrap;align-items:center;margin-bottom:10px;gap:8px}
.field:last-child{margin-bottom:0}
.field label{flex:1 1 180px;font-size:12px;color:var(--text)}
.field .hint{width:100%;font-size:10px;color:var(--dim);margin-top:-4px}
.field input[type="number"],.field input[type="text"],.field select{
background:var(--bg);border:1px solid var(--border);color:var(--text);
font-family:'Courier New',monospace;font-size:12px;padding:6px 8px;
border-radius:4px;width:140px}
.field input[type="number"]:focus,.field input[type="text"]:focus,.field select:focus{
outline:none;border-color:var(--accent)}
.field input[type="checkbox"]{accent-color:var(--accent);width:16px;height:16px}
.save-btn{display:block;width:100%;padding:12px;margin-top:16px;
background:var(--accent);color:#000;font-family:'Courier New',monospace;
font-size:14px;font-weight:bold;letter-spacing:2px;border:none;border-radius:6px;
cursor:pointer}
.save-btn:active{opacity:0.8}
.save-btn:disabled{opacity:0.4;cursor:not-allowed}
.toast{position:fixed;top:16px;left:50%;transform:translateX(-50%);
padding:10px 24px;border-radius:6px;font-size:12px;font-family:'Courier New',monospace;
z-index:9999;opacity:0;transition:opacity 0.3s;pointer-events:none}
.toast.show{opacity:1}
.toast.ok{background:var(--ok);color:#000}
.toast.err{background:var(--red);color:#fff}
.home-btn{position:fixed;bottom:16px;left:16px;background:var(--card);
border:1px solid var(--border);color:var(--accent);width:40px;height:40px;
border-radius:50%;display:flex;align-items:center;justify-content:center;
text-decoration:none;font-size:18px;z-index:100}
</style>
</head>
<body>
<h1>&#x2699; DRIFTER SETTINGS</h1>

<div class="section">
<h2>ALERT THRESHOLDS</h2>
<div class="field">
  <label for="coolant_amber">Coolant amber (&deg;C)</label>
  <input type="number" id="coolant_amber" step="1">
  <div class="hint">Coolant temp warning level (default 104&deg;C)</div>
</div>
<div class="field">
  <label for="coolant_red">Coolant red (&deg;C)</label>
  <input type="number" id="coolant_red" step="1">
  <div class="hint">Coolant temp critical level (default 108&deg;C)</div>
</div>
<div class="field">
  <label for="voltage_undercharge">Voltage undercharge (V)</label>
  <input type="number" id="voltage_undercharge" step="0.1">
  <div class="hint">Low alternator voltage warning (default 13.2V)</div>
</div>
<div class="field">
  <label for="voltage_critical">Voltage critical (V)</label>
  <input type="number" id="voltage_critical" step="0.1">
  <div class="hint">Critical low voltage threshold (default 12.0V)</div>
</div>
<div class="field">
  <label for="stft_lean_idle">STFT lean idle (%)</label>
  <input type="number" id="stft_lean_idle" step="0.5">
  <div class="hint">Short-term fuel trim lean threshold at idle (default 12.0%)</div>
</div>
<div class="field">
  <label for="ltft_lean_warn">LTFT lean warn (%)</label>
  <input type="number" id="ltft_lean_warn" step="0.5">
  <div class="hint">Long-term fuel trim lean warning (default 15.0%)</div>
</div>
<div class="field">
  <label for="ltft_lean_crit">LTFT lean critical (%)</label>
  <input type="number" id="ltft_lean_crit" step="0.5">
  <div class="hint">Long-term fuel trim lean critical (default 25.0%)</div>
</div>
</div>

<div class="section">
<h2>VOICE SETTINGS</h2>
<div class="field">
  <label for="voice_cooldown">Voice cooldown (seconds)</label>
  <input type="number" id="voice_cooldown" step="1" min="0">
  <div class="hint">Minimum seconds between voice alerts (default 15)</div>
</div>
<div class="field">
  <label for="tts_engine">TTS engine</label>
  <select id="tts_engine">
    <option value="piper">piper</option>
    <option value="espeak">espeak</option>
  </select>
  <div class="hint">Text-to-speech engine for voice alerts</div>
</div>
<div class="field">
  <label for="voice_min_level">Minimum alert level</label>
  <select id="voice_min_level">
    <option value="0">0 &mdash; All alerts</option>
    <option value="1">1 &mdash; Info and above</option>
    <option value="2">2 &mdash; Amber and above</option>
    <option value="3">3 &mdash; Red only</option>
  </select>
  <div class="hint">Only voice alerts at or above this severity level</div>
</div>
</div>

<div class="section">
<h2>DISPLAY</h2>
<div class="field">
  <label for="temp_unit">Temperature unit</label>
  <select id="temp_unit">
    <option value="C">Celsius (&deg;C)</option>
    <option value="F">Fahrenheit (&deg;F)</option>
  </select>
  <div class="hint">Temperature display unit for dashboard</div>
</div>
<div class="field">
  <label for="pressure_unit">Pressure unit</label>
  <select id="pressure_unit">
    <option value="PSI">PSI</option>
    <option value="kPa">kPa</option>
    <option value="bar">bar</option>
  </select>
  <div class="hint">Tire pressure display unit</div>
</div>
</div>

<div class="section">
<h2>LLM</h2>
<div class="field">
  <label for="llm_model">Model name</label>
  <input type="text" id="llm_model" placeholder="(use default)">
  <div class="hint">Ollama model for mechanic chat (empty = config default)</div>
</div>
<div class="field">
  <label for="llm_max_tokens">Max tokens</label>
  <input type="number" id="llm_max_tokens" step="50" min="50">
  <div class="hint">Maximum response token length (default 500)</div>
</div>
<div class="field">
  <label for="llm_tools_enabled">Tool calling enabled</label>
  <input type="checkbox" id="llm_tools_enabled">
  <div class="hint">Allow LLM to execute diagnostic tool calls</div>
</div>
</div>

<div class="section">
<h2>DATA</h2>
<div class="field">
  <label for="data_retention_days">Retention days</label>
  <input type="number" id="data_retention_days" step="1" min="1">
  <div class="hint">Days to keep logged data before purging (default 90)</div>
</div>
</div>

<button class="save-btn" id="save-btn">SAVE</button>

<div class="toast" id="toast"></div>

<a href="/" class="home-btn" title="Back to dashboard">&#x25C0;</a>

<script>
const FIELDS = [
  {id:'coolant_amber', type:'number'},
  {id:'coolant_red', type:'number'},
  {id:'voltage_undercharge', type:'number'},
  {id:'voltage_critical', type:'number'},
  {id:'stft_lean_idle', type:'number'},
  {id:'ltft_lean_warn', type:'number'},
  {id:'ltft_lean_crit', type:'number'},
  {id:'voice_cooldown', type:'number'},
  {id:'tts_engine', type:'select'},
  {id:'voice_min_level', type:'select'},
  {id:'temp_unit', type:'select'},
  {id:'pressure_unit', type:'select'},
  {id:'llm_model', type:'text'},
  {id:'llm_max_tokens', type:'number'},
  {id:'llm_tools_enabled', type:'checkbox'},
  {id:'data_retention_days', type:'number'},
];

function showToast(msg, ok) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast show ' + (ok ? 'ok' : 'err');
  setTimeout(() => { t.className = 'toast'; }, 3000);
}

function populate(settings) {
  FIELDS.forEach(f => {
    const el = document.getElementById(f.id);
    if (!el) return;
    const val = settings[f.id];
    if (val === undefined || val === null) return;
    if (f.type === 'checkbox') el.checked = !!val;
    else if (f.type === 'select') el.value = String(val);
    else el.value = val;
  });
}

function gather() {
  const s = {};
  FIELDS.forEach(f => {
    const el = document.getElementById(f.id);
    if (!el) return;
    if (f.type === 'checkbox') s[f.id] = el.checked;
    else if (f.type === 'number') s[f.id] = parseFloat(el.value);
    else s[f.id] = el.value;
  });
  return s;
}

fetch('/api/settings')
  .then(r => r.json())
  .then(populate)
  .catch(() => showToast('Failed to load settings', false));

document.getElementById('save-btn').addEventListener('click', function() {
  const btn = this;
  btn.disabled = true;
  btn.textContent = 'SAVING...';
  fetch('/api/settings', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(gather()),
  })
  .then(r => r.json())
  .then(d => {
    if (d.ok) showToast('Settings saved', true);
    else showToast(d.error || 'Save failed', false);
  })
  .catch(() => showToast('Network error', false))
  .finally(() => { btn.disabled = false; btn.textContent = 'SAVE'; });
});
</script>
</body>
</html>"""



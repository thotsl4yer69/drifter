# HUD: STATUS Page Redesign + Flipper Zero Page

**Date:** 2026-05-06  
**Scope:** `src/screen_dash.html` only — no backend changes required.  
**Approved mockups:** Status service grid + Flipper layout B (sidebar + dense log).

---

## 1. STATUS Page Redesign

### Problem
The STATUS page shows CPU temp, RAM, disk, and uptime but has no visibility into service health. All 17 `drifter-*` services run silently — a failed service is invisible until diagnostics break. The watchdog already publishes `services: {name: state}` in every `drifter/system/watchdog` message; the HUD ignores it.

### Layout (480×320)

```
┌─────────────────────────────────────────────────────┐
│  STATUS                                             │
│  [ALERT BOX — current diagnostic message]           │
│  DTC: NO CODES                                      │
├──────────────┬──────────────┬──────────────────────┤
│ ● canbridge  │ ● alerts     │ ● logger             │
│ ● anomaly    │ ● analyst    │ ● voice              │
│ ● vivi       │ ● hotspot    │ ● homesync           │
│ ● watchdog   │ ● realdash   │ ● rf                 │
│ ● wardrive   │ ● dashboard  │ ● fbmirror           │
│ ● voicein    │ ● flipper    │                      │
├─────────────────────────────────────────────────────┤
│  CPU 47°C    MEM 62%    DISK 31%    UP 3h20m       │
└─────────────────────────────────────────────────────┘
```

Dot colors: green (`active`) · red (`failed`) · amber (any other state / no data yet).

### HTML changes

Replace the existing `<div class="sys-grid">` block (4 cells) with two new elements:

1. **Service chip grid** — `<div class="svc-grid" id="svc-grid">` containing 17 pre-rendered chips. Each chip:
   ```html
   <div class="svc-chip" id="svc-canbridge">
     <span class="svc-dot"></span><span class="svc-name">canbridge</span>
   </div>
   ```
   Chip IDs use the short name (strip `drifter-` prefix) so JS can address them as `svc-${shortName}`.

2. **Sys footer strip** — `<div class="sys-footer">` replacing the sys-grid, with four inline `<span>` pairs (label + value). Stays at the bottom of `.status-layout`.

### CSS changes

```css
.svc-grid {
  display: grid; grid-template-columns: repeat(3, 1fr);
  gap: 3px; flex: 1; overflow: hidden;
}
.svc-chip {
  display: flex; align-items: center; gap: 4px;
  padding: 3px 5px; border-radius: 3px;
  background: #111; border: 1px solid #1e1e1e;
  font-size: 8.5px; color: #888; letter-spacing: 0.3px;
}
.svc-dot {
  width: 5px; height: 5px; border-radius: 50%;
  background: #555; flex-shrink: 0;
}
.svc-chip.ok   { color: #aaa; }
.svc-chip.ok   .svc-dot { background: #4caf50; box-shadow: 0 0 4px rgba(76,175,80,0.5); }
.svc-chip.fail { color: #ff8888; border-color: #3a1111; }
.svc-chip.fail .svc-dot { background: #ff4444; box-shadow: 0 0 4px rgba(255,68,68,0.5); }
.svc-chip.warn { color: #ffdd88; border-color: #2a2000; }
.svc-chip.warn .svc-dot {
  background: #ffbb00; box-shadow: 0 0 4px rgba(255,187,0,0.4);
  animation: svc-pulse 1.2s infinite;
}
@keyframes svc-pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }
.sys-footer {
  display: flex; justify-content: space-around;
  padding: 4px; border-top: 1px solid #1a1a1a; flex-shrink: 0;
  font-size: 8.5px;
}
.sys-footer .sf-label { color: #444; letter-spacing: 1px; margin-right: 3px; }
.sys-footer .sf-val   { color: #777; }
```

### JS changes

**State object** — add to `const S`:
```js
svcStates: {}   // { 'drifter-canbridge': 'active', ... }
```

**MQTT router** — in the existing `/system/watchdog` handler, add:
```js
if (data.services && typeof data.services === 'object') {
  S.svcStates = data.services;
}
```

**`updateStatusUI()`** — add after the DTC block:
```js
const shortNames = [
  'canbridge','alerts','logger','anomaly','analyst','voice',
  'vivi','hotspot','homesync','watchdog','realdash','rf',
  'wardrive','dashboard','fbmirror','voicein','flipper'
];
shortNames.forEach(name => {
  const el = document.getElementById('svc-' + name);
  if (!el) return;
  const state = S.svcStates['drifter-' + name];
  el.className = 'svc-chip' +
    (state === 'active' ? ' ok' : state === 'failed' ? ' fail' : state ? ' warn' : '');
});
```

**Sys footer** — update element IDs to match the new `sf-*` structure. The four values (cpuTemp, memPct, diskPct, uptime) already exist in `S`; `updateStatusUI()` just needs to target the new IDs.

---

## 2. Flipper Zero HUD Page (Page 6)

### Problem
`flipper_bridge.py` and `drifter-flipper.service` are fully wired — the bridge auto-detects the Flipper over USB serial, starts a Sub-GHz monitor at 433.92 MHz, and publishes captures to MQTT. None of this is visible in the HUD.

### Layout (480×320) — Layout B: sidebar + dense log

```
┌──────────────────────────────────────────────────┐
│ MZ1312                              ●  OK        │ ← topbar
├──────────────┬───────────────────────────────────┤
│ FLIPPER      │ 433.92 MHz LOG          ● LIVE   │
│ ● CONNECTED  │ 44:01  433.92  Princeton · 0xA3  │
│              │ 43:58  433.92  CAME 12bit · 0x9C │
│ FW  0.99.1   │ 43:45  433.92  RAW · 48 samples  │
│ SUB ON       │ 43:31  433.92  RAW · 61 samples  │
│ CAP 47       │ 43:12  433.92  Princeton · 0x7D  │
│ 42m          │ 42:55  433.92  RAW · 29 samples  │
│              │ 42:44  433.92  Princeton · 0xA3  │
├──────────────┴───────────────────────────────────┤
│ ● ● ● ● ● ● ●                        FLIPPER   │ ← bottombar (7 dots)
└──────────────────────────────────────────────────┘
```

Sidebar (140px wide): connection state badge, firmware, sub-GHz on/off, capture count, session duration.  
Log panel (flex-grow): scrolling log of `drifter/flipper/subghz` captures, newest at top, max 30 rows kept in memory.

### MQTT sources

| Topic | Used for |
|---|---|
| `drifter/flipper/status` | Connection state, firmware, sub-GHz active flag |
| `drifter/flipper/subghz` | Each capture: `{protocol, frequency, rssi, data, ts}` |

The status payload from `publish_status()` includes `state` (`connected`/`searching`/`disconnected`/`offline`) and, when connected, hardware info from `_parse_hw_info()` (firmware version field: `firmware_version` or `fw_version`).

### HTML changes

Add page 6 after the existing SCAN page div, before the bottom bar:

```html
<!-- PAGE 6: FLIPPER ZERO -->
<div class="page" id="page-6">
  <div class="flipper-layout">
    <div class="flip-sidebar">
      <div class="flip-title">FLIPPER</div>
      <div class="flip-state" id="fv-state">SEARCHING</div>
      <div class="flip-stats">
        <div class="flip-stat"><span class="flip-slabel">FW</span><span class="flip-sval" id="fv-fw">--</span></div>
        <div class="flip-stat"><span class="flip-slabel">SUB-GHz</span><span class="flip-sval" id="fv-sub">--</span></div>
        <div class="flip-stat"><span class="flip-slabel">CAP</span><span class="flip-sval" id="fv-cap">0</span></div>
        <div class="flip-stat"><span class="flip-slabel">SESSION</span><span class="flip-sval" id="fv-dur">--</span></div>
      </div>
    </div>
    <div class="flip-log-panel">
      <div class="flip-log-header">
        <span>433.92 MHz LOG</span>
        <span class="flip-live" id="fv-live"></span>
      </div>
      <div class="flip-log" id="fv-log"><div class="flip-empty">WAITING FOR FLIPPER</div></div>
    </div>
  </div>
</div>
```

Add 7th dot to the bottom bar: `<div class="page-dot" id="dot-6"></div>`.

### CSS additions

```css
.flipper-layout {
  display: flex; height: 100%; overflow: hidden;
}
.flip-sidebar {
  width: 140px; border-right: 1px solid #1a1a1a;
  display: flex; flex-direction: column; padding: 8px; gap: 6px; flex-shrink: 0;
}
.flip-title    { font-size: 10px; color: #ff8c00; letter-spacing: 2px; }
.flip-state    { font-size: 9px; padding: 2px 0; color: #555; }
.flip-state.connected    { color: #4caf50; }
.flip-state.searching    { color: #ffbb00; animation: svc-pulse 1.2s infinite; }
.flip-state.disconnected { color: #ff4444; }
.flip-stats    { display: flex; flex-direction: column; gap: 4px; margin-top: 4px; }
.flip-stat     { font-size: 8px; display: flex; gap: 4px; }
.flip-slabel   { color: #555; }
.flip-sval     { color: #999; }
.flip-log-panel {
  flex: 1; display: flex; flex-direction: column; padding: 6px 8px; gap: 4px; overflow: hidden;
}
.flip-log-header {
  font-size: 8px; color: #555; letter-spacing: 1px;
  display: flex; justify-content: space-between; flex-shrink: 0;
}
.flip-live { color: #ff8c00; }
.flip-log  { flex: 1; overflow: hidden; display: flex; flex-direction: column; gap: 3px; }
.flip-log-row {
  display: grid; grid-template-columns: 40px 50px 1fr 34px;
  gap: 4px; font-size: 8.5px; align-items: center;
}
.flip-log-row.fresh { background: #1a1200; border-radius: 2px; }
.flip-ts   { color: #444; }
.flip-freq { color: #ff8c00; }
.flip-data { color: #888; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.flip-rssi { color: #555; text-align: right; }
.flip-empty { font-size: 9px; color: #444; padding: 8px 0; }
```

### JS changes

**State object** — add to `const S`:
```js
flipperState: '',      // 'connected' | 'searching' | 'disconnected' | 'offline' | ''
flipperFw: '',
flipperSubOn: false,
flipperCapCount: 0,
flipperSessionStart: 0,
flipperLog: []         // max 30 entries, newest first
```

**Constants**:
```js
const PAGE_NAMES = ['DRIVE','TYRES','ENGINE','STATUS','RF','SCAN','FLIPPER'];
const PAGES = 7;
```

**MQTT router** — add two new handlers:
```js
else if (topic.endsWith('/flipper/status')) {
  S.flipperState = data.state || '';
  S.flipperFw = data.firmware_version || data.fw_version || data.firmware || '';
  S.flipperSubOn = !!(data.subghz_monitoring || data.subghz_active);
  if (data.state === 'connected' && !S.flipperSessionStart) {
    S.flipperSessionStart = Date.now();
  }
  if (data.state !== 'connected') S.flipperSessionStart = 0;
}
else if (topic.endsWith('/flipper/subghz')) {
  S.flipperCapCount++;
  const entry = {
    ts: data.ts || (Date.now() / 1000),
    freq: data.frequency_mhz || '433.92',
    proto: data.protocol || 'RAW',
    detail: data.data || (data.samples ? data.samples + ' samples' : ''),
    rssi: data.rssi != null ? Math.round(data.rssi) : null
  };
  S.flipperLog.unshift(entry);
  if (S.flipperLog.length > 30) S.flipperLog.pop();
}
```

**`updateFlipperUI()`** — new function:
```js
function updateFlipperUI() {
  const stateEl = document.getElementById('fv-state');
  const state = S.flipperState;
  stateEl.textContent = state ? ('● ' + state.toUpperCase()) : '● OFFLINE';
  stateEl.className = 'flip-state ' + (state || 'disconnected');

  setEl('fv-fw', S.flipperFw || '--');
  const subEl = document.getElementById('fv-sub');
  if (subEl) { subEl.textContent = S.flipperSubOn ? 'ON' : '--'; subEl.style.color = S.flipperSubOn ? 'var(--green)' : '#555'; }
  setEl('fv-cap', String(S.flipperCapCount));

  // Session duration
  if (S.flipperSessionStart) {
    const secs = Math.floor((Date.now() - S.flipperSessionStart) / 1000);
    const h = Math.floor(secs / 3600), m = Math.floor((secs % 3600) / 60);
    setEl('fv-dur', h > 0 ? h + 'h' + m + 'm' : m + 'm');
  } else {
    setEl('fv-dur', '--');
  }

  // Live indicator
  const liveEl = document.getElementById('fv-live');
  if (liveEl) liveEl.textContent = S.flipperSubOn ? '● LIVE' : '';

  // Log rows
  const log = document.getElementById('fv-log');
  log.textContent = '';
  if (S.flipperLog.length === 0) {
    const e = document.createElement('div');
    e.className = 'flip-empty';
    e.textContent = state === 'connected' ? 'NO CAPTURES YET' : 'WAITING FOR FLIPPER';
    log.appendChild(e);
    return;
  }
  S.flipperLog.forEach((entry, i) => {
    const row = document.createElement('div');
    row.className = 'flip-log-row' + (i < 2 ? ' fresh' : '');
    const ts = new Date(entry.ts * 1000);
    const tsStr = ts.getMinutes().toString().padStart(2,'0') + ':' + ts.getSeconds().toString().padStart(2,'0');
    const detail = [entry.proto, entry.detail].filter(Boolean).join(' · ').slice(0, 28);
    const rssi = entry.rssi != null ? entry.rssi + 'dB' : '--';
    row.innerHTML =
      `<span class="flip-ts">${tsStr}</span>` +
      `<span class="flip-freq">${entry.freq}</span>` +
      `<span class="flip-data"></span>` +
      `<span class="flip-rssi">${rssi}</span>`;
    row.querySelector('.flip-data').textContent = detail;
    log.appendChild(row);
  });
}
```

`flip-data` content is set via `.textContent` (not `innerHTML`) to prevent XSS from SSID-like protocol strings captured over the air.

**Update dispatch** — add to the `currentPage` branch in the render loop:
```js
else if (currentPage === 6) updateFlipperUI();
```

---

## Page Count Impact

The bottom bar gains one dot (`id="dot-6"`). The `goToPage()` function, swipe handlers, and keyboard shortcuts (key `7` → page 6) already generalise via `PAGES` — updating that constant is sufficient.

---

## What Is Not Changing

- No new MQTT topics, no backend changes.
- Pages 0–5 are untouched except STATUS (page 3).
- The `drifter-flipper.service` systemd unit and `flipper_bridge.py` are unchanged.
- No new Python dependencies.

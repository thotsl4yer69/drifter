# HUD STATUS + Flipper Zero Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the STATUS HUD page to show a 17-service health chip grid, and add a new Flipper Zero page (page 6) with sidebar + live 433.92 MHz capture log.

**Architecture:** All changes are in `src/screen_dash.html` — a single-file HUD served from the Pi. The file contains HTML, CSS, and JS. Changes follow the existing pattern: HTML structure in the `<div id="hud-root">` section, CSS in the `<style>` block at the top, JS state in `const S`, MQTT routing in `handleMsg()`, UI updates in dedicated `update*UI()` functions.

**Tech Stack:** Vanilla HTML/CSS/JS, WebSocket MQTT fan-out from `web_dashboard.py` on port 8081. No build tools.

---

## File Map

| File | Changes |
|---|---|
| `src/screen_dash.html` | All changes (HTML + CSS + JS) |

---

### Task 1: STATUS page HTML — replace sys-grid with svc-grid + sys-footer

**Files:**
- Modify: `src/screen_dash.html` (STATUS page section, ~lines 799–814)

Current STATUS page HTML (`page-3`):
```html
<div class="page" id="page-3">
  <div class="status-layout">
    <div class="status-title">STATUS</div>
    <div class="status-alert-box" id="status-alert">WAITING FOR DATA</div>
    <div class="dtc-section">
      <div class="dtc-label">DIAGNOSTIC TROUBLE CODES</div>
      <div id="dtc-list"><span class="dtc-none">NO DATA</span></div>
    </div>
    <div class="sys-grid">
      <div class="sys-cell"><span class="s-label">CPU</span><span class="s-value" id="sv-cpu">--</span></div>
      <div class="sys-cell"><span class="s-label">MEM</span><span class="s-value" id="sv-mem">--</span></div>
      <div class="sys-cell"><span class="s-label">DISK</span><span class="s-value" id="sv-disk">--</span></div>
      <div class="sys-cell"><span class="s-label">UP</span><span class="s-value" id="sv-up">--</span></div>
    </div>
  </div>
</div>
```

- [ ] **Step 1: Replace the STATUS page HTML**

Replace the entire `<div class="page" id="page-3">` block with:

```html
  <!-- PAGE 3: STATUS -->
  <div class="page" id="page-3">
    <div class="status-layout">
      <div class="status-title">STATUS</div>
      <div class="status-alert-box" id="status-alert">WAITING FOR DATA</div>
      <div class="dtc-section">
        <div class="dtc-label">DIAGNOSTIC TROUBLE CODES</div>
        <div id="dtc-list"><span class="dtc-none">NO DATA</span></div>
      </div>
      <div class="svc-grid" id="svc-grid">
        <div class="svc-chip" id="svc-canbridge"><span class="svc-dot"></span><span class="svc-name">canbridge</span></div>
        <div class="svc-chip" id="svc-alerts"><span class="svc-dot"></span><span class="svc-name">alerts</span></div>
        <div class="svc-chip" id="svc-logger"><span class="svc-dot"></span><span class="svc-name">logger</span></div>
        <div class="svc-chip" id="svc-anomaly"><span class="svc-dot"></span><span class="svc-name">anomaly</span></div>
        <div class="svc-chip" id="svc-analyst"><span class="svc-dot"></span><span class="svc-name">analyst</span></div>
        <div class="svc-chip" id="svc-voice"><span class="svc-dot"></span><span class="svc-name">voice</span></div>
        <div class="svc-chip" id="svc-vivi"><span class="svc-dot"></span><span class="svc-name">vivi</span></div>
        <div class="svc-chip" id="svc-hotspot"><span class="svc-dot"></span><span class="svc-name">hotspot</span></div>
        <div class="svc-chip" id="svc-homesync"><span class="svc-dot"></span><span class="svc-name">homesync</span></div>
        <div class="svc-chip" id="svc-watchdog"><span class="svc-dot"></span><span class="svc-name">watchdog</span></div>
        <div class="svc-chip" id="svc-realdash"><span class="svc-dot"></span><span class="svc-name">realdash</span></div>
        <div class="svc-chip" id="svc-rf"><span class="svc-dot"></span><span class="svc-name">rf</span></div>
        <div class="svc-chip" id="svc-wardrive"><span class="svc-dot"></span><span class="svc-name">wardrive</span></div>
        <div class="svc-chip" id="svc-dashboard"><span class="svc-dot"></span><span class="svc-name">dashboard</span></div>
        <div class="svc-chip" id="svc-fbmirror"><span class="svc-dot"></span><span class="svc-name">fbmirror</span></div>
        <div class="svc-chip" id="svc-voicein"><span class="svc-dot"></span><span class="svc-name">voicein</span></div>
        <div class="svc-chip" id="svc-flipper"><span class="svc-dot"></span><span class="svc-name">flipper</span></div>
      </div>
      <div class="sys-footer">
        <span><span class="sf-label">CPU</span><span class="sf-val" id="sf-cpu">--</span></span>
        <span><span class="sf-label">MEM</span><span class="sf-val" id="sf-mem">--</span></span>
        <span><span class="sf-label">DISK</span><span class="sf-val" id="sf-disk">--</span></span>
        <span><span class="sf-label">UP</span><span class="sf-val" id="sf-up">--</span></span>
      </div>
    </div>
  </div>
```

- [ ] **Step 2: Verify HTML is well-formed**

```bash
python3 -c "
from html.parser import HTMLParser
class V(HTMLParser): pass
V().feed(open('src/screen_dash.html').read())
print('HTML parse OK')
"
```
Expected: `HTML parse OK` (no exception).

---

### Task 2: STATUS page CSS — add service chip and footer styles

**Files:**
- Modify: `src/screen_dash.html` (CSS `<style>` block, after existing `.sys-grid` / `.sys-cell` rules)

- [ ] **Step 1: Find the existing sys-grid CSS to locate insertion point**

```bash
grep -n "sys-grid\|sys-cell\|s-label\|s-value" src/screen_dash.html | head -15
```

Note the last line number of the existing `.sys-cell` / `.s-value` block.

- [ ] **Step 2: Add service chip CSS after the existing sys-grid rules**

After the block ending with `.s-value { ... }`, insert:

```css
/* ═══ SERVICE CHIP GRID (STATUS page) ═══ */
.svc-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:3px;flex:1;overflow:hidden}
.svc-chip{display:flex;align-items:center;gap:4px;padding:3px 5px;border-radius:3px;background:#111;border:1px solid #1e1e1e;font-size:8.5px;color:#888;letter-spacing:0.3px}
.svc-dot{width:5px;height:5px;border-radius:50%;background:#555;flex-shrink:0}
.svc-name{overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
.svc-chip.ok{color:#aaa}
.svc-chip.ok .svc-dot{background:#4caf50;box-shadow:0 0 4px rgba(76,175,80,0.5)}
.svc-chip.fail{color:#ff8888;border-color:#3a1111}
.svc-chip.fail .svc-dot{background:#ff4444;box-shadow:0 0 4px rgba(255,68,68,0.5)}
.svc-chip.warn{color:#ffdd88;border-color:#2a2000}
.svc-chip.warn .svc-dot{background:#ffbb00;box-shadow:0 0 4px rgba(255,187,0,0.4);animation:svc-pulse 1.2s infinite}
@keyframes svc-pulse{0%,100%{opacity:1}50%{opacity:0.4}}
/* ═══ SYS FOOTER STRIP (STATUS page) ═══ */
.sys-footer{display:flex;justify-content:space-around;padding:4px;border-top:1px solid #1a1a1a;flex-shrink:0;font-size:8.5px}
.sf-label{color:#444;letter-spacing:1px;margin-right:3px}
.sf-val{color:#777}
```

- [ ] **Step 3: Verify HTML parse still clean**

```bash
python3 -c "
from html.parser import HTMLParser
class V(HTMLParser): pass
V().feed(open('src/screen_dash.html').read())
print('HTML parse OK')
"
```
Expected: `HTML parse OK`.

---

### Task 3: STATUS page JS — state, MQTT router, updateStatusUI()

**Files:**
- Modify: `src/screen_dash.html` (JS section)

- [ ] **Step 1: Add `svcStates` to the state object**

Find `const S = {` (around line 956). The object currently ends with:
```js
  connected:false, lastUpdate:0
};
```

Change to:
```js
  connected:false, lastUpdate:0,
  // Service health (STATUS page)
  svcStates:{}
};
```

- [ ] **Step 2: Extract service states in the watchdog MQTT handler**

Find the existing `/system/watchdog` handler:
```js
  else if (topic.endsWith('/system/watchdog')) {
    const sys = data.system || {};
    if (sys.cpu_temp) S.cpuTemp = sys.cpu_temp;
```

Add one line after `const sys = data.system || {};`:
```js
    if (data.services && typeof data.services === 'object') S.svcStates = data.services;
```

- [ ] **Step 3: Replace `updateStatusUI()` sys-grid block with svc-grid + footer**

Find and replace in `updateStatusUI()`. The current tail of the function reads:
```js
  // System
  setEl('sv-cpu', S.cpuTemp > 0 ? Math.round(S.cpuTemp) + '°C' : '--');
  setEl('sv-mem', S.memPct > 0 ? Math.round(S.memPct) + '%' : '--');
  setEl('sv-disk', S.diskPct > 0 ? Math.round(S.diskPct) + '%' : '--');
  setEl('sv-up', S.uptime || '--');
}
```

Replace with:
```js
  // Service chips
  ['canbridge','alerts','logger','anomaly','analyst','voice',
   'vivi','hotspot','homesync','watchdog','realdash','rf',
   'wardrive','dashboard','fbmirror','voicein','flipper'].forEach(name => {
    const el = document.getElementById('svc-' + name);
    if (!el) return;
    const state = S.svcStates['drifter-' + name];
    el.className = 'svc-chip' +
      (state === 'active' ? ' ok' : state === 'failed' ? ' fail' : state ? ' warn' : '');
  });
  // Sys footer
  setEl('sf-cpu', S.cpuTemp > 0 ? Math.round(S.cpuTemp) + '°C' : '--');
  setEl('sf-mem', S.memPct > 0 ? Math.round(S.memPct) + '%' : '--');
  setEl('sf-disk', S.diskPct > 0 ? Math.round(S.diskPct) + '%' : '--');
  setEl('sf-up', S.uptime || '--');
}
```

- [ ] **Step 4: Run existing tests to confirm no regressions**

```bash
cd /home/kali/drifter && python -m pytest tests/ -q
```
Expected: `279 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/screen_dash.html
git commit -m "feat(hud): STATUS page — 17-service chip grid + sys footer strip"
```

---

### Task 4: Flipper Zero page HTML + 7th bottom-bar dot

**Files:**
- Modify: `src/screen_dash.html` (after the SCAN page div, before bottom bar)

- [ ] **Step 1: Add page-6 HTML after the SCAN page closing tag**

Find `</div><!-- /pages -->` (just after the SCAN page `</div></div></div>`). Insert before it:

```html
  <!-- PAGE 6: FLIPPER ZERO -->
  <div class="page" id="page-6">
    <div class="flipper-layout">
      <div class="flip-sidebar">
        <div class="flip-title">FLIPPER</div>
        <div class="flip-state" id="fv-state">● OFFLINE</div>
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

- [ ] **Step 2: Add 7th dot to the bottom bar**

Find the bottom bar section:
```html
  <div class="page-dot" id="dot-5"></div>
  <span class="auto-ind" id="auto-ind">AUTO</span>
```

Change to:
```html
  <div class="page-dot" id="dot-5"></div>
  <div class="page-dot" id="dot-6"></div>
  <span class="auto-ind" id="auto-ind">AUTO</span>
```

- [ ] **Step 3: Verify HTML parse**

```bash
python3 -c "
from html.parser import HTMLParser
class V(HTMLParser): pass
V().feed(open('src/screen_dash.html').read())
print('HTML parse OK')
"
```
Expected: `HTML parse OK`.

---

### Task 5: Flipper Zero page CSS

**Files:**
- Modify: `src/screen_dash.html` (CSS block, after the svc-pulse/sys-footer rules added in Task 2)

- [ ] **Step 1: Add Flipper page CSS after the `sf-val` rule**

```css
/* ═══ FLIPPER ZERO PAGE ═══ */
.flipper-layout{display:flex;height:100%;overflow:hidden}
.flip-sidebar{width:140px;border-right:1px solid #1a1a1a;display:flex;flex-direction:column;padding:8px;gap:6px;flex-shrink:0}
.flip-title{font-size:10px;color:#ff8c00;letter-spacing:2px}
.flip-state{font-size:9px;padding:2px 0;color:#555}
.flip-state.connected{color:#4caf50}
.flip-state.searching{color:#ffbb00;animation:svc-pulse 1.2s infinite}
.flip-state.disconnected{color:#ff4444}
.flip-stats{display:flex;flex-direction:column;gap:4px;margin-top:4px}
.flip-stat{font-size:8px;display:flex;gap:4px}
.flip-slabel{color:#555}
.flip-sval{color:#999}
.flip-log-panel{flex:1;display:flex;flex-direction:column;padding:6px 8px;gap:4px;overflow:hidden}
.flip-log-header{font-size:8px;color:#555;letter-spacing:1px;display:flex;justify-content:space-between;flex-shrink:0}
.flip-live{color:#ff8c00}
.flip-log{flex:1;overflow:hidden;display:flex;flex-direction:column;gap:3px}
.flip-log-row{display:grid;grid-template-columns:40px 50px 1fr 34px;gap:4px;font-size:8.5px;align-items:center}
.flip-log-row.fresh{background:#1a1200;border-radius:2px}
.flip-ts{color:#444}
.flip-freq{color:#ff8c00}
.flip-data{color:#888;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
.flip-rssi{color:#555;text-align:right}
.flip-empty{font-size:9px;color:#444;padding:8px 0}
```

- [ ] **Step 2: Verify HTML parse**

```bash
python3 -c "
from html.parser import HTMLParser
class V(HTMLParser): pass
V().feed(open('src/screen_dash.html').read())
print('HTML parse OK')
"
```
Expected: `HTML parse OK`.

---

### Task 6: Flipper Zero page JS — state, MQTT router, updateFlipperUI()

**Files:**
- Modify: `src/screen_dash.html` (JS section)

- [ ] **Step 1: Add Flipper state fields to `const S`**

In `const S`, after `svcStates:{}` (added in Task 3), add:

```js
  // Flipper Zero (page 6)
  flipperState:'', flipperFw:'', flipperSubOn:false,
  flipperCapCount:0, flipperSessionStart:0, flipperLog:[]
```

So the end of `const S` reads:
```js
  connected:false, lastUpdate:0,
  svcStates:{},
  // Flipper Zero (page 6)
  flipperState:'', flipperFw:'', flipperSubOn:false,
  flipperCapCount:0, flipperSessionStart:0, flipperLog:[]
};
```

- [ ] **Step 2: Add Flipper MQTT handlers in `handleMsg()`**

Find the last `else if` in the MQTT router — the `/system/watchdog` handler — and add two new branches after it (before the closing `}`):

```js
  else if (topic.endsWith('/flipper/status')) {
    S.flipperState = data.state || '';
    S.flipperFw = data.firmware_version || data.fw_version || data.firmware || '';
    S.flipperSubOn = !!(data.subghz_monitoring || data.subghz_active);
    if (data.state === 'connected' && !S.flipperSessionStart) S.flipperSessionStart = Date.now();
    if (data.state !== 'connected') S.flipperSessionStart = 0;
  }
  else if (topic.endsWith('/flipper/subghz')) {
    S.flipperCapCount++;
    S.flipperLog.unshift({
      ts: data.ts || (Date.now() / 1000),
      freq: data.frequency_mhz || '433.92',
      proto: data.protocol || 'RAW',
      detail: data.data || (data.samples ? data.samples + ' samples' : ''),
      rssi: data.rssi != null ? Math.round(data.rssi) : null
    });
    if (S.flipperLog.length > 30) S.flipperLog.pop();
  }
```

- [ ] **Step 3: Add `updateFlipperUI()` function**

Add the following function after `updateScanUI()` (and before `updateAlertIndicator()`):

```js
function updateFlipperUI() {
  const state = S.flipperState;
  const stateEl = document.getElementById('fv-state');
  stateEl.textContent = state ? ('● ' + state.toUpperCase()) : '● OFFLINE';
  stateEl.className = 'flip-state ' + (state || 'disconnected');

  setEl('fv-fw', S.flipperFw || '--');
  const subEl = document.getElementById('fv-sub');
  if (subEl) {
    subEl.textContent = S.flipperSubOn ? 'ON' : '--';
    subEl.style.color = S.flipperSubOn ? 'var(--green)' : '#555';
  }
  setEl('fv-cap', String(S.flipperCapCount));

  if (S.flipperSessionStart) {
    const secs = Math.floor((Date.now() - S.flipperSessionStart) / 1000);
    const h = Math.floor(secs / 3600), m = Math.floor((secs % 3600) / 60);
    setEl('fv-dur', h > 0 ? h + 'h' + m + 'm' : m + 'm');
  } else {
    setEl('fv-dur', '--');
  }

  const liveEl = document.getElementById('fv-live');
  if (liveEl) liveEl.textContent = S.flipperSubOn ? '● LIVE' : '';

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
      '<span class="flip-ts">' + tsStr + '</span>' +
      '<span class="flip-freq">' + entry.freq + '</span>' +
      '<span class="flip-data"></span>' +
      '<span class="flip-rssi">' + rssi + '</span>';
    row.querySelector('.flip-data').textContent = detail;
    log.appendChild(row);
  });
}
```

Note: `flip-data` uses `.textContent` (not `innerHTML`) to prevent XSS from RF-captured protocol strings.

- [ ] **Step 4: Add Flipper to the update dispatch loop**

Find:
```js
  else if (currentPage === 5) updateScanUI();
```

Change to:
```js
  else if (currentPage === 5) updateScanUI();
  else if (currentPage === 6) updateFlipperUI();
```

- [ ] **Step 5: Run existing tests**

```bash
cd /home/kali/drifter && python -m pytest tests/ -q
```
Expected: `279 passed`.

---

### Task 7: Page count constants + keyboard shortcut

**Files:**
- Modify: `src/screen_dash.html` (JS constants section, ~line 977)

- [ ] **Step 1: Update PAGE_NAMES and PAGES**

Find:
```js
const PAGE_NAMES = ['DRIVE','TYRES','ENGINE','STATUS','RF','SCAN'];
let currentPage = 0;
const PAGES = 6;
```

Change to:
```js
const PAGE_NAMES = ['DRIVE','TYRES','ENGINE','STATUS','RF','SCAN','FLIPPER'];
let currentPage = 0;
const PAGES = 7;
```

- [ ] **Step 2: Add key `7` shortcut**

Find:
```js
else if (k === '6') { setAutoCycle(false); goToPage(5); }
```

Add after it:
```js
else if (k === '7') { setAutoCycle(false); goToPage(6); }
```

- [ ] **Step 3: Run existing tests**

```bash
cd /home/kali/drifter && python -m pytest tests/ -q
```
Expected: `279 passed`.

- [ ] **Step 4: Commit all Flipper page changes**

```bash
git add src/screen_dash.html
git commit -m "feat(hud): add Flipper Zero page — sidebar + 433.92 MHz capture log"
```

---

### Task 8: Visual verification + final commit

**Files:**
- No code changes — verification only.

- [ ] **Step 1: Open the HUD in a browser**

```bash
python3 -m http.server 9000 --directory src/ &
```
Open `http://localhost:9000/screen_dash.html`.

- [ ] **Step 2: Verify STATUS page (press key `4` to navigate)**

Check:
- 17 service chips visible in a 3-column grid (no chip overflows or missing)
- `CPU`, `MEM`, `DISK`, `UP` values visible in footer strip
- All chips start with no state class (grey dot) — correct, no watchdog data yet

- [ ] **Step 3: Inject simulated service states via browser console**

Open DevTools console and run:
```js
S.svcStates = {
  'drifter-canbridge':'active','drifter-alerts':'active',
  'drifter-logger':'active','drifter-anomaly':'active',
  'drifter-analyst':'failed','drifter-voice':'active',
  'drifter-vivi':'active','drifter-hotspot':'active',
  'drifter-homesync':'active','drifter-watchdog':'active',
  'drifter-realdash':'active','drifter-rf':'active',
  'drifter-wardrive':'active','drifter-dashboard':'active',
  'drifter-fbmirror':'active','drifter-voicein':'activating',
  'drifter-flipper':'active'
};
S.cpuTemp=47.2; S.memPct=62.1; S.diskPct=31.0; S.uptime='3h20m';
updateStatusUI();
```

Expected: 16 green chips, `analyst` red, `voicein` amber (pulsing), footer shows real values.

- [ ] **Step 4: Verify Flipper page (press key `7`)**

Check:
- 7th dot active in bottom bar, label shows `FLIPPER`
- Sidebar shows `● OFFLINE`, all stats `--`
- Log panel shows `WAITING FOR FLIPPER`

- [ ] **Step 5: Inject simulated Flipper state + 3 captures via console**

```js
S.flipperState = 'connected';
S.flipperFw = '0.99.1';
S.flipperSubOn = true;
S.flipperSessionStart = Date.now() - 2520000; // 42 min ago
S.flipperCapCount = 3;
S.flipperLog = [
  {ts: Date.now()/1000,      freq:'433.92', proto:'Princeton', detail:'0xA3F12C', rssi:-62},
  {ts: Date.now()/1000 - 3,  freq:'433.92', proto:'CAME 12bit', detail:'0x9C4',  rssi:-71},
  {ts: Date.now()/1000 - 17, freq:'433.92', proto:'RAW',        detail:'48 samples', rssi:-84}
];
updateFlipperUI();
```

Expected: sidebar shows `● CONNECTED` (green), `FW 0.99.1`, `SUB ON` (green), `CAP 3`, `SESSION 42m`, log shows 3 rows with top 2 highlighted (`.fresh`), `● LIVE` in header.

- [ ] **Step 6: Verify swipe and page dot navigation works for all 7 pages**

Swipe through all 7 pages, confirm dots 0–6 activate correctly and `FLIPPER` label appears.

- [ ] **Step 7: Stop test server**

```bash
kill %1
```

- [ ] **Step 8: Run full test suite one final time**

```bash
cd /home/kali/drifter && python -m pytest tests/ -q
```
Expected: `279 passed`.

- [ ] **Step 9: Final commit**

```bash
git add src/screen_dash.html
git status
git diff --cached --stat
git commit -m "feat(hud): STATUS service grid + Flipper Zero page — visual verification complete"
```

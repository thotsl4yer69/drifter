// ════════════════════════════════════════════════════════════════
// DRIFTER ADAPTER — the production data source (brief §5 contract).
// Default: real WS (:8081 telemetry frames) + REST (/api/* cold-start
// & a single consolidated low-rate poll), mapped onto the canonical
// state shape (sim.js · freshState). `?sim=1` swaps in the mock bus.
//
// Same interface as the mock: subscribe(fn), getState(), setMode(m),
// setScenario/setHw/setLink (dev overrides), fireAlert (no-op live).
//
// Design guarantees honored here:
//  · honest states — real baseline starts EMPTY (ecu pending, gps none,
//    link lost, no alerts/aircraft, flat spectrum). Nothing is invented;
//    values appear only when a real frame arrives.
//  · link resilience — exp backoff + jitter + navigator.onLine; flips
//    state.link = live|lost and surfaces the LINK LOST banner.
//  · AUD currency, vivi2/query, never auto-LLM (UI-side).
// ════════════════════════════════════════════════════════════════
import { createSim, freshState } from './sim.js';

const params = new URLSearchParams(location.search);
const SIM = params.has('sim');

// ── helpers ─────────────────────────────────────────────────────
const num = (v) => (typeof v === 'number' && isFinite(v) ? v : (typeof v === 'string' && v.trim() !== '' && isFinite(+v) ? +v : null));
const HIST = 90;
function pushHist(state, k, v) {
  if (v == null) return;
  const h = state.hist[k];
  h.push(v);
  if (h.length > HIST) h.shift();
}
function gearFor(speed) {
  if (speed == null) return 'N';
  if (speed < 1) return 'N';
  if (speed < 18) return '1';
  if (speed < 38) return '2';
  if (speed < 62) return '3';
  if (speed < 88) return '4';
  return '5';
}
const sevForLevel = (lv) => (lv >= 3 ? 'crit' : lv >= 2 ? 'warn' : 'info');

// Honest "nothing yet" baseline for the live node (vs sim's populated one).
function liveBaseline() {
  const s = freshState();
  s.link = 'lost';
  s.hw = { ecu: 'pending', gps: 'none', bt: 'down', weatherKey: false, sdr: 'unknown' };
  s.power = { undervoltNow: false, undervoltSinceBoot: false, throttled: false };
  s.speed = 0; s.rpm = 0; s.gear = 'N'; s.coolant = 0; s.voltage = 0; s.throttle = 0;
  s.trip = { km: 0, fuel: 0, cost: 0, l100: 0, durS: 0 };
  s.alerts = [];
  s.dtcs = [];
  s.tpms = [
    { pos: 'FL', kpa: null, c: null }, { pos: 'FR', kpa: null, c: null },
    { pos: 'RL', kpa: null, c: null }, { pos: 'RR', kpa: null, c: null },
  ];
  s.rf.aircraft = [];
  s.rf.spectrum = new Array(64).fill(-100);
  s.rf.mods = { rtl_433: 'idle', dump1090: 'idle', spectrum: 'idle', rfaudio: 'idle' };
  s.rf.hits = 0; s.rf.adsb = 0; s.rf.tpmsSeen = 0;
  s.gps = { lat: null, lon: null, hdg: 0, fix: 'none', sats: 0, acc: null };
  s.vivi = { status: 'awaiting link', lastSaid: '' };
  s.recon.wardrive = [];
  s.recon.blePersist = null;
  s.recon.hidPayloads = [];
  s.recon.auditAllowlist = [];
  s.system.services = [];
  s.system.hwPresence = [];
  s.system.broker = { ok: false, clients: 0, msgs: 0 };
  s.system.homeSync = 'unknown';
  s.system.throttle = { uvNow: false, uvBoot: false, capNow: false, capBoot: false, raw: '—' };
  s.chat = [];
  s.hist = { speed: [], rpm: [], coolant: [], voltage: [] };
  s.sysCpu = null; s.sysMem = null; s.sysTemp = null;
  return s;
}

function createRealAdapter() {
  const state = liveBaseline();
  const subs = new Set();
  let notifyScheduled = false;
  function notify() {
    notifyScheduled = false;
    subs.forEach((fn) => fn(state));
  }
  function schedule() {
    if (notifyScheduled) return;
    notifyScheduled = true;
    setTimeout(notify, 80); // coalesce bursts → ~12Hz UI updates
  }

  // ── topic → state mapping ──────────────────────────────────────
  function applyTopic(topic, data) {
    try {
      const d = data;
      switch (true) {
        case topic === 'drifter/snapshot': {
          if (d && typeof d === 'object') {
            const set = (k, v) => { const n = num(v); if (n != null) state[k] = n; };
            set('rpm', d.rpm); set('coolant', d.coolant); set('voltage', d.voltage);
            const sp = num(d.speed); if (sp != null) { state.speed = sp; state.gear = gearFor(sp); }
            const th = num(d.throttle); if (th != null) state.throttle = th > 1 ? th / 100 : th;
            state.hw.ecu = 'ok';
            pushHist(state, 'rpm', state.rpm); pushHist(state, 'coolant', state.coolant);
            pushHist(state, 'voltage', state.voltage); pushHist(state, 'speed', state.speed);
          }
          break;
        }
        case topic === 'drifter/engine/rpm': { const v = num(d?.value ?? d); if (v != null) { state.rpm = v; state.hw.ecu = 'ok'; pushHist(state, 'rpm', v); } break; }
        case topic === 'drifter/engine/coolant': { const v = num(d?.value ?? d); if (v != null) { state.coolant = v; state.hw.ecu = 'ok'; pushHist(state, 'coolant', v); } break; }
        case topic === 'drifter/engine/throttle': { const v = num(d?.value ?? d); if (v != null) state.throttle = v > 1 ? v / 100 : v; break; }
        case topic === 'drifter/power/voltage': { const v = num(d?.value ?? d); if (v != null) { state.voltage = v; pushHist(state, 'voltage', v); } break; }
        case topic === 'drifter/vehicle/speed': { const v = num(d?.value ?? d); if (v != null) { state.speed = v; state.gear = gearFor(v); pushHist(state, 'speed', v); } break; }
        case topic === 'drifter/system/status': {
          const st = d?.state;
          if (st === 'online') state.hw.ecu = 'ok';
          else if (st === 'hw_pending') state.hw.ecu = 'pending';
          break;
        }
        case topic === 'drifter/alert/level': { const lv = num(d?.level ?? d); if (lv != null) state.alertLevel = lv; break; }
        case topic === 'drifter/alert/active': {
          const arr = Array.isArray(d) ? d : Array.isArray(d?.active) ? d.active : null;
          if (arr) {
            state.alerts = arr.map((a, i) => ({
              id: a.id ?? a.code ?? a.rule ?? i,
              sev: a.sev || sevForLevel(num(a.level) ?? 1),
              code: (a.code || a.rule || 'ALERT').toString().toUpperCase().slice(0, 12),
              msg: a.message || a.msg || '',
              ageS: num(a.age_s ?? a.ageS) ?? 0,
            }));
          }
          break;
        }
        case topic === 'drifter/diag/dtc': {
          const stored = (d?.stored || []).map((c) => ({ code: typeof c === 'string' ? c : c.code, desc: c.desc || '', state: 'stored' }));
          const pending = (d?.pending || []).map((c) => ({ code: typeof c === 'string' ? c : c.code, desc: c.desc || '', state: 'pending' }));
          state.dtcs = [...stored, ...pending];
          break;
        }
        case topic === 'drifter/trip/stats': {
          if (d && typeof d === 'object') {
            state.trip = {
              km: num(d.distance_km ?? d.km) ?? state.trip.km,
              fuel: num(d.fuel_l ?? d.fuel) ?? state.trip.fuel,
              cost: num(d.cost ?? d.cost_aud) ?? state.trip.cost, // AUD (brief §6.10)
              l100: num(d.l100 ?? d.cur_l_per_100km ?? d.avg_l_per_100km) ?? 0,
              durS: num(d.duration_s ?? d.durS) ?? state.trip.durS,
            };
            if (d.odo != null) state.odo = num(d.odo);
          }
          break;
        }
        case topic === 'drifter/gps/fix': {
          if (d && typeof d === 'object') {
            const lat = num(d.lat), lon = num(d.lng ?? d.lon);
            const mode = num(d.mode) ?? 0;
            if (d.fix === false || mode < 2 || lat == null) state.hw.gps = 'acquiring';
            else { state.hw.gps = 'fix'; state.gps.lat = lat; state.gps.lon = lon; state.gps.fix = mode >= 3 ? '3D' : '2D'; }
            if (d.sats != null) state.gps.sats = num(d.sats);
            if (d.acc != null) state.gps.acc = num(d.acc);
            if (d.track_deg != null) state.heading = num(d.track_deg);
          }
          break;
        }
        case topic === 'drifter/rf/spectrum/summary':
        case topic === 'drifter/rf/spectrum': {
          const bins = Array.isArray(d) ? d : Array.isArray(d?.bins) ? d.bins : Array.isArray(d?.spectrum) ? d.spectrum : null;
          if (bins) state.rf.spectrum = bins.map((v) => num(v) ?? -100);
          if (d?.peak_mhz != null) state.rf.peakMhz = num(d.peak_mhz);
          if (d?.peak_db != null) state.rf.peakDb = num(d.peak_db);
          break;
        }
        case topic === 'drifter/rf/status': {
          if (d && typeof d === 'object') {
            state.hw.sdr = d.sdr_detected ? 'ok' : 'absent';
            state.rf.mods = {
              rtl_433: d.rtl433_active ? 'on' : 'idle',
              dump1090: d.dump1090_active ? 'on' : 'idle',
              spectrum: d.spectrum_active ? 'on' : 'idle',
              rfaudio: d.rfaudio_active ? 'on' : 'idle',
            };
          }
          break;
        }
        case topic === 'drifter/rf/adsb': {
          const arr = Array.isArray(d) ? d : Array.isArray(d?.aircraft) ? d.aircraft : null;
          if (arr) {
            state.rf.aircraft = arr.slice(0, 12).map((a) => ({
              id: a.callsign || a.id || a.hex || '????',
              brg: num(a.bearing ?? a.brg) ?? 0,
              rng: Math.max(0.12, Math.min(0.92, (num(a.distance_km ?? a.rng) ?? 9) / 30)),
              alt: Math.round((num(a.alt_ft ?? a.alt) ?? 0) / 1000),
              ghost: !!(a.ghost || a.spoofed),
            }));
            state.rf.adsb = state.rf.aircraft.length;
          }
          break;
        }
        case topic === 'drifter/rf/tpms/snapshot': {
          const corners = d?.corners || d;
          if (corners && typeof corners === 'object') {
            const map = { fl: 'FL', fr: 'FR', rl: 'RL', rr: 'RR' };
            let seen = 0;
            state.tpms = ['fl', 'fr', 'rl', 'rr'].map((k) => {
              const c = corners[k] || corners[map[k]] || {};
              const kpa = num(c.kpa ?? c.psi != null ? c.psi * 6.895 : c.kpa);
              if (kpa != null) seen++;
              return { pos: map[k], kpa: kpa != null ? Math.round(kpa) : null, c: num(c.temp_c ?? c.c) };
            });
            state.rf.tpmsSeen = seen;
          }
          break;
        }
        case topic === 'drifter/system/watchdog': {
          if (d && typeof d === 'object') {
            const svc = d.services || d.units;
            if (svc && typeof svc === 'object') {
              state.system.services = Object.entries(svc).map(([name, v]) => {
                const restarts = num(v?.restarts) ?? 0;
                const active = typeof v === 'boolean' ? v : v?.active !== false;
                const st = restarts >= 5 ? 'gave-up' : (!active ? 'flapping' : restarts > 0 ? 'flapping' : 'ok');
                return { name: name.replace(/^drifter-/, ''), restarts, state: st };
              });
            }
            if (d.cpu != null) state.sysCpu = num(d.cpu);
            if (d.mem != null) state.sysMem = num(d.mem);
            if (d.temp != null) state.sysTemp = num(d.temp);
            if (d.overall === 'degraded' && d.auto_demoted) state.autoDemoted = true;
            if (d.throttle) {
              state.power.undervoltNow = !!d.throttle.uv_now;
              state.power.undervoltSinceBoot = !!d.throttle.uv_boot;
              state.system.throttle = { uvNow: !!d.throttle.uv_now, uvBoot: !!d.throttle.uv_boot, capNow: !!d.throttle.cap_now, capBoot: !!d.throttle.cap_boot, raw: d.throttle.raw || '—' };
            }
            if (d.mqtt_clients != null) state.system.broker = { ok: true, clients: num(d.mqtt_clients), msgs: num(d.msg_rate) ?? 0 };
          }
          break;
        }
        case topic.startsWith('drifter/hw/'): {
          const dev = topic.slice('drifter/hw/'.length);
          const connected = d?.connected === true ? 'ok' : (d?.connected === false ? (dev === 'bluetooth' ? 'down' : 'absent') : '?');
          const i = state.system.hwPresence.findIndex(([n]) => n === dev);
          if (i >= 0) state.system.hwPresence[i] = [dev, connected];
          else state.system.hwPresence.push([dev, connected]);
          if (dev === 'rtl_sdr') state.hw.sdr = connected === 'ok' ? 'ok' : 'absent';
          if (dev === 'bluetooth') state.hw.bt = connected === 'ok' ? 'up' : 'down';
          if (dev === 'gps' && connected !== 'ok' && state.hw.gps === 'none') state.hw.gps = 'none';
          break;
        }
        case topic === 'drifter/network/status': {
          if (d && typeof d === 'object') {
            if (d.ap_fallback || d.state === 'ap_fallback') state.system.radio = 'ap';
            else if (d.state === 'connected') state.system.radio = 'client';
            state.system.homeSync = d.internet ? 'online · client mode' : 'pending · awaiting client mode';
          }
          break;
        }
        case topic === 'drifter/wardrive/snapshot':
        case topic === 'drifter/wardrive/wifi': {
          const arr = Array.isArray(d) ? d : Array.isArray(d?.networks) ? d.networks : null;
          if (arr) state.recon.wardrive = arr.slice(0, 8).map((n) => ({
            ssid: n.ssid || '(hidden)', bssid: n.bssid || '—', ch: num(n.ch ?? n.channel) ?? 0,
            rssi: num(n.rssi) ?? -90, enc: n.enc || n.security || 'WPA2', own: !!n.own,
          }));
          break;
        }
        case topic === 'drifter/ble/persist': {
          if (d && (d.mac || d.address)) state.recon.blePersist = {
            mac: d.mac || d.address, kind: d.kind || d.label || 'beacon',
            sightings: num(d.sightings) ?? 0, spanMin: num(d.span_min ?? d.spanMin) ?? 0, lastSeenMin: num(d.last_seen_min ?? d.lastSeenMin) ?? 0,
          };
          break;
        }
        case topic === 'drifter/vivi2/status': { if (d?.status) state.vivi.status = d.status; break; }
        case topic === 'drifter/vivi2/response': { if (d?.text || d?.message) state.vivi.lastSaid = d.text || d.message; break; }
        default: break;
      }
      schedule();
    } catch (e) { /* never let a malformed frame break the UI */ }
  }

  // ── WebSocket with resilient reconnect ─────────────────────────
  let ws = null, retry = 0, retryTimer = null;
  const wsURL = () => `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.hostname}:8081`;
  function connect() {
    if (typeof navigator !== 'undefined' && navigator.onLine === false) { scheduleReconnect(); return; }
    try { ws = new WebSocket(wsURL()); } catch (e) { scheduleReconnect(); return; }
    ws.onopen = () => { retry = 0; if (state.link !== 'live') { state.link = 'live'; schedule(); } coldStart(); };
    ws.onmessage = (ev) => {
      let frame; try { frame = JSON.parse(ev.data); } catch { return; }
      if (frame && frame.topic) applyTopic(frame.topic, frame.data);
    };
    ws.onclose = () => { if (state.link !== 'lost') { state.link = 'lost'; schedule(); } scheduleReconnect(); };
    ws.onerror = () => { try { ws.close(); } catch {} };
  }
  function scheduleReconnect() {
    clearTimeout(retryTimer);
    const base = Math.min(1000 * 2 ** retry, 30000); // exp backoff, cap 30s
    const jitter = base * 0.3 * Math.random();
    retry++;
    retryTimer = setTimeout(connect, base + jitter);
  }
  if (typeof window !== 'undefined') {
    window.addEventListener('online', () => { retry = 0; clearTimeout(retryTimer); connect(); });
  }

  // ── REST: cold-start snapshot + consolidated low-rate poll ─────
  async function coldStart() {
    try {
      const r = await fetch('/api/state', { headers: { accept: 'application/json' } });
      if (!r.ok) return;
      const snap = await r.json();
      // latest_state keys are `topic` with drifter/ stripped and / → _
      for (const [k, v] of Object.entries(snap || {})) {
        if (k === 'snapshot') applyTopic('drifter/snapshot', v);
        else applyTopic('drifter/' + k.replace(/_/g, '/'), v);
      }
      schedule();
    } catch (e) { /* offline cold-start is fine; WS will fill in */ }
  }
  // single consolidated 30s reconcile (brief §2.3 — one tick, not many pollers)
  setInterval(() => { if (state.link === 'live') coldStart(); }, 30000);

  connect();

  // ── command surface ────────────────────────────────────────────
  async function post(path, body) {
    try {
      await fetch(path, { method: 'POST', headers: { 'content-type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
    } catch (e) { /* surfaced via lack of state change; honest */ }
  }

  return {
    real: true,
    subscribe(fn) { subs.add(fn); fn(state); return () => subs.delete(fn); },
    getState: () => state,
    setMode(m) { state.mode = m; state.autoDemoted = false; schedule(); post(`/api/mode/${m}`); },
    setScenario() { /* sim-only */ },
    setHw(k, v) { state.hw[k] = v; schedule(); },
    setLink(v) { state.link = v; schedule(); },
    autoDemote() { state.mode = 'diag'; state.autoDemoted = true; schedule(); },
    fireAlert() { /* live alerts come from the bus */ },
    // production command helpers used by surfaces
    viviQuery(text) { post('/api/vivi/query', { query: text, topic: 'drifter/vivi2/query' }); },
    restartService(unit) { post(`/api/service/${unit}`, { action: 'restart' }); },
    setOrigin(lat, lon) { post('/api/gps/manual', { lat, lon }); },
  };
}

export const DrifterSim = SIM ? createSim() : createRealAdapter();

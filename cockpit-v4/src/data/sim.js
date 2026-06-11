// ════════════════════════════════════════════════════════════════
// DRIFTER SIM — mock telemetry bus (bench/demo, `?sim=1`).
// Ported 1:1 from the handoff shared/drifter-sim.js, repackaged as a
// factory (createSim) instead of assigning window.DrifterSim. Mirrors
// the real data the cockpit binds to so the UI is fully exercised
// without a vehicle. The REAL adapter (adapter.js) is the default.
// ════════════════════════════════════════════════════════════════
const TAU = Math.PI * 2;
const HIST = 90;

function noise(t, seed) {
  return (
    Math.sin(t * 0.9 + seed) * 0.5 +
    Math.sin(t * 0.23 + seed * 2.7) * 0.3 +
    Math.sin(t * 0.057 + seed * 5.1) * 0.2
  );
}
const clamp = (x, a, b) => Math.max(a, Math.min(b, x));

function gearFor(speed) {
  if (speed < 1) return 'N';
  if (speed < 18) return '1';
  if (speed < 38) return '2';
  if (speed < 62) return '3';
  if (speed < 88) return '4';
  return '5';
}

// The canonical state shape every widget reads. Also used (as the honest
// "nothing yet" baseline) by the real adapter.
export function freshState() {
  return {
    scenario: 'drive',
    mode: 'drive',
    autoDemoted: false,
    link: 'live',
    hw: { ecu: 'ok', gps: 'fix', bt: 'down', weatherKey: false, sdr: 'ok' },
    power: { undervoltNow: false, undervoltSinceBoot: true, throttled: false },
    t: 0,
    speed: 0, rpm: 800, gear: 'N',
    coolant: 84, voltage: 14.1,
    throttle: 0,
    odo: 184223.4,
    trip: { km: 0, fuel: 0, cost: 0, l100: 0, durS: 0 },
    tpms: [
      { pos: 'FL', kpa: 232, c: 24 },
      { pos: 'FR', kpa: 229, c: 24 },
      { pos: 'RL', kpa: 235, c: 23 },
      { pos: 'RR', kpa: 218, c: 26 },
    ],
    gps: { lat: -36.7570, lon: 144.2794, hdg: 0, fix: '3D', sats: 11, acc: 4 },
    heading: 38,
    rf: {
      peakMhz: 433.92, peakDb: -38,
      mods: { rtl_433: 'on', dump1090: 'on', spectrum: 'idle', rfaudio: 'idle' },
      hits: 14, adsb: 6, tpmsSeen: 4,
      spectrum: new Array(64).fill(-80),
      aircraft: [
        { id: 'VH-ZNE', brg: 40, rng: 0.55, alt: 37, spd: 0.020, ghost: false },
        { id: 'QFA612', brg: 152, rng: 0.78, alt: 31, spd: -0.013, ghost: false },
        { id: 'VH-XKD', brg: 233, rng: 0.34, alt: 8, spd: 0.028, ghost: false },
        { id: '?GHOST', brg: 318, rng: 0.62, alt: 0, spd: 0.017, ghost: true },
      ],
    },
    alerts: [
      { id: 1, sev: 'warn', code: 'TPMS-RR', msg: 'RR pressure 218 kPa · 6% below set', ageS: 312, ack: false },
      { id: 2, sev: 'info', code: 'ANOMALY', msg: 'voltage ripple 0.3V @ idle — learned envelope edge', ageS: 1140, ack: false },
    ],
    dtcs: [{ code: 'P0171', desc: 'System too lean · bank 1', state: 'stored' }],
    vivi: { status: 'link ok · listening for wake', lastSaid: 'Coolant trending normal. RR tire is 6% under — worth a look at the next stop.' },
    recon: {
      sentry: { armed: false, pendingConfirm: false },
      wardrive: [
        { ssid: 'MZ1312_DRIFTER', bssid: 'A2:5F:…:31', ch: 6, rssi: -28, enc: 'WPA2', own: true },
        { ssid: 'Telstra-9F2C', bssid: '38:DE:…:9A', ch: 11, rssi: -67, enc: 'WPA2' },
        { ssid: 'BendigoFreeWiFi', bssid: '7C:21:…:E0', ch: 1, rssi: -74, enc: 'OPEN' },
        { ssid: '(hidden)', bssid: 'D0:04:…:7B', ch: 6, rssi: -81, enc: 'WPA3' },
        { ssid: 'NETGEAR-Guest', bssid: '9C:3D:…:44', ch: 9, rssi: -84, enc: 'WPA2' },
      ],
      blePersist: { mac: 'C8:2B:…:1F', kind: 'tile-like beacon', sightings: 4, spanMin: 38, lastSeenMin: 122 },
      hidPayloads: [
        { id: 'recon_basic', desc: 'win · sysinfo + netstat dump', risk: 'high' },
        { id: 'wifi_grab', desc: 'win · export wlan profiles', risk: 'high' },
      ],
      marauderHw: true,
      auditAllowlist: ['MZ-LAB', 'MZ-TEST'],
    },
    system: {
      radio: 'ap',
      broker: { ok: true, clients: 38, msgs: 142 },
      homeSync: 'pending · awaiting client mode',
      throttle: { uvNow: false, uvBoot: true, capNow: false, capBoot: true, raw: '0x50005' },
      services: (() => {
        const defs = [
          ['obd-telemetry', 0], ['can-watch', 0], ['gps-pub', 0], ['trip-computer', 0],
          ['alert-engine', 0], ['threshold-learn', 0], ['session-log', 0], ['dtc-poll', 0],
          ['rf-rtl433', 1], ['rf-dump1090', 0], ['rf-spectrum', 0], ['rf-audio', 0],
          ['rf-classify', 0], ['tpms-decode', 0], ['adsb-track', 0], ['flycatcher', 0],
          ['ble-watch', 7], ['wardrive', 0], ['kismet-bridge', 12], ['ghost-detect', 0],
          ['marauder-io', 0], ['wifi-audit', 0], ['hid-server', 0], ['sentry', 0],
          ['alpr', 3], ['vision', 2], ['flipper-link', 1], ['can-discovery', 0],
          ['vivi2-llm', 4], ['voice-stt', 0], ['tts-out', 0], ['mechanic', 0],
          ['weather-pull', 0], ['feeds-pull', 0], ['watchdog', 0], ['mqtt-broker', 0],
          ['dash-http', 0], ['home-sync', 9],
        ];
        return defs.map(([name, restarts]) => ({
          name, restarts,
          state: name === 'kismet-bridge' ? 'gave-up' : name === 'home-sync' ? 'waiting' : restarts > 3 ? 'flapping' : 'ok',
        }));
      })(),
      hwPresence: [
        ['obd-ii elm327', 'ok'], ['usb gps', 'ok'], ['rtl-sdr v3', 'ok'],
        ['bt hci0', 'down'], ['esp32 marauder', 'ok'], ['flipper zero', 'absent'],
        ['spi lcd 3.5"', 'ok'], ['cam front', 'ok'],
      ],
    },
    chat: [
      { who: 'op', msg: 'why is the RR tire low?', t: '07:02' },
      { who: 'vivi', msg: 'RR has lost ~14 kPa over 9 days — consistent with a slow bead or valve leak, not a puncture (no rapid drop events). Re-torque and soap-test at the next stop; pressure alerts will re-arm automatically.', t: '07:02' },
      { who: 'op', msg: 'and the P0171?', t: '07:04' },
      { who: 'vivi', msg: 'Lean on bank 1 with LTFT +5.8%. Top suspects: intake leak after MAF, then MAF drift. STFT spikes at idle support an unmetered-air leak. Smoke test first — cheap and conclusive.', t: '07:05' },
    ],
    hist: { speed: [], rpm: [], coolant: [], voltage: [] },
    sysCpu: 23, sysMem: 41, sysTemp: 52,
  };
}

export function createSim() {
  const state = freshState();
  let nextAlertId = 10;
  const subs = new Set();

  function pushHist(k, v) {
    const h = state.hist[k];
    h.push(v);
    if (h.length > HIST) h.shift();
  }

  function drive(dt) {
    const t = state.t;
    const sc = state.hw.ecu === 'ok' ? state.scenario : 'idle';
    let target;
    if (sc === 'idle') target = 0;
    else if (sc === 'spirited') target = 78 + noise(t * 1.7, 3) * 55;
    else target = 52 + noise(t * 0.8, 1) * 32;
    if (sc === 'drive' && Math.sin(t * 0.11) > 0.82) target = 0;
    target = clamp(target, 0, sc === 'spirited' ? 160 : 110);

    const accel = sc === 'spirited' ? 22 : 11;
    const diff = target - state.speed;
    state.speed = clamp(state.speed + clamp(diff, -accel * dt * 2.2, accel * dt), 0, 180);

    state.gear = gearFor(state.speed);
    state.throttle = clamp(diff / 25 + 0.18, 0, 1);

    const ratios = { N: 0, 1: 95, 2: 52, 3: 33, 4: 24, 5: 19 };
    const base = state.gear === 'N' ? 780 : state.speed * ratios[state.gear];
    const idle = 760 + noise(t * 3, 7) * 40;
    state.rpm = clamp(Math.max(base, idle) + state.throttle * 260, 650, 6900);

    const load = state.rpm / 6500;
    state.coolant = clamp(state.coolant + (86 + load * 9 - state.coolant) * dt * 0.05, 70, 112);
    state.voltage = clamp(14.2 - load * 0.35 + noise(t * 2.2, 11) * 0.12, 11.4, 14.6);

    if (state.speed > 1) {
      const km = (state.speed / 3600) * dt;
      state.trip.km += km;
      const lph = 0.9 + state.throttle * 9.5;
      state.trip.fuel += (lph / 3600) * dt;
      state.trip.cost = state.trip.fuel * 1.89;
      state.odo += km;
    }
    state.trip.durS += dt;
    state.trip.l100 = state.trip.km > 0.05 ? (state.trip.fuel / state.trip.km) * 100 : 0;

    state.heading = (state.heading + noise(t * 0.4, 13) * 14 * dt + (state.speed > 2 ? dt * 2 : 0)) % 360;
    const mps = state.speed / 3.6;
    state.gps.lat += Math.cos((state.heading / 360) * TAU) * mps * dt * 9e-6;
    state.gps.lon += Math.sin((state.heading / 360) * TAU) * mps * dt * 1.1e-5;
    state.gps.acc = 3 + Math.abs(noise(t, 17)) * 3;

    const sp = state.rf.spectrum;
    for (let i = 0; i < sp.length; i++) {
      let v = -78 + Math.abs(noise(t * 4 + i * 0.6, i)) * 10;
      if (i === 21) v = -38 + noise(t * 6, 21) * 6;
      if (i === 44) v = -52 + noise(t * 5, 44) * 8;
      if (i === 9 && Math.sin(t * 0.7) > 0.4) v = -47;
      sp[i] = sp[i] * 0.7 + v * 0.3;
    }
    state.rf.peakDb = Math.max(...sp);
    state.rf.peakMhz = [433.92, 433.92, 1090, 315.0][Math.abs(Math.round(noise(t * 0.2, 29) * 1.6)) % 4];
    if (Math.random() < dt * 0.4) state.rf.hits += 1;
    state.rf.adsb = 4 + Math.round(Math.abs(noise(t * 0.3, 31)) * 5);
    for (const ac of state.rf.aircraft) {
      ac.brg = (ac.brg + dt * (ac.ghost ? 9 : 4)) % 360;
      ac.rng = Math.max(0.12, Math.min(0.92, ac.rng + ac.spd * dt));
      if (ac.rng >= 0.92 || ac.rng <= 0.12) ac.spd = -ac.spd;
    }

    state.sysCpu = clamp(22 + Math.abs(noise(t * 1.1, 37)) * 30, 5, 96);
    state.sysMem = clamp(40 + noise(t * 0.2, 41) * 8, 20, 90);
    state.sysTemp = clamp(50 + load * 14 + noise(t, 43) * 4, 35, 85);
    state.recon.wardrive.forEach((n, i) => { if (!n.own) n.rssi = Math.round(clamp(n.rssi + noise(t * 0.8, 53 + i) * 0.8, -90, -50)); });

    for (const a of state.alerts) a.ageS += dt;

    pushHist('speed', state.speed);
    pushHist('rpm', state.rpm);
    pushHist('coolant', state.coolant);
    pushHist('voltage', state.voltage);
  }

  function fireAlert() {
    const pool = [
      { sev: 'crit', code: 'COOLANT', msg: 'coolant 106°C · above amber threshold 104' },
      { sev: 'warn', code: 'VOLT-DIP', msg: 'voltage dipped 11.8V under load · alternator suspect' },
      { sev: 'warn', code: 'RF-REPLAY', msg: 'repeated keyfob burst ×4 @ 433.92M · possible replay' },
      { sev: 'info', code: 'ANOMALY', msg: 'intake temp deviates from learned envelope' },
    ];
    const a = pool[Math.floor(Math.random() * pool.length)];
    state.alerts.unshift({ ...a, id: nextAlertId++, ageS: 0, ack: false });
    if (state.alerts.length > 6) state.alerts.pop();
  }

  let last = performance.now();
  let raf = null;
  let acc = 0;
  function loop(now) {
    const dt = Math.min((now - last) / 1000, 0.1);
    last = now;
    state.t += dt;
    drive(dt);
    acc += dt;
    if (acc >= 0.12) {
      acc = 0;
      subs.forEach((fn) => fn(state));
    }
    raf = requestAnimationFrame(loop);
  }

  return {
    real: false,
    subscribe(fn) {
      subs.add(fn);
      if (!raf) { last = performance.now(); raf = requestAnimationFrame(loop); }
      fn(state);
      return () => subs.delete(fn);
    },
    getState: () => state,
    setScenario(s) { state.scenario = s; },
    setMode(m) { state.mode = m; state.autoDemoted = false; },
    setHw(k, v) { state.hw[k] = v; },
    setLink(v) { state.link = v; },
    autoDemote() { state.mode = 'diag'; state.autoDemoted = true; },
    fireAlert,
  };
}

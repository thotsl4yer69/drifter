// DRIVE display evidence only. Never commands hardware or changes backend state.
// Freshness is time since receipt, not proof of physical sensor accuracy.
export const DISPLAY_TTL = Object.freeze({ speed: 3000, rpm: 3000, coolant: 10000, voltage: 10000, perception: 2500 });
const METRICS = Object.freeze({
  'drifter/vehicle/speed': 'speed', 'drifter/engine/rpm': 'rpm',
  'drifter/engine/coolant': 'coolant', 'drifter/power/voltage': 'voltage',
});
const EVENTS = Object.freeze({
  'drifter/vision/object': 'objects', 'drifter/vision/fcw/warning': 'fcw',
  'drifter/vision/perception/event': 'event',
});
export function finiteNumber(value) {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  if (typeof value === 'string' && value.trim() !== '') {
    const n = Number(value); return Number.isFinite(n) ? n : null;
  }
  return null;
}
export function emptyDisplayState() { return { samples: {}, events: {} }; }
export function invalidateDisplay(state) { state.display = emptyDisplayState(); }

export function observeDisplayFrame(state, topic, data, now = Date.now(), cached = false) {
  // REST snapshots are cached. They must never refresh a live-data timestamp.
  // Aggregate snapshot fields have no per-PID acquisition time: wait for PID topics.
  if (cached || !Number.isFinite(now)) return;
  const display = state.display || (state.display = emptyDisplayState());
  const metric = Object.prototype.hasOwnProperty.call(METRICS, topic) ? METRICS[topic] : null;
  if (metric) {
    const value = finiteNumber(data && typeof data === 'object' ? data.value : data);
    if (value !== null) display.samples[metric] = { value, receivedAt: now };
  }
  const event = Object.prototype.hasOwnProperty.call(EVENTS, topic) ? EVENTS[topic] : null;
  if (event) {
    const valid = data && typeof data === 'object' && !Array.isArray(data);
    display.events[event] = valid ? { data, receivedAt: now } : null;
  }
}
function ageOf(packet, now) {
  if (!packet || !Number.isFinite(packet.receivedAt)) return Infinity;
  const age = now - packet.receivedAt;
  return age >= 0 ? age : Infinity; // clock rollback must not keep stale data alive
}
export function metricView(state, key, now = Date.now(), demo = false) {
  if (state?.link !== 'live') return { value: null, status: 'LINK LOST', age: null };
  if (demo) {
    const value = finiteNumber(state?.[key]);
    return { value, status: value === null ? 'NO SAMPLE' : 'TEST DATA', age: 0 };
  }
  const packet = state?.display?.samples?.[key];
  if (!packet) return { value: null, status: 'WAITING', age: null };
  const age = ageOf(packet, now);
  if (age > (DISPLAY_TTL[key] ?? 0)) return { value: null, status: 'STALE', age };
  return { value: finiteNumber(packet.value), status: 'LIVE OBD', age };
}
function recentEvent(state, key, now) {
  const packet = state?.display?.events?.[key];
  if (ageOf(packet, now) > DISPLAY_TTL.perception) return null;
  const data = packet.data;
  // Producers use Unix seconds in `ts`. Also tolerate explicit millisecond clocks.
  // A retained/replayed warning with an old or invalid source timestamp is not live.
  if (Object.prototype.hasOwnProperty.call(data, 'ts')) {
    const ts = finiteNumber(data.ts);
    if (ts === null || ts <= 0) return null;
    const sourceAge = now - (ts < 1e12 ? ts * 1000 : ts);
    if (sourceAge < -1000 || sourceAge > DISPLAY_TTL.perception) return null;
  }
  return data;
}
export function perceptionView(state, now = Date.now()) {
  const p = state?.perception || {};
  const backend = String(p.backend || '').toLowerCase();
  const backendLabel = backend === 'hailo' ? 'HAILO' : backend === 'onnx' ? 'CPU ONNX' : backend ? backend.toUpperCase() : 'UNVERIFIED';
  const base = {
    tone: 'off',
    headline: 'VISION UNAVAILABLE',
    detail: 'Vehicle telemetry does not depend on vision.',
    badge: 'NO VISION',
    hailo: backend === 'hailo' ? 'ACTIVE' : backend === 'onnx' ? 'CPU' : 'UNVERIFIED',
    camera: p.vision === 'online' ? 'STREAM' : 'UNKNOWN',
  };
  if (state?.link !== 'live') return { ...base, headline: 'VISION LINK LOST', badge: 'NO LIVE DATA' };
  if (p.vision !== 'online') return base;
  const fcw = recentEvent(state, 'fcw', now);
  const event = recentEvent(state, 'event', now);
  const warning = fcw && fcw.active !== false ? fcw :
    event && ['warn', 'warning', 'crit', 'critical'].includes(event.severity) ? event : null;
  if (warning) {
    const ttc = finiteNumber(warning.ttc_s);
    const distance = finiteNumber(warning.distance_m);
    return { ...base, tone: 'hazard', headline: 'REPORTED ROAD HAZARD',
      badge: ttc !== null && ttc >= 0 ? `${ttc.toFixed(1)}s TTC` : 'CHECK ROAD',
      detail: `${distance !== null && distance >= 0 ? distance.toFixed(1) + ' m · ' : ''}Experimental context; not collision protection.` };
  }
  const frame = recentEvent(state, 'objects', now);
  const objects = Array.isArray(frame?.objects) ? frame.objects.filter(o => o && typeof o === 'object' && typeof o.class === 'string').slice(0, 12) : [];
  if (objects.length) return { ...base, tone: 'reported', headline: 'DETECTIONS REPORTED',
    badge: `${objects.length} OBJECT${objects.length === 1 ? '' : 'S'}`, camera: 'STREAM',
    detail: `${objects[0].class.slice(0, 40)} · ${backendLabel} inference report.` };
  // An online backend means the service/model initialised; an empty recent
  // detection window still never means the road/scene is clear.
  return { ...base, headline: 'VISION ACTIVE', badge: 'MONITORING',
    detail: `${backendLabel} backend online · no recent detections.` };
}
export function hardwareView(state, now = Date.now(), demo = false) {
  const linked = state?.link === 'live';
  const p = perceptionView(state, now);
  const obd = ['speed', 'rpm', 'coolant', 'voltage'].some(k => metricView(state, k, now, demo).value !== null);
  const status = value => linked ? value : 'STALE';
  return [
    ['OBD', status(obd ? 'DATA' : 'WAITING')],
    ['GPS', status(state?.hw?.gps === 'fix' ? 'FIX' : 'NO FIX')],
    ['HAILO', status(p.hailo)], ['CAM', status(p.camera)],
    ['SDR', status(state?.hw?.sdr === 'ok' ? 'DETECTED' : 'NO DEVICE')],
    ['REC', status(state?.perception?.dashcam === 'recording' ? 'RECORDING' :
      state?.perception?.dashcam === 'monitoring' ? 'MONITORING' :
      state?.perception?.dashcam === 'ready' || state?.perception?.dashcam === 'online' ? 'READY' : 'UNKNOWN')],
    ['LINK', linked ? 'LIVE' : 'LOST'],
  ];
}
export function readPreference(key, fallback, allowed) {
  try { const v = localStorage.getItem(key); return allowed.includes(v) ? v : fallback; }
  catch { return fallback; }
}
export function writePreference(key, value) {
  try { localStorage.setItem(key, value); } catch { /* kiosk storage may be unavailable */ }
}

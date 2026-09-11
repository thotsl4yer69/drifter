// Vehicle readings keep their observation time across REST and WS delivery.
export const VEHICLE_TTL = 15;
const metrics = {
  'drifter/engine/rpm': 'rpm', 'drifter/engine/coolant': 'coolant',
  'drifter/engine/throttle': 'throttle', 'drifter/power/voltage': 'voltage',
  'drifter/vehicle/speed': 'speed',
};
export function expireVehicleData(state, now = Date.now() / 1000) {
  if (!Number.isFinite(state.dtcSampleTs) || now - state.dtcSampleTs > 90) state.dtcAvailable = false;
  for (const key of Object.values(metrics)) {
    const ts = state.vehicleSampleTs?.[key];
    if (!Number.isFinite(ts) || now - ts > VEHICLE_TTL || ts - now > 5) state[key] = null;
  }
  state.hw.ecu = ['rpm', 'speed', 'coolant'].some(k => state[k] != null) ? 'ok' : 'pending';
  if (state.hw.ecu !== 'ok' && state.trip) state.trip.l100 = null;
}
export function applyVehicleTopic(state, topic, data, now = Date.now() / 1000) {
  if (!(topic in metrics) && topic !== 'drifter/snapshot') return false;
  if (!data || typeof data !== 'object') return true;
  if (!['obd_bridge', 'can_bridge'].includes(data.source)) return true;
  state.vehicleSampleTs ??= {};
  function record(key, value, ts) {
    if (typeof value !== 'number' || !Number.isFinite(value) || !Number.isFinite(ts)) return;
    if (now - ts > VEHICLE_TTL || ts - now > 5 || ts <= (state.vehicleSampleTs[key] ?? -Infinity)) return;
    state[key] = key === 'throttle' ? value / 100 : value;
    state.vehicleSampleTs[key] = ts;
    if (state.hist[key]) {
      state.hist[key].push(value);
      if (state.hist[key].length > 90) state.hist[key].shift();
    }
  }
  if (topic === 'drifter/snapshot') {
    for (const key of Object.values(metrics)) record(key, data[key], data.sample_ts?.[key] ?? data.ts);
  } else record(metrics[topic], data.value, data.ts);
  expireVehicleData(state, now);
  return true;
}

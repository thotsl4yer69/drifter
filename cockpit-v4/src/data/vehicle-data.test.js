import test from 'node:test';
import assert from 'node:assert/strict';
import { applyVehicleTopic, expireVehicleData } from './vehicle-data.js';

const state = () => ({ hw: {}, hist: { rpm: [], speed: [], coolant: [], voltage: [] } });
const sample = (value, ts = 100) => ({ value, ts, source: 'obd_bridge' });

test('measured speed works without a GPS fix', () => {
  const s = state();
  applyVehicleTopic(s, 'drifter/vehicle/speed', sample(42), 100);
  assert.equal(s.speed, 42);
  assert.equal(s.hw.ecu, 'ok');
  assert.equal(s.gps, undefined);
});
test('individual samples expire even while another PID keeps arriving', () => {
  const s = state();
  applyVehicleTopic(s, 'drifter/engine/rpm', sample(800), 100);
  applyVehicleTopic(s, 'drifter/vehicle/speed', sample(0, 116), 116);
  assert.equal(s.rpm, null);
  assert.equal(s.speed, 0);
  expireVehicleData(s, 132);
  assert.equal(s.speed, null);
  assert.equal(s.hw.ecu, 'pending');
});
test('fresh snapshot envelope cannot renew a stale observation', () => {
  const s = state();
  applyVehicleTopic(s, 'drifter/snapshot', {
    rpm: 800, speed: 0, ts: 120, sample_ts: { rpm: 100, speed: 119 }, source: 'obd_bridge',
  }, 120);
  assert.equal(s.rpm, null);
  assert.equal(s.speed, 0);
});
test('low throttle remains a percentage', () => {
  const s = state();
  applyVehicleTopic(s, 'drifter/engine/throttle', sample(1), 100);
  assert.equal(s.throttle, 0.01);
});
test('cached duplicates do not grow history or overwrite newer data', () => {
  const s = state();
  applyVehicleTopic(s, 'drifter/engine/rpm', sample(900, 101), 101);
  applyVehicleTopic(s, 'drifter/engine/rpm', sample(800), 102);
  applyVehicleTopic(s, 'drifter/engine/rpm', sample(900, 101), 102);
  assert.deepEqual(s.hist.rpm, [900]);
  assert.equal(s.rpm, 900);
});
test('malformed and synthetic readings never enable the ECU indicator', () => {
  const s = state();
  for (const data of [null, { value: 800, ts: 100 }, { ...sample(800), source: 'fuzz' }, sample(NaN)]) {
    applyVehicleTopic(s, 'drifter/engine/rpm', data, 100);
  }
  expireVehicleData(s, 100);
  assert.equal(s.hw.ecu, 'pending');
  assert.equal(s.rpm, null);
});

import test from 'node:test';
import assert from 'node:assert/strict';
import { DISPLAY_TTL, finiteNumber, emptyDisplayState, observeDisplayFrame, invalidateDisplay, metricView, perceptionView, hardwareView, readPreference, writePreference } from '../src/data/display-state.js';
const NOW = 1790500000000;
const state = () => ({ link: 'live', hw: { gps: 'none' }, perception: { vision: 'online' }, display: emptyDisplayState() });
const frame = (s, key, data, at = NOW, cached = false) => observeDisplayFrame(s, key, data, at, cached);
const event = (s, type, data, at = NOW) => frame(s, `drifter/vision/${type}`, data, at);
for (const value of [null, undefined, '', ' ', true, false, NaN, Infinity, {}, []]) {
  test(`reject non-measurement ${String(value)} (${typeof value})`, () => assert.equal(finiteNumber(value), null));
}
for (const value of [0, '0', 12.4, '12.4', -3]) {
  test(`preserve finite measurement ${JSON.stringify(value)}`, () => assert.equal(finiteNumber(value), Number(value)));
}
test('WS connection alone does not invent zero or infer speed from GPS', () => {
  const s = state(); s.speed = 0; s.hw.gps = 'fix';
  assert.deepEqual(metricView(s, 'speed', NOW), {value:null,status:'WAITING',age:null});
});
test('OBD speed renders without GPS and zero is a legitimate sample', () => {
  const s = state(); frame(s, 'drifter/vehicle/speed', 0);
  assert.equal(metricView(s, 'speed', NOW).value, 0);
});
test('each PID has independent freshness; RPM cannot invent coolant', () => {
  const s = state(); frame(s, 'drifter/engine/rpm', {value:'850'});
  assert.equal(metricView(s, 'rpm', NOW).value, 850);
  assert.equal(metricView(s, 'coolant', NOW).value, null);
});
for (const [key, topic] of [['speed','vehicle/speed'],['rpm','engine/rpm'],['coolant','engine/coolant'],['voltage','power/voltage']]) {
  test(`${key} expires at its own TTL without more frames`, () => {
    const s = state(); frame(s, `drifter/${topic}`, 42);
    assert.equal(metricView(s,key,NOW+DISPLAY_TTL[key]).value,42);
    assert.equal(metricView(s,key,NOW+DISPLAY_TTL[key]+1).status,'STALE');
    assert.equal(metricView(s,key,NOW+DISPLAY_TTL[key]+1).value,null);
  });
}
test('constant-valued samples refresh (stationary vehicle)', () => {
  const s = state(); frame(s, 'drifter/vehicle/speed', 0);
  frame(s, 'drifter/vehicle/speed', 0, NOW+2000);
  assert.equal(metricView(s,'speed',NOW+4000).value,0);
});
test('malformed sample cannot refresh an expired PID', () => {
  const s = state(); frame(s,'drifter/engine/rpm',800);
  frame(s,'drifter/engine/rpm',{value:null},NOW+4000);
  assert.equal(metricView(s,'rpm',NOW+4000).status,'STALE');
});
test('REST cache and aggregate snapshots cannot promote live values', () => {
  const s = state(); frame(s,'drifter/engine/rpm',900,NOW,true);
  frame(s,'drifter/snapshot',{rpm:950,speed:50},NOW);
  assert.equal(metricView(s,'rpm',NOW).value,null);
  frame(s,'drifter/engine/rpm',800);
  frame(s,'drifter/engine/rpm',9000,NOW+5000,true);
  assert.equal(metricView(s,'rpm',NOW+5000).status,'STALE');
});
test('lost link suppresses readings immediately and invalidation needs new samples', () => {
  const s = state(); frame(s,'drifter/engine/rpm',900); s.link='lost';
  assert.equal(metricView(s,'rpm',NOW).value,null);
  invalidateDisplay(s); s.link='live';
  assert.equal(metricView(s,'rpm',NOW).status,'WAITING');
});
test('clock rollback is not freshness', () => {
  const s = state(); frame(s,'drifter/engine/rpm',900);
  assert.equal(metricView(s,'rpm',NOW-1).status,'STALE');
});
test('unknown/inherited topic names do not mutate display', () => {
  const s=state(); for(const topic of ['__proto__','constructor','toString','other']) frame(s,topic,123);
  assert.deepEqual(s.display,emptyDisplayState());
});
test('simulation is explicit and never substitutes live missing data', () => {
  const s=state(); s.speed=70;
  assert.equal(metricView(s,'speed',NOW).value,null);
  assert.equal(metricView(s,'speed',NOW,true).status,'TEST DATA');
});
test('offline vision never implies a clear road', () => {
  const s=state(); s.perception.vision='offline';
  assert.equal(perceptionView(s,NOW).headline,'VISION UNAVAILABLE');
});
test('online service does not prove Hailo, camera or clear road', () => {
  const view=perceptionView(state(),NOW);
  assert.equal(view.headline,'VISION UNVERIFIED');
  assert.equal(view.hailo,'UNVERIFIED'); assert.equal(view.camera,'UNKNOWN');
});
test('empty detections do not prove a clear road', () => {
  const s=state(); event(s,'object',{objects:[],ts:NOW/1000});
  assert.equal(perceptionView(s,NOW).headline,'VISION UNVERIFIED');
});
test('fresh detections are reported and then expire', () => {
  const s=state(); event(s,'object',{objects:[null,{class:'car'}],ts:NOW/1000});
  assert.equal(perceptionView(s,NOW).badge,'1 OBJECT');
  assert.equal(perceptionView(s,NOW+2501).headline,'VISION UNVERIFIED');
});
test('old source ts cannot be revived by receiving a retained detection', () => {
  const s=state(); event(s,'object',{objects:[{class:'car'}],ts:(NOW-9000)/1000});
  assert.equal(perceptionView(s,NOW).headline,'VISION UNVERIFIED');
});
for(const ts of [null,0,'bad',(NOW+2000)/1000]) {
  test(`invalid/future source timestamp ${ts} cannot activate FCW`, () => {
    const s=state(); event(s,'fcw/warning',{active:true,ttc_s:1,ts});
    assert.notEqual(perceptionView(s,NOW).tone,'hazard');
  });
}
test('active FCW is reported, formats TTC and expires', () => {
  const s=state(); event(s,'fcw/warning',{active:true,ttc_s:1.2,distance_m:8,ts:NOW/1000});
  assert.equal(perceptionView(s,NOW).badge,'1.2s TTC');
  assert.equal(perceptionView(s,NOW).tone,'hazard');
  assert.notEqual(perceptionView(s,NOW+2501).tone,'hazard');
});
test('inactive FCW explicitly clears a warning', () => {
  const s=state(); event(s,'fcw/warning',{active:true}); event(s,'fcw/warning',{active:false});
  assert.notEqual(perceptionView(s,NOW).tone,'hazard');
});
for(const severity of ['warn','warning','crit','critical']) {
  test(`${severity} perception events surface`,()=> {
    const s=state();event(s,'perception/event',{severity,ts:NOW/1000});
    assert.equal(perceptionView(s,NOW).tone,'hazard');
  });
}
test('non-hazard event does not escalate', () => {
  const s=state();event(s,'perception/event',{severity:'info'});
  assert.notEqual(perceptionView(s,NOW).tone,'hazard');
});
test('non-finite TTC does not render NaN or Infinity',()=>{
  const s=state();event(s,'fcw/warning',{active:true,ttc_s:Infinity,distance_m:-1});
  assert.equal(perceptionView(s,NOW).badge,'CHECK ROAD');
});
test('lost link suppresses retained hazards and hardware green states', () => {
  const s=state();event(s,'fcw/warning',{active:true});s.link='lost';
  assert.equal(perceptionView(s,NOW).headline,'VISION LINK LOST');
  assert.ok(hardwareView(s,NOW).every(([key,v])=>key==='LINK'?v==='LOST':v==='STALE'));
});
test('dashcam online means READY, not RECORDING', () => {
  const s=state();s.perception.dashcam='online';
  assert.equal(hardwareView(s,NOW).find(([k])=>k==='REC')[1],'READY');
  s.perception.dashcam='recording';
  assert.equal(hardwareView(s,NOW).find(([k])=>k==='REC')[1],'RECORDING');
});
test('denied or corrupt storage never stops screen navigation', () => {
  globalThis.localStorage={getItem(){throw Error('denied');},setItem(){throw Error('full');}};
  assert.equal(readPreference('surface','cockpit',['cockpit']),'cockpit');
  assert.doesNotThrow(()=>writePreference('surface','map'));
  globalThis.localStorage={getItem(){return 'invalid';}};
  assert.equal(readPreference('surface','cockpit',['cockpit']),'cockpit');
  delete globalThis.localStorage;
});

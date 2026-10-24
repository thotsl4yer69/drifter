// Executes the actual adapter with isolated fake I/O; does not use a Pi or ECU.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {metricView,perceptionView} from '../src/data/display-state.js';
const moduleURL=new URL('../src/data/display-state.js',import.meta.url).href;
const source=await fs.readFile(new URL('../src/data/adapter.js',import.meta.url),'utf8');
const fixture=()=>({hw:{},power:{},rf:{},gps:{},vivi:{},perception:{},recon:{},system:{},hist:{}});
let now=1790500000000;
let cache={engine_rpm:9999,vehicle_speed:88,vision_object:{objects:[{class:'car'}]}};
let socket;
globalThis.location={search:'',protocol:'http:',hostname:'fixture.local'};
globalThis.window={addEventListener(){}};
Object.defineProperty(globalThis,'navigator',{configurable:true,value:{onLine:true}});
globalThis.WebSocket=class {constructor(){socket=this;}close(){this.onclose?.();}};
globalThis.fetch=async()=>({ok:true,json:async()=>cache});
const oldNow=Date.now, oldTimeout=globalThis.setTimeout, oldInterval=globalThis.setInterval;
Date.now=()=>now;
globalThis.setTimeout=()=>0;globalThis.setInterval=()=>0;
globalThis.__displayFixture=fixture;
const prepared=source.replace("import { createSim, freshState } from './sim.js';",'const freshState=globalThis.__displayFixture; const createSim=()=>{throw Error("unexpected simulation")};')
 .replace("from './display-state.js'",`from '${moduleURL}'`);
const {DrifterSim}=await import('data:text/javascript;base64,'+Buffer.from(prepared).toString('base64'));
const s=DrifterSim.getState();
const send=(topic,data)=>socket.onmessage({data:JSON.stringify({topic,data})});
const settle=()=>new Promise(resolve=>setImmediate(resolve));

test('adapter cold-start REST cache is not live PID or camera evidence',async()=>{
  socket.onopen();await settle();
  assert.equal(metricView(s,'rpm',now).status,'WAITING');
  assert.equal(metricView(s,'speed',now).status,'WAITING');
  assert.deepEqual(s.display.events,{});
});
test('real WS topic paths feed independent live gauges without GPS',()=>{
  send('drifter/vehicle/speed',51);send('drifter/engine/rpm',{value:850});
  assert.equal(metricView(s,'speed',now).value,51);
  assert.equal(metricView(s,'rpm',now).value,850);
  assert.equal(metricView(s,'voltage',now).value,null);
});
test('malformed WebSocket frame leaves actual adapter usable',()=>{
  assert.doesNotThrow(()=>socket.onmessage({data:'not-json'}));
  send('drifter/power/voltage',{value:13.8});
  assert.equal(metricView(s,'voltage',now).value,13.8);
});
test('adapter rejects old retained FCW using its source timestamp',()=>{
  send('drifter/vision/status',{state:'online'});
  send('drifter/vision/fcw/warning',{active:true,ts:(now-10000)/1000});
  assert.equal(perceptionView(s,now).headline,'VISION ACTIVE');
});
test('disconnect clears receipt evidence and reconnect cannot resurrect it',async()=>{
  socket.onclose();assert.equal(s.link,'lost');
  assert.deepEqual(s.display.samples,{});assert.deepEqual(s.display.events,{});
  socket.onopen();await settle();
  assert.equal(metricView(s,'rpm',now).status,'WAITING');
  now+=1000;send('drifter/engine/rpm',900);
  assert.equal(metricView(s,'rpm',now).value,900);
});
process.on('exit',()=>{Date.now=oldNow;globalThis.setTimeout=oldTimeout;globalThis.setInterval=oldInterval;});

test('adapter records authoritative recon status and ALPR events',()=>{
  send('drifter/recon/status',{state:'online',session_id:'recon-1',event_count:5,evidence_count:4,chain_head:'abc'});
  send('drifter/vision/alpr/plate',{plate:'ABC123',confidence:0.9,ts:now/1000,camera_id:'front'});
  assert.equal(s.recon.status.sessionId,'recon-1');
  assert.equal(s.recon.status.eventCount,5);
  assert.equal(s.recon.status.evidenceCount,4);
  assert.equal(s.recon.recentPlates[0].plate,'ABC123');
});

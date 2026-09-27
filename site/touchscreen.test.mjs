import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {enhanceHome,touchscreenSection,previewProvenance,validatePreview} from './touchscreen-page.mjs';
const read=name=>readFileSync(new URL(name,import.meta.url),'utf8');
const original=read('home-source.html'), enhanced=enhanceHome(original), section=touchscreenSection();
const compiler=read('complete-site.mjs'), css=read('touchscreen-site.css');
const image=readFileSync(new URL(previewProvenance.image,import.meta.url));
test('insertion preserves the original homepage byte-for-byte outside the two intended additions',()=>{
 assert.equal(enhanced.replace(section+'\n ','').replace('<a class="text-cta touchscreen-jump" href="#touchscreen">See the touchscreen <span class="arrow" aria-hidden="true">↗</span></a>',''),original);
});
test('one labelled dashboard section and working discoverability anchor',()=>{
 assert.equal((enhanced.match(/id="touchscreen"/g)||[]).length,1);
 assert.ok(enhanced.includes('href="#touchscreen"'));
 assert.ok(section.includes('aria-labelledby="touchscreen-title"'));
 assert.ok(section.includes('id="touchscreen-title"'));
 assert.equal((enhanced.match(/<h1\b/g)||[]).length,1);
});
test('composition drift fails loudly rather than silently dropping or duplicating the section',()=>{
 assert.throws(()=>enhanceHome(original.replace('class="system section shell"','class="changed"')));
 assert.throws(()=>enhanceHome(enhanced));
 assert.throws(()=>enhanceHome(original+original));
 assert.throws(()=>enhanceHome(original.replace('class="hero-actions"','class="changed"')));
});
test('image provenance is pinned and cannot be presented as hardware evidence',()=>{
 assert.equal(previewProvenance.sourceCommit,'5b83a6a4243e38ff1888e4542a1b323388d07937');
 for(const text of ['SYNTHETIC TEST DATA','React 16 test harness','Not a physical Pi or vehicle capture','not the complete production React 18'])assert.ok(section.includes(text),text);
 assert.equal(createHash('sha256').update(image).digest('hex'),'7587830c1b4cee4101b054f8e93f7001cb5d4e34cf70b09b963c2383202de3d5');
});
test('preview has reviewed 800x480 static SVG source, reserved dimensions and accessible description',()=>{
 assert.doesNotThrow(()=>validatePreview(image));
 for(const text of ['width="800" height="480"','loading="lazy"','decoding="async"','aria-describedby="touchscreen-caption"','alt="DRIFTER'])assert.ok(section.includes(text),text);
});
test('invalid or wrong-sized preview fails before public output is replaced',()=>{
 for(const bad of [Buffer.alloc(0),Buffer.alloc(32),Buffer.alloc(64)])assert.throws(()=>validatePreview(bad));
 const wrong=Buffer.from(image.toString('utf8').replace('width="800"','width="400"'));assert.throws(()=>validatePreview(wrong));
 assert.ok(compiler.indexOf('validatePreview(touchscreenPreview)')<compiler.indexOf('await rm(out'));
});
test('all seven workspaces, three themes and research-mode boundaries are described',()=>{
 for(const name of ['DRIVE','MAP','DIAG','RF','FOOT','VIVI','SYSTEM','DAY','NIGHT','AMBER','operating-mode restrictions'])assert.ok(section.includes(name),name);
 assert.ok(!section.includes('<button'));
});
test('freshness is per-PID receipt, with valid zero and reconnect semantics',()=>{
 for(const text of ['3 seconds','10 seconds','fresh PID receipt','genuine zero','reconnected link needs new readings','OBD speed does not depend'])assert.ok(section.includes(text),text);
});
test('inference and physical acceptance remain explicitly unverified',()=>{
 for(const text of ['UNVERIFIED','still need implementation and validation','not collision protection','ten cold boots','30-minute telemetry run','remain physical acceptance work'])assert.ok(section.includes(text),text);
 assert.ok(!section.includes('data-status="passed"'));
});
test('compiler copies the exact image and fingerprints all new production inputs',()=>{
 for(const text of ["from './touchscreen-page.mjs'","read('touchscreen-site.css')","const home=enhanceHome(base)","write(previewProvenance.image,touchscreenPreview)","read('touchscreen-page.mjs'),touchscreenCSS",'.update(touchscreenPreview)','touchscreen:previewProvenance'])assert.ok(compiler.includes(text),text);
});
test('responsive image and touch links have scoped CSS',()=>{
 for(const text of ['.touchscreen-preview img{','width:100%','height:auto','@media(max-width:700px)','min-height:48px',':focus-visible'])assert.ok(css.includes(text),text);
});
test('engineering and field-guide links are substantive and are not fake submissions',()=>{
 assert.ok(section.includes('href="field-guide.html"'));
 assert.ok(section.includes('/docs/TOUCHSCREEN_REVIEW_2026-09-27.md'));
 assert.ok(section.includes('rel="noopener noreferrer"'));
 for(const text of ['<form','fetch(','localStorage','<script'])assert.ok(!section.includes(text),text);
});

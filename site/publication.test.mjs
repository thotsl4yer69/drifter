import test from 'node:test';
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {readFileSync,readdirSync} from 'node:fs';
import {dirname,join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {inflateSync} from 'node:zlib';
const root=dirname(fileURLToPath(import.meta.url));
const read=name=>readFileSync(join(root,'dist',name),'utf8');
const production='https://drifter-vim-js-projects-cdc6aae4.vercel.app/';
function build(env={}) {
  const e={...process.env};
  for(const k of ['SITE_URL','VERCEL_ENV','VERCEL_PROJECT_PRODUCTION_URL','VERCEL_URL'])delete e[k];
  return execFileSync(process.execPath,['build.mjs'],{cwd:root,env:{...e,...env},encoding:'utf8',stdio:['ignore','pipe','pipe']});
}
test('no-host build does not invent public URLs',()=>{
  build(); const s=read('index.html');
  assert.ok(!s.includes('rel="canonical"'));assert.ok(!readdirSync(join(root,'dist')).includes('sitemap.xml'));
});
test('original Field Recorder copy preserved, enquiry path enhanced',()=>{
 const s=read('index.html');
 assert.ok(s.includes('A memory'));assert.ok(s.includes('class="hero-apply"'));
 assert.match(s,/<a class="solid-btn" data-apply href="mailto:/);
 assert.ok(s.includes('id="jump-event"'));assert.ok(s.includes('const $$ ='));
});
test('distribution is an explicit public-file allowlist',()=>{
 const names=readdirSync(join(root,'dist'));
 assert.equal(names.length,10);assert.ok(!names.includes('build.mjs'));
 for(const p of ['index.html','press.html','privacy.html','404.html','og-card.png','brand-wordmark.svg','favicon.svg','manifest.webmanifest','version.json','robots.txt'])assert.ok(names.includes(p),p);
});
test('production URL generates aligned metadata and sitemap',()=>{
 build({SITE_URL:production,VERCEL_ENV:'production'});
 assert.ok(read('index.html').includes('rel="canonical" href="'+production+'"'));
 assert.ok(read('index.html').includes(production+'og-card.png'));
 assert.ok(read('sitemap.xml').includes(production+'privacy.html'));
 assert.ok(read('robots.txt').includes(production+'sitemap.xml'));
 assert.ok(!read('index.html').includes('thotsl4yer69.github.io'));
});
test('Vercel project production URL is accepted without protocol',()=>{
 build({VERCEL_PROJECT_PRODUCTION_URL:new URL(production).host});
 assert.ok(read('index.html').includes('rel="canonical" href="'+production+'"'));
});
test('preview is noindex and disallowed to crawlers',()=>{
 build({SITE_URL:production,VERCEL_ENV:'preview'});
 assert.ok(read('index.html').includes('noindex, nofollow'));
 assert.equal(read('robots.txt'),'User-agent: *\nDisallow: /\n');
});
test('malformed or credentialed public site URL fails rather than leaking',()=>{
 for(const url of ['javascript:alert(1)','https://user:secret@host.test/','http://public.test/','https://host.test/?secret=1'])assert.throws(()=>build({SITE_URL:url}));
});
test('generated social card is a decodable 1200 by 630 PNG',()=>{
 const b=readFileSync(join(root,'dist','og-card.png'));
 assert.deepEqual([...b.subarray(0,8)],[137,80,78,71,13,10,26,10]);
 assert.equal(b.readUInt32BE(16),1200);assert.equal(b.readUInt32BE(20),630);
 const data=[];let offset=8;
 while(offset<b.length){const length=b.readUInt32BE(offset),type=b.toString('ascii',offset+4,offset+8);if(type==='IDAT')data.push(b.subarray(offset+8,offset+8+length));offset+=length+12;}
 assert.equal(offset,b.length);assert.equal(inflateSync(Buffer.concat(data)).length,630*(1200*3+1));
});
test('regenerate portable output after environment-specific tests',()=>{
 build();assert.ok(read('version.json').includes('Field Recorder 01.1'));
});

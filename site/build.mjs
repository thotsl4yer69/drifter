/** DRIFTER static publisher. Node built-ins only. Never reads the vehicle runtime. */
import {readFile,writeFile,mkdir,rm,copyFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {dirname,join} from 'node:path';
import {createHash} from 'node:crypto';

const root=dirname(fileURLToPath(import.meta.url)), out=join(root,'dist');
const read=name=>readFile(join(root,name),'utf8');
const write=(name,data)=>writeFile(join(out,name),data);
const escape=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function replaceOnce(source,pattern,replacement,label){
  if(!pattern.test(source))throw new Error(`Source contract missing: ${label}`);
  return source.replace(pattern,(...args)=>typeof replacement === 'function' ? replacement(...args) : replacement);
}
function resolveSiteURL(){
  const value=process.env.SITE_URL || process.env.VERCEL_PROJECT_PRODUCTION_URL || '';
  if(!value)return null;
  const url=new URL(value.includes('://')?value:`https://${value}`);
  if(!['https:','http:'].includes(url.protocol)||url.username||url.password||url.search||url.hash)throw new Error('SITE_URL must be an HTTP(S) site URL without credentials, query or fragment.');
  if(url.protocol!=='https:'&&!['localhost','127.0.0.1'].includes(url.hostname))throw new Error('Public SITE_URL must use HTTPS.');
  return url.href.replace(/\/?$/,'/');
}
const siteURL=resolveSiteURL();
const app=await read('field-app.js'), tweaks=await read('field-refinements.css');
const wordmark=await read('brand-wordmark.svg');
const favicon='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" fill="#151713"/><path d="M20 15h11L18 49H7zM41 15h11L39 49H28z" fill="#eeede6"/></svg>';
await rm(out,{recursive:true,force:true});await mkdir(out,{recursive:true});

function meta(html,path){
  html=html.replace(/<meta\s+(?:name|property)="(?:og:image(?::[^\"]+)?|og:url|twitter:image|twitter:card)"[^>]*>/g,'').replace(/<link\s+rel="(?:canonical|manifest)"[^>]*>/g,'');
  let additions='<meta property="og:site_name" content="DRIFTER VIM by MAZLABZ"><meta property="og:locale" content="en_AU"><link rel="manifest" href="manifest.webmanifest">';
  if(siteURL){
    const url=new URL(path,siteURL).href, image=new URL('og-card.png',siteURL).href;
    additions+=`<link rel="canonical" href="${escape(url)}"><meta property="og:url" content="${escape(url)}"><meta property="og:image" content="${escape(image)}"><meta property="og:image:width" content="1200"><meta property="og:image:height" content="630"><meta property="og:image:alt" content="DRIFTER VIM — A memory for your machine. Field edition."><meta name="twitter:card" content="summary_large_image"><meta name="twitter:image" content="${escape(image)}">`;
  }else{ additions+='<meta name="twitter:card" content="summary">'; }
  if(process.env.VERCEL_ENV==='preview') additions+='<meta name="robots" content="noindex, nofollow">';
  return html.replace('</head>',additions+'\n</head>');
}
let home=await read('index.html');
if(!home.includes('A memory')||!home.includes('id="ribbon"'))throw new Error('Expected Field Recorder source. Refusing to build the old dashboard-card site.');
home=replaceOnce(home,/<script>[\s\S]*?<\/script>/,`<script>\n${app}\n</script>`,'homepage script');
home=replaceOnce(home,/<\/style>/,tweaks+'\n</style>','homepage styles');
home=replaceOnce(home,/<a class="text-cta" href="#context">[\s\S]*?<\/a>/,
  '<p class="product-definition">Raspberry Pi + OBD-II.<br>Local drive recording and incident capture.</p><div class="hero-actions"><a class="hero-apply" data-apply href="mailto:mazlabz.ai@gmail.com?subject=DRIFTER%20VIM%20field%20application">Join the field test <span class="arrow" aria-hidden="true">↗</span></a><a class="text-cta" href="#context">How recording works <span class="arrow" aria-hidden="true">↓</span></a></div>', 'hero enquiry route');
home=replaceOnce(home,/<button type="button" class="solid-btn" data-apply>([\s\S]*?)<\/button>/,
  (_whole,inside)=>'<a class="solid-btn" data-apply href="mailto:mazlabz.ai@gmail.com?subject=DRIFTER%20VIM%20field%20application">'+inside+'</a>', 'no-JS application link');
home=home.replace('<button class="play-btn" type="button"','<button class="play-btn" type="button" disabled').replace('id="time-range" type="range"','id="time-range" type="range" disabled').replace(/class="time-chapter" type="button"/g,'class="time-chapter" type="button" disabled');
home=replaceOnce(home,/<div class="transport-hint">([\s\S]*?)<\/div>\s*<\/div>\s*<\/section>/,
  (_whole,inside)=>'<div class="transport-hint">'+inside+'</div></div><div class="phase-explainer shell"><p id="phase-copy">The rolling buffer holds the lead-up. Capture has not been triggered.</p><button id="jump-event" class="jump-event" type="button">JUMP TO EVENT / 00</button></div></section>', 'recorder context');
home=home.replace('maxlength="1200"','maxlength="600"');
home=home.replace('nothing is sent until you send it from your email app.','nothing is sent until you send it from your email app. <span class="contact-line">Contact: mazlabz.ai@gmail.com</span>');
home=home.replace(/<div class="footer-links">/,'<div class="footer-links"><a href="privacy.html">Data &amp; contact ↗</a>');
home=home.replace('</main>','<noscript><div class="shell no-js-contact">The recorder illustration needs JavaScript. To apply without it, <a href="mailto:mazlabz.ai@gmail.com?subject=DRIFTER%20VIM%20field%20application">email your vehicle and hardware to MAZLABZ</a>.</div></noscript></main>');
// The no-JS timeline is explicitly illustrative; do not leave an inert button.
home=home.replace('<button id="jump-event"','<button disabled id="jump-event"');
const finalApp=home.replace("const play = $('.play-btn'), display", "$('#jump-event').disabled=false;\n  const play = $('.play-btn'), display");
await write('index.html',meta(finalApp,''));

let press=await read('press.html');
press=press.replace(/<a href="\.\/">/g,'<a href="./">');
press=press.replace('</head>',`<style>${tweaks}</style></head>`);
await write('press.html',meta(press,'press.html'));
const privacy=await read('privacy.html');await write('privacy.html',meta(privacy,'privacy.html'));
let notFound=await read('404.html');
// A nested invalid route must return to site root, never a relative parent loop.
notFound=notFound.replace(/href="\.\/"/g,`href="${siteURL?escape(siteURL):'/'}"`).replace('</head>','<meta name="robots" content="noindex"></head>');
await write('404.html',notFound);
await write('brand-wordmark.svg',wordmark);await write('favicon.svg',favicon);
await copyFile(join(root,'og-card.png'),join(out,'og-card.png'));
await write('manifest.webmanifest',JSON.stringify({name:'DRIFTER VIM — Field Recorder',short_name:'DRIFTER VIM',start_url:'./',scope:'./',display:'browser',background_color:'#eeede6',theme_color:'#151713',icons:[{src:'favicon.svg',sizes:'any',type:'image/svg+xml',purpose:'any'}]},null,2));
const preview=process.env.VERCEL_ENV==='preview';
await write('robots.txt',preview?'User-agent: *\nDisallow: /\n':`User-agent: *\nAllow: /\n${siteURL?'Sitemap: '+new URL('sitemap.xml',siteURL).href+'\n':''}`);
if(siteURL)await write('sitemap.xml','<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'+['','press.html','privacy.html'].map(p=>`<url><loc>${escape(new URL(p,siteURL).href)}</loc></url>`).join('')+'</urlset>\n');
const revision=process.env.VERCEL_GIT_COMMIT_SHA || 'local-build';
await write('version.json',JSON.stringify({edition:'Field Recorder 01.1',revision,sourceSHA256:createHash('sha256').update(await read('index.html')).digest('hex')},null,2));
console.log(`DRIFTER Field Recorder 01.1 built into ${out}.`);
console.log(siteURL?`Canonical site: ${siteURL}`:'No public URL supplied: no invented canonical URL or sitemap emitted. Vercel supplies the production URL at build time.');

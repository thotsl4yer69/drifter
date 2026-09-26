/** Build the complete Field Recorder website from local, allowlisted source files. */
import {readFile,writeFile,mkdir,rm} from 'node:fs/promises';
import {dirname,join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import {createAssets,zipFiles} from './assets.mjs';
import {makeSocialCard} from './social-card.mjs';
import * as pages from './pages.mjs';
export const root=dirname(fileURLToPath(import.meta.url));
export function resolveURL(env=process.env){const input=env.SITE_URL||env.VERCEL_PROJECT_PRODUCTION_URL||'';if(!input)return null;const url=new URL(input.includes('://')?input:'https://'+input);if(!['http:','https:'].includes(url.protocol)||url.username||url.password||url.search||url.hash)throw Error('SITE_URL must be a plain HTTP(S) site URL.');if(url.protocol!=='https:'&&!['localhost','127.0.0.1'].includes(url.hostname))throw Error('Public SITE_URL must use HTTPS.');return url.href.replace(/\/?$/,'/');}
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const json=s=>JSON.stringify(s).replace(/</g,'\\u003c');
export async function build(){
 const out=join(root,'dist'),read=name=>readFile(join(root,name),'utf8'),write=(name,data)=>writeFile(join(out,name),data);
 const host=resolveURL(),preview=process.env.VERCEL_ENV==='preview';
 const [base,refinements,extension,common,originalRenderer,wordmark]=await Promise.all(['home-source.html','field-refinements.css','complete.css','site.js','recorder-runtime.js','brand-wordmark.svg'].map(read));
 if(!base.includes('A memory')||!base.includes('id="ribbon"'))throw Error('Field Recorder homepage source is required.');
 const css=(await read('brand-base.css'))+'\n'+refinements+'\n'+extension;
 if(!css.includes('--paper'))throw Error('Field Recorder brand tokens are missing.');
 const renderer=originalRenderer;
 const assets=createAssets(wordmark);
 const factsPDF=await readFile(join(root,'assets/drifter-factsheet.pdf')),worksheetPDF=await readFile(join(root,'assets/drifter-field-worksheet.pdf'));
 if(factsPDF.toString('ascii',0,5)!=='%PDF-'||worksheetPDF.toString('ascii',0,5)!=='%PDF-')throw Error('Media PDFs are missing or invalid.');
 await rm(out,{recursive:true,force:true});await mkdir(join(out,'assets'),{recursive:true});
 function render(name,body,extra=''){
  // A disabled baseline prevents accidental GET submission when JavaScript is off.
  if(name==='apply.html')body=body.replace('class="solid-btn" type="submit"','class="solid-btn" type="submit" disabled');
  const info=pages.pageInfo[name],canonical=name==='index.html'?'':name;let meta=`<meta property="og:type" content="website"><meta property="og:site_name" content="DRIFTER VIM / MAZLABZ"><meta property="og:locale" content="en_AU"><meta property="og:title" content="${esc(info.title)} — DRIFTER VIM"><meta property="og:description" content="${esc(info.description)}"><meta name="twitter:card" content="${host?'summary_large_image':'summary'}">`;
  if(host&&name!=='404.html'){const url=new URL(canonical,host).href,img=new URL('og-card.png',host).href;meta+=`<link rel="canonical" href="${esc(url)}"><meta property="og:url" content="${esc(url)}"><meta property="og:image" content="${esc(img)}"><meta property="og:image:width" content="1200"><meta property="og:image:height" content="630"><meta property="og:image:alt" content="DRIFTER Field Recorder original wordmark."><meta name="twitter:image" content="${esc(img)}">`;meta+='<script type="application/ld+json">'+json({'@context':'https://schema.org','@type':'WebPage',name:info.title,url,description:info.description,isPartOf:{'@type':'WebSite',name:'DRIFTER VIM',url:host},publisher:{'@type':'Organization',name:'MAZLABZ'}})+'</script>';}
  if(preview||name==='404.html')meta+='<meta name="robots" content="noindex, nofollow">';
  return `<!doctype html><html lang="en-AU"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="theme-color" content="#151713"><title>${esc(info.title)} — DRIFTER VIM</title><meta name="description" content="${esc(info.description)}">${meta}<link rel="icon" href="favicon.svg" type="image/svg+xml"><link rel="manifest" href="manifest.webmanifest"><style>${css}</style><noscript><style>.menu-toggle{display:none!important}.site-header{height:auto;min-height:72px;flex-wrap:wrap;padding-block:16px}.nav{display:flex!important;position:static!important;flex-wrap:wrap;padding:0!important;border:0!important;background:transparent;gap:16px}.nav a{font-size:12px!important;min-height:44px;display:inline-flex;align-items:center}.nav-cta{margin:0!important}</style></noscript></head><body data-edition="01.2" data-page="${name}">${pages.header(name)}<main id="main">${body}</main>${pages.footer()}${extra}<script>${common}
document.querySelector('#apply-form button[type="submit"]')?.removeAttribute('disabled');</script>${name==='index.html'?'<script>'+renderer+'</script>':''}</body></html>`;
 }
 const home=base;
 const generated={'index.html':render('index.html',home),'recorder.html':render('recorder.html',pages.recorderPage()),'hardware.html':render('hardware.html',pages.hardwarePage()),'field-notes.html':render('field-notes.html',pages.fieldNotesPage()),'field-guide.html':render('field-guide.html',pages.fieldGuidePage()),'apply.html':render('apply.html',pages.applyPage()),'press.html':render('press.html',pages.pressPage(assets),'<script type="application/json" id="media-sources">'+json(Object.fromEntries(assets.map(a=>[a.file,a.svg])))+'</script>'),'privacy.html':render('privacy.html',pages.privacyPage()),'404.html':render('404.html','<section class="page-hero shell"><div class="page-rail mono">404 / Record not found</div><div class="page-heading"><h1>This one<br><em>got away.</em></h1><div class="page-lede"><p>There is no page at this address.</p><a class="solid-btn" href="'+(host?esc(host):'/')+'">Back to DRIFTER VIM ↗</a></div></div></section>')};
 // Nested 404 routes must not resolve assets or navigation below a missing path.
 if(host)generated['404.html']=generated['404.html'].replace('<head>','<head><base href="'+esc(host)+'">');else generated['404.html']=generated['404.html'].replace('<head>','<head><base href="/">');
 for(const [name,content]of Object.entries(generated))await write(name,content);
 const mediaEntries=assets.map(a=>[a.file,a.svg]);
 for(const [name,content]of mediaEntries)await write('assets/'+name,content);
 await write('assets/drifter-factsheet.pdf',factsPDF);await write('assets/drifter-field-worksheet.pdf',worksheetPDF);
 const notes='# DRIFTER VIM — Media kit / Field Recorder 01.2\n\nUse DRIFTER VIM — Vehicle Intelligence Module, by MAZLABZ.\n\nKeep the original wordmark proportions and at least half a letter-height of clear space. The inverse wordmark uses paper-coloured paths on a transparent background.\n\nIllustrations are not recorded telemetry, hardware photographs or evidence of compatibility. The time windows shown are the architecture settings; a full pre-event window requires time to accumulate and available data.\n\nThe project is a hardware-integrated prototype under active hardening. No customer, revenue, readiness or universal-compatibility claims are supplied.\n\nFor PNG exports, use the media page buttons. The SVG sources remain editable. No font files are included.\n\nSources reviewed 26 September 2026:\nhttps://github.com/thotsl4yer69/drifter/issues/67\nhttps://github.com/thotsl4yer69/drifter/pull/69\nhttps://github.com/thotsl4yer69/drifter/blob/main/docs/BETA_TESTER_GUIDE.md\nhttps://github.com/thotsl4yer69/drifter/blob/main/PROJECT_STATUS.md\n\nContact: mazlabz.ai@gmail.com\n';
 mediaEntries.push(['drifter-factsheet.pdf',factsPDF],['drifter-field-worksheet.pdf',worksheetPDF],['README.md',notes],['palette.json',JSON.stringify({paper:'#EEEDE6',ink:'#151713',muted:'#626B59',line:'#B6C0A9',event:'#FFB640'},null,2)]);
 await write('assets/drifter-media-kit.zip',zipFiles(mediaEntries));await write('assets/README.md',notes);
 await write('brand-wordmark.svg',wordmark);
 const favicon='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" fill="#151713"/><path d="M20 15h11L18 49H7zM41 15h11L39 49H28z" fill="#eeede6"/></svg>';
 await write('favicon.svg',favicon);await write('og-card.png',makeSocialCard(wordmark));
 await write('manifest.webmanifest',JSON.stringify({name:'DRIFTER VIM — Field Recorder',short_name:'DRIFTER VIM',start_url:'./',scope:'./',display:'browser',background_color:'#eeede6',theme_color:'#151713',icons:[{src:'favicon.svg',sizes:'any',type:'image/svg+xml',purpose:'any'}]},null,2));
 await write('robots.txt',preview?'User-agent: *\nDisallow: /\n':'User-agent: *\nAllow: /\n'+(host?'Sitemap: '+new URL('sitemap.xml',host).href+'\n':''));
 if(host)await write('sitemap.xml','<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'+Object.keys(generated).filter(n=>n!=='404.html').map(n=>'<url><loc>'+esc(new URL(n==='index.html'?'':n,host).href)+'</loc></url>').join('')+'</urlset>\n');
 const sourceHash=createHash('sha256').update([base,await read('brand-base.css'),refinements,extension,common,originalRenderer,wordmark,await read('pages.mjs'),await read('assets.mjs'),await read('complete-site.mjs'),await read('social-card.mjs')].join('\n')).update(factsPDF).update(worksheetPDF).digest('hex');
 await write('version.json',JSON.stringify({edition:pages.EDITION,revision:process.env.VERCEL_GIT_COMMIT_SHA||'local-build',sourceSHA256:sourceHash,pages:Object.keys(generated),assetCount:assets.length,sourceReviewed:'2026-09-26'},null,2));
 console.log(pages.EDITION+': '+Object.keys(generated).length+' pages, '+assets.length+' vector assets, 2 PDFs and a media ZIP.');
 console.log('Public output: '+out);console.log(host?'Canonical base: '+host:'No public URL configured: no invented canonical or sitemap.');
 return {out,pages:Object.keys(generated),assets};
}

/* DRIFTER Field Edition 01.1 — progressive enhancement, no network calls. */
(() => {
  'use strict';
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => [...document.querySelectorAll(s)];
  const reduce = matchMedia('(prefers-reduced-motion: reduce)');
  const menu = $('.menu-toggle'), nav = $('#navigation');
  const setMenu = (open) => {
    if (!menu || !nav) return;
    nav.classList.toggle('open', open);
    menu.setAttribute('aria-expanded', String(open));
    menu.textContent = open ? 'CLOSE −' : 'MENU +';
    document.body.classList.toggle('menu-open', open);
  };
  menu?.addEventListener('click', () => setMenu(menu.getAttribute('aria-expanded') !== 'true'));
  $$('.nav a').forEach(a => a.addEventListener('click', () => setMenu(false)));
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && menu?.getAttribute('aria-expanded') === 'true') {
      setMenu(false); menu.focus();
    }
  });
  matchMedia('(min-width: 761px)').addEventListener('change', e => { if (e.matches) setMenu(false); });

  // Keep real mail links as the baseline. Enhanced forms never claim to send.
  const dialog = $('#application'), form = $('#apply-form');
  let returnFocus = null;
  $$('[data-apply]').forEach(a => a.addEventListener('click', e => {
    if (typeof dialog?.showModal !== 'function') return;
    e.preventDefault(); returnFocus = a; setMenu(false);
    dialog.showModal(); document.body.classList.add('dialog-open');
    $('#vehicle').focus();
  }));
  $('[data-close]')?.addEventListener('click', () => dialog.close());
  dialog?.addEventListener('close', () => {
    document.body.classList.remove('dialog-open'); returnFocus?.focus({preventScroll:true});
  });
  dialog?.addEventListener('click', e => {
    if (e.target !== dialog) return;
    const b = dialog.getBoundingClientRect();
    if (e.clientX < b.left || e.clientX > b.right || e.clientY < b.top || e.clientY > b.bottom) dialog.close();
  });
  const announce = s => {
    const message = $('#form-message');
    if (message) { message.textContent = s; message.classList.add('show'); }
  };
  const fieldValue = id => $(id).value.trim();
  const validate = () => {
    if (!form) return false;
    for (const id of ['#vehicle', '#adapter', '#goal']) {
      const input = $(id);
      input.setCustomValidity(input.value.trim() ? '' : 'Please enter a value, not only spaces.');
    }
    return form.reportValidity();
  };
  form?.addEventListener('input', e => {
    if ('setCustomValidity' in e.target) e.target.setCustomValidity('');
    $('#form-message')?.classList.remove('show');
    $('#copy-area')?.classList.remove('show');
  });
  const applicationText = () => [
    'DRIFTER VIM — Field application', '',
    `Vehicle: ${fieldValue('#vehicle')}`,
    `OBD adapter: ${fieldValue('#adapter')}`,
    `Computer: ${fieldValue('#hardware')}`, '',
    'What I would like to test:', fieldValue('#goal'), '',
    'This is a prototype field-testing application, not an order.'
  ].join('\n');
  form?.addEventListener('submit', e => {
    e.preventDefault(); if (!validate()) return;
    const href = 'mailto:mazlabz.ai@gmail.com?subject=' + encodeURIComponent('DRIFTER VIM — Field application') + '&body=' + encodeURIComponent(applicationText());
    announce('Application prepared, not sent. Your email app may open next. Press Send there to submit it. Otherwise copy the application and email mazlabz.ai@gmail.com.');
    window.location.href = href;
  });
  $('#copy-application')?.addEventListener('click', async () => {
    if (!validate()) return;
    const text = applicationText();
    try {
      if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
      await navigator.clipboard.writeText(text);
      announce('Copied. Email the application to mazlabz.ai@gmail.com. Nothing has been submitted from this page.');
    } catch {
      const area = $('#copy-area'); area.value = text; area.classList.add('show'); area.focus(); area.select();
      announce('Select and copy the application below, then email it to mazlabz.ai@gmail.com.');
    }
  });

  const canvas = $('#ribbon'), scene = $('#scene'), range = $('#time-range');
  if (!canvas || !scene || !range) return;
  const ctx = canvas.getContext('2d', { alpha:false });
  const cache = document.createElement('canvas'), ink = cache.getContext('2d', { alpha:false });
  if (!ctx || !ink) return; // Static text remains usable on unsupported browsers.
  const play = $('.play-btn'), display = $('#time-display'), phase = $('#phase-name');
  const phaseCopy = $('#phase-copy'), chapters = $$('.time-chapter');
  let W=1, H=1, dpr=1, time=Number(range.value), yaw=0, pitch=0;
  let playing=false, raf=0, last=0, onscreen=true, dirty=true, holdUntil=0, lastSecond=null;
  let baseRenders=0, frames=0;
  const surface = (u,v) => ({x:u*330, y:79*Math.sin(u*4.55)*Math.exp(-.28*u*u)+38*Math.sin(v*2.9)*Math.exp(-2.3*u*u)+13*Math.cos(u*7+v)*Math.exp(-u*u), z:v*128});
  // The sculpture geometry is immutable. Build once, not on each animation tick.
  const ribbons = Array.from({length:88}, (_,k) => {
    const pts = Array.from({length:151}, (_,j) => surface(-1+2*j/150,-1+2*k/87));
    return {pts, back:pts.map(p=>({...p,z:p.z+2.4})).reverse()};
  });
  const project = p => {
    const a=-.18+yaw, b=.61+pitch;
    const x=p.x*Math.cos(a)-p.z*Math.sin(a), z=p.x*Math.sin(a)+p.z*Math.cos(a);
    const y=-p.y*Math.cos(b)+z*Math.sin(b), depth=p.y*Math.sin(b)+z*Math.cos(b);
    const s=Math.min(W/660,H/332)/(1+depth/1250);
    return {x:W*.5+x*s, y:H*.52+y*s};
  };
  const path = (c, points) => {
    c.beginPath(); points.forEach((p,i) => { const q=project(p); i?c.lineTo(q.x,q.y):c.moveTo(q.x,q.y); });
  };
  const clock = n => {
    const v=Math.abs(n);
    return (n<0?'−':n>0?'+':'') + String(Math.floor(v/60)).padStart(2,'0')+':'+String(v%60).padStart(2,'0');
  };
  function updateText() {
    const n=Math.round(time);
    if (n===lastSecond) return;
    lastSecond=n; range.value=String(n); display.textContent=clock(n);
    const which=n<0?0:n===0?1:2;
    phase.textContent=['Before the event','The capture moment','After the event'][which];
    if (phaseCopy) phaseCopy.textContent=[
      'The rolling buffer holds the lead-up. Capture has not been triggered.',
      'The trigger marks the event. The pre-event window is preserved.',
      'Recording continues for the post-event tail, up to 45 seconds.'
    ][which];
    range.setAttribute('aria-valuetext',n<0?`${-n} seconds before the event`:n===0?'The event':`${n} seconds after the event`);
    chapters.forEach((b,i) => b.setAttribute('aria-pressed',String(i===which)));
  }
  const requestDraw = () => { if (!raf && onscreen && !document.hidden) raf=requestAnimationFrame(frame); };
  function stop() {
    playing=false; play.classList.remove('playing'); play.setAttribute('aria-pressed','false');
    play.setAttribute('aria-label',reduce.matches?'Jump to the next capture chapter':'Play capture-window illustration');
  }
  function choose(value, scroll=false) {
    stop(); time=Math.max(-90,Math.min(45,value)); holdUntil=0; updateText(); requestDraw();
    if (scroll) $('#recorder').scrollIntoView({behavior:reduce.matches?'auto':'smooth',block:'start'});
  }
  function paintBase() {
    ink.setTransform(dpr,0,0,dpr,0,0); ink.fillStyle='#141713'; ink.fillRect(0,0,W,H);
    const glow=ink.createRadialGradient(W*.60,H*.49,0,W*.60,H*.49,W*.70);
    glow.addColorStop(0,'#283024'); glow.addColorStop(.56,'#1a2017'); glow.addColorStop(1,'#141713');
    ink.fillStyle=glow; ink.fillRect(0,0,W,H); ink.lineWidth=.55; ink.strokeStyle='rgba(176,190,160,.085)';
    for (let k=-5;k<=5;k++) { path(ink,[{x:-370,y:-119,z:k*42},{x:370,y:-119,z:k*42}]); ink.stroke(); }
    for (let k=-8;k<=8;k++) { path(ink,[{x:k*47,y:-119,z:-215},{x:k*47,y:-119,z:215}]); ink.stroke(); }
    ribbons.forEach(({pts,back},k) => {
      path(ink,[...pts,...back]); ink.closePath(); ink.fillStyle='rgba(6,10,5,.5)'; ink.fill(); path(ink,pts);
      const alpha=.22+.50*k/87, grad=ink.createLinearGradient(W*.05,0,W*.97,0);
      grad.addColorStop(0,`rgba(184,196,173,${alpha*.25})`); grad.addColorStop(.26,`rgba(237,239,224,${alpha})`);
      grad.addColorStop(.57,`rgba(242,245,231,${alpha*.88})`); grad.addColorStop(.87,`rgba(222,233,205,${alpha*.62})`);
      grad.addColorStop(1,`rgba(190,204,175,${alpha*.15})`); ink.strokeStyle=grad; ink.lineWidth=.70; ink.stroke();
    });
    path(ink,Array.from({length:81},(_,i)=>surface(1/3,-1+2*i/80))); ink.strokeStyle='rgba(255,182,64,.40)'; ink.lineWidth=.8; ink.stroke();
    dirty=false; baseRenders++;
  }
  function draw() {
    if (dirty) paintBase();
    ctx.setTransform(dpr,0,0,dpr,0,0); ctx.drawImage(cache,0,0,W,H);
    const u=(time+90)/135*2-1;
    path(ctx,Array.from({length:81},(_,i)=>surface(u,-1+2*i/80))); ctx.strokeStyle='#ffb640'; ctx.lineWidth=1.8; ctx.stroke();
    const front=surface(u,1), q=project(front), base=project({...front,y:-119});
    ctx.setLineDash([2,4]); ctx.strokeStyle='rgba(255,182,64,.45)'; ctx.lineWidth=.75;
    ctx.beginPath(); ctx.moveTo(q.x,q.y); ctx.lineTo(base.x,base.y+10); ctx.stroke(); ctx.setLineDash([]);
    ctx.fillStyle='#ffb640'; ctx.fillRect(q.x-2.5,q.y-2.5,5,5);
    const fade=ctx.createLinearGradient(0,0,0,H);
    fade.addColorStop(0,'rgba(20,23,19,.4)'); fade.addColorStop(.20,'rgba(20,23,19,0)');
    fade.addColorStop(.77,'rgba(20,23,19,0)'); fade.addColorStop(1,'rgba(20,23,19,.65)');
    ctx.fillStyle=fade; ctx.fillRect(0,0,W,H); frames++;
    // QA counters; no analytics, tracking or network transmission.
    canvas.dataset.baseRenders=String(baseRenders); canvas.dataset.frames=String(frames);
  }
  function frame(now) {
    raf=0;
    if (playing) {
      const dt=Math.min((now-last)/1000,.1); last=now;
      if (now>=holdUntil) {
        const next=Math.min(45,time+dt*15);
        if (time<0 && next>=0) { time=0; holdUntil=now+700; } else time=next;
      }
      updateText(); if (time>=45) stop();
    }
    draw(); if (playing) requestDraw();
  }
  function resize() {
    const r=scene.getBoundingClientRect();
    const w=Math.max(1,r.width), h=Math.max(1,r.height), ratio=Math.min(devicePixelRatio||1,2);
    if (W===w && H===h && dpr===ratio && canvas.width>1) return;
    W=w; H=h; dpr=ratio;
    canvas.width=cache.width=Math.round(W*dpr); canvas.height=cache.height=Math.round(H*dpr);
    dirty=true; requestDraw();
  }
  range.disabled=false; play.disabled=false; chapters.forEach(b=>b.disabled=false);
  range.addEventListener('input',()=>choose(Number(range.value)));
  play.addEventListener('click',()=>{
    if (reduce.matches) { choose(time<0?0:time<45?45:-90); return; }
    if (playing) { stop(); return; }
    if (time>=45) time=-90;
    playing=true; holdUntil=0; last=performance.now();
    play.classList.add('playing'); play.setAttribute('aria-pressed','true'); play.setAttribute('aria-label','Pause capture-window illustration'); requestDraw();
  });
  chapters.forEach(b=>b.addEventListener('click',()=>choose(Number(b.dataset.time),true)));
  $('#jump-event')?.addEventListener('click',()=>choose(0));
  scene.addEventListener('pointermove',e=>{
    if (reduce.matches || e.pointerType==='touch') return;
    const r=scene.getBoundingClientRect();
    const a=(e.clientX-r.left-r.width/2)/r.width*.12, b=(e.clientY-r.top-r.height/2)/r.height*.10;
    if (Math.abs(a-yaw)<.002 && Math.abs(b-pitch)<.002) return;
    yaw=a; pitch=b; dirty=true; requestDraw();
  });
  scene.addEventListener('pointerleave',()=>{if(yaw||pitch){yaw=0;pitch=0;dirty=true;requestDraw();}});
  document.addEventListener('visibilitychange',()=>{if(document.hidden)stop();else requestDraw();});
  if ('IntersectionObserver' in window) new IntersectionObserver(entries=>{
    onscreen=entries[0].isIntersecting; if(!onscreen)stop();else requestDraw();
  },{rootMargin:'120px'}).observe(scene);
  if ('ResizeObserver' in window) new ResizeObserver(resize).observe(scene);
  else window.addEventListener('resize',resize,{passive:true});
  reduce.addEventListener('change',()=>{yaw=0;pitch=0;dirty=true;stop();requestDraw();});
  stop(); updateText(); resize(); scene.classList.add('ready');
})();

"""HTML templates served by web_dashboard.

These are pure presentation — kept in their own module so web_dashboard.py
stays focused on server wiring.  Each template is a self-contained page
(no external CSS/JS dependencies) so the dashboard works on a phone
without any network besides the Pi's hotspot.
"""

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="theme-color" content="#050708">
<title>DRIFTER</title>
<script>
/* Inline theme boot — applies before CSS so the page never flashes a
   different palette. Persisted as `drifter-theme`; valid values
   uncaged | ghost | drift, default uncaged. */
(function(){
  try{
    var t=localStorage.getItem('drifter-theme');
    if(t!=='ghost'&&t!=='drift'&&t!=='uncaged') t='uncaged';
    document.documentElement.dataset.theme=t;
  }catch(e){}
})();
</script>
<style>
*{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent}
:root{
  /* Geometry, motion, type — invariant across themes */
  --radius-sm:6px;--radius:10px;--radius-lg:14px;
  --safe-bottom:env(safe-area-inset-bottom,0px);
  --safe-top:env(safe-area-inset-top,0px);
  --ease:cubic-bezier(.4,0,.2,1);
  --fs-xs:11px;--fs-sm:13px;--fs-md:15px;--fs-lg:20px;
  --fs-val:28px;--fs-val-lg:44px;
  --font-mono:'JetBrains Mono','Fira Code',ui-monospace,monospace;
  --font-display:'JetBrains Mono','Fira Code',ui-monospace,monospace;
}

/* ── THEMES ─ uncaged (default) / ghost / drift ─ persisted in
   localStorage as `drifter-theme`. Switcher in the status bar cycles. */
:root[data-theme="uncaged"], :root:not([data-theme]) {
  --bg:#0a0a0a;--bg-elev:#0e1310;--bg-glow:#003a22;
  --card:#131918;--card-hi:#1a2220;
  --border:#1a2a26;--border-hi:#243a35;
  --text:#e8f2eb;--text-dim:#7a9990;--text-mute:#4f6c63;--dim:#7a9990;
  --accent:#00ff88;--accent-glow:rgba(0,255,136,.35);
  --ok:#00ff88;--info:#7af2c8;--amber:#fbbf24;--red:#ff4d6d;
  --ok-glow:rgba(0,255,136,.25);--amber-glow:rgba(251,191,36,.35);--red-glow:rgba(255,77,109,.4);
}
:root[data-theme="ghost"] {
  --bg:#050505;--bg-elev:#0b0b0b;--bg-glow:transparent;
  --card:#0f0f0f;--card-hi:#161616;
  --border:#1e1e1e;--border-hi:#2b2b2b;
  --text:#e8e8e8;--text-dim:#888;--text-mute:#555;--dim:#888;
  --accent:#e8e8e8;--accent-glow:rgba(232,232,232,.18);
  --ok:#d8d8d8;--info:#c8c8c8;--amber:#cccccc;--red:#ff5555;
  --ok-glow:rgba(216,216,216,.15);--amber-glow:rgba(204,204,204,.18);--red-glow:rgba(255,85,85,.35);
  --font-display:'Inter','Helvetica Neue',ui-sans-serif,sans-serif;
}
:root[data-theme="drift"] {
  --bg:#0a0e1a;--bg-elev:#11162a;--bg-glow:#2a1c00;
  --card:#181d2f;--card-hi:#232842;
  --border:#2a324a;--border-hi:#3a435e;
  --text:#ffd9a3;--text-dim:#b58a52;--text-mute:#6f5a3a;--dim:#b58a52;
  --accent:#ffb800;--accent-glow:rgba(255,184,0,.35);
  --ok:#ffb800;--info:#ffd9a3;--amber:#ffb800;--red:#ff4444;
  --ok-glow:rgba(255,184,0,.3);--amber-glow:rgba(255,184,0,.3);--red-glow:rgba(255,68,68,.4);
}
:root[data-theme="drift"] body::before {
  content:'';position:fixed;inset:0;pointer-events:none;z-index:1000;
  background:repeating-linear-gradient(0deg,rgba(0,0,0,.07) 0,rgba(0,0,0,.07) 1px,transparent 1px,transparent 3px);
  mix-blend-mode:multiply;
}

.theme-switch{
  background:transparent;border:1px solid var(--border);color:var(--text-mute);
  padding:3px 8px;border-radius:2px;font-family:var(--font-mono);font-size:10px;
  letter-spacing:1.5px;cursor:pointer;transition:.14s;line-height:1;
}
.theme-switch:hover{color:var(--accent);border-color:var(--accent)}
html,body{background:var(--bg);color:var(--text);overscroll-behavior:none}
body{
  font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;
  font-feature-settings:'tnum' 1,'ss01' 1;
  overflow-x:hidden;-webkit-font-smoothing:antialiased;text-rendering:geometricPrecision;
  padding-bottom:calc(64px + var(--safe-bottom));
  background:
    radial-gradient(1200px 600px at 50% -150px, var(--bg-glow) 0%, transparent 60%),
    var(--bg);
}

/* Header */
.header{
  text-align:center;padding:calc(12px + var(--safe-top)) 12px 10px;
  border-bottom:1px solid var(--border);position:sticky;top:0;z-index:100;
  background:linear-gradient(180deg,var(--bg-elev) 0%,var(--bg) 100%);
  backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);
}
.header h1{font-size:20px;letter-spacing:8px;color:var(--accent);font-weight:700;text-shadow:0 0 18px var(--accent-glow)}
.header .sub{font-size:var(--fs-xs);color:var(--text-mute);margin-top:3px;letter-spacing:2px}
.mode-pill{
  padding:4px 12px;border:1px solid var(--accent);border-radius:2px;
  font-size:var(--fs-xs);letter-spacing:2px;color:var(--accent);
  text-shadow:0 0 8px var(--accent-glow);font-family:monospace;
}
.mode-pill.foot{color:var(--ok);border-color:var(--ok);text-shadow:0 0 8px var(--ok-glow)}
.mode-switch{
  background:transparent;border:1px solid var(--border);color:var(--text-mute);
  padding:4px 10px;border-radius:2px;font-family:monospace;font-size:var(--fs-xs);
  letter-spacing:1.5px;cursor:pointer;transition:.14s;
}
.mode-switch:hover{color:var(--accent);border-color:var(--accent)}
.mode-switch:disabled{opacity:.4;cursor:wait}

/* Status bar */
.status-bar{
  display:flex;justify-content:space-between;align-items:center;
  padding:8px 16px;font-size:var(--fs-sm);letter-spacing:1px;
  border-bottom:1px solid var(--border);background:var(--bg-elev);
}
.status-dot{
  width:8px;height:8px;border-radius:50%;display:inline-block;margin-right:6px;
  vertical-align:middle;transition:background .3s var(--ease),box-shadow .3s var(--ease);
}
.dot-ok{background:var(--ok);box-shadow:0 0 8px var(--ok-glow)}
.dot-warn{background:var(--amber);box-shadow:0 0 8px var(--amber-glow)}
.dot-off{background:#303a43}
#data-age{font-variant-numeric:tabular-nums}

/* Alert banner */
.alert-banner{
  padding:12px 16px;font-size:13px;font-weight:700;letter-spacing:1px;
  text-align:center;display:none;border-bottom:1px solid transparent;
}
.alert-ok{background:linear-gradient(180deg,rgba(74,222,128,.08),rgba(74,222,128,.03));color:var(--ok);display:block;border-bottom-color:rgba(74,222,128,.15)}
.alert-info{background:linear-gradient(180deg,rgba(96,165,250,.1),rgba(96,165,250,.03));color:var(--info);display:block;border-bottom-color:rgba(96,165,250,.2)}
.alert-amber{background:linear-gradient(180deg,rgba(251,191,36,.14),rgba(251,191,36,.05));color:var(--amber);display:block;border-bottom-color:rgba(251,191,36,.3);animation:pulse-soft 2.4s var(--ease) infinite}
.alert-red{background:linear-gradient(180deg,rgba(248,113,113,.18),rgba(248,113,113,.06));color:var(--red);display:block;border-bottom-color:rgba(248,113,113,.35);animation:pulse-hard 1.2s var(--ease) infinite}
@keyframes pulse-soft{0%,100%{opacity:1}50%{opacity:.78}}
@keyframes pulse-hard{0%,100%{box-shadow:inset 0 0 0 0 var(--red-glow)}50%{box-shadow:inset 0 0 30px 2px var(--red-glow)}}

/* Cards + grid */
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;padding:10px}
.card{
  background:linear-gradient(180deg,var(--card) 0%,#0f141a 100%);
  border:1px solid var(--border);border-radius:var(--radius);
  padding:12px 10px;text-align:center;position:relative;overflow:hidden;
  transition:border-color .3s var(--ease),box-shadow .3s var(--ease);
}
.card::before{
  content:"";position:absolute;inset:0;border-radius:inherit;pointer-events:none;
  background:radial-gradient(140% 80% at 50% -30%,rgba(255,255,255,.04),transparent 55%);
}
.card.flash{
  border-color:var(--accent);
  box-shadow:0 0 0 1px var(--accent-glow),0 0 24px -4px var(--accent-glow);
  transition:none;
}
.card .label{font-size:var(--fs-xs);color:var(--text-mute);text-transform:uppercase;letter-spacing:1.8px;font-weight:600}
.card .value{font-size:var(--fs-val);font-weight:700;margin:6px 0 2px;font-variant-numeric:tabular-nums;letter-spacing:-.5px;line-height:1;transition:color .25s var(--ease)}
.card .unit{font-size:var(--fs-xs);color:var(--text-mute);letter-spacing:.5px}
.card .bar{height:4px;background:#1a2229;border-radius:3px;margin-top:8px;overflow:hidden}
.card .bar-fill{height:100%;border-radius:3px;transition:width .35s var(--ease),background .35s var(--ease)}
.card.lg .value{font-size:var(--fs-val-lg);letter-spacing:-1px}
.card.med .value{font-size:32px}

/* RPM tachometer bar */
.bar-zones{position:relative;height:5px;background:#1a2229;border-radius:3px;margin-top:8px;overflow:hidden}
.bar-zone-ok{position:absolute;left:0;top:0;height:100%;width:78%;background:linear-gradient(90deg,rgba(74,222,128,.25),rgba(74,222,128,.6))}
.bar-zone-warn{position:absolute;left:78%;top:0;height:100%;width:8%;background:rgba(251,191,36,.6)}
.bar-zone-red{position:absolute;left:86%;top:0;height:100%;width:14%;background:rgba(248,113,113,.7)}
.bar-needle{
  position:absolute;top:-2px;width:3px;height:9px;
  background:#fff;border-radius:2px;transform:translateX(-50%);
  transition:left .2s var(--ease);
  box-shadow:0 0 6px rgba(255,255,255,.7);
}

/* Fuel-trim bar (centred on zero) */
.trim-bar-wrap{position:relative;height:4px;background:#1a2229;border-radius:3px;margin-top:8px;overflow:hidden}
.trim-bar-center{position:absolute;left:50%;top:0;width:1px;height:100%;background:var(--text-mute);opacity:.5}
.trim-bar-fill{position:absolute;top:0;height:100%;border-radius:3px;transition:left .35s var(--ease),width .35s var(--ease),background .35s var(--ease)}

/* TPMS */
.tpms-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;padding:0 10px}
.tpms-card{
  background:linear-gradient(180deg,var(--card),#0f141a);
  border:1px solid var(--border);border-radius:var(--radius);
  padding:10px 12px;display:flex;justify-content:space-between;align-items:center;
}
.tpms-card .pos{font-size:var(--fs-xs);color:var(--text-mute);font-weight:700;letter-spacing:2px;text-transform:uppercase}
.tpms-card .psi{font-size:24px;font-weight:700;font-variant-numeric:tabular-nums;letter-spacing:-.5px}
.tpms-card .temp{font-size:var(--fs-xs);color:var(--text-mute);font-variant-numeric:tabular-nums}

/* Section headers */
.section{
  padding:18px 16px 6px;font-size:var(--fs-xs);color:var(--text-mute);
  text-transform:uppercase;letter-spacing:3px;font-weight:600;
  display:flex;align-items:center;gap:10px;
}
.section::before,.section::after{
  content:"";flex:1;height:1px;
  background:linear-gradient(90deg,transparent,var(--border),transparent);
}

/* Alert message card */
.alert-msg{
  padding:12px 16px;font-size:13px;line-height:1.45;
  background:linear-gradient(180deg,var(--card),#0f141a);
  margin:0 10px;border:1px solid var(--border);border-radius:var(--radius);
  min-height:44px;display:flex;align-items:center;
  cursor:pointer;transition:border-color .2s var(--ease);
}
.alert-msg:active{border-color:var(--border-hi)}

/* DTC pills */
.dtc-list{padding:6px 16px 8px;font-size:12px}
.dtc-code{
  display:inline-block;background:rgba(248,113,113,.12);color:var(--red);
  padding:3px 10px;border-radius:999px;margin:2px;font-weight:700;
  border:1px solid rgba(248,113,113,.25);letter-spacing:.5px;
}
.dtc-pending{background:rgba(251,191,36,.1);color:var(--amber);border-color:rgba(251,191,36,.25)}

/* System rows */
.sys-row{display:flex;justify-content:space-between;align-items:center;padding:6px 16px;font-size:12px}
.sys-row .lbl{color:var(--text-mute);letter-spacing:.5px}
.sys-row span:last-child{font-variant-numeric:tabular-nums;color:var(--text)}

/* Chips */
.chip{
  padding:10px 14px;background:var(--card);border:1px solid var(--border);
  border-radius:999px;color:var(--text-dim);font-family:inherit;font-size:var(--fs-sm);
  cursor:pointer;white-space:nowrap;min-height:44px;display:inline-flex;align-items:center;
  transition:border-color .15s var(--ease),color .15s var(--ease),background .15s var(--ease);
}
.chip:active,.chip.active{
  border-color:var(--accent);color:var(--accent);
  background:rgba(34,211,238,.08);box-shadow:0 0 0 1px var(--accent-glow) inset;
}

/* Bottom tab bar */
.tabbar{
  position:fixed;left:0;right:0;bottom:0;z-index:200;
  display:grid;grid-template-columns:repeat(4,1fr);
  padding:6px 8px calc(6px + var(--safe-bottom));
  background:linear-gradient(180deg,rgba(5,7,8,.92),var(--bg));
  border-top:1px solid var(--border);
  backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);
}
.tabbar a,.tabbar button{
  background:transparent;border:0;outline:0;cursor:pointer;
  color:var(--text-dim);font-family:inherit;font-size:var(--fs-xs);letter-spacing:1px;
  text-decoration:none;padding:6px 8px;min-height:48px;
  display:flex;flex-direction:column;align-items:center;gap:4px;
  transition:color .15s var(--ease);
}
.tabbar a:active,.tabbar button:active,.tabbar a.active,.tabbar button.active{color:var(--accent)}
.tabbar .ico{
  width:28px;height:28px;stroke:currentColor;fill:none;stroke-width:1.8;
  stroke-linecap:round;stroke-linejoin:round;display:block;
  transition:transform .2s var(--ease);
}
.tabbar a:active .ico,.tabbar button:active .ico{transform:scale(.92)}
.tabbar button.active .ico{filter:drop-shadow(0 0 6px var(--accent-glow))}
.tabbar a.active .ico{filter:drop-shadow(0 0 6px var(--accent-glow))}

/* (Old light-mode block removed in Phase 3 — replaced by the
   uncaged/ghost/drift theme system at the top of this stylesheet.) */

/* Responsive */
@media(max-width:480px){
  :root{--fs-val:24px;--fs-val-lg:36px}
  .grid{grid-template-columns:repeat(auto-fit,minmax(min(140px,45vw),1fr));gap:8px;padding:8px}
  .card{padding:10px 8px}
  .tpms-grid{gap:6px;padding:0 8px}
  .tabbar{padding:8px 4px calc(8px + var(--safe-bottom))}
}
@media(min-width:481px) and (max-height:340px){
  :root{--fs-val:22px;--fs-val-lg:32px}
  .header{padding-top:6px;padding-bottom:4px}
  .header h1{font-size:16px}
  .section{padding:10px 16px 4px}
  .card{padding:8px}
}

/* Disconnected overlay */
.disconnected{
  position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);
  background:var(--bg-elev);padding:26px 32px;border-radius:var(--radius-lg);
  text-align:center;z-index:999;max-width:320px;
  border:1px solid rgba(248,113,113,.4);
  box-shadow:0 20px 60px -10px rgba(0,0,0,.7),0 0 30px rgba(248,113,113,.1);
}
.disconnected h2{color:var(--red);margin-bottom:10px;letter-spacing:3px}
.disconnected p{color:var(--text-dim);font-size:12px;line-height:1.6}
.disconnected .retry-info{color:var(--amber);font-size:11px;margin-top:10px}
.hidden{display:none!important}

/* Toasts */
.toast-container{
  position:fixed;bottom:calc(80px + var(--safe-bottom));left:50%;
  transform:translateX(-50%);z-index:1000;
  display:flex;flex-direction:column-reverse;gap:8px;
  pointer-events:none;max-width:92%;
}
.toast{
  padding:10px 16px;border-radius:999px;font-size:12px;letter-spacing:.5px;
  pointer-events:auto;animation:toast-in .25s var(--ease);
  display:flex;align-items:center;gap:8px;
  backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);
  box-shadow:0 8px 24px -6px rgba(0,0,0,.6);
}
.toast.info{background:rgba(96,165,250,.14);color:var(--info);border:1px solid rgba(96,165,250,.35)}
.toast.warn{background:rgba(251,191,36,.14);color:var(--amber);border:1px solid rgba(251,191,36,.4)}
.toast.error{background:rgba(248,113,113,.16);color:var(--red);border:1px solid rgba(248,113,113,.45)}
.toast.success{background:rgba(74,222,128,.14);color:var(--ok);border:1px solid rgba(74,222,128,.4)}
@keyframes toast-in{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:translateY(0)}}
@keyframes toast-out{from{opacity:1}to{opacity:0;transform:translateY(-8px)}}

/* Help tooltip */
.help-icon{
  display:inline-flex;align-items:center;justify-content:center;width:32px;height:32px;border-radius:50%;
  background:var(--card);color:var(--text-mute);font-size:13px;
  line-height:32px;cursor:pointer;margin-left:4px;vertical-align:middle;
  border:1px solid var(--border);user-select:none;font-weight:700;
  transition:color .15s var(--ease),border-color .15s var(--ease);
}
.help-icon:hover,.help-icon:focus{color:var(--accent);border-color:var(--accent);outline:none}
.help-tip{
  display:none;position:absolute;left:0;right:0;top:100%;z-index:50;
  background:var(--bg-elev);border:1px solid var(--border);border-radius:var(--radius);
  padding:10px 12px;font-size:11px;color:var(--text-dim);line-height:1.55;
  margin-top:6px;font-weight:400;text-transform:none;letter-spacing:0;text-align:left;
  box-shadow:0 8px 24px -6px rgba(0,0,0,.6);
}
.help-tip.show{display:block;animation:toast-in .18s var(--ease)}

/* Alert expand */
.alert-expand{
  background:var(--bg-elev);margin:0 10px;border-radius:0 0 var(--radius) var(--radius);
  padding:0 16px;font-size:12px;line-height:1.55;
  border:1px solid var(--border);border-top:none;
  max-height:0;overflow:hidden;transition:max-height .3s var(--ease),padding .3s var(--ease);
}
.alert-expand.open{max-height:400px;padding:12px 16px;overflow-y:auto}
.alert-expand .advice-text{color:var(--text-dim);margin-bottom:10px}
.alert-expand .alert-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:6px}
.alert-expand .alert-actions button{
  padding:6px 12px;background:var(--card);border:1px solid var(--border);
  border-radius:999px;color:var(--accent);
  font-family:inherit;font-size:11px;cursor:pointer;letter-spacing:.5px;
}
.alert-expand .alert-actions button:active{border-color:var(--accent)}

/* Alert history */
.alert-history{max-height:240px;overflow-y:auto;padding:6px 16px 8px;font-size:11px}
.alert-history-item{
  display:flex;justify-content:space-between;padding:6px 0;
  border-bottom:1px solid var(--border);color:var(--text-dim);
}
.alert-history-item .ah-time{color:var(--text-mute);white-space:nowrap;margin-left:8px;font-variant-numeric:tabular-nums}

/* Hardware overlay */
.hw-overlay{
  position:fixed;top:0;left:0;right:0;bottom:0;
  background:var(--bg);z-index:500;
  display:flex;flex-direction:column;
  transition:opacity .5s var(--ease);padding-top:var(--safe-top);
}
.hw-overlay.fade-out{opacity:0;pointer-events:none}
.hw-header{text-align:center;padding:24px 0 12px}
.hw-header h2{font-size:18px;letter-spacing:6px;color:var(--accent);text-shadow:0 0 18px var(--accent-glow)}
.hw-header .hw-sub{font-size:10px;color:var(--text-mute);margin-top:6px;letter-spacing:2px}
.hw-list{padding:8px 16px;flex:1;overflow-y:auto}
.hw-item{
  display:flex;align-items:center;gap:14px;padding:14px;margin-bottom:10px;
  background:linear-gradient(180deg,var(--card),#0f141a);
  border:1px solid var(--border);border-radius:var(--radius);
}
.hw-dot{width:10px;height:10px;border-radius:50%;flex-shrink:0}
.hw-dot.ok{background:var(--ok);box-shadow:0 0 8px var(--ok-glow)}
.hw-dot.missing,.hw-dot.down,.hw-dot.error{background:var(--red);box-shadow:0 0 8px var(--red-glow)}
.hw-dot.waiting{background:var(--amber);box-shadow:0 0 8px var(--amber-glow);animation:pulse-soft 1.5s infinite}
.hw-dot.setup{background:var(--info);box-shadow:0 0 8px rgba(96,165,250,.35)}
.hw-info{flex:1}
.hw-name{font-size:13px;font-weight:700;letter-spacing:1.5px}
.hw-detail{font-size:11px;color:var(--text-dim);margin-top:3px;line-height:1.4}
.hw-services{padding:12px 16px 20px;font-size:10px;color:var(--text-mute);text-align:center;line-height:1.8}
.hw-services span{margin:0 4px}
.hw-svc-ok{color:var(--ok)}
.hw-svc-fail{color:var(--red)}
.hw-svc-off{color:var(--text-mute)}

/* Ask input + action buttons */
.ask-input{
  flex:1;padding:10px 14px;background:var(--card);border:1px solid var(--border);
  border-radius:var(--radius);color:var(--text);
  font-family:inherit;font-size:13px;outline:none;
  transition:border-color .15s var(--ease),box-shadow .15s var(--ease);
}
.ask-input:focus{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-glow)}
.btn{
  padding:10px 14px;border-radius:var(--radius);cursor:pointer;
  font-family:inherit;font-size:12px;font-weight:700;letter-spacing:1px;
  white-space:nowrap;line-height:1;
  transition:transform .1s var(--ease),background .15s var(--ease);
}
.btn:active{transform:scale(.97)}
.btn.primary{background:var(--accent);color:#001519;border:1px solid var(--accent)}
.btn.secondary{background:var(--card);color:var(--text-dim);border:1px solid var(--border)}
.btn.danger{background:rgba(248,113,113,.1);color:var(--red);border:1px solid rgba(248,113,113,.35)}
.btn.mic{padding:10px 12px;background:var(--card);color:var(--text-dim);border:1px solid var(--border);font-size:16px}

/* Analysis card */
.diag-card{
  background:linear-gradient(180deg,var(--card),#0f141a);
  border:1px solid var(--border);border-radius:var(--radius);
  padding:14px;margin:4px 10px 10px;
}

/* Details / summary disclosure */
details summary{
  cursor:pointer;padding:6px 0;list-style:none;
  color:var(--text-dim);font-size:11px;letter-spacing:.5px;
}
details summary::-webkit-details-marker{display:none}
details summary::before{content:"▸ ";color:var(--text-mute)}
details[open] summary::before{content:"▾ "}
</style>
</head>
<body>

<div class="header" style="display:flex;align-items:center;justify-content:space-between;gap:14px">
  <div>
    <h1>DRIFTER</h1>
    <div class="sub">2004 JAGUAR X-TYPE 2.5L V6 &mdash; MZ1312</div>
  </div>
  <div style="display:flex;align-items:center;gap:10px">
    <span id="mode-pill" class="mode-pill drive">DRIVE</span>
    <button id="mode-switch" class="mode-switch" title="Switch persona">→ FOOT</button>
  </div>
</div>

<div class="status-bar">
  <span><span class="status-dot dot-off" id="dot-conn"></span><span id="conn-text">CONNECTING</span></span>
  <span><span class="status-dot dot-ok" id="dot-vivi" title="Vivi"></span><span id="vivi-text">VIVI</span></span>
  <button class="theme-switch" id="theme-switch" title="Cycle theme" aria-label="Cycle theme">⏻</button>
  <span id="data-age">--</span>
</div>

<div class="alert-banner alert-ok" id="alert-banner">SYSTEMS NOMINAL</div>

<!-- ── ASK VIVI — promoted to top so it's the primary action on the HUD ── -->
<div class="section">ASK VIVI</div>
<div style="padding:6px 10px 12px">
  <div id="ask-chips" style="display:flex;flex-wrap:wrap;gap:5px;margin-bottom:8px">
    <button class="chip" onclick="chipAsk(this)">Safe to drive?</button>
    <button class="chip" onclick="chipAsk(this)">Explain my fuel trims</button>
    <button class="chip" onclick="chipAsk(this)">What do my DTCs mean?</button>
    <button class="chip" onclick="chipAsk(this)">Likely cause of alert?</button>
    <button class="chip" onclick="chipAsk(this)">Next service items?</button>
  </div>
  <div style="display:flex;gap:6px;margin-bottom:6px">
    <input id="ask-input" type="text" class="ask-input"
      placeholder="Ask Vivi anything about your X-Type&hellip;"
      style="flex:1;font-size:14px;padding:10px 12px">
    <button id="mic-btn" onclick="toggleMic()" title="Voice input" class="btn mic" aria-label="Voice input">&#x1f3a4;</button>
    <button id="ask-btn" onclick="askMechanic()" class="btn primary">ASK</button>
    <button id="cancel-btn" onclick="cancelQuery()" class="btn danger hidden">CANCEL</button>
  </div>
  <div id="ask-output"
    style="font-size:13px;color:var(--text);line-height:1.55;white-space:pre-wrap;
           min-height:48px;padding:8px 2px">
    Hi. I&rsquo;m Vivi — ask me anything about the car. Voice replies stream automatically when you use the mic.
  </div>
  <div style="display:flex;justify-content:space-between;align-items:center;font-size:9px;padding:2px 2px 0">
    <button id="clear-conv" onclick="clearConversation()" title="Clear conversation"
      style="background:transparent;border:none;color:var(--text-mute);font-family:var(--font-mono);
             font-size:9px;letter-spacing:1px;cursor:pointer;padding:2px 0">CLEAR</button>
    <span id="ask-meta" style="color:var(--text-mute);font-family:var(--font-mono)"></span>
  </div>
</div>

<div class="section">ENGINE</div>
<div class="grid">
  <div class="card lg" id="c-rpm">
    <div class="label">RPM</div>
    <div class="value" id="v-rpm">--</div>
    <div class="bar-zones">
      <div class="bar-zone-ok"></div>
      <div class="bar-zone-warn"></div>
      <div class="bar-zone-red"></div>
      <div class="bar-needle" id="b-rpm" style="left:0"></div>
    </div>
  </div>
  <div class="card lg" id="c-speed">
    <div class="label">SPEED</div>
    <div class="value" id="v-speed">--</div>
    <div class="unit">km/h</div>
  </div>
  <div class="card med" id="c-coolant">
    <div class="label">COOLANT</div>
    <div class="value" id="v-coolant">--</div>
    <div class="unit">&deg;C &nbsp;<span style="font-size:9px;color:var(--dim)">normal 86-98</span></div>
    <div class="bar" style="position:relative">
      <div class="bar-fill" id="b-coolant" style="width:0;background:var(--ok)"></div>
      <!-- Normal range markers at 86°C (57.5%) and 98°C (72.5%) of 40-145°C span -->
      <div style="position:absolute;top:0;left:57.5%;width:1px;height:100%;background:#444"></div>
      <div style="position:absolute;top:0;left:72.5%;width:1px;height:100%;background:#444"></div>
    </div>
  </div>
  <div class="card med" id="c-voltage">
    <div class="label">VOLTAGE</div>
    <div class="value" id="v-voltage">--</div>
    <div class="unit">V</div>
  </div>
</div>

<div class="section">FUEL</div>
<div class="grid">
  <div class="card" style="position:relative">
    <div class="label">STFT B1 <span class="help-icon" tabindex="0" onclick="toggleHelp(this)" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();toggleHelp(this)}" data-help="Short-Term Fuel Trim Bank 1: How much the ECU is adjusting fuel right now. Positive = adding fuel (lean). Negative = removing fuel (rich). Normal: &plusmn;5%.">?</span></div>
    <div class="value" id="v-stft1">--</div>
    <div class="unit">%</div>
    <div class="trim-bar-wrap"><div class="trim-bar-center"></div><div class="trim-bar-fill" id="tb-stft1"></div></div>
    <div class="help-tip"></div>
  </div>
  <div class="card" style="position:relative">
    <div class="label">STFT B2 <span class="help-icon" tabindex="0" onclick="toggleHelp(this)" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();toggleHelp(this)}" data-help="Short-Term Fuel Trim Bank 2: Same as B1 but for the other cylinder bank. Both banks high = shared vacuum leak. One bank high = bank-specific issue.">?</span></div>
    <div class="value" id="v-stft2">--</div>
    <div class="unit">%</div>
    <div class="trim-bar-wrap"><div class="trim-bar-center"></div><div class="trim-bar-fill" id="tb-stft2"></div></div>
    <div class="help-tip"></div>
  </div>
  <div class="card" style="position:relative">
    <div class="label">LTFT B1 <span class="help-icon" tabindex="0" onclick="toggleHelp(this)" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();toggleHelp(this)}" data-help="Long-Term Fuel Trim Bank 1: The ECU's learned fuel adjustment. High positive = sustained lean (vacuum leak, dirty MAF). Persists across restarts. Normal: &plusmn;5%.">?</span></div>
    <div class="value" id="v-ltft1">--</div>
    <div class="unit">%</div>
    <div class="trim-bar-wrap"><div class="trim-bar-center"></div><div class="trim-bar-fill" id="tb-ltft1"></div></div>
    <div class="help-tip"></div>
  </div>
  <div class="card" style="position:relative">
    <div class="label">LTFT B2 <span class="help-icon" tabindex="0" onclick="toggleHelp(this)" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();toggleHelp(this)}" data-help="Long-Term Fuel Trim Bank 2: Same as LTFT B1 but for the other cylinder bank. Compare both banks to isolate bank-specific issues.">?</span></div>
    <div class="value" id="v-ltft2">--</div>
    <div class="unit">%</div>
    <div class="trim-bar-wrap"><div class="trim-bar-center"></div><div class="trim-bar-fill" id="tb-ltft2"></div></div>
    <div class="help-tip"></div>
  </div>
</div>

<div class="section">PERFORMANCE</div>
<div class="grid">
  <div class="card" style="position:relative">
    <div class="label">LOAD <span class="help-icon" tabindex="0" onclick="toggleHelp(this)" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();toggleHelp(this)}" data-help="Engine Load: How hard the engine is working (0-100%). Idle ~15-25%. Cruising ~30-50%. Full throttle ~80-100%.">?</span></div>
    <div class="value" id="v-load">--</div>
    <div class="unit">%</div>
    <div class="bar"><div class="bar-fill" id="b-load" style="width:0;background:var(--accent)"></div></div>
    <div class="help-tip"></div>
  </div>
  <div class="card">
    <div class="label">THROTTLE</div>
    <div class="value" id="v-throttle">--</div>
    <div class="unit">%</div>
    <div class="bar"><div class="bar-fill" id="b-throttle" style="width:0;background:var(--accent)"></div></div>
  </div>
  <div class="card" style="position:relative">
    <div class="label">IAT <span class="help-icon" tabindex="0" onclick="toggleHelp(this)" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();toggleHelp(this)}" data-help="Intake Air Temperature: Air temp entering the engine. Normal: 20-45&deg;C. Above 50&deg;C = heat soak risk, reduced power. Above 65&deg;C = critical.">?</span></div>
    <div class="value" id="v-iat">--</div>
    <div class="unit">&deg;C</div>
    <div class="help-tip"></div>
  </div>
  <div class="card" style="position:relative">
    <div class="label">MAF <span class="help-icon" tabindex="0" onclick="toggleHelp(this)" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();toggleHelp(this)}" data-help="Mass Air Flow: Air entering the engine in grams/second. Idle: 2.5-6.0 g/s. Below 2.5 at warm idle = dirty/failing MAF sensor. Clean with electronics cleaner.">?</span></div>
    <div class="value" id="v-maf">--</div>
    <div class="unit">g/s</div>
    <div class="help-tip"></div>
  </div>
</div>

<div class="section">DIAGNOSTICS</div>
<div class="alert-msg" id="alert-msg" onclick="toggleAlertExpand()" style="cursor:pointer" title="Tap for details">Waiting for data...</div>
<div class="alert-expand" id="alert-expand">
  <div class="advice-text" id="alert-advice">Loading guidance...</div>
  <div class="alert-actions">
    <button onclick="askAboutAlert()">&#x1f527; Ask Mechanic</button>
    <button onclick="dismissAlert()">&#x23f8; Dismiss 10min</button>
    <button onclick="toggleAlertHistory()">&#x1f4dc; History</button>
  </div>
</div>
<div class="alert-history hidden" id="alert-history"></div>
<div class="dtc-list" id="dtc-list"></div>

<div class="section">TIRES</div>
<div class="tpms-grid">
  <div class="tpms-card" id="tpms-fl">
    <div><div class="pos">FL</div><div class="psi" id="v-tpms-fl-psi">--</div></div>
    <div class="temp" id="v-tpms-fl-temp">--</div>
  </div>
  <div class="tpms-card" id="tpms-fr">
    <div><div class="pos">FR</div><div class="psi" id="v-tpms-fr-psi">--</div></div>
    <div class="temp" id="v-tpms-fr-temp">--</div>
  </div>
  <div class="tpms-card" id="tpms-rl">
    <div><div class="pos">RL</div><div class="psi" id="v-tpms-rl-psi">--</div></div>
    <div class="temp" id="v-tpms-rl-temp">--</div>
  </div>
  <div class="tpms-card" id="tpms-rr">
    <div><div class="pos">RR</div><div class="psi" id="v-tpms-rr-psi">--</div></div>
    <div class="temp" id="v-tpms-rr-temp">--</div>
  </div>
</div>

<div class="section">SYSTEM</div>
<div id="sys-info">
  <div class="sys-row"><span class="lbl">CPU Temp</span><span id="v-cpu-temp">--</span></div>
  <div class="sys-row"><span class="lbl">Disk</span><span id="v-disk">--</span></div>
  <div class="sys-row"><span class="lbl">Memory</span><span id="v-mem">--</span></div>
  <div class="sys-row"><span class="lbl">Uptime</span><span id="v-uptime">--</span></div>
</div>

<div class="section">DIAGNOSIS</div>
<div id="diag-card" class="diag-card">
  <div id="diag-primary" style="font-size:15px;font-weight:700;color:var(--ok)">No report yet &mdash; complete a drive to generate one</div>
  <div id="diag-evidence" style="font-size:12px;color:var(--text-dim);margin-top:6px;line-height:1.5"></div>
  <div id="diag-actions" style="margin-top:10px;font-size:12px;color:var(--text-dim);line-height:1.6;white-space:pre-line"></div>
  <div id="diag-safety" style="color:var(--red);font-weight:700;display:none;margin-top:8px;letter-spacing:1px">&#x26a0; SAFETY CRITICAL</div>
</div>
<div style="padding:0 10px 8px">
  <button onclick="triggerAnalysis()" class="btn secondary" style="width:100%;padding:12px">RUN ANALYSIS</button>
</div>
<div style="padding:0 16px 4px">
  <details style="font-size:11px;color:var(--text-dim)">
    <summary>Full report JSON</summary>
    <pre id="diag-json" style="font-size:10px;overflow-x:auto;color:var(--text-dim);margin-top:6px;padding:8px;background:var(--card);border:1px solid var(--border);border-radius:var(--radius-sm)"></pre>
  </details>
</div>

<div class="section">RECENT DRIVES</div>
<div id="sessions-list" style="padding:6px 16px 2px;font-size:12px;color:var(--dim)">Loading...</div>

<div class="section">WARDRIVE</div>
<div id="wardrive-panel" style="padding:6px 10px 4px">
  <div style="display:flex;gap:8px;font-size:11px;color:var(--dim);margin-bottom:6px">
    <span>&#x1f4f6; Wi-Fi: <b id="wd-wifi-count" style="color:var(--text)">--</b></span>
    <span>&bull;</span>
    <span>&#x1f4f1; BT: <b id="wd-bt-count" style="color:var(--text)">--</b></span>
    <span style="margin-left:auto" id="wd-session-totals" style="color:var(--dim)"></span>
  </div>
  <div id="wd-networks" style="font-size:11px;color:var(--dim)">No scan yet</div>
</div>

<div class="section">ADS-B AIRCRAFT</div>
<div id="adsb-panel" style="padding:6px 10px 8px;font-size:11px;color:var(--dim)">
  No data yet — ADS-B scan runs every 5 min (requires dump1090)
</div>

<div class="section">BLE</div>
<div id="ble-panel" style="padding:6px 10px 8px;font-size:11px;color:var(--dim)">
  No detections — scanner listening passively
</div>

<!-- ASK panel was here — promoted to the top of the page in Phase 3. -->
<div style="height:80px"></div>

<nav class="tabbar" aria-label="Primary">
  <a href="/" class="active" aria-label="Dashboard">
    <svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3a9 9 0 0 0-9 9h3M12 3a9 9 0 0 1 9 9h-3M12 3v4M5.3 6.3l2.1 2.1M18.7 6.3l-2.1 2.1M12 12l5-4"/><circle cx="12" cy="12" r="1.5"/></svg>
    <span>LIVE</span>
  </a>
  <a href="/settings" aria-label="Settings">
    <svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg>
    <span>SETTINGS</span>
  </a>
  <button id="audio-btn" aria-label="Enable voice alerts on this device" aria-pressed="false">
    <svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M11 5 6 9H2v6h4l5 4V5z"/><path d="M15.5 8.5a5 5 0 0 1 0 7M19 5a10 10 0 0 1 0 14"/></svg>
    <span>VOICE</span>
  </button>
</nav>

<div class="disconnected hidden" id="dc-overlay">
  <h2>DISCONNECTED</h2>
  <p>Connecting to vehicle&hellip;<br>Check that the MZ1312_DRIFTER hotspot is active and your phone is connected to it.</p>
  <div class="retry-info" id="dc-retry">Retrying in 2s&hellip;</div>
</div>

<div class="toast-container" id="toast-container"></div>

<div class="hw-overlay" id="hw-overlay">
  <div class="hw-header">
    <h2>DRIFTER</h2>
    <div class="hw-sub">HARDWARE STATUS</div>
  </div>
  <div class="hw-list" id="hw-list" style="flex:1;overflow-y:auto;min-height:0"></div>
  <div class="hw-services" id="hw-services" style="flex:1;overflow-y:auto;min-height:0"></div>
  <div style="flex-shrink:0;text-align:center;padding:16px;border-top:1px solid var(--border)"><button onclick="hwOverlayDismissed=true;document.getElementById('hw-overlay').classList.add('fade-out')" style="background:var(--accent);color:#000;border:none;padding:14px 48px;border-radius:8px;font-size:16px;font-weight:700;letter-spacing:2px;cursor:pointer;min-height:48px">SKIP</button></div>
</div>

<script>
const WS_URL = `ws://${location.hostname}:8081`;
const AUDIO_WS_URL = `ws://${location.hostname}:8082`;
let ws = null;
let audioWs = null;
let audioEnabled = false;
let audioCtx = null;
let lastDataTime = 0;
let hwOverlayDismissed = false;
let hwPollTimer = null;
// Auto-dismiss hardware overlay after 5 seconds so dashboard is always accessible
setTimeout(()=>{
  if(!hwOverlayDismissed){
    hwOverlayDismissed=true;
    const ol=document.getElementById('hw-overlay');
    if(ol) ol.classList.add('fade-out');
  }
}, 5000);
let wsRetryDelay = 2000;
const WS_RETRY_MAX = 16000;

// ── Toast Notification System ──
function showToast(message, type='info', duration=4000){
  const container = document.getElementById('toast-container');
  if(!container) return;
  const t = document.createElement('div');
  t.className = 'toast ' + type;
  t.textContent = message;
  container.appendChild(t);
  setTimeout(()=>{
    t.style.animation='toast-out .3s ease forwards';
    setTimeout(()=>t.remove(), 300);
  }, duration);
}

// ── Help Tooltip Toggle ──
function toggleHelp(icon){
  const card = icon.closest('.card');
  if(!card) return;
  const tip = card.querySelector('.help-tip');
  if(!tip) return;
  const isOpen = tip.classList.contains('show');
  // Close all other tips
  document.querySelectorAll('.help-tip.show').forEach(t=>t.classList.remove('show'));
  if(!isOpen){
    tip.textContent = icon.dataset.help || '';
    tip.classList.add('show');
  }
}
// Close tips on outside click
document.addEventListener('click', (e)=>{
  if(!e.target.classList.contains('help-icon')){
    document.querySelectorAll('.help-tip.show').forEach(t=>t.classList.remove('show'));
  }
});

// ── Alert Interaction ──
let alertHistory = [];
let currentAlertMsg = '';
let dismissedAlerts = {};

function toggleAlertExpand(){
  const el = document.getElementById('alert-expand');
  if(!el) return;
  const isOpen = el.classList.contains('open');
  if(isOpen){ el.classList.remove('open'); return; }
  el.classList.add('open');
  // Fetch advice for current alert
  if(currentAlertMsg && currentAlertMsg !== 'Systems nominal'){
    fetch('/api/mechanic/advice?alert='+encodeURIComponent(currentAlertMsg))
      .then(r=>r.json()).then(d=>{
        const advEl = document.getElementById('alert-advice');
        if(d.advice && d.advice.length){
          advEl.innerHTML = d.advice.map(a=>'<div style="margin-bottom:4px">&bull; '+esc(typeof a==='string'?a:a.text||JSON.stringify(a))+'</div>').join('');
        } else {
          advEl.textContent = 'No specific guidance available for this alert.';
        }
      }).catch(()=>{});
  }
}
function askAboutAlert(){
  if(!currentAlertMsg) return;
  document.getElementById('ask-input').value = 'Explain this alert and what I should do: ' + currentAlertMsg;
  document.getElementById('alert-expand').classList.remove('open');
  askMechanic();
  // Scroll to Ask Mechanic section
  document.getElementById('ask-input').scrollIntoView({behavior:'smooth',block:'center'});
}
function dismissAlert(){
  if(currentAlertMsg){
    dismissedAlerts[currentAlertMsg] = Date.now() + 600000; // 10 min
    // Persist to sessionStorage so it survives page refresh
    try{sessionStorage.setItem('drifter_dismissed',JSON.stringify(dismissedAlerts))}catch(e){}
    showToast('Alert dismissed for 10 minutes', 'info');
    document.getElementById('alert-expand').classList.remove('open');
  }
}
// Restore dismissed alerts from sessionStorage
try{
  const saved=sessionStorage.getItem('drifter_dismissed');
  if(saved){
    const parsed=JSON.parse(saved);
    const now=Date.now();
    for(const[k,v] of Object.entries(parsed)){
      if(v>now) dismissedAlerts[k]=v; // Only restore non-expired
    }
  }
}catch(e){}
function toggleAlertHistory(){
  const el = document.getElementById('alert-history');
  el.classList.toggle('hidden');
  if(!el.classList.contains('hidden')){
    el.innerHTML = alertHistory.length ?
      alertHistory.slice(-50).reverse().map(a=>{
        const t = new Date(a.ts).toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit',second:'2-digit'});
        const colors = {0:'var(--ok)',1:'var(--info)',2:'var(--amber)',3:'var(--red)'};
        return `<div class="alert-history-item"><span style="color:${colors[a.level]||'var(--dim)'}">${esc(a.message)}</span><span class="ah-time">${t}</span></div>`;
      }).join('') :
      '<div style="color:var(--dim);padding:8px;text-align:center">No alert history yet</div>';
  }
}

// ── Hardware Status ──
function pollHardware(){
  fetch('/api/hardware').then(r=>r.json()).then(hw=>{
    const ol = document.getElementById('hw-overlay');
    if(!ol) return;
    if(hw.ready || hwOverlayDismissed){
      ol.classList.add('fade-out');
      if(hwPollTimer){clearInterval(hwPollTimer);hwPollTimer=null;}
      return;
    }
    ol.classList.remove('fade-out');
    // Render summary items
    const list = document.getElementById('hw-list');
    list.innerHTML = (hw.summary||[]).map(s=>{
      const dot = s.status;
      return `<div class="hw-item"><div class="hw-dot ${dot}"></div><div class="hw-info"><div class="hw-name">${s.item}</div><div class="hw-detail">${s.detail}</div></div></div>`;
    }).join('');
    // Render services
    const svcs = document.getElementById('hw-services');
    if(hw.services){
      svcs.innerHTML = Object.entries(hw.services).map(([k,v])=>{
        const cls = v==='active'?'hw-svc-ok':v==='failed'?'hw-svc-fail':'hw-svc-off';
        const name = k.replace('drifter-','');
        return `<span class="${cls}">${name}</span>`;
      }).join(' ');
    }
  }).catch(()=>{});
}
// Poll hardware every 5s until data arrives
pollHardware();
hwPollTimer = setInterval(pollHardware, 5000);

// ── Color helpers ──
function rpmColor(v){return v>6500?'var(--red)':v>5500?'var(--amber)':'var(--ok)'}
function coolantColor(v){return v>=108?'var(--red)':v>100?'var(--amber)':'var(--ok)'}
function voltColor(v){return v<12?'var(--red)':v<13.2?'var(--amber)':'var(--ok)'}
function trimColor(v){return Math.abs(v)>12?'var(--amber)':Math.abs(v)>8?'var(--info)':'var(--ok)'}
function iatColor(v){return v>65?'var(--amber)':v>50?'var(--info)':'var(--ok)'}
function psiColor(v){return v<20?'var(--red)':v<26?'var(--amber)':'var(--ok)'}

function setVal(id, val, color){
  const el=document.getElementById(id);
  if(!el)return;
  el.textContent=val;
  if(color)el.style.color=color;
}

// ── Animated number counter ──
// Tweens the displayed value from its current number to `target` over
// `duration` ms with an ease-out curve so gauges feel smooth instead of
// snapping.  If the element already shows a non-numeric value we skip the
// tween and set directly (first paint, etc.).
const _tweens = new Map();  // id -> rAF handle, so a newer update cancels the previous tween
function animateVal(id, target, fmt, color, duration){
  const el = document.getElementById(id);
  if(!el) return;
  duration = duration || 280;
  fmt = fmt || (v => Math.round(v));
  // Cancel any in-flight tween on this element.
  const prev = _tweens.get(id);
  if(prev) cancelAnimationFrame(prev);
  const from = parseFloat(el.textContent);
  // If we can't parse the current text (first render, "--", etc.) just snap.
  if(!isFinite(from)){
    el.textContent = fmt(target);
    if(color) el.style.color = color;
    return;
  }
  // If the delta is tiny, skip the tween entirely — feels like wasted motion.
  if(Math.abs(target - from) < 0.1){
    el.textContent = fmt(target);
    if(color) el.style.color = color;
    return;
  }
  if(color) el.style.color = color;
  const start = performance.now();
  function step(now){
    const t = Math.min(1, (now - start) / duration);
    const eased = 1 - Math.pow(1 - t, 3);             // ease-out cubic
    el.textContent = fmt(from + (target - from) * eased);
    if(t < 1){
      _tweens.set(id, requestAnimationFrame(step));
    } else {
      _tweens.delete(id);
    }
  }
  _tweens.set(id, requestAnimationFrame(step));
}
function setBar(id, pct, color){
  const el=document.getElementById(id);
  if(!el)return;
  el.style.width=Math.min(100,Math.max(0,pct))+'%';
  if(color)el.style.background=color;
}
function setTrimBar(id, val, color){
  // val is fuel trim %, range clamped to ±25%. Bar grows left or right from centre.
  const el=document.getElementById(id);
  if(!el)return;
  const pct=Math.min(25,Math.max(-25,val));
  const halfW=Math.abs(pct)/25*50; // 0-50% of half-width
  if(pct>=0){el.style.left='50%';el.style.width=halfW+'%';}
  else{el.style.left=(50-halfW)+'%';el.style.width=halfW+'%';}
  if(color)el.style.background=color;
}
function flash(cardId){
  const el=document.getElementById(cardId);
  if(!el)return;
  el.classList.add('flash');
  setTimeout(()=>el.classList.remove('flash'),300);
}

// ── Process incoming MQTT data ──
function handleMessage(msg){
  const {topic, data} = msg;
  lastDataTime = Date.now();
  // Dismiss hardware overlay once real data flows
  if(!hwOverlayDismissed && topic.includes('/engine/')){
    hwOverlayDismissed = true;
    const ol = document.getElementById('hw-overlay');
    if(ol) ol.classList.add('fade-out');
  }
  const v = data.value;

  if(topic.endsWith('/rpm') && v!==undefined){
    animateVal('v-rpm', v, n => Math.round(n).toString(), rpmColor(v));
    // Position needle along zone bar (0-7000 RPM = 0-100%)
    const needle=document.getElementById('b-rpm');
    if(needle) needle.style.left=Math.min(100,(v/7000)*100)+'%';
    flash('c-rpm');
  }
  else if(topic.endsWith('/coolant') && v!==undefined){
    animateVal('v-coolant', v, n => Math.round(n).toString(), coolantColor(v));
    setBar('b-coolant', ((v-40)/80)*100, coolantColor(v));
    flash('c-coolant');
  }
  else if(topic.endsWith('/speed') && v!==undefined){
    animateVal('v-speed', v, n => Math.round(n).toString());
    flash('c-speed');
  }
  else if(topic.endsWith('/voltage') && v!==undefined){
    animateVal('v-voltage', v, n => n.toFixed(1), voltColor(v));
    flash('c-voltage');
  }
  else if(topic.endsWith('/stft1') && v!==undefined){
    setVal('v-stft1', (v>=0?'+':'')+v.toFixed(1), trimColor(v));
    setTrimBar('tb-stft1', v, trimColor(v));
  }
  else if(topic.endsWith('/stft2') && v!==undefined){
    setVal('v-stft2', (v>=0?'+':'')+v.toFixed(1), trimColor(v));
    setTrimBar('tb-stft2', v, trimColor(v));
  }
  else if(topic.endsWith('/ltft1') && v!==undefined){
    setVal('v-ltft1', (v>=0?'+':'')+v.toFixed(1), trimColor(v));
    setTrimBar('tb-ltft1', v, trimColor(v));
  }
  else if(topic.endsWith('/ltft2') && v!==undefined){
    setVal('v-ltft2', (v>=0?'+':'')+v.toFixed(1), trimColor(v));
    setTrimBar('tb-ltft2', v, trimColor(v));
  }
  else if(topic.endsWith('/load') && v!==undefined){
    animateVal('v-load', v, n => n.toFixed(0));
    setBar('b-load', v, 'var(--accent)');
  }
  else if(topic.endsWith('/throttle') && v!==undefined){
    animateVal('v-throttle', v, n => n.toFixed(0));
    setBar('b-throttle', v, 'var(--accent)');
  }
  else if(topic.endsWith('/iat') && v!==undefined){
    animateVal('v-iat', v, n => Math.round(n).toString(), iatColor(v));
  }
  else if(topic.endsWith('/maf') && v!==undefined){
    animateVal('v-maf', v, n => n.toFixed(1));
  }
  // Alert level
  else if(topic.endsWith('/alert/level')){
    const lvl = data.level || 0;
    const banner = document.getElementById('alert-banner');
    const names = {0:'SYSTEMS NOMINAL',1:'INFO',2:'CAUTION',3:'ALERT'};
    const cls = {0:'alert-ok',1:'alert-info',2:'alert-amber',3:'alert-red'};
    banner.className = 'alert-banner ' + (cls[lvl]||'alert-ok');
    banner.dataset.level = lvl;
    // Only show level name if no message text is stored
    if(!banner.dataset.msg) banner.textContent = names[lvl] || 'OK';
    maybeHaptic(lvl);
  }
  // Alert message
  else if(topic.endsWith('/alert/message')){
    const el = document.getElementById('alert-msg');
    const lvl = data.level || 0;
    const message = data.message || 'Systems nominal';
    const colors = {0:'var(--ok)',1:'var(--info)',2:'var(--amber)',3:'var(--red)'};
    // Check dismissed
    const now = Date.now();
    if(dismissedAlerts[message] && dismissedAlerts[message] > now){
      return; // Still dismissed
    }
    delete dismissedAlerts[message]; // Expired
    currentAlertMsg = message;
    el.style.color = colors[lvl] || 'var(--text)';
    el.textContent = message;
    // Track alert history
    if(lvl > 0 && message !== 'Systems nominal'){
      alertHistory.push({level:lvl, message:message, ts:now});
      if(alertHistory.length > 50) alertHistory.shift();
    }
    // Mirror active alerts on the banner too
    const banner = document.getElementById('alert-banner');
    if(lvl > 0 && message){
      banner.dataset.msg = message;
      banner.textContent = message;
    } else {
      delete banner.dataset.msg;
      const names = {0:'SYSTEMS NOMINAL',1:'INFO',2:'CAUTION',3:'ALERT'};
      banner.textContent = names[lvl] || 'SYSTEMS NOMINAL';
    }
  }
  // DTCs
  else if(topic.endsWith('/dtc')){
    renderDtcs(data.stored||[], data.pending||[]);
  }
  // TPMS
  else if(topic.includes('/rf/tpms/') && !topic.endsWith('/snapshot')){
    const pos = topic.split('/').pop();
    if(['fl','fr','rl','rr'].includes(pos)){
      const psi = data.pressure_psi;
      const temp = data.temp_c;
      if(psi!==null&&psi!==undefined){
        setVal(`v-tpms-${pos}-psi`, psi.toFixed(0)+' PSI', psiColor(psi));
      }
      if(temp!==null&&temp!==undefined){
        setVal(`v-tpms-${pos}-temp`, temp.toFixed(0)+'\u00b0C');
      }
    }
  }
  // Wardrive
  else if(topic.includes('/wardrive/')){
    handleWardrive(topic, data);
  }
  // ADS-B
  else if(topic.endsWith('/rf/adsb')){
    handleAdsb(data);
  }
  // Watchdog / system
  else if(topic.endsWith('/system/watchdog')){
    const sys = data.system || {};
    if(sys.cpu_temp) setVal('v-cpu-temp', sys.cpu_temp.toFixed(0)+'\u00b0C');
    if(sys.disk_percent) setVal('v-disk', sys.disk_percent.toFixed(0)+'% ('+
      (sys.disk_free_gb||'?')+'GB free)');
    if(sys.memory_percent) setVal('v-mem', sys.memory_percent.toFixed(0)+'%');
    if(sys.uptime_seconds){
      const h = Math.floor(sys.uptime_seconds/3600);
      const m = Math.floor((sys.uptime_seconds%3600)/60);
      setVal('v-uptime', h+'h '+m+'m');
    }
  }
  // ── Vivi voice replies ──
  // When the user speaks to Vivi via mic, mirror her reply into the
  // ASK output area so the conversation is visible in the HUD.
  else if(topic === 'drifter/vivi/response' && data && data.response){
    if(queryBusy) return;  // don't clobber an in-flight typed query
    const out = document.getElementById('ask-output');
    const meta = document.getElementById('ask-meta');
    if(out){
      out.style.color='var(--text)';
      out.textContent = data.response;
    }
    if(meta && data.query){
      meta.textContent = '🎙 ' + data.query;
    }
  }
  else if(topic === 'drifter/vivi/status' && data && data.status){
    const dot = document.getElementById('dot-vivi');
    if(dot){
      dot.className = 'status-dot ' + (
        data.status === 'idle' ? 'dot-ok' :
        data.status === 'speaking' ? 'dot-warn' :
        data.status === 'thinking' ? 'dot-warn' :
        'dot-warn'
      );
      dot.title = 'Vivi: ' + data.status;
    }
    const txt = document.getElementById('vivi-text');
    if(txt) txt.textContent = data.status.toUpperCase();
  }
}

// ── WebSocket Connection (exponential backoff) ──
function connect(){
  ws = new WebSocket(WS_URL);
  ws.onopen = ()=>{
    wsRetryDelay = 2000; // Reset backoff on success
    document.getElementById('dc-overlay').classList.add('hidden');
    document.getElementById('dot-conn').className='status-dot dot-ok';
    document.getElementById('conn-text').textContent='LIVE';
    showToast('Connected to DRIFTER', 'success', 2000);
  };
  ws.onmessage = (e)=>{
    try{handleMessage(JSON.parse(e.data))}catch(err){}
  };
  ws.onclose = ()=>{
    document.getElementById('dc-overlay').classList.remove('hidden');
    document.getElementById('dot-conn').className='status-dot dot-off';
    document.getElementById('conn-text').textContent='OFFLINE';
    const retryEl = document.getElementById('dc-retry');
    if(retryEl) retryEl.textContent = 'Retrying in '+(wsRetryDelay/1000)+'s\u2026';
    setTimeout(connect, wsRetryDelay);
    wsRetryDelay = Math.min(wsRetryDelay * 2, WS_RETRY_MAX); // Exponential backoff
  };
  ws.onerror = ()=>ws.close();
}

// ── Audio WebSocket ──
function connectAudio(){
  audioWs = new WebSocket(AUDIO_WS_URL);
  audioWs.binaryType = 'arraybuffer';
  audioWs.onmessage = (e)=>{
    if(!audioEnabled || !audioCtx) return;
    // Decode WAV and play
    audioCtx.decodeAudioData(e.data.slice(0)).then(buf=>{
      const src = audioCtx.createBufferSource();
      src.buffer = buf;
      src.connect(audioCtx.destination);
      src.start(0);
    }).catch(()=>{});
  };
  audioWs.onclose = ()=>setTimeout(connectAudio, 5000);
  audioWs.onerror = ()=>audioWs.close();
}

// ── Audio Toggle ──
document.getElementById('audio-btn').addEventListener('click', ()=>{
  audioEnabled = !audioEnabled;
  const btn = document.getElementById('audio-btn');
  btn.classList.toggle('active', audioEnabled);
  btn.setAttribute('aria-pressed', audioEnabled ? 'true' : 'false');
  if(navigator.vibrate) navigator.vibrate(10);
  if(audioEnabled && !audioCtx){
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    connectAudio();
  }
  showToast(audioEnabled ? 'Voice alerts enabled' : 'Voice alerts muted',
            audioEnabled ? 'success' : 'info', 1800);
});

// ── Haptic for high-severity alerts (Android only) ──
let _lastHapticLevel = 0;
function maybeHaptic(level){
  if(!navigator.vibrate) return;
  if(level === _lastHapticLevel) return;
  _lastHapticLevel = level;
  if(level >= 3) navigator.vibrate([120, 80, 120, 80, 120]);   // critical
  else if(level >= 2) navigator.vibrate([80, 60, 80]);          // amber
}

// ── Data Age Timer ──
setInterval(()=>{
  const el = document.getElementById('data-age');
  if(!lastDataTime){el.textContent='NO DATA';return}
  const age = (Date.now()-lastDataTime)/1000;
  if(age<2) el.textContent='LIVE';
  else if(age<10) el.textContent=age.toFixed(0)+'s ago';
  else el.textContent='STALE ('+age.toFixed(0)+'s)';
  el.style.color = age<5?'var(--ok)':age<30?'var(--amber)':'var(--red)';
}, 1000);

// ── Diagnosis ──
function triggerAnalysis(){
  fetch('/api/analyse',{method:'POST'})
    .then(r=>r.json())
    .then(d=>{document.getElementById('diag-primary').textContent='Analysis triggered — check back in ~60s';})
    .catch(()=>{});
}
function loadReport(){
  fetch('/api/report').then(r=>r.json()).then(report=>{
    if(!report||!report.session_id) return;
    const ps=report.primary_suspect||{};
    const conf=ps.confidence!=null?` (${ps.confidence}%)`:'';
    document.getElementById('diag-primary').textContent=(ps.diagnosis||'Unknown')+conf;
    document.getElementById('diag-primary').style.color=report.safety_critical?'var(--red)':'var(--ok)';
    document.getElementById('diag-evidence').textContent=ps.evidence||'';
    const actions=(report.action_items||[]).map(a=>`• ${a}`).join('\n');
    document.getElementById('diag-actions').textContent=actions;
    document.getElementById('diag-safety').style.display=report.safety_critical?'':'none';
    document.getElementById('diag-json').textContent=JSON.stringify(report,null,2);
  }).catch(()=>{});
}
loadReport();
setInterval(loadReport,30000);

// ── Wardrive live updates ──
function handleWardrive(topic, data){
  if(topic.endsWith('/wardrive/wifi')){
    const nets=data.scan||[];
    document.getElementById('wd-wifi-count').textContent=nets.length;
    const tot=data.session_total||0;
    document.getElementById('wd-session-totals').textContent=
      `session: ${tot} unique SSIDs`;
    if(!nets.length){
      document.getElementById('wd-networks').textContent='No Wi-Fi networks in range';
      return;
    }
    const sorted=[...nets].sort((a,b)=>(b.signal_dbm||0)-(a.signal_dbm||0));
    document.getElementById('wd-networks').innerHTML=sorted.slice(0,8).map(n=>{
      const dbm=n.signal_dbm!=null?n.signal_dbm+'dBm':'';
      const sec=n.security?`<span style="color:#555;margin-left:4px">${esc(n.security)}</span>`:'';
      return `<div style="display:flex;justify-content:space-between;padding:2px 0;border-bottom:1px solid #1a1a1a">
        <span style="color:var(--text)">${esc(n.ssid||'<hidden>')}</span>
        <span style="color:var(--dim)">${esc(n.channel||'')}${dbm?'&ensp;'+dbm:''}${sec}</span>
      </div>`;
    }).join('');
  }
  else if(topic.endsWith('/wardrive/bt')){
    const devs=data.devices||[];
    document.getElementById('wd-bt-count').textContent=devs.length;
  }
}

// ── ADS-B live updates ──
function handleAdsb(data){
  const panel=document.getElementById('adsb-panel');
  const aircraft=data.aircraft||[];
  if(!aircraft.length){
    panel.textContent=`No aircraft detected (${data.count||0} in scan, ${data.messages||0} msgs)`;
    return;
  }
  panel.innerHTML=aircraft.slice(0,6).map(a=>{
    const cs=(a.flight||a.hex||'?').trim();
    const alt=a.altitude?Math.round(a.altitude).toLocaleString()+"ft":'--';
    const spd=a.speed?Math.round(a.speed)+"kt":'--';
    const rssi=a.rssi!=null?a.rssi.toFixed(0)+'dBFS':'';
    return `<div style="display:flex;justify-content:space-between;padding:2px 0;border-bottom:1px solid #1a1a1a">
      <span style="color:var(--accent);font-weight:bold">${esc(cs)}</span>
      <span style="color:var(--dim)">${alt}&ensp;${spd}${rssi?'&ensp;'+rssi:''}</span>
    </div>`;
  }).join('');
}

// ── BLE live updates (Phase 4.6) ──
// Polls /api/ble/recent every 5s. Endpoint is hotspot-only — remote
// browsers see 403. Renders target name (hardware family label only),
// MAC prefix, RSSI, and age. Alerts get a red marker.
async function loadBle(){
  try{
    const r=await fetch('/api/ble/recent?limit=8');
    if(!r.ok) return;
    const d=await r.json();
    renderBle(d.detections||[]);
  }catch(e){}
}
function renderBle(detections){
  const panel=document.getElementById('ble-panel');
  if(!panel) return;
  if(!detections.length){
    panel.textContent='No detections — scanner listening passively';
    return;
  }
  const now=Date.now()/1000;
  panel.innerHTML=detections.slice(0,8).map(d=>{
    const age=Math.max(0,Math.round(now-(d.ts||0)));
    const ageStr=age<60?age+'s':age<3600?Math.round(age/60)+'m':Math.round(age/3600)+'h';
    const macPfx=(d.mac||'').slice(0,8);
    const alertCol=d.is_alert?'#ff5151':'var(--accent)';
    return `<div style="display:flex;justify-content:space-between;padding:2px 0;border-bottom:1px solid #1a1a1a">
      <span style="color:${alertCol};font-weight:bold">${esc(d.target||'?')}${d.is_alert?' ⚠':''}</span>
      <span style="color:var(--dim)">${esc(macPfx)}&hellip;&ensp;${d.rssi}dBm&ensp;${ageStr}</span>
    </div>`;
  }).join('');
}
loadBle();
setInterval(loadBle, 5000);

function esc(s){const d=document.createElement('div');d.textContent=String(s||'');return d.innerHTML;}

// ── DTC description enrichment ──
const dtcCache = {};
async function fetchDtcDesc(code){
  if(dtcCache[code]!==undefined) return dtcCache[code];
  try{
    const r=await fetch('/api/mechanic/dtc/'+code);
    const d=await r.json();
    dtcCache[code]=d.desc||'';
  }catch(e){dtcCache[code]='';}
  return dtcCache[code];
}
async function renderDtcs(stored, pending){
  const el=document.getElementById('dtc-list');
  if(!stored.length&&!pending.length){
    el.innerHTML='<span style="color:var(--ok);font-size:11px">No DTCs</span>';
    return;
  }
  const all=[...stored.map(c=>({c,p:false})),...pending.map(c=>({c,p:true}))];
  const descs=await Promise.all(all.map(({c})=>fetchDtcDesc(c)));
  el.innerHTML=all.map(({c,p},i)=>{
    const desc=descs[i]?`<span style="font-size:10px;color:var(--dim);display:block;margin-top:1px">${descs[i]}</span>`:'';
    return `<div style="margin:3px 0"><span class="dtc-code${p?' dtc-pending':''}">${c}</span>${desc}</div>`;
  }).join('');
}

// ── Recent Drives ──
function loadSessions(){
  fetch('/api/sessions').then(r=>r.json()).then(sessions=>{
    const el=document.getElementById('sessions-list');
    if(!sessions||!sessions.length){el.textContent='No sessions recorded yet';return;}
    el.innerHTML=sessions.slice(0,5).map(s=>{
      const d=new Date((s.start_ts||0)*1000);
      const dateStr=d.toLocaleDateString('en-GB',{day:'2-digit',month:'short',year:'2-digit'});
      const dur=Math.round((s.duration_seconds||0)/60);
      const dist=(s.distance_km||0).toFixed(1);
      const cool=s.max_coolant?Math.round(s.max_coolant)+'°C':'--';
      const volt=s.min_voltage?s.min_voltage.toFixed(1)+'V':'--';
      const alerts=s.alert_count||0;
      const alertBadge=alerts?`<span style="color:var(--amber);margin-left:6px">${alerts} alert${alerts>1?'s':''}</span>`:'';
      return `<div style="border-left:2px solid #2a2a2a;padding:5px 0 5px 10px;margin-bottom:6px">
        <div style="color:var(--text);font-size:11px">${dateStr}&ensp;<span style="color:var(--dim)">${dur}min &bull; ${dist}&thinsp;km</span>${alertBadge}</div>
        <div style="font-size:10px;color:var(--dim);margin-top:2px">Cool ${cool} &bull; ${volt}</div>
      </div>`;
    }).join('');
  }).catch(()=>{});
}
loadSessions();

// ── Ask Mechanic (LLM with Streaming) ──
let queryBusy=false;
let queryAbort=null;
let queryTimer=null;

function _submitQuery(q){
  if(queryBusy||!q) return;
  queryBusy=true;
  const out=document.getElementById('ask-output');
  const meta=document.getElementById('ask-meta');
  const btn=document.getElementById('ask-btn');
  const cancelBtn=document.getElementById('cancel-btn');
  out.style.color='var(--dim)';
  out.innerHTML='<span style="animation:pulse 1.5s infinite">\u25cf\u25cf\u25cf Thinking\u2026</span>';
  if(meta) meta.textContent='';
  btn.disabled=true;
  btn.classList.add('hidden');
  cancelBtn.classList.remove('hidden');

  // Elapsed time counter
  const startTime=Date.now();
  queryTimer=setInterval(()=>{
    const elapsed=((Date.now()-startTime)/1000).toFixed(0);
    if(meta) meta.textContent=elapsed+'s elapsed';
  }, 1000);

  // Try streaming first, fall back to non-streaming
  queryAbort = new AbortController();
  fetch('/api/query/stream',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({query:q}),
    signal:queryAbort.signal
  }).then(resp=>{
    if(!resp.ok) throw new Error('Stream unavailable');
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let fullText='';
    let model='';
    let tokens=0;
    out.textContent='';
    out.style.color='var(--text)';

    function readChunk(){
      return reader.read().then(({done, value})=>{
        if(done) return;
        const text = decoder.decode(value, {stream:true});
        const lines = text.split('\n');
        for(const line of lines){
          if(!line.startsWith('data: ')) continue;
          try{
            const d=JSON.parse(line.slice(6));
            if(d.error){
              out.style.color='var(--red)';
              out.textContent='Error: '+d.error;
              return;
            }
            if(d.token){
              fullText+=d.token;
              // Escape and render
              const escText=fullText.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
              out.innerHTML=escText.replace(/\n/g,'<br>');
            }
            if(d.done){
              model=d.model||'';
              tokens=d.tokens||0;
            }
          }catch(e){}
        }
        return readChunk();
      });
    }
    return readChunk().then(()=>{
      if(meta){
        const m=(model||'').split('/').pop();
        const elapsed=((Date.now()-startTime)/1000).toFixed(1);
        meta.textContent=(m?m+' \u00b7 ':'')+(tokens?tokens+' tok \u00b7 ':'')+elapsed+'s';
      }
    });
  }).catch(err=>{
    if(err.name==='AbortError'){
      out.style.color='var(--amber)';
      out.textContent='Query cancelled.';
      return;
    }
    // Fallback to non-streaming
    return fetch('/api/query',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query:q}),signal:queryAbort.signal})
      .then(r=>r.json())
      .then(d=>{
        if(d.error){out.style.color='var(--red)';out.textContent='Error: '+d.error;}
        else{
          out.style.color='var(--text)';
          const text=d.response||'';
          const escText=text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
          out.innerHTML=escText.replace(/\n/g,'<br>');
          if(meta){
            const m=(d.model||'').split('/').pop();
            const elapsed=((Date.now()-startTime)/1000).toFixed(1);
            meta.textContent=(m?m+' \u00b7 ':'')+(d.tokens?d.tokens+' tok \u00b7 ':'')+elapsed+'s';
          }
        }
      });
  }).catch(err=>{
    if(err.name!=='AbortError'){
      out.style.color='var(--red)';out.textContent='Request failed \u2014 is Ollama running?';
    }
  }).finally(()=>{
    queryBusy=false;queryAbort=null;
    btn.disabled=false;btn.classList.remove('hidden');btn.textContent='ASK';
    cancelBtn.classList.add('hidden');
    if(queryTimer){clearInterval(queryTimer);queryTimer=null;}
  });
}

function cancelQuery(){
  if(queryAbort) queryAbort.abort();
  showToast('Query cancelled', 'info', 2000);
}

function askMechanic(){
  const q=document.getElementById('ask-input').value.trim();
  _submitQuery(q);
}

// Quick-pick chip — highlight it, fill the input, and submit immediately
function chipAsk(el){
  document.querySelectorAll('.chip').forEach(c=>c.classList.remove('active'));
  el.classList.add('active');
  const q=el.textContent;
  document.getElementById('ask-input').value=q;
  _submitQuery(q);
}

// CLEAR — wipe conversation locally + tell Vivi to drop her history
// (POST → dashboard publishes drifter/vivi/control {"action":"reset"}).
function clearConversation(){
  fetch('/api/vivi/reset',{method:'POST'}).catch(()=>{});
  const out=document.getElementById('ask-output');
  const meta=document.getElementById('ask-meta');
  if(out){out.style.color='var(--text)';out.textContent='Cleared. Ask away.';}
  if(meta) meta.textContent='';
  document.querySelectorAll('.chip.active').forEach(c=>c.classList.remove('active'));
}

// Voice input via Web Speech API
let recognition=null;
function toggleMic(){
  const SpeechRecognition=window.SpeechRecognition||window.webkitSpeechRecognition;
  const btn=document.getElementById('mic-btn');
  if(!SpeechRecognition){
    document.getElementById('ask-output').textContent='Voice input not supported in this browser.';
    return;
  }
  if(recognition){
    recognition.stop();
    return;
  }
  recognition=new SpeechRecognition();
  recognition.lang='en-GB';
  recognition.interimResults=false;
  recognition.maxAlternatives=1;
  btn.style.color='var(--red)';
  btn.title='Listening... tap to cancel';
  recognition.onresult=e=>{
    const transcript=e.results[0][0].transcript;
    document.getElementById('ask-input').value=transcript;
    _submitQuery(transcript);
  };
  recognition.onerror=()=>{};
  recognition.onend=()=>{
    recognition=null;
    btn.style.color='var(--dim)';
    btn.title='Voice input';
  };
  recognition.start();
}

document.getElementById('ask-input').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();askMechanic();}});

// ── Mode pill + persona switch ──
async function refreshModePill(){
  try{
    const m = await fetch('/api/mode').then(r=>r.json());
    const pill = document.getElementById('mode-pill');
    const btn  = document.getElementById('mode-switch');
    pill.textContent = m.mode.toUpperCase();
    pill.className = 'mode-pill ' + m.mode;
    btn.textContent = m.mode === 'foot' ? '→ DRIVE' : '→ FOOT';
    btn.dataset.target = m.mode === 'foot' ? 'drive' : 'foot';
  }catch(e){}
}
document.getElementById('mode-switch').addEventListener('click', async (e)=>{
  const btn = e.currentTarget;
  const target = btn.dataset.target || 'foot';
  if (!confirm('Switch persona to ' + target.toUpperCase() + '?\\n\\nThis will start/stop services to match the new mode.')) return;
  btn.disabled = true;
  btn.textContent = 'SWITCHING…';
  try{
    const res = await fetch('/api/mode/' + target, {method:'POST'});
    const data = await res.json();
    if (data.status !== 'dispatched'){
      alert('mode switch rejected: ' + JSON.stringify(data));
      btn.disabled = false;
      refreshModePill();
      return;
    }
    // Switch runs detached because it can SIGTERM us mid-call (when the
    // dashboard's own mode flips out). Wait for systemctl to finish, then
    // hop to the new persona's port.
    setTimeout(() => {
      const port = target === 'foot' ? 8090 : 8080;
      location.href = 'http://' + location.hostname + ':' + port + '/';
    }, 4500);
  }catch(err){
    alert('mode switch error: ' + err);
    btn.disabled = false;
  }
});
refreshModePill();
setInterval(refreshModePill, 10000);

// ── Theme cycle ──
// Boot script (top of page) reads localStorage and sets <html data-theme>;
// here we wire the status-bar button to cycle through uncaged → ghost → drift.
(function(){
  const themes=['uncaged','ghost','drift'];
  function setTheme(name){
    document.documentElement.dataset.theme=name;
    try{localStorage.setItem('drifter-theme',name);}catch(e){}
    const m=document.querySelector('meta[name="theme-color"]');
    if(m){
      const bg=getComputedStyle(document.documentElement).getPropertyValue('--bg').trim();
      if(bg) m.setAttribute('content',bg);
    }
  }
  const btn=document.getElementById('theme-switch');
  if(btn){
    btn.addEventListener('click',()=>{
      const cur=document.documentElement.dataset.theme||'uncaged';
      setTheme(themes[(themes.indexOf(cur)+1)%themes.length]);
    });
  }
  setTheme(document.documentElement.dataset.theme||'uncaged');
})();

// ── Start ──
connect();
</script>
</body>
</html>"""




SETTINGS_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="theme-color" content="#050708">
<title>DRIFTER SETTINGS</title>
<script>
(function(){try{
  var t=localStorage.getItem('drifter_theme')||'auto';
  if(t==='auto') t=window.matchMedia('(prefers-color-scheme: light)').matches?'light':'dark';
  if(t==='light'){
    document.documentElement.setAttribute('data-theme','light');
    var m=document.querySelector('meta[name=theme-color]');
    if(m) m.setAttribute('content','#f4f6f8');
  }
}catch(e){}})();
</script>
<style>
*{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent}
:root{
  --bg:#050708;--bg-elev:#0c1013;--card:#12181c;--border:#1f2933;--border-hi:#2a3640;
  --text:#e7eef4;--text-dim:#8a98a5;--text-mute:#4b5763;--dim:#8a98a5;
  --accent:#22d3ee;--accent-glow:rgba(34,211,238,.35);
  --ok:#4ade80;--info:#60a5fa;--amber:#fbbf24;--red:#f87171;
  --radius-sm:6px;--radius:10px;--radius-lg:14px;
  --safe-bottom:env(safe-area-inset-bottom,0px);
  --safe-top:env(safe-area-inset-top,0px);
  --ease:cubic-bezier(.4,0,.2,1);
  /* Type scale */
  --fs-xs:11px;--fs-sm:13px;--fs-md:15px;--fs-lg:20px;
  --fs-val:28px;--fs-val-lg:44px;
}
html,body{background:var(--bg);color:var(--text);overscroll-behavior:none}
body{
  font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;
  font-feature-settings:'tnum' 1,'ss01' 1;
  overflow-x:hidden;-webkit-font-smoothing:antialiased;
  padding:calc(18px + var(--safe-top)) 16px calc(96px + var(--safe-bottom));
  max-width:760px;margin:0 auto;
  background:
    radial-gradient(1200px 600px at 50% -150px,#0e1c23 0%,transparent 60%),
    var(--bg);
}
h1{
  font-size:20px;letter-spacing:8px;color:var(--accent);margin-bottom:18px;
  text-align:center;font-weight:700;text-shadow:0 0 18px var(--accent-glow);
}

/* Grouped section (iOS-style) */
.section{
  background:linear-gradient(180deg,var(--card) 0%,#0f141a 100%);
  border:1px solid var(--border);border-radius:var(--radius-lg);
  padding:0;margin-bottom:20px;overflow:hidden;
}
.section h2{
  font-size:10px;color:var(--text-mute);letter-spacing:3px;font-weight:600;
  padding:14px 16px 6px;text-transform:uppercase;
}
.section h2 + .field{border-top:1px solid var(--border)}
.field{
  display:flex;flex-wrap:wrap;align-items:center;gap:12px;
  padding:12px 16px;border-bottom:1px solid var(--border);
  transition:background .15s var(--ease);
}
.field:last-child{border-bottom:none}
.field:active{background:rgba(255,255,255,.02)}
.field label{flex:1 1 180px;font-size:13px;color:var(--text);font-weight:500;line-height:1.3}
.field .hint{width:100%;font-size:11px;color:var(--text-mute);line-height:1.4;margin-top:2px}

/* Inputs */
.field input[type="number"],
.field input[type="text"],
.field select{
  background:var(--bg);border:1px solid var(--border);color:var(--text);
  font-family:inherit;font-size:13px;padding:10px 12px;
  border-radius:var(--radius-sm);width:100%;max-width:280px;min-height:44px;outline:none;
  font-variant-numeric:tabular-nums;
  transition:border-color .15s var(--ease),box-shadow .15s var(--ease);
}
.field input[type="number"]:focus,
.field input[type="text"]:focus,
.field select:focus{
  border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-glow);
}
.field select{
  appearance:none;-webkit-appearance:none;
  background-image:url("data:image/svg+xml;charset=utf-8,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='8' viewBox='0 0 12 8'%3E%3Cpath fill='%238a98a5' d='M6 8 0 0h12z'/%3E%3C/svg%3E");
  background-repeat:no-repeat;background-position:right 10px center;
  padding-right:28px;
}

/* iOS-style toggle switch (wraps native checkbox) */
.switch{
  position:relative;display:inline-block;width:48px;height:28px;flex-shrink:0;
}
.switch input{opacity:0;width:0;height:0}
.switch .slider{
  position:absolute;inset:0;cursor:pointer;background:var(--border-hi);
  border-radius:999px;transition:background .25s var(--ease);
}
.switch .slider::before{
  content:"";position:absolute;left:2px;top:2px;width:24px;height:24px;
  background:#fff;border-radius:50%;
  transition:transform .25s var(--ease),box-shadow .25s var(--ease);
  box-shadow:0 2px 6px rgba(0,0,0,.4);
}
.switch input:checked + .slider{background:var(--accent);box-shadow:0 0 12px var(--accent-glow)}
.switch input:checked + .slider::before{transform:translateX(20px)}

/* Save button — sticky at the bottom above the tab bar */
.save-bar{
  position:fixed;left:0;right:0;
  bottom:calc(64px + var(--safe-bottom));
  padding:10px 16px;
  background:linear-gradient(180deg,rgba(5,7,8,0),rgba(5,7,8,.95) 40%);
  z-index:150;
}
.save-btn{
  display:block;width:100%;max-width:760px;margin:0 auto;
  padding:14px;background:var(--accent);color:#001519;
  font-family:inherit;font-size:13px;font-weight:700;letter-spacing:3px;
  border:none;border-radius:var(--radius);cursor:pointer;
  transition:transform .1s var(--ease),opacity .2s var(--ease);
  box-shadow:0 8px 24px -6px var(--accent-glow);
}
.save-btn:active{transform:scale(.98)}
.save-btn:disabled{opacity:.5;cursor:not-allowed}

.toast{
  position:fixed;top:calc(16px + var(--safe-top));left:50%;transform:translateX(-50%);
  padding:10px 22px;border-radius:999px;font-size:12px;font-family:inherit;letter-spacing:1px;
  z-index:9999;opacity:0;transition:opacity .25s var(--ease),transform .25s var(--ease);pointer-events:none;
  backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);
  box-shadow:0 8px 24px -6px rgba(0,0,0,.6);
}
.toast.show{opacity:1;transform:translate(-50%,6px)}
.toast.ok{background:rgba(74,222,128,.18);color:var(--ok);border:1px solid rgba(74,222,128,.4)}
.toast.err{background:rgba(248,113,113,.18);color:var(--red);border:1px solid rgba(248,113,113,.4)}

/* Bottom tab bar (matches dashboard) */
.tabbar{
  position:fixed;left:0;right:0;bottom:0;z-index:200;
  display:grid;grid-template-columns:repeat(3,1fr);
  padding:6px 8px calc(6px + var(--safe-bottom));
  background:linear-gradient(180deg,rgba(5,7,8,.92),var(--bg));
  border-top:1px solid var(--border);
  backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);
}
.tabbar a{
  color:var(--text-dim);font-family:inherit;font-size:var(--fs-xs);letter-spacing:1px;
  text-decoration:none;padding:6px 8px;min-height:48px;
  display:flex;flex-direction:column;align-items:center;gap:4px;
}
.tabbar a.active,.tabbar a:active{color:var(--accent)}
.tabbar .ico{
  width:28px;height:28px;stroke:currentColor;fill:none;stroke-width:1.8;
  stroke-linecap:round;stroke-linejoin:round;display:block;
  transition:transform .2s var(--ease);
}
.tabbar a:active .ico{transform:scale(.92)}
.tabbar a.active .ico{filter:drop-shadow(0 0 6px var(--accent-glow))}

/* (Settings inherits the uncaged/ghost/drift theme set on :root from
   the dashboard side. No per-theme overrides needed here.) */
</style>
</head>
<body>
<h1>SETTINGS</h1>

<div class="section">
<h2>Alert thresholds</h2>
<div class="field">
  <label for="coolant_amber">Coolant amber</label>
  <input type="number" id="coolant_amber" step="1">
  <div class="hint">Coolant temp warning level (default 104&deg;C)</div>
</div>
<div class="field">
  <label for="coolant_red">Coolant red</label>
  <input type="number" id="coolant_red" step="1">
  <div class="hint">Coolant temp critical level (default 108&deg;C)</div>
</div>
<div class="field">
  <label for="voltage_undercharge">Voltage undercharge</label>
  <input type="number" id="voltage_undercharge" step="0.1">
  <div class="hint">Low alternator voltage warning (default 13.2&thinsp;V)</div>
</div>
<div class="field">
  <label for="voltage_critical">Voltage critical</label>
  <input type="number" id="voltage_critical" step="0.1">
  <div class="hint">Critical low voltage threshold (default 12.0&thinsp;V)</div>
</div>
<div class="field">
  <label for="stft_lean_idle">STFT lean idle</label>
  <input type="number" id="stft_lean_idle" step="0.5">
  <div class="hint">Short-term fuel trim lean threshold at idle (default 12.0%)</div>
</div>
<div class="field">
  <label for="ltft_lean_warn">LTFT lean warn</label>
  <input type="number" id="ltft_lean_warn" step="0.5">
  <div class="hint">Long-term fuel trim lean warning (default 15.0%)</div>
</div>
<div class="field">
  <label for="ltft_lean_crit">LTFT lean critical</label>
  <input type="number" id="ltft_lean_crit" step="0.5">
  <div class="hint">Long-term fuel trim lean critical (default 25.0%)</div>
</div>
</div>

<div class="section">
<h2>Voice</h2>
<div class="field">
  <label for="voice_cooldown">Voice cooldown (seconds)</label>
  <input type="number" id="voice_cooldown" step="1" min="0">
  <div class="hint">Minimum seconds between voice alerts (default 15)</div>
</div>
<div class="field">
  <label for="tts_engine">TTS engine</label>
  <select id="tts_engine">
    <option value="piper">piper</option>
    <option value="espeak">espeak</option>
  </select>
  <div class="hint">Text-to-speech engine for voice alerts</div>
</div>
<div class="field">
  <label for="voice_min_level">Minimum alert level</label>
  <select id="voice_min_level">
    <option value="0">All alerts</option>
    <option value="1">Info and above</option>
    <option value="2">Amber and above</option>
    <option value="3">Red only</option>
  </select>
  <div class="hint">Only voice alerts at or above this severity level</div>
</div>
</div>

<div class="section">
<h2>Display</h2>
<div class="field">
  <label for="theme">Theme</label>
  <select id="theme">
    <option value="auto">Match system</option>
    <option value="dark">Dark</option>
    <option value="light">Light</option>
  </select>
  <div class="hint">Applies instantly on this device &mdash; stored locally, not synced to the vehicle</div>
</div>
<div class="field">
  <label for="temp_unit">Temperature unit</label>
  <select id="temp_unit">
    <option value="C">Celsius (&deg;C)</option>
    <option value="F">Fahrenheit (&deg;F)</option>
  </select>
  <div class="hint">Temperature display unit for dashboard</div>
</div>
<div class="field">
  <label for="pressure_unit">Pressure unit</label>
  <select id="pressure_unit">
    <option value="PSI">PSI</option>
    <option value="kPa">kPa</option>
    <option value="bar">bar</option>
  </select>
  <div class="hint">Tire pressure display unit</div>
</div>
</div>

<div class="section">
<h2>Mechanic (LLM)</h2>
<div class="field">
  <label for="llm_model">Model name</label>
  <input type="text" id="llm_model" placeholder="(use default)">
  <div class="hint">Ollama model for mechanic chat (empty = config default)</div>
</div>
<div class="field">
  <label for="llm_max_tokens">Max tokens</label>
  <input type="number" id="llm_max_tokens" step="50" min="50">
  <div class="hint">Maximum response token length (default 500)</div>
</div>
<div class="field">
  <label for="llm_tools_enabled">Tool calling</label>
  <label class="switch">
    <input type="checkbox" id="llm_tools_enabled">
    <span class="slider"></span>
  </label>
  <div class="hint">Allow the LLM to execute diagnostic tool calls</div>
</div>
</div>

<div class="section">
<h2>Data</h2>
<div class="field">
  <label for="data_retention_days">Retention (days)</label>
  <input type="number" id="data_retention_days" step="1" min="1">
  <div class="hint">Days to keep logged data before purging (default 90)</div>
</div>
</div>

<div class="save-bar">
  <button class="save-btn" id="save-btn">SAVE</button>
</div>

<div class="toast" id="toast"></div>

<nav class="tabbar" aria-label="Primary">
  <a href="/" aria-label="Dashboard">
    <svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3a9 9 0 0 0-9 9h3M12 3a9 9 0 0 1 9 9h-3M12 3v4M5.3 6.3l2.1 2.1M18.7 6.3l-2.1 2.1M12 12l5-4"/><circle cx="12" cy="12" r="1.5"/></svg>
    <span>LIVE</span>
  </a>
  <a href="/settings" class="active" aria-label="Settings">
    <svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg>
    <span>SETTINGS</span>
  </a>
</nav>

<script>
const FIELDS = [
  {id:'coolant_amber', type:'number'},
  {id:'coolant_red', type:'number'},
  {id:'voltage_undercharge', type:'number'},
  {id:'voltage_critical', type:'number'},
  {id:'stft_lean_idle', type:'number'},
  {id:'ltft_lean_warn', type:'number'},
  {id:'ltft_lean_crit', type:'number'},
  {id:'voice_cooldown', type:'number'},
  {id:'tts_engine', type:'select'},
  {id:'voice_min_level', type:'select'},
  {id:'temp_unit', type:'select'},
  {id:'pressure_unit', type:'select'},
  {id:'llm_model', type:'text'},
  {id:'llm_max_tokens', type:'number'},
  {id:'llm_tools_enabled', type:'checkbox'},
  {id:'data_retention_days', type:'number'},
];

function showToast(msg, ok) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast show ' + (ok ? 'ok' : 'err');
  setTimeout(() => { t.className = 'toast'; }, 3000);
}

function populate(settings) {
  FIELDS.forEach(f => {
    const el = document.getElementById(f.id);
    if (!el) return;
    const val = settings[f.id];
    if (val === undefined || val === null) return;
    if (f.type === 'checkbox') el.checked = !!val;
    else if (f.type === 'select') el.value = String(val);
    else el.value = val;
  });
}

function gather() {
  const s = {};
  FIELDS.forEach(f => {
    const el = document.getElementById(f.id);
    if (!el) return;
    if (f.type === 'checkbox') s[f.id] = el.checked;
    else if (f.type === 'number') s[f.id] = parseFloat(el.value);
    else s[f.id] = el.value;
  });
  return s;
}

// ── Theme picker ────────────────────────────────────────────────
// Stored only in localStorage so every client can pick its own theme
// without pushing it to the server or to the other services.
function applyTheme(mode) {
  let effective = mode;
  if (effective === 'auto') {
    effective = window.matchMedia('(prefers-color-scheme: light)').matches
                  ? 'light' : 'dark';
  }
  const html = document.documentElement;
  if (effective === 'light') html.setAttribute('data-theme', 'light');
  else html.removeAttribute('data-theme');
  const m = document.querySelector('meta[name=theme-color]');
  if (m) m.setAttribute('content', effective === 'light' ? '#f4f6f8' : '#050708');
}
(function initTheme() {
  const sel = document.getElementById('theme');
  if (!sel) return;
  const saved = (localStorage.getItem('drifter_theme') || 'auto');
  sel.value = saved;
  sel.addEventListener('change', () => {
    try { localStorage.setItem('drifter_theme', sel.value); } catch (e) {}
    applyTheme(sel.value);
  });
  // Follow OS preference live when mode is 'auto'.
  window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', () => {
    if ((localStorage.getItem('drifter_theme') || 'auto') === 'auto') applyTheme('auto');
  });
})();

fetch('/api/settings')
  .then(r => r.json())
  .then(populate)
  .catch(() => showToast('Failed to load settings', false));

document.getElementById('save-btn').addEventListener('click', function() {
  const btn = this;
  btn.disabled = true;
  btn.textContent = 'SAVING...';
  fetch('/api/settings', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(gather()),
  })
  .then(r => r.json())
  .then(d => {
    if (d.ok) showToast('Settings saved', true);
    else showToast(d.error || 'Save failed', false);
  })
  .catch(() => showToast('Network error', false))
  .finally(() => { btn.disabled = false; btn.textContent = 'SAVE'; });
});
</script>
</body>
</html>"""



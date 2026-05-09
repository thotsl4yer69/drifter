"""
MZ1312 DRIFTER — situational map page (/map/ble)

Self-contained Leaflet map. All assets vendored at /static/leaflet/ —
phones tethered to the MZ1312_DRIFTER hotspot can't reliably reach
unpkg, so we never depend on the public CDN.

Layers (toggled by parent cockpit chips via postMessage):
  ble     — BLE detections from ble_history.db (already had GPS)
  adsb    — live ADS-B aircraft from /api/wardrive
  police  — persistent-contact halos (mac matches /api/ble/persistent)
  drone   — placeholder (no upstream signal source today)
  ap      — placeholder (wardrive WiFi has no per-AP GPS)

Basemaps: OSM (default for first paint, reliable offline-style) and
Esri World Imagery (hybrid view with reference labels overlay) — toggle
via the "MAP / SAT" button.

UNCAGED TECHNOLOGY — EST 1991
"""

BLE_MAP_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<title>DRIFTER — situational map</title>
<link rel="stylesheet" href="/static/leaflet/leaflet.css">
<style>
  :root { --bg:#000; --fg:#dcdcdc; --dim:#7a7a7a; --accent:#ffae42; --alert:#ff5151; }
  html,body { margin:0; padding:0; height:100%; background:var(--bg); color:var(--fg); font-family:system-ui,sans-serif; }
  /* Iframe map fills the whole frame — the cockpit chip row above
     drives layer toggles via postMessage, so the in-frame bar is
     hidden to stop it visually colliding with the chips. */
  #map { position:absolute; top:0; bottom:0; left:0; right:0; background:#111; }
  #bar { display:none; }
  #bar h1 { margin:0; font-size:13px; letter-spacing:.1em; color:var(--accent); flex:0 0 auto; }
  #bar .spacer { flex:1; }
  #bar select, #bar button {
    background:#181818; color:var(--fg); border:1px solid #2a2a2a;
    padding:5px 10px; font-size:12px; border-radius:3px; font-family:inherit;
  }
  #bar button { cursor:pointer; }
  #bar button:hover { border-color:var(--accent); }
  #bar button.on { border-color:var(--accent); color:var(--accent); }
  #status { color:var(--dim); font-size:11px; }
  .leaflet-popup-content-wrapper { background:#111; color:var(--fg); border-radius:4px; }
  .leaflet-popup-tip { background:#111; }
  .pulse { border-radius:50%; animation: pulse-ring 1.6s ease-out infinite; }
  @keyframes pulse-ring {
    0%   { box-shadow: 0 0 0 0 rgba(255,81,81,0.7); }
    70%  { box-shadow: 0 0 0 18px rgba(255,81,81,0); }
    100% { box-shadow: 0 0 0 0 rgba(255,81,81,0); }
  }
  /* ADS-B aircraft glyph — rotated by track via inline transform. */
  .plane-icon {
    width:22px; height:22px; line-height:22px; text-align:center;
    color:#67e8f9; font-size:18px; text-shadow:0 0 4px rgba(103,232,249,.7);
    pointer-events:auto; user-select:none;
  }
  /* Persistent-contact halo — sits underneath BLE marker. */
  .persist-halo {
    border-radius:50%; border:2px solid rgba(255,174,66,0.55);
    box-shadow:0 0 12px rgba(255,174,66,0.35);
    pointer-events:none;
  }
</style>
</head>
<body>
<div id="bar">
  <h1>DRIFTER MAP</h1>
  <select id="window">
    <option value="drive" selected>Current drive</option>
    <option value="3600">Last 1h</option>
    <option value="86400">Last 24h</option>
    <option value="604800">Last 7d</option>
  </select>
  <button id="basemap" title="Toggle basemap">SAT</button>
  <button id="refresh">Refresh</button>
  <span class="spacer"></span>
  <span id="status">loading&hellip;</span>
</div>
<div id="map"></div>

<script src="/static/leaflet/leaflet.js"></script>
<script>
delete L.Icon.Default.prototype._getIconUrl;
L.Icon.Default.mergeOptions({
  iconUrl:       '/static/leaflet/marker-icon.png',
  iconRetinaUrl: '/static/leaflet/marker-icon-2x.png',
  shadowUrl:     '/static/leaflet/marker-shadow.png',
});

const TARGET_COLORS = { axon:'#ff5151', airtag:'#3b82f6', tile:'#22c55e' };
function colorFor(t){ return TARGET_COLORS[t] || '#9ca3af'; }
function esc(s){ const d=document.createElement('div'); d.textContent=String(s==null?'':s); return d.innerHTML; }

// Default centre: DRIFTER home (Long Gully). Replaced as soon as
// /api/feeds/summary lands with the live origin (or a real GPS fix).
const map = L.map('map', { zoomControl: true, worldCopyJump: true })
  .setView([-36.7596, 144.2531], 11);

// Track the last time the operator manually panned/zoomed. Auto-recenter
// (origin updates, browser-geo broadcasts) is suppressed for 30s after
// any manual interaction so the operator's view sticks.
let _lastUserPanTs = 0;
const PAN_DEBOUNCE_MS = 30000;
map.on('dragstart zoomstart', () => { _lastUserPanTs = Date.now(); });
function _canAutoRecenter(){
  return (Date.now() - _lastUserPanTs) > PAN_DEBOUNCE_MS;
}

// On load, snap to the feeds-summary origin so first paint shows the
// operator's actual region, not a degenerate (0,0) world view.
fetch('/api/feeds/summary').then(r => r.ok ? r.json() : null).then(s => {
  const o = s && s.origin;
  if (o && typeof o.lat === 'number' && typeof o.lon === 'number') {
    map.setView([o.lat, o.lon], 13);
    renderOrigin(o.lat, o.lon, o.source || 'home');
  }
}).catch(() => {});

// ── Basemaps ─────────────────────────────────────────────────────
// OSM default (works without external sat tiles when offline-ish).
// Esri World Imagery for hybrid sat view + a separate Esri reference
// labels layer so streets/POI labels stay legible on satellite.
const tileOSM = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19, attribution: '&copy; OpenStreetMap',
});
const tileSat = L.tileLayer(
  'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
  { maxZoom: 19, attribution: 'Tiles &copy; Esri &mdash; World Imagery' });
const tileLabels = L.tileLayer(
  'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
  { maxZoom: 19, opacity: 0.85 });
let basemapMode = 'osm';
tileOSM.addTo(map);
function setBasemap(mode){
  basemapMode = mode;
  tileOSM.remove(); tileSat.remove(); tileLabels.remove();
  if (mode === 'sat') { tileSat.addTo(map); tileLabels.addTo(map); }
  else                { tileOSM.addTo(map); }
  const b = document.getElementById('basemap');
  if (b) { b.classList.toggle('on', mode==='sat'); b.textContent = mode==='sat' ? 'MAP' : 'SAT'; }
}
document.getElementById('basemap').addEventListener('click', ()=>{
  setBasemap(basemapMode === 'sat' ? 'osm' : 'sat');
});

// ── Layer groups (toggled by parent cockpit chips) ──────────────
const layers = {
  ble:       L.layerGroup().addTo(map),
  adsb:      L.layerGroup().addTo(map),
  police:    L.layerGroup().addTo(map),    // PERSIST chip in cockpit
  drone:     L.layerGroup(),               // placeholder, no source yet
  ap:        L.layerGroup(),               // placeholder, no GPS per AP
  incidents: L.layerGroup().addTo(map),    // EMV incident pins
  poi:       L.layerGroup(),               // emergency-services POIs
  radar:     L.layerGroup(),               // BoM radar image overlay
  weather:   L.layerGroup().addTo(map),    // origin pin + future overlays
};
const layerVisible = {
  ble:true, adsb:true, police:true, drone:false, ap:false,
  incidents:true, poi:false, radar:false, weather:true,
};
let _radarOverlay = null;

// ── Persistent-contact identities (refreshed by loadAndRender) ──
let persistentIdentities = new Set();

// ── BLE detections ──────────────────────────────────────────────
function popupBle(d) {
  const ts = d.ts ? new Date(d.ts * 1000).toISOString().replace('T',' ').slice(0,19) : '?';
  const target = esc(d.target || '?');
  const tcolor = colorFor(d.target);
  return `
    <div style="font-size:12px;line-height:1.4">
      <div style="color:${tcolor};font-weight:bold">${target}${d.is_alert ? ' &#9888;' : ''}</div>
      <div>${esc(ts)}</div>
      <div>MAC: ${esc(d.mac || '?')}</div>
      <div>RSSI: ${d.rssi != null ? esc(d.rssi)+' dBm' : '?'}</div>
      ${d.adv_name ? '<div>Name: '+esc(d.adv_name)+'</div>' : ''}
      ${d.manufacturer_id ? '<div>Mfr: '+esc(d.manufacturer_id)+'</div>' : ''}
      <div style="color:#7a7a7a;margin-top:4px">drive: ${esc(d.drive_id || '?')}</div>
    </div>`;
}

async function loadBleAndPersistent() {
  const sel = document.getElementById('window').value;
  let url;
  if (sel === 'drive') {
    const dRes = await fetch('/api/ble/history?limit=1').catch(()=>null);
    if (!dRes || !dRes.ok) return { plotted:0, total:0, missing:0 };
    const drv = (await dRes.json()).drive_id;
    if (!drv) { layers.ble.clearLayers(); layers.police.clearLayers(); return {plotted:0,total:0,missing:0}; }
    url = `/api/ble/history?drive_id=${encodeURIComponent(drv)}&limit=2000`;
  } else {
    const since = (Date.now()/1000) - parseInt(sel, 10);
    url = `/api/ble/history?since=${since}&limit=2000`;
  }

  // Fire history + persistent in parallel — they're independent.
  const [hRes, pRes] = await Promise.all([
    fetch(url).catch(()=>null),
    fetch('/api/ble/persistent?window=604800').catch(()=>null),
  ]);

  // Persistent identities → set of `mac` strings whose history rows
  // should also get the orange halo. Identity is `target:mac` for the
  // common case, so derive matching MACs from the contacts list.
  persistentIdentities = new Set();
  if (pRes && pRes.ok) {
    try {
      const pj = await pRes.json();
      for (const c of (pj.contacts || [])) {
        // identity is "target:mac" (mfr) or "target:adv_name" — the
        // common shape we care about for halo plotting is the mac one.
        const idParts = String(c.identity||'').split(':');
        if (idParts.length >= 2 && /^[0-9a-fA-F:]{11,}$/.test(idParts.slice(1).join(':'))) {
          persistentIdentities.add(idParts.slice(1).join(':').toUpperCase());
        }
      }
    } catch(e) {}
  }

  layers.ble.clearLayers();
  layers.police.clearLayers();

  if (!hRes || !hRes.ok) return {plotted:0,total:0,missing:0};
  const body = await hRes.json();
  const dets = body.detections || [];
  const bounds = [];
  let plotted = 0, missing = 0;
  for (const d of dets) {
    if (d.lat == null || d.lng == null) { missing++; continue; }
    const m = L.circleMarker([d.lat, d.lng], {
      radius: 7,
      color: colorFor(d.target),
      fillColor: colorFor(d.target),
      fillOpacity: 0.85,
      weight: d.is_alert ? 3 : 1,
      className: d.is_alert ? 'pulse' : '',
    }).bindPopup(popupBle(d));
    m.addTo(layers.ble);

    // Persistent-contact halo — same coord, larger orange ring.
    if (d.mac && persistentIdentities.has(String(d.mac).toUpperCase())) {
      L.circleMarker([d.lat, d.lng], {
        radius: 14, color:'#ffae42', fill:false, weight:2, opacity:0.7,
      }).addTo(layers.police);
    }
    bounds.push([d.lat, d.lng]);
    plotted++;
  }
  if (bounds.length) map.fitBounds(bounds, { padding:[40,40], maxZoom:16 });
  return { plotted, total: body.count || dets.length, missing };
}

// ── ADS-B aircraft (live, polled) ───────────────────────────────
function planeIcon(track){
  return L.divIcon({
    className: 'plane-icon',
    html: `<div style="transform:rotate(${(track||0)|0}deg)">&#9992;</div>`,
    iconSize: [22, 22], iconAnchor: [11, 11],
  });
}
async function refreshAdsb() {
  let r;
  try { r = await fetch('/api/wardrive'); } catch(e) { return 0; }
  if (!r.ok) return 0;
  const j = await r.json();
  const aircraft = (j.adsb && j.adsb.aircraft) || [];
  layers.adsb.clearLayers();
  let n = 0;
  for (const a of aircraft) {
    const lat = a.lat, lng = a.lon;
    if (lat == null || lng == null) continue;
    const flight = String(a.flight || a.hex || '?').trim();
    const alt = a.altitude != null ? Math.round(a.altitude)+' ft' : '?';
    const spd = a.speed    != null ? Math.round(a.speed)+' kt'   : '?';
    const trk = a.track    != null ? Math.round(a.track)+'°'      : '?';
    L.marker([lat, lng], { icon: planeIcon(a.track), title: flight })
      .bindPopup(`
        <div style="font-size:12px;line-height:1.4">
          <div style="color:#67e8f9;font-weight:bold">&#9992; ${esc(flight)}</div>
          <div>alt: ${esc(alt)} &middot; spd: ${esc(spd)}</div>
          <div>trk: ${esc(trk)} &middot; hex: ${esc(a.hex||'?')}</div>
        </div>`)
      .addTo(layers.adsb);
    n++;
  }
  return n;
}

// ── Master refresh ──────────────────────────────────────────────
async function loadAndRender() {
  const status = document.getElementById('status');
  status.textContent = 'loading…';
  const [ble, planes] = await Promise.all([loadBleAndPersistent(), refreshAdsb()]);
  const halos = layers.police.getLayers().length;
  status.textContent =
    `BLE ${ble.plotted}/${ble.total}` +
    (ble.missing ? ` (${ble.missing} no-GPS)` : '') +
    ` · ADS-B ${planes}` +
    (halos ? ` · persist ${halos}` : '');
}

document.getElementById('refresh').addEventListener('click', loadAndRender);
document.getElementById('window').addEventListener('change', loadAndRender);
loadAndRender();
// ADS-B aircraft move — refresh on a faster cadence than full reload.
setInterval(refreshAdsb, 10000);
// BLE history grows slower; full reload every 60s.
setInterval(loadAndRender, 60000);

// ── Cross-frame controls (parent cockpit drives the map) ────────
window.addEventListener('message', (e) => {
  const d = e.data || {};
  if (d.type === 'recenter' && typeof d.lat === 'number' && typeof d.lng === 'number') {
    // Honour the same pan-debounce as origin updates — the operator's
    // manual pan should stick over a stale browser-geo broadcast.
    if (_canAutoRecenter()) {
      map.setView([d.lat, d.lng], d.zoom || 14, { animate: true });
    }
    if (!window._youPin) {
      window._youPin = L.circleMarker([d.lat, d.lng], {
        radius: 9, color:'#ffffff', fillColor:'#ffae42', fillOpacity:0.9,
        weight: 2, className: 'pulse',
      }).addTo(map).bindPopup('You');
    } else {
      window._youPin.setLatLng([d.lat, d.lng]);
    }
  }
  if (d.type === 'layer' && d.layer && layers[d.layer]) {
    const showing = d.action === 'show';
    layerVisible[d.layer] = showing;
    if (showing) layers[d.layer].addTo(map);
    else         layers[d.layer].remove();
  }
  if (d.type === 'incidents' && Array.isArray(d.items)) {
    renderIncidents(d.items);
  }
  if (d.type === 'aircraft' && Array.isArray(d.items)) {
    renderAircraftFromFeed(d.items);
  }
  if (d.type === 'poi' && Array.isArray(d.items)) {
    renderPois(d.items);
  }
  if (d.type === 'radar' && d.meta) {
    renderRadar(d.meta);
  }
  if (d.type === 'origin' && typeof d.lat === 'number' && typeof d.lon === 'number') {
    const newSource = d.source || 'home';
    // Recenter only when the source flips (home↔gps) AND the operator
    // hasn't manually panned in the last 30s. The pin always moves so
    // the orange/grey marker is current regardless.
    if (window._originSource !== newSource && _canAutoRecenter()) {
      map.setView([d.lat, d.lon], 13, { animate: true });
    }
    window._originSource = newSource;
    renderOrigin(d.lat, d.lon, newSource);
  }
});

// ── Phase 3 layer renderers ────────────────────────────────────
const EMV_COLORS = {
  fire:'#ff5151', flood:'#3b82f6', storm:'#a855f7',
  mva:'#fbbf24', rta:'#fbbf24', other:'#f97316',
};
function emvColor(cat){
  const k = String(cat||'').toLowerCase();
  if(k.indexOf('fire') >= 0) return EMV_COLORS.fire;
  if(k.indexOf('flood') >= 0) return EMV_COLORS.flood;
  if(k.indexOf('storm') >= 0) return EMV_COLORS.storm;
  if(k.indexOf('mva') >= 0 || k.indexOf('rta') >= 0 || k.indexOf('crash') >= 0 || k.indexOf('vehicle') >= 0) return EMV_COLORS.mva;
  return EMV_COLORS.other;
}
function renderIncidents(items){
  layers.incidents.clearLayers();
  for (const it of items) {
    const lat = (typeof it.lat === 'number') ? it.lat : null;
    const lng = (typeof it.lng === 'number') ? it.lng : (typeof it.lon === 'number' ? it.lon : null);
    if (lat == null || lng == null) continue;
    const col = emvColor(it.category1 || it.category);
    const dist = (typeof it.distance_km === 'number') ? it.distance_km.toFixed(1)+' km' : '?';
    const popup = `<div style="font-size:12px;line-height:1.4">
      <div style="color:${col};font-weight:bold">${esc(it.category1||it.category||'?')}</div>
      <div>${esc(it.category2||'')}</div>
      <div>${esc(it.location||it.suburb||'')}</div>
      <div>${esc(it.status||'')} · ${esc(it.sourceOrg||'')} · ${esc(dist)}</div>
      <div style="color:#7a7a7a;margin-top:4px">${esc(it.updated||it.ts||'')}</div>
    </div>`;
    L.circleMarker([lat, lng], {
      radius: 8, color: col, fillColor: col, fillOpacity: 0.7, weight: 2,
    }).bindPopup(popup).addTo(layers.incidents);
  }
}
function renderAircraftFromFeed(items){
  layers.adsb.clearLayers();
  for (const a of items) {
    const lat = (typeof a.lat === 'number') ? a.lat : null;
    const lng = (typeof a.lng === 'number') ? a.lng : (typeof a.lon === 'number' ? a.lon : null);
    if (lat == null || lng == null) continue;
    const flight = String(a.flight || a.hex || '?').trim();
    const alt = (a.altitude!=null) ? Math.round(a.altitude)+' ft' : '?';
    const dist = (typeof a.distance_km === 'number') ? a.distance_km.toFixed(1)+' km' : '?';
    const isIx = a.interesting === true;
    const col = isIx ? '#ff5151' : '#67e8f9';
    const sz = isIx ? 28 : 22;
    const icon = L.divIcon({
      className: 'plane-icon',
      html: `<div style="transform:rotate(${(a.track||0)|0}deg);color:${col};font-size:${sz-4}px;text-shadow:0 0 6px ${col}">&#9992;</div>`,
      iconSize: [sz, sz], iconAnchor: [sz/2, sz/2],
    });
    L.marker([lat, lng], { icon, title: flight })
      .bindPopup(`<div style="font-size:12px;line-height:1.4">
        <div style="color:${col};font-weight:bold">&#9992; ${esc(flight)}${isIx?' ★':''}</div>
        <div>alt: ${esc(alt)} · ${esc(dist)}</div>
        <div>hex: ${esc(a.hex||'?')}</div>
      </div>`)
      .addTo(layers.adsb);
  }
}
const POI_COLORS = {
  police:'#3b82f6', fire_station:'#ff5151',
  hospital:'#22c55e', ambulance_station:'#f3f4f6',
};
function renderPois(items){
  layers.poi.clearLayers();
  for (const p of items) {
    const lat = (typeof p.lat === 'number') ? p.lat : null;
    const lng = (typeof p.lng === 'number') ? p.lng : (typeof p.lon === 'number' ? p.lon : null);
    if (lat == null || lng == null) continue;
    const col = POI_COLORS[p.kind] || '#9ca3af';
    const dist = (typeof p.distance_km === 'number') ? p.distance_km.toFixed(1)+' km' : '?';
    const popup = `<div style="font-size:12px;line-height:1.4">
      <div style="color:${col};font-weight:bold">${esc(p.name||p.kind||'?')}</div>
      <div>${esc(p.operator||'')}</div>
      <div>${esc(p.kind||'')} · ${esc(dist)}</div>
    </div>`;
    L.circleMarker([lat, lng], {
      radius: 6, color: col, fillColor: col, fillOpacity: 0.8, weight: 1,
    }).bindPopup(popup).addTo(layers.poi);
  }
}
function renderRadar(meta){
  if (!meta || !Array.isArray(meta.bbox) || meta.bbox.length !== 2) {
    // No bbox — just refresh existing overlay if present.
    if (_radarOverlay && typeof _radarOverlay.setUrl === 'function') {
      _radarOverlay.setUrl('/api/radar.gif?t=' + Date.now());
    }
    return;
  }
  if (_radarOverlay) {
    layers.radar.removeLayer(_radarOverlay);
    _radarOverlay = null;
  }
  _radarOverlay = L.imageOverlay('/api/radar.gif?t=' + Date.now(), meta.bbox, { opacity: 0.5 });
  _radarOverlay.addTo(layers.radar);
}
function renderOrigin(lat, lon, source){
  layers.weather.clearLayers();
  const col = (source === 'gps') ? '#ffae42' : '#9ca3af';
  L.circleMarker([lat, lon], {
    radius: 7, color: col, fillColor: col, fillOpacity: 0.9, weight: 2,
  }).bindPopup('Origin (' + esc(source) + ')').addTo(layers.weather);
}
</script>
</body>
</html>
"""

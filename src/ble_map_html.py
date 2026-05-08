"""
MZ1312 DRIFTER — BLE forensic map page (/map/ble)

Self-contained Leaflet map. All assets vendored at /static/leaflet/ —
phones tethered to the MZ1312_DRIFTER hotspot can't reliably reach
unpkg, so we never depend on the public CDN.

UNCAGED TECHNOLOGY — EST 1991
"""

BLE_MAP_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<title>DRIFTER — BLE map</title>
<link rel="stylesheet" href="/static/leaflet/leaflet.css">
<style>
  :root { --bg:#000; --fg:#dcdcdc; --dim:#7a7a7a; --accent:#ffae42; --alert:#ff5151; }
  html,body { margin:0; padding:0; height:100%; background:var(--bg); color:var(--fg); font-family:system-ui,sans-serif; }
  #map { position:absolute; top:50px; bottom:0; left:0; right:0; background:#111; }
  #bar {
    position:absolute; top:0; left:0; right:0; height:50px; padding:8px 12px;
    box-sizing:border-box; background:#0a0a0a; border-bottom:1px solid #222;
    display:flex; gap:10px; align-items:center; font-size:12px; z-index:1000;
  }
  #bar h1 { margin:0; font-size:13px; letter-spacing:.1em; color:var(--accent); flex:0 0 auto; }
  #bar .spacer { flex:1; }
  #bar select, #bar button {
    background:#181818; color:var(--fg); border:1px solid #2a2a2a;
    padding:5px 10px; font-size:12px; border-radius:3px;
  }
  #bar button { cursor:pointer; }
  #bar button:hover { border-color:var(--accent); }
  #status { color:var(--dim); font-size:11px; }
  .leaflet-popup-content-wrapper { background:#111; color:var(--fg); border-radius:4px; }
  .leaflet-popup-tip { background:#111; }
  .pulse {
    border-radius:50%;
    animation: pulse-ring 1.6s ease-out infinite;
  }
  @keyframes pulse-ring {
    0%   { box-shadow: 0 0 0 0 rgba(255,81,81,0.7); }
    70%  { box-shadow: 0 0 0 18px rgba(255,81,81,0); }
    100% { box-shadow: 0 0 0 0 rgba(255,81,81,0); }
  }
</style>
</head>
<body>
<div id="bar">
  <h1>BLE MAP</h1>
  <select id="window">
    <option value="drive" selected>Current drive</option>
    <option value="3600">Last 1h</option>
    <option value="86400">Last 24h</option>
    <option value="604800">Last 7d</option>
  </select>
  <button id="refresh">Refresh</button>
  <span class="spacer"></span>
  <span id="status">loading&hellip;</span>
</div>
<div id="map"></div>

<script src="/static/leaflet/leaflet.js"></script>
<script>
// Default Leaflet icons reference relative paths under their CSS, but
// our vendored layout uses /static/leaflet/ so re-point the icon URLs.
delete L.Icon.Default.prototype._getIconUrl;
L.Icon.Default.mergeOptions({
  iconUrl:       '/static/leaflet/marker-icon.png',
  iconRetinaUrl: '/static/leaflet/marker-icon-2x.png',
  shadowUrl:     '/static/leaflet/marker-shadow.png',
});

const TARGET_COLORS = {
  axon:    '#ff5151',
  airtag:  '#3b82f6',
  tile:    '#22c55e',
};

const map = L.map('map', { zoomControl: true }).setView([0, 0], 2);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19,
  attribution: '&copy; OpenStreetMap',
}).addTo(map);

let markerLayer = L.layerGroup().addTo(map);

function colorFor(target) {
  return TARGET_COLORS[target] || '#9ca3af';
}

function popupHtml(d) {
  const ts = d.ts ? new Date(d.ts * 1000).toISOString().replace('T',' ').slice(0, 19) : '?';
  return `
    <div style="font-size:12px;line-height:1.4">
      <div style="color:${colorFor(d.target)};font-weight:bold">${d.target}${d.is_alert ? ' &#9888;' : ''}</div>
      <div>${ts}</div>
      <div>MAC: ${d.mac || '?'}</div>
      <div>RSSI: ${d.rssi != null ? d.rssi + ' dBm' : '?'}</div>
      ${d.adv_name ? '<div>Name: ' + d.adv_name + '</div>' : ''}
      ${d.manufacturer_id ? '<div>Mfr: ' + d.manufacturer_id + '</div>' : ''}
      <div style="color:#7a7a7a;margin-top:4px">drive: ${d.drive_id || '?'}</div>
    </div>
  `;
}

async function loadAndRender() {
  const status = document.getElementById('status');
  status.textContent = 'loading…';
  const sel = document.getElementById('window').value;
  let url;
  if (sel === 'drive') {
    const dRes = await fetch('/api/ble/history?limit=1').catch(() => null);
    if (!dRes || !dRes.ok) { status.textContent = 'history unavailable'; return; }
    const drv = (await dRes.json()).drive_id;
    if (!drv) { status.textContent = 'no current drive'; markerLayer.clearLayers(); return; }
    url = `/api/ble/history?drive_id=${encodeURIComponent(drv)}&limit=2000`;
  } else {
    const since = (Date.now() / 1000) - parseInt(sel, 10);
    url = `/api/ble/history?since=${since}&limit=2000`;
  }

  const res = await fetch(url);
  if (!res.ok) { status.textContent = 'error ' + res.status; return; }
  const body = await res.json();
  const dets = body.detections || [];
  markerLayer.clearLayers();

  let plotted = 0, missingGps = 0;
  const bounds = [];
  for (const d of dets) {
    if (d.lat == null || d.lng == null) { missingGps++; continue; }
    const m = L.circleMarker([d.lat, d.lng], {
      radius: 7,
      color: colorFor(d.target),
      fillColor: colorFor(d.target),
      fillOpacity: 0.85,
      weight: d.is_alert ? 3 : 1,
      className: d.is_alert ? 'pulse' : '',
    }).bindPopup(popupHtml(d));
    m.addTo(markerLayer);
    bounds.push([d.lat, d.lng]);
    plotted++;
  }
  if (bounds.length) map.fitBounds(bounds, { padding: [40, 40], maxZoom: 16 });
  status.textContent = `${plotted} plotted${missingGps ? ' · ' + missingGps + ' without GPS' : ''} · ${body.count || dets.length} total`;
}

document.getElementById('refresh').addEventListener('click', loadAndRender);
document.getElementById('window').addEventListener('change', loadAndRender);
loadAndRender();
</script>
</body>
</html>
"""

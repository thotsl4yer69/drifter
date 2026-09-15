import React from 'react';

const mono = { fontFamily: 'JetBrains Mono, monospace' };
const btn = {
  minHeight: 46, padding: '10px 14px', borderRadius: 7,
  border: '1px solid var(--stroke-acc)', background: 'var(--glass-strong)',
  color: 'var(--acc)', cursor: 'pointer', fontSize: 10, letterSpacing: '0.08em',
  fontFamily: 'JetBrains Mono, monospace', textTransform: 'uppercase',
};
const btnDim = { ...btn, borderColor: 'var(--stroke-2)', color: 'var(--fg-mute)' };
const btnCrit = { ...btn, borderColor: 'rgba(var(--red-rgb),0.65)', color: 'var(--red)' };
const selectStyle = {
  ...mono, minHeight: 44, borderRadius: 6, border: '1px solid var(--stroke)',
  background: 'var(--inset-bg)', color: 'var(--fg)', padding: '0 9px', fontSize: 9,
};

async function api(path, body) {
  const init = body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  };
  try {
    const r = await fetch(path, init);
    let data = {};
    try { data = await r.json(); } catch (_) { data = { ok: false, error: `HTTP ${r.status}` }; }
    if (!r.ok && data.ok !== false) data.ok = false;
    return data;
  } catch (err) {
    return { ok: false, error: `field API unavailable: ${String(err?.message || err)}` };
  }
}

function Pill({ children, live, crit }) {
  return <span style={{ ...mono, fontSize: 8, padding: '4px 7px', borderRadius: 999,
    border: `1px solid ${crit ? 'rgba(var(--red-rgb),0.5)' : live ? 'rgba(var(--teal-rgb),0.5)' : 'var(--stroke)'}`,
    color: crit ? 'var(--red)' : live ? 'var(--teal)' : 'var(--fg-dim)', whiteSpace: 'nowrap' }}>{children}</span>;
}

function Card({ title, meta, children, style }) {
  return <section style={{ border: '1px solid var(--stroke)', borderRadius: 10, background: 'var(--glass)', padding: 12, minWidth: 0, ...style }}>
    <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 8, marginBottom: 10, flexWrap: 'wrap' }}>
      <span className="stencil" style={{ fontSize: 9, color: 'var(--fg)' }}>{title}</span>
      {meta ? <span style={{ ...mono, fontSize: 7.5, color: 'var(--fg-deep)' }}>{meta}</span> : null}
    </div>
    {children}
  </section>;
}

function JsonNote({ value }) {
  if (!value) return null;
  const text = typeof value === 'string' ? value : JSON.stringify(value);
  return <div style={{ ...mono, fontSize: 8.5, lineHeight: 1.55, color: 'var(--fg-mute)', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: 140, overflow: 'auto' }}>{text}</div>;
}

function VehiclePanel({ status, refreshStatus }) {
  const [scan, setScan] = React.useState(null);
  const [result, setResult] = React.useState(null);
  const [busy, setBusy] = React.useState('');
  const [pinByMac, setPinByMac] = React.useState({});
  const [wifiPw, setWifiPw] = React.useState({});

  const run = async (label, path, body = {}) => {
    setBusy(label); setResult(null);
    try {
      const out = await api(path, body);
      setResult(out);
      await refreshStatus();
      return out;
    } finally { setBusy(''); }
  };
  const doScan = async () => {
    setBusy('scan');
    const out = await api('/api/field/obd/scan', { seconds: 6 });
    setScan(out); setResult(out); setBusy('');
  };
  const auto = () => run('auto', '/api/field/obd/auto');
  const test = () => run('test', '/api/field/obd/test');

  const cfg = status?.config || {};
  const eff = status?.effective_link || {};
  const service = status?.service || {};
  const explicitLink = String(cfg.DRIFTER_ELM_LINK || '').toLowerCase();
  const configured = cfg.DRIFTER_TRANSPORT === 'elm327' ||
    (['bluetooth', 'bt'].includes(explicitLink) && Boolean(cfg.ELM_BT_MAC)) ||
    (['wifi', 'tcp', 'network'].includes(explicitLink) && Boolean(cfg.ELM_WIFI_HOST)) ||
    (['serial', 'usb', 'tty', 'rfcomm'].includes(explicitLink) && Boolean(cfg.OBD_SERIAL_DEV));
  const mqtt = typeof status?.mqtt === 'object' && status.mqtt ? status.mqtt : {};
  const mqttState = typeof status?.mqtt === 'string'
    ? (status.mqtt.includes('online') ? 'online' : status.mqtt.includes('error') ? 'adapter_error' : 'waiting')
    : String(mqtt.state || 'waiting').toLowerCase();
  const adapterOk = mqtt.adapter_ok === true || ['online', 'ecu_waiting'].includes(mqttState);
  const ecuOk = mqtt.ecu_ok === true || mqttState === 'online';
  const linkBad = ['adapter_error', 'bus_unreachable', 'error'].includes(mqttState);
  const bluetooth = Array.isArray(scan?.bluetooth) ? [...scan.bluetooth].sort((a, b) => Number(Boolean(b.likely_elm)) - Number(Boolean(a.likely_elm))) : [];
  const wifi = Array.isArray(scan?.wifi) ? [...scan.wifi].sort((a, b) => Number(Boolean(b.likely_elm)) - Number(Boolean(a.likely_elm))) : [];
  const serial = Array.isArray(scan?.serial) ? scan.serial : [];

  return <div style={{ display: 'grid', gap: 10 }}>
    <Card title="vehicle link" meta="adapter proof → ECU proof → live PIDs">
      <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap', marginBottom: 9 }}>
        <Pill live={configured}>{configured ? 'ELM CONFIGURED' : 'NOT CONFIGURED'}</Pill>
        <Pill live={service.active}>{service.ActiveState || 'service ?'}</Pill>
        <Pill live={adapterOk} crit={linkBad}>{linkBad ? 'ADAPTER ERROR' : adapterOk ? 'ADAPTER ONLINE' : 'ADAPTER WAITING'}</Pill>
        <Pill live={ecuOk} crit={mqttState === 'bus_unreachable'}>{ecuOk ? 'ECU LINK ONLINE' : 'ECU WAITING'}</Pill>
        {mqtt.protocol ? <Pill live={ecuOk}>{mqtt.protocol}</Pill> : null}
      </div>
      <div style={{ ...mono, fontSize: 10, color: 'var(--fg)', lineHeight: 1.7, overflowWrap: 'anywhere' }}>
        <b>{eff.mode || 'auto'}</b>{eff.bt_mac ? ` · ${eff.bt_mac}` : ''}{eff.wifi_host ? ` · ${eff.wifi_host}:${eff.wifi_port}` : ''}{eff.serial_dev ? ` · ${eff.serial_dev}` : ''}
      </div>
      {mqtt.reason ? <div style={{ ...mono, fontSize: 8.5, color: linkBad ? 'var(--red)' : 'var(--fg-dim)', marginTop: 5 }}>{mqtt.reason}</div> : null}
      <div style={{ ...mono, fontSize: 8.5, color: 'var(--fg-dim)', marginTop: 4 }}>
        DRIFTER separates adapter reachability from Jaguar ECU communication. A visible reader is not treated as a live vehicle link.
      </div>
    </Card>

    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(120px,1fr))', gap: 8 }}>
      <button style={btn} disabled={Boolean(busy)} onClick={auto}>{busy === 'auto' ? 'AUTO…' : 'AUTO DETECT'}</button>
      <button style={btn} disabled={Boolean(busy)} onClick={doScan}>{busy === 'scan' ? 'SCANNING…' : 'SCAN ADAPTERS'}</button>
      <button style={btn} disabled={Boolean(busy)} onClick={test}>{busy === 'test' ? 'TESTING…' : 'TEST ECU'}</button>
    </div>

    {scan ? <Card title="discovered adapters" meta="tap one to prove + save"><div style={{ display: 'grid', gap: 8 }}>
      {bluetooth.map((d) => <div key={d.mac} style={{ borderBottom: '1px dotted var(--edge)', paddingBottom: 8 }}>
        <div style={{ display: 'flex', gap: 7, alignItems: 'center', flexWrap: 'wrap' }}>
          <span style={{ ...mono, fontSize: 9.5, color: 'var(--fg)', flex: '1 1 170px', overflowWrap: 'anywhere' }}>{d.name || 'Bluetooth OBD'} · {d.mac}</span>
          {d.likely_elm ? <Pill live>likely ELM</Pill> : null}<Pill live={d.paired}>{d.paired ? 'paired' : 'unpaired'}</Pill>
        </div>
        <div style={{ display: 'flex', gap: 7, marginTop: 7, flexWrap: 'wrap' }}>
          {d.paired ? <button style={btnDim} onClick={() => run('select', '/api/field/obd/select', { mode: 'bluetooth', mac: d.mac })}>CONNECT + SAVE</button> : <>
            <input aria-label={`PIN ${d.mac}`} value={pinByMac[d.mac] || '1234'} onChange={(e) => setPinByMac((x) => ({ ...x, [d.mac]: e.target.value }))} inputMode="numeric" style={{ ...mono, width: 86, minHeight: 44, borderRadius: 6, border: '1px solid var(--stroke)', background: 'var(--inset-bg)', color: 'var(--fg)', padding: '0 10px' }} />
            <button style={btnDim} onClick={async () => { const p = await run('pair', '/api/field/obd/pair', { mac: d.mac, pin: pinByMac[d.mac] || '1234' }); if (p?.ok) await run('select', '/api/field/obd/select', { mode: 'bluetooth', mac: d.mac }); }}>PAIR + CONNECT</button>
          </>}
        </div>
      </div>)}
      {serial.map((device) => <div key={device} style={{ display: 'flex', alignItems: 'center', gap: 8, borderBottom: '1px dotted var(--edge)', paddingBottom: 8, flexWrap: 'wrap' }}>
        <span style={{ ...mono, fontSize: 9, color: 'var(--fg)', flex: '1 1 170px', overflowWrap: 'anywhere' }}>USB/SERIAL · {device}</span>
        <button style={btnDim} onClick={() => run('select', '/api/field/obd/select', { mode: 'serial', device, baud: 38400 })}>CONNECT + SAVE</button>
      </div>)}
      {wifi.map((n) => <div key={n.ssid} style={{ borderBottom: '1px dotted var(--edge)', paddingBottom: 8 }}>
        <div style={{ display: 'flex', gap: 7, alignItems: 'center', flexWrap: 'wrap' }}><span style={{ ...mono, fontSize: 9, color: 'var(--fg)', flex: '1 1 170px', overflowWrap: 'anywhere' }}>{n.ssid} · signal {n.signal || '?'}</span>{n.likely_elm ? <Pill live>likely ELM</Pill> : null}</div>
        <div style={{ display: 'flex', gap: 7, marginTop: 7, flexWrap: 'wrap' }}>
          <input aria-label={`Wi-Fi password ${n.ssid}`} placeholder={n.security ? 'Wi-Fi password' : 'open network'} value={wifiPw[n.ssid] || ''} onChange={(e) => setWifiPw((x) => ({ ...x, [n.ssid]: e.target.value }))} style={{ ...mono, flex: '1 1 150px', minWidth: 0, minHeight: 44, borderRadius: 6, border: '1px solid var(--stroke)', background: 'var(--inset-bg)', color: 'var(--fg)', padding: '0 10px' }} />
          <button style={btnDim} onClick={async () => { const j = await run('wifi', '/api/field/obd/wifi-connect', { ssid: n.ssid, password: wifiPw[n.ssid] || '' }); if (j?.ok) await auto(); }}>JOIN + AUTO DETECT</button>
        </div>
      </div>)}
      {!bluetooth.length && !wifi.length && !serial.length ? <JsonNote value="No candidates seen. Confirm the ELM327 is powered in the OBD-II port, ignition is RUN, then scan again." /> : null}
    </div></Card> : null}
    {result ? <Card title={result.ok === false ? 'action failed' : 'last result'}><JsonNote value={result.error || result.output || result} /></Card> : null}
  </div>;
}

function SpectrumMini({ spectrum }) {
  const bins = Array.isArray(spectrum?.bins) ? spectrum.bins : [];
  const vals = bins.map((b) => Number(typeof b === 'number' ? b : b?.db ?? b?.level_db_max ?? b?.level_db_mean)).filter(Number.isFinite);
  if (!vals.length) return <div style={{ ...mono, fontSize: 8.5, color: 'var(--fg-deep)' }}>No completed sweep yet.</div>;
  const min = Math.min(...vals), max = Math.max(...vals), range = Math.max(1, max - min);
  const take = vals.length > 100 ? vals.filter((_, i) => i % Math.ceil(vals.length / 100) === 0) : vals;
  return <div style={{ display: 'flex', alignItems: 'end', height: 66, gap: 1, borderBottom: '1px solid var(--stroke)' }}>{take.map((v, i) => <span key={i} title={`${v.toFixed(1)} relative dB`} style={{ flex: 1, minWidth: 1, height: `${12 + ((v - min) / range) * 52}px`, background: 'var(--cyan)', opacity: 0.35 + ((v - min) / range) * 0.65 }} />)}</div>;
}

function FindingRow({ f, selected, onPick }) {
  const delta = Number(f.delta_db);
  return <button type="button" onClick={onPick} style={{ width: '100%', textAlign: 'left', border: selected ? '1px solid var(--stroke-acc)' : '1px solid transparent', borderBottomColor: 'var(--edge)', borderRadius: 6, padding: '9px 8px', background: selected ? 'rgba(var(--acc-rgb),0.08)' : 'transparent', cursor: 'pointer', color: 'inherit' }}>
    <div style={{ display: 'grid', gridTemplateColumns: '48px 74px minmax(0,1fr) 46px', gap: 5, alignItems: 'center' }}>
      <span style={{ ...mono, fontSize: 7.5, color: f.status === 'NEW' ? 'var(--acc)' : 'var(--teal)' }}>{f.status || 'SEEN'}</span>
      <b style={{ ...mono, fontSize: 9.5, color: 'var(--fg)', fontWeight: 500 }}>{Number(f.freq_mhz).toFixed(3)}M</b>
      <span style={{ ...mono, fontSize: 8, color: 'var(--fg-mute)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{f.classification || f.context || f.source || 'energy peak'}</span>
      <span style={{ ...mono, fontSize: 8, color: Number.isFinite(delta) && delta >= 12 ? 'var(--acc)' : 'var(--fg-dim)', textAlign: 'right' }}>{Number.isFinite(delta) ? `+${delta.toFixed(0)}` : ''}</span>
    </div>
  </button>;
}

function suggestedDemod(finding) {
  const text = `${finding?.context || ''} ${finding?.classification || ''}`.toLowerCase();
  if (text.includes('airband')) return 'am';
  if (text.includes('fm broadcast')) return 'wfm';
  return 'nfm';
}

function RfPanel({ status, refresh }) {
  const ops = status?.ops || {}, findingsPayload = status?.findings || {}, hunt = status?.hunt || {}, capture = status?.capture || {}, audio = status?.rfaudio || {}, zoom = status?.zoom || {}, corr = status?.correlation || {};
  const findings = Array.isArray(findingsPayload.findings) ? findingsPayload.findings : [];
  const [selectedFreq, setSelectedFreq] = React.useState(null);
  const [listenMode, setListenMode] = React.useState('auto');
  const [captureSec, setCaptureSec] = React.useState(15);
  const selected = findings.find((f) => Number(f.freq_mhz) === Number(selectedFreq)) || findings[0] || null;
  React.useEffect(() => { if (!selectedFreq && findings[0]) setSelectedFreq(findings[0].freq_mhz); }, [findings.length, selectedFreq]);
  const command = async (action, extra = {}) => { await api('/api/field/rf/command', { action, ...extra }); setTimeout(refresh, 180); };
  const freq = selected ? Number(selected.freq_mhz) : null;
  const busy = ops.mode && !['idle', ''].includes(ops.mode);
  const sdrConnected = typeof status?.hardware?.connected === 'boolean' ? status.hardware.connected : null;
  const autoDemod = suggestedDemod(selected);
  const effectiveDemod = listenMode === 'auto' ? autoDemod : listenMode;
  const zoomForSelected = zoom?.bins && (!selected || Math.abs(Number(zoom.center_mhz) - freq) < 0.2);

  return <div style={{ display: 'grid', gap: 10 }}>
    <Card title="RF operator" meta={`region ${ops.region || 'AU'} · receive-only`}>
      <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap', marginBottom: 9 }}>
        <Pill live={sdrConnected === true} crit={sdrConnected === false}>RTL-SDR {sdrConnected === true ? 'READY' : sdrConnected === false ? 'MISSING' : 'UNKNOWN'}</Pill>
        <Pill live={!busy}>{(ops.mode || 'idle').toUpperCase()}</Pill><Pill>{`owner ${ops.owner || 'idle'}`}</Pill>{audio.state && audio.state !== 'idle' ? <Pill live>{`audio ${audio.state}`}</Pill> : null}
        <Pill>{`wifi ${corr.wifi_devices || corr.wardrive_wifi || 0}`}</Pill><Pill>{`ble ${corr.ble_devices || 0}`}</Pill>
      </div>
      <div style={{ ...mono, fontSize: 10, color: 'var(--fg)', marginBottom: 7 }}>{ops.stage || 'ready'}</div>
      <div style={{ height: 5, borderRadius: 3, background: 'var(--bg-1)', overflow: 'hidden' }}><div style={{ height: '100%', width: `${Math.max(0, Math.min(100, Number(ops.progress) || 0))}%`, background: 'var(--acc)' }} /></div>
      {ops.error ? <div style={{ ...mono, marginTop: 7, fontSize: 8.5, color: 'var(--red)' }}>{ops.error}</div> : null}
    </Card>
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(115px,1fr))', gap: 8 }}><button style={btn} onClick={() => command('survey')}>SURVEY</button><button style={btnDim} onClick={() => command('baseline_save')}>SAVE BASELINE</button><button style={btnDim} onClick={() => command('survey_stop')}>STOP SURVEY</button><button style={btnCrit} onClick={() => command('recover')}>RESET RF</button></div>
    <Card title="spectrum overview" meta={status?.spectrum?.scan_range_mhz || '24-1766 MHz'}><SpectrumMini spectrum={status?.spectrum} /></Card>
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(260px,1fr))', gap: 10 }}>
      <Card title="findings" meta={`${findings.length} ranked`}><div style={{ maxHeight: 310, overflow: 'auto' }}>{findings.length ? findings.map((f, i) => <FindingRow key={f.id || `${f.freq_mhz}-${i}`} f={f} selected={selected === f} onPick={() => setSelectedFreq(f.freq_mhz)} />) : <JsonNote value="No ranked findings yet. Tap SURVEY. DRIFTER will broad-scan, calculate a local noise floor, then zoom the strongest candidates automatically." />}</div></Card>
      <Card title="signal investigation" meta={selected ? `${Number(selected.freq_mhz).toFixed(5)} MHz` : 'select finding'}>{selected ? <>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(110px,1fr))', gap: '5px 12px', marginBottom: 10 }}>{[['context',selected.context || 'unlabelled'],['peak',selected.peak_db != null ? `${selected.peak_db} rel dB` : 'n/a'],['noise',selected.noise_db != null ? `${selected.noise_db} rel dB` : 'n/a'],['delta',selected.delta_db != null ? `+${selected.delta_db} dB` : 'n/a'],['bandwidth',selected.bandwidth_khz != null ? `${selected.bandwidth_khz} kHz` : 'unknown'],['source',selected.source || 'survey']].map(([k,v]) => <div key={k} style={{ ...mono, fontSize: 8.5, color: 'var(--fg-mute)', overflowWrap: 'anywhere' }}><span style={{ color: 'var(--fg-deep)' }}>{k} · </span>{v}</div>)}</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(130px,1fr))', gap: 8 }}>
          {hunt.active ? <button style={btnCrit} onClick={() => command('hunt_stop')}>STOP HUNT</button> : <button style={btn} onClick={() => command('hunt_start', { freq_mhz: freq })}>HUNT</button>}
          {audio.state === 'playing' || audio.state === 'scanning' ? <button style={btnCrit} onClick={() => command('listen_stop')}>STOP LISTEN</button> : <button style={btn} onClick={() => command('listen', { freq_mhz: freq, mode: effectiveDemod })}>LISTEN · {effectiveDemod}</button>}
          <select aria-label="Demodulation" value={listenMode} onChange={(e) => setListenMode(e.target.value)} style={selectStyle}><option value="auto">AUTO · {autoDemod}</option><option value="am">AM</option><option value="nfm">NFM</option><option value="wfm">WFM</option><option value="usb">USB</option><option value="lsb">LSB</option></select>
          <button style={btnDim} onClick={() => command('zoom', { freq_mhz: freq, span_khz: 500 })}>ZOOM ±250K</button>
          <select aria-label="IQ capture duration" value={captureSec} onChange={(e) => setCaptureSec(Number(e.target.value))} style={selectStyle}><option value={5}>IQ · 5 SEC</option><option value={15}>IQ · 15 SEC</option><option value={30}>IQ · 30 SEC</option></select>
          <button style={btnDim} onClick={() => command('capture', { freq_mhz: freq, duration_s: captureSec })}>CAPTURE IQ · {captureSec}S</button>
        </div>
        {zoomForSelected ? <div style={{ marginTop: 12 }}><div style={{ ...mono, fontSize: 7.5, color: 'var(--fg-deep)', marginBottom: 5 }}>TARGETED ZOOM · {zoom.span_khz || 500} kHz</div><SpectrumMini spectrum={zoom} /></div> : null}
        {hunt.active ? <div style={{ marginTop: 12, border: '1px solid var(--stroke)', borderRadius: 8, padding: 10, background: 'var(--inset-bg)' }}><div style={{ ...mono, fontSize: 8, color: 'var(--fg-deep)' }}>SIGNAL HUNT · RELATIVE POWER</div><div style={{ ...mono, fontSize: 30, color: 'var(--cyan)', margin: '6px 0' }}>{hunt.peak_db != null ? `${hunt.peak_db} dB` : '—'}</div><div style={{ ...mono, fontSize: 9, color: hunt.trend === 'stronger' ? 'var(--teal)' : hunt.trend === 'weaker' ? 'var(--red)' : 'var(--fg-mute)' }}>{(hunt.trend || 'acquiring').toUpperCase()} {hunt.trend_db != null ? `· ${hunt.trend_db > 0 ? '+' : ''}${hunt.trend_db} dB` : ''}</div></div> : null}
      </> : <JsonNote value="Select a finding first." />}</Card>
    </div>
    {capture?.ts ? <Card title="last IQ capture" meta={capture.ok ? 'SigMF saved' : 'failed'}><JsonNote value={capture.ok ? `${capture.data_file} · ${(Number(capture.bytes || 0) / 1e6).toFixed(1)} MB · ${capture.duration_s}s @ ${capture.freq_mhz} MHz` : capture.error} /></Card> : null}
  </div>;
}

function SystemPanel({ status }) {
  const [result, setResult] = React.useState(null);
  const incident = status?.incident || {};
  const last = status?.last_incident?.id ? status.last_incident : (incident?.last || {});
  const first = last?.first_change || {};
  const flags = Array.isArray(last?.correlation_flags) ? last.correlation_flags : [];

  const captureNow = async () => {
    setResult(await api('/api/field/incident/capture', { reason: 'manual_field_capture' }));
  };

  return <div style={{ display: 'grid', gap: 10 }}>
    <Card title="vehicle black box" meta={`${Number(incident.pre_seconds || 90)}s pre · ${Number(incident.post_seconds || 45)}s post`}>
      <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap', marginBottom: 9 }}>
        <Pill live>BUFFERING</Pill>
        <Pill live={incident.active} crit={false}>{incident.active ? 'CAPTURING INCIDENT' : 'ARMED'}</Pill>
        <Pill>{`${Number(incident.buffer_records || 0)} records buffered`}</Pill>
      </div>
      {incident.active ? <div style={{ ...mono, fontSize: 9, lineHeight: 1.6, color: 'var(--acc)' }}>
        {incident.id}<br />{Array.isArray(incident.reasons) ? incident.reasons.join(' · ') : ''}
      </div> : null}
      {last?.id ? <div style={{ marginTop: 9, borderTop: '1px dotted var(--edge)', paddingTop: 9 }}>
        <div style={{ ...mono, fontSize: 8, color: 'var(--fg-deep)', marginBottom: 5 }}>LAST INCIDENT · {last.id}</div>
        <div style={{ ...mono, fontSize: 9, color: 'var(--fg-mute)', lineHeight: 1.6 }}>
          {Array.isArray(last.reasons) ? last.reasons.join(' · ') : ''}
        </div>
        {first?.sensor ? <div style={{ ...mono, marginTop: 7, fontSize: 10, color: 'var(--fg)' }}>
          FIRST CHANGE · <b>{String(first.sensor).toUpperCase()}</b> · {Number(first.offset_s) >= 0 ? '+' : ''}{first.offset_s}s · {first.baseline} → {first.value}
        </div> : <div style={{ ...mono, marginTop: 7, fontSize: 8.5, color: 'var(--fg-dim)' }}>No material first-mover identified in the captured window.</div>}
        {flags.length ? <div style={{ marginTop: 7 }}>{flags.slice(0, 4).map((f, i) => <div key={`${f.flag}-${i}`} style={{ ...mono, fontSize: 8.5, color: 'var(--teal)', lineHeight: 1.6 }}>{String(f.flag || '').replaceAll('_', ' ')}</div>)}</div> : null}
      </div> : <div style={{ ...mono, fontSize: 8.5, color: 'var(--fg-dim)' }}>No completed incident yet. Automatic triggers include unstable/collapsing idle, extreme trims, voltage collapse, coolant events, OBD loss while running, and anomaly-engine events.</div>}
    </Card>

    <button style={btn} onClick={captureNow}>CAPTURE INCIDENT NOW</button>

    <Card title="field health" meta="recover without unplugging">
      <div style={{ display: 'grid', gap: 7 }}>{['boot','watchdog','lcd','network'].map((k) => <div key={k} style={{ display: 'flex', gap: 8, borderBottom: '1px dotted var(--edge)', paddingBottom: 6, flexWrap: 'wrap' }}><span style={{ ...mono, fontSize: 8.5, width: 80, color: 'var(--fg-deep)' }}>{k}</span><span style={{ ...mono, fontSize: 8.5, color: 'var(--fg-mute)', overflowWrap: 'anywhere', flex: '1 1 180px' }}>{JSON.stringify(status?.[k] || {})}</span></div>)}</div>
    </Card>
    <button style={btnCrit} onClick={async () => setResult(await api('/api/field/display/recover', {}))}>RECOVER WHITE / BLANK DISPLAY</button>
    {result ? <Card title={result.ok === false ? 'action failed' : 'field action'}><JsonNote value={result.error || result.output || result.queued || result} /></Card> : null}
  </div>;
}

export function FieldOverlay() {
  const [open, setOpen] = React.useState(false), [tab, setTab] = React.useState('vehicle');
  const [obd, setObd] = React.useState({}), [rf, setRf] = React.useState({}), [sys, setSys] = React.useState({}), [health, setHealth] = React.useState('checking');
  const refreshObd = React.useCallback(async () => { const x = await api('/api/field/obd/status'); setObd(x); return x; }, []);
  const refreshRf = React.useCallback(async () => { const x = await api('/api/field/rf/status'); setRf(x); return x; }, []);
  const refreshSys = React.useCallback(async () => { const x = await api('/api/field/system/status'); setSys(x); return x; }, []);
  React.useEffect(() => {
    let dead = false;
    (async () => {
      const x = await refreshObd(); if (dead) return;
      await Promise.all([refreshRf(), refreshSys()]); if (dead) return;
      setHealth(x?.ok === false ? 'degraded' : 'ready');
      const cfg = x?.config || {};
      const configured = cfg.DRIFTER_TRANSPORT === 'elm327' || Boolean(cfg.ELM_BT_MAC || cfg.ELM_WIFI_HOST);
      if (!configured && sessionStorage.getItem('dr-field-closed') !== '1') { setTab('vehicle'); setOpen(true); }
    })();
    return () => { dead = true; };
  }, [refreshObd, refreshRf, refreshSys]);
  React.useEffect(() => {
    if (!open) return undefined;
    const refreshActive = () => {
      if (tab === 'vehicle') refreshObd();
      else if (tab === 'rf') refreshRf();
      else refreshSys();
    };
    refreshActive();
    const id = setInterval(refreshActive, 1600);
    return () => clearInterval(id);
  }, [open, tab, refreshObd, refreshRf, refreshSys]);
  const close = () => { sessionStorage.setItem('dr-field-closed', '1'); setOpen(false); };
  const rfBusy = rf?.ops?.mode && rf.ops.mode !== 'idle';
  const incidentBusy = sys?.incident?.active;
  return <><button type="button" onClick={() => setOpen(true)} style={{ ...btn, position: 'fixed', zIndex: 950, right: 14, bottom: 14, minHeight: 42, padding: '8px 12px', boxShadow: '0 8px 30px rgba(0,0,0,.35)', background: 'rgba(7,9,13,.92)' }}><span style={{ color: health === 'degraded' ? 'var(--red)' : incidentBusy || rfBusy ? 'var(--acc)' : 'var(--teal)' }}>●</span> FIELD OPS</button>{!open ? null : <div className="dr" style={{ position: 'fixed', inset: 0, zIndex: 1000, background: 'var(--bg-0)', color: 'var(--fg)', overflow: 'auto' }}><div style={{ position: 'sticky', top: 0, zIndex: 2, display: 'flex', alignItems: 'center', gap: 6, padding: '8px 10px', background: 'rgba(7,9,13,.97)', borderBottom: '1px solid var(--stroke-acc)', flexWrap: 'wrap' }}><span className="stencil" style={{ fontSize: 10, color: 'var(--acc)', marginRight: 4, flex: '1 1 150px' }}>DRIFTER · FIELD OPS</span>{['vehicle','rf','system'].map((k) => <button type="button" key={k} onClick={() => setTab(k)} style={{ ...(tab === k ? btn : btnDim), minHeight: 40, padding: '7px 10px' }}>{k}</button>)}<button type="button" onClick={close} style={{ ...btnCrit, minHeight: 40, padding: '7px 10px' }}>CLOSE</button></div><main style={{ padding: 10, maxWidth: 1180, margin: '0 auto' }}>{tab === 'vehicle' ? <VehiclePanel status={obd} refreshStatus={refreshObd} /> : null}{tab === 'rf' ? <RfPanel status={rf} refresh={refreshRf} /> : null}{tab === 'system' ? <SystemPanel status={sys} /> : null}</main></div>}</>;
}

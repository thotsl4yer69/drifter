import React from 'react';
import { HonestState } from '../shared/widgets.jsx';
import { LgTile } from './ledger.jsx';

function Value({ label, value, hot = false }) {
  return <div style={{ display:'flex', justifyContent:'space-between', gap:12, padding:'6px 0', borderBottom:'1px dotted var(--edge)' }}>
    <span className="mono" style={{ fontSize:9, color:'var(--fg-mute)' }}>{label}</span>
    <b className="mono" style={{ fontSize:10, color: hot ? 'var(--acc)' : 'var(--fg)', fontWeight:500, textAlign:'right', overflowWrap:'anywhere' }}>{value ?? '—'}</b>
  </div>;
}

export function ReconMain({ sim, onMode }) {
  const active = sim.mode === 'recon';
  const status = sim.recon?.status || {};
  const last = sim.recon?.lastEvent;
  const plates = Array.isArray(sim.recon?.recentPlates) ? sim.recon.recentPlates : [];
  const backend = String(sim.perception?.backend || 'unknown').toUpperCase();
  const vision = String(sim.perception?.vision || 'offline').toUpperCase();
  const recording = String(sim.perception?.dashcam || 'unknown').toUpperCase();
  const camera = sim.perception?.cameraId || 'front';
  const chain = status.chainHead ? String(status.chainHead).slice(0, 16) + '…' : '—';

  return <div style={{ display:'flex', flexDirection:'column', gap:10, padding:10, minHeight:0 }}>
    <div className="dr-tile bracketed" style={{ padding:'12px 14px', display:'flex', alignItems:'center', gap:14 }}>
      <div style={{ flex:1 }}>
        <div className="stencil" style={{ fontSize:11, letterSpacing:'.13em', color: active ? 'var(--acc)' : 'var(--fg)' }}>RECON / HAILO SURVEILLANCE</div>
        <div className="mono" style={{ fontSize:9, color:'var(--fg-mute)', marginTop:4 }}>
          single camera owner · local inference · evidence indexed on device
        </div>
      </div>
      <div className="mono" style={{ fontSize:10, color: active ? 'var(--teal)' : 'var(--fg-mute)' }}>
        {active ? '● RECON ACTIVE' : `MODE: ${String(sim.mode || 'unknown').toUpperCase()}`}
      </div>
      <button type="button" className="dr-touch-action mono"
        onClick={() => onMode(active ? 'drive' : 'recon')}>
        {active ? 'RETURN DRIVE' : 'ENTER RECON'}
      </button>
    </div>

    {!active ? <HonestState kind="offline" label="recon not armed" hint="Enter RECON to activate evidence capture and ALPR." /> : null}

    <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:10, minHeight:0 }}>
      <LgTile label="perception engine" meta="Hailo / camera runtime" live={active && vision === 'ONLINE'}>
        <Value label="backend" value={backend} hot={backend === 'HAILO'} />
        <Value label="vision" value={vision} />
        <Value label="camera" value={camera} />
        <Value label="recording" value={recording} hot={recording === 'RECORDING'} />
        <Value label="detections" value={Array.isArray(sim.perception?.objects) ? sim.perception.objects.length : 0} />
      </LgTile>

      <LgTile label="evidence ledger" meta="append-only · SHA-256 chain" live={active && status.state === 'online'}>
        <Value label="session" value={status.sessionId || 'waiting'} />
        <Value label="events" value={status.eventCount ?? 0} />
        <Value label="chain head" value={chain} />
        <Value label="last kind" value={last?.kind || '—'} />
        <Value label="last seq" value={last?.seq ?? '—'} />
      </LgTile>
    </div>

    <div style={{ display:'grid', gridTemplateColumns:'1.15fr .85fr', gap:10, minHeight:0 }}>
      <LgTile label="latest scene" meta="event-indexed detections">
        {Array.isArray(sim.perception?.objects) && sim.perception.objects.length ? (
          <div style={{ display:'flex', flexDirection:'column', gap:5 }}>
            {sim.perception.objects.slice(0,6).map((obj, i) => (
              <Value key={i} label={String(obj.class || 'object').toUpperCase()}
                value={obj.confidence != null ? `${Math.round(Number(obj.confidence) * 100)}%` : 'reported'} />
            ))}
          </div>
        ) : <HonestState kind="acquiring" label="monitoring scene" hint="No recent detections reported." compact />}
      </LgTile>

      <LgTile label="recent plates" meta="RECON-only OCR">
        {plates.length ? plates.map((p, i) => (
          <Value key={p.plate + i} label={p.plate}
            value={p.confidence != null ? `${Math.round(Number(p.confidence) * 100)}%` : 'seen'} />
        )) : <HonestState kind="acquiring" label="no plate events" hint="ALPR only runs on evidence-bearing vehicle crops." compact />}
      </LgTile>
    </div>

    <div className="mono" style={{ fontSize:8, color:'var(--fg-deep)', padding:'0 4px' }}>
      GPS {sim.hw?.gps === 'fix' ? `${sim.gps?.lat?.toFixed?.(5) ?? sim.gps?.lat}, ${sim.gps?.lon?.toFixed?.(5) ?? sim.gps?.lon}` : 'NO FIX'} ·
      ledger {status.ledgerPath || 'not active'}
    </div>
  </div>;
}

// ════════════════════════════════════════════════════════════════
// MAP SURFACE — LEDGER full map (offline mz1312 tiles, origin marker,
// live ADS-B aircraft, honest GPS gating). Ported 1:1.
// ════════════════════════════════════════════════════════════════
import React from 'react';
import { DrMap, HonestState, drFmt } from '../shared/widgets.jsx';

function MpAircraft({ sim }) {
  return (
    <svg width="100%" height="100%" style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }}>
      {sim.rf.aircraft.map((ac) => {
        const a = ((ac.brg - 90) / 180) * Math.PI;
        const x = 50 + Math.cos(a) * ac.rng * 38;
        const y = 50 + Math.sin(a) * ac.rng * 38;
        const col = ac.ghost ? 'var(--red)' : 'var(--cyan)';
        return (
          <g key={ac.id} transform={`translate(${x * 12.04} ${y * 7.0})`}>
            <path d="M 0 -6 L 4.5 5 L 0 2.4 L -4.5 5 Z" fill={col} opacity="0.95" transform={`rotate(${ac.brg + 90})`} style={{ filter: `drop-shadow(0 0 5px ${col})` }}></path>
            <text x="8" y="3" fill={col} fontSize="8.5" fontFamily="var(--f-mono)">{ac.ghost ? 'GHOST' : ac.id}</text>
            <text x="8" y="13" fill="var(--fg-dim)" fontSize="7" fontFamily="var(--f-mono)">{ac.ghost ? 'no callsign' : `FL${ac.alt}0`}</text>
          </g>
        );
      })}
    </svg>
  );
}

export function MpMain({ sim }) {
  const noGps = sim.hw.gps !== 'fix';
  const [layer, setLayer] = React.useState('street');
  return (
    <div style={{ position: 'relative', overflow: 'hidden', minHeight: 0, minWidth: 0 }}>
      <DrMap sim={sim} zoom={1.3} dim={noGps} showRoute={!noGps} />
      {!noGps ? <MpAircraft sim={sim} /> : null}

      {noGps ? (
        <div style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center', background: 'rgba(0,0,0,0.5)' }}>
          <div className="dr-tile" style={{ padding: '16px 22px', background: 'var(--glass-strong)', maxWidth: 400 }}>
            <HonestState kind={sim.hw.gps === 'acquiring' ? 'acquiring' : 'no-hw'}
              label={sim.hw.gps === 'acquiring' ? 'awaiting gps fix' : 'gps · no device'}
              hint="drag the origin marker to set a manual position (POST /api/gps/manual) — coarse browser location is rejected" />
          </div>
        </div>
      ) : (
        <React.Fragment>
          <div style={{ position: 'absolute', left: '50%', top: '50%', transform: 'translate(-50%, -130%)', textAlign: 'center', cursor: 'grab' }} title="drag to set GPS origin">
            <div className="mono" style={{ fontSize: 8, color: 'var(--fg)', background: 'var(--glass-strong)', border: '1px solid var(--stroke-2)', borderRadius: 4, padding: '2px 7px', whiteSpace: 'nowrap' }}>origin · drag to set</div>
            <div style={{ width: 1, height: 10, background: 'var(--fg-dim)', margin: '0 auto' }}></div>
          </div>
          <div className="mono" style={{ position: 'absolute', left: 14, bottom: 12, fontSize: 9, color: 'var(--fg-mute)', textShadow: 'var(--map-ink-shadow)', whiteSpace: 'nowrap' }}>
            {sim.gps.lat.toFixed(5)} / {sim.gps.lon.toFixed(5)} · hdg {drFmt.n0(sim.heading)}° · acc {(sim.gps.acc ?? 0).toFixed(1)} m
          </div>
        </React.Fragment>
      )}

      <div style={{ position: 'absolute', right: 14, top: 14, display: 'flex', flexDirection: 'column', gap: 6 }}>
        {[['⌖', 'browser locate (≤100 m only)'], ['◑', 'satellite layer'], ['⊙', 'recenter origin'], ['⛶', 'fullscreen']].map(([g, tip]) => (
          <button type="button" key={g} title={tip} onClick={() => g === '◑' && setLayer(layer === 'street' ? 'sat' : 'street')}
            style={{ width: 38, height: 38, display: 'grid', placeItems: 'center', borderRadius: 8, cursor: 'pointer', fontSize: 15, color: 'var(--fg-mute)', background: 'var(--glass-strong)', border: '1px solid var(--stroke-2)' }}>{g}</button>
        ))}
      </div>

      <div style={{ position: 'absolute', right: 14, bottom: 12, display: 'flex', alignItems: 'center', gap: 8 }}>
        <span className="dr-pill" style={{ background: 'var(--glass-strong)' }}>tiles · <b className="mono" style={{ color: 'var(--teal)' }}>offline ✓</b> bendigo ±60 km · z9–16</span>
        <span className="mono" style={{ fontSize: 7.5, color: 'var(--fg-deep)', letterSpacing: '0.1em', whiteSpace: 'nowrap' }}>DATA · OSM · ADSB.LOL</span>
      </div>

      <div style={{ position: 'absolute', left: 14, top: 14 }}>
        <span className="dr-pill" style={{ background: 'var(--glass-strong)' }}><span className="dot" style={{ background: 'var(--cyan)', boxShadow: '0 0 8px var(--cyan)' }}></span>ads-b · {sim.rf.adsb} aircraft</span>
      </div>
    </div>
  );
}

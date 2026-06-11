// ════════════════════════════════════════════════════════════════
// A′ · LEDGER POCKET — DRIVE collapsed to phone. Ported 1:1.
// ════════════════════════════════════════════════════════════════
import React from 'react';
import { useSim, Spark, TapeGauge, ShiftLights, BigVal, DrMap, HonestState, drFmt } from '../shared/widgets.jsx';

function PkGauge({ label, num, unit, frac, color, alarm, noHw }) {
  return (
    <div className="dr-tile" style={{ padding: '10px 12px' }}>
      <div className="dr-label" style={{ fontSize: 9 }}>{label}</div>
      {noHw ? (
        <div className="mono" style={{ fontSize: 9, color: 'var(--fg-dim)', letterSpacing: '0.08em', padding: '8px 0 6px' }}>⊘ ECU NOT CONNECTED</div>
      ) : (
      <React.Fragment>
      <div className="mono" style={{ fontSize: 24, fontWeight: 500, color: alarm ? 'var(--red)' : 'var(--fg)', margin: '4px 0 6px' }}>
        {num}<span style={{ fontSize: 9, color: 'var(--fg-dim)', marginLeft: 3 }}>{unit}</span>
      </div>
      <div style={{ height: 4, borderRadius: 2, background: 'rgba(255,255,255,0.06)', position: 'relative', overflow: 'hidden' }}>
        <div style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: `${Math.round(Math.max(0, Math.min(1, frac)) * 100)}%`, background: color, boxShadow: `0 0 6px ${color}`, transition: 'width 140ms linear' }}></div>
      </div>
      </React.Fragment>
      )}
    </div>
  );
}

export function DirPhone({ t, onNav }) {
  const sim = useSim();
  const [tab, setTab] = React.useState('cock');
  const sevC = { crit: 'var(--red)', warn: 'var(--acc)', info: 'var(--cyan)' };
  const noGps = sim.hw.gps !== 'fix';
  const noEcu = sim.hw.ecu !== 'ok';
  return (
    <div style={{ position: 'absolute', inset: 0, zIndex: 1, display: 'flex', flexDirection: 'column', padding: '12px 12px 0', gap: 10 }} data-screen-label="A′ · POCKET">
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span className="stencil" style={{ fontSize: 10, color: 'var(--acc)', textShadow: 'var(--acc-glow)' }}>drifter</span>
        <span className="mono" style={{ fontSize: 7, color: 'var(--fg-dim)', letterSpacing: '0.16em' }}>MZ1312</span>
        <div style={{ flex: 1 }}></div>
        <span className={`dr-pill${sim.mode !== 'drive' ? ' hot' : ''}`} style={{ fontSize: 8 }}>{sim.mode}</span>
        <span className="dr-pill" style={{ fontSize: 8, color: noGps ? 'var(--cyan)' : undefined }}><span className="dot" style={noGps ? { background: 'var(--cyan)' } : null}></span>gps</span>
        <span className="dr-pill" style={{ fontSize: 8, color: noEcu ? 'var(--fg-dim)' : undefined }}>can {noEcu ? '⊘' : '●'}</span>
        {sim.power.undervoltSinceBoot ? <span className="dr-pill hot" style={{ fontSize: 8 }}>⚡uv</span> : null}
        <span className="mono" style={{ fontSize: 9, color: 'var(--fg-mute)' }}>{new Date().toTimeString().slice(0, 5)}</span>
      </div>

      {sim.link === 'lost' ? (
        <div className="mono" style={{ padding: '5px 10px', borderRadius: 6, border: '1px solid rgba(var(--red-rgb),0.5)', background: 'rgba(var(--red-rgb),0.10)', color: 'var(--red)', fontSize: 8.5, letterSpacing: '0.1em', flex: 'none' }}>
          ● LINK LOST — retrying · values frozen
        </div>
      ) : null}

      <div className="dr-tile bracketed" style={{ padding: '14px 18px 16px', flex: 'none', opacity: sim.link === 'lost' ? 0.55 : 1, transition: 'opacity 600ms' }}>
        {noGps ? (
          <HonestState kind={sim.hw.gps === 'acquiring' ? 'acquiring' : 'no-hw'}
            label={sim.hw.gps === 'acquiring' ? 'gps acquiring' : 'gps · no device'}
            hint={sim.hw.gps === 'acquiring' ? 'awaiting 3D fix — no speed until real' : 'plug in the usb gps dongle'} />
        ) : (
        <React.Fragment>
        <div style={{ display: 'flex', alignItems: 'center' }}>
          <BigVal num={drFmt.n0(sim.speed)} unit="km/h" size={72} glow />
          <div style={{ marginLeft: 'auto', textAlign: 'center' }}>
            <div className="mono" style={{ fontSize: 26, color: 'var(--acc)', textShadow: 'var(--acc-glow)' }}>{sim.gear}</div>
            <ShiftLights rpm={sim.rpm} />
          </div>
        </div>
        <div style={{ marginTop: 8 }}>
          <TapeGauge value={sim.speed} min={0} max={130} ghosts={[60, 100]} color="var(--cyan)" ladder={[{ t: '0' }, { t: '60 zone', hot: true }, { t: '130' }]} />
        </div>
        </React.Fragment>
        )}
      </div>

      <div className="dr-tile" style={{ height: 178, flex: 'none', overflow: 'hidden', position: 'relative' }}>
        <DrMap sim={sim} zoom={1.1} dim={noGps} showRoute={!noGps} />
        {noGps ? (
          <div style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center', background: 'rgba(0,0,0,0.5)' }}>
            <HonestState kind={sim.hw.gps === 'acquiring' ? 'acquiring' : 'no-hw'} label={sim.hw.gps === 'acquiring' ? 'awaiting gps fix' : 'gps · no device'} hint="map never shows a fake position" compact />
          </div>
        ) : (
        <React.Fragment>
        <span className="dr-pill hot" style={{ position: 'absolute', top: 8, right: 8, background: 'var(--glass-strong)', fontSize: 8 }}><span className="dot"></span>{(sim.gps.acc ?? 0).toFixed(0)}m</span>
        <span className="mono" style={{ position: 'absolute', bottom: 8, left: 10, fontSize: 8, color: 'var(--fg-mute)', textShadow: 'var(--map-ink-shadow)', whiteSpace: 'nowrap' }}>{sim.gps.lat.toFixed(3)} / {sim.gps.lon.toFixed(3)}</span>
        </React.Fragment>
        )}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, flex: 'none' }}>
        <PkGauge label="rpm" num={drFmt.n1(sim.rpm / 1000)} unit="×1k" frac={sim.rpm / 7000} color="var(--cyan)" alarm={sim.rpm > 6300} noHw={noEcu} />
        <PkGauge label="coolant" num={drFmt.n0(sim.coolant)} unit="°C" frac={(sim.coolant - 40) / 80} color="var(--acc)" alarm={sim.coolant > 104} noHw={noEcu} />
        <PkGauge label="voltage" num={drFmt.n1(sim.voltage)} unit="V" frac={(sim.voltage - 11) / 4} color="var(--teal)" alarm={sim.voltage < 12} noHw={noEcu} />
        <PkGauge label="trip" num={drFmt.n1(sim.trip.km)} unit="km" frac={0} color="var(--vio)" />
      </div>

      {sim.alerts[0] ? (
        <div className="dr-tile" style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 12px', borderLeft: `2px solid ${sevC[sim.alerts[0].sev]}`, flex: 'none' }}>
          <span className="mono" style={{ fontSize: 8.5, color: sevC[sim.alerts[0].sev], whiteSpace: 'nowrap', flex: 'none' }}>{sim.alerts[0].code}</span>
          <span style={{ fontSize: 11, color: 'var(--fg)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{sim.alerts[0].msg}</span>
          <span className="mono" style={{ fontSize: 8, color: 'var(--fg-deep)', marginLeft: 'auto' }}>+{Math.max(0, sim.alerts.length - 1)}</span>
        </div>
      ) : null}

      <div className="dr-tile" style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 12px', flex: 'none' }}>
        <div style={{ width: 26, height: 26, borderRadius: '50%', flex: 'none', background: 'radial-gradient(circle at 32% 30%, var(--teal), rgba(94,234,212,0.06) 70%)', boxShadow: '0 0 12px rgba(94,234,212,0.4)' }}></div>
        <div style={{ fontSize: 10.5, color: 'var(--fg)', overflow: 'hidden', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' }}>{sim.vivi.lastSaid || 'ask vivi anything'}</div>
        <span className="dr-ghost live" style={{ flex: 'none' }}>talk</span>
      </div>

      <div style={{ flex: 1 }}></div>

      <div style={{ display: 'flex', justifyContent: 'space-around', borderTop: '1px solid var(--stroke)', padding: '10px 0 14px', background: 'var(--inset-bg)', margin: '0 -12px' }}>
        {[['cock', '⊞'], ['map', '⌖'], ['rf', '⊚'], ['data', '▤'], ['arms', '⊗']].map(([k, g]) => (
          <button type="button" key={k} onClick={() => (onNav ? onNav(k) : setTab(k))} style={{ background: 'transparent', border: 0, textAlign: 'center', cursor: 'pointer', color: tab === k ? 'var(--acc)' : 'var(--fg-dim)', textShadow: tab === k ? 'var(--acc-glow)' : 'none', minWidth: 44 }}>
            <div style={{ fontSize: 17 }}>{g}</div>
            <div className="stencil" style={{ fontSize: 6.5 }}>{k}</div>
          </button>
        ))}
      </div>
    </div>
  );
}

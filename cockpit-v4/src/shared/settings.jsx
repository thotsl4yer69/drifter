// ════════════════════════════════════════════════════════════════
// SETTINGS + DEV PANEL — production replacement for the design-canvas
// tweaks protocol. Persists user prefs (theme/density/scanlines/mode)
// to localStorage. The honest-state toggles (ecu/gps/link/scenario)
// are BENCH overrides — only shown with ?dev=1 or ?sim, never applied
// over live data automatically.
// ════════════════════════════════════════════════════════════════
import React from 'react';
import { DrifterSim } from '../data/adapter.js';

const KEY = 'dr-cockpit-settings';
const DEFAULTS = { theme: 'uncaged', density: 'regular', scanlines: true, mode: 'drive' };

export function useSettings() {
  const [t, setT] = React.useState(() => {
    try { return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(KEY) || '{}') }; }
    catch { return { ...DEFAULTS }; }
  });
  const setTweak = React.useCallback((keyOrEdits, val) => {
    const edits = typeof keyOrEdits === 'object' && keyOrEdits !== null ? keyOrEdits : { [keyOrEdits]: val };
    setT((prev) => {
      const next = { ...prev, ...edits };
      try { localStorage.setItem(KEY, JSON.stringify(next)); } catch {}
      return next;
    });
  }, []);
  return [t, setTweak];
}

export const DEV = (() => { try { const p = new URLSearchParams(location.search); return p.has('dev') || p.has('sim'); } catch { return false; } })();

// ── Dev/bench panel ─────────────────────────────────────────────
function Section({ label }) {
  return <div className="stencil" style={{ fontSize: 8, color: 'var(--fg-deep)', letterSpacing: '0.14em', padding: '8px 0 2px' }}>{label}</div>;
}
function Seg({ value, options, onChange }) {
  return (
    <div style={{ display: 'flex', gap: 3, flexWrap: 'wrap' }}>
      {options.map((o) => (
        <button key={o} type="button" onClick={() => onChange(o)} className="mono"
          style={{ fontSize: 8.5, letterSpacing: '0.06em', padding: '4px 8px', borderRadius: 4, cursor: 'pointer',
            border: `1px solid ${value === o ? 'var(--stroke-acc)' : 'var(--stroke-2)'}`,
            color: value === o ? 'var(--acc)' : 'var(--fg-dim)', background: value === o ? 'rgba(var(--acc-rgb),0.08)' : 'transparent' }}>{o}</button>
      ))}
    </div>
  );
}
function Row({ label, children }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, margin: '4px 0' }}>
      <span className="mono" style={{ fontSize: 8.5, color: 'var(--fg-mute)' }}>{label}</span>
      {children}
    </div>
  );
}

export function DevPanel({ t, setTweak }) {
  const [open, setOpen] = React.useState(false);
  const [dev, setDev] = React.useState({ scenario: 'drive', ecu: true, gps: 'fix', linkLost: false });
  const sim = !DrifterSim.real;
  const setD = (k, v) => {
    setDev((p) => ({ ...p, [k]: v }));
    if (k === 'scenario') DrifterSim.setScenario(v);
    if (k === 'ecu') DrifterSim.setHw('ecu', v ? 'ok' : 'pending');
    if (k === 'gps') DrifterSim.setHw('gps', v);
    if (k === 'linkLost') DrifterSim.setLink(v ? 'lost' : 'live');
  };
  return (
    <React.Fragment>
      <button type="button" onClick={() => setOpen(!open)} title="dev / bench panel"
        style={{ position: 'fixed', right: 12, bottom: 12, zIndex: 2000, width: 34, height: 34, borderRadius: 8, cursor: 'pointer',
          border: '1px solid var(--stroke-acc)', background: 'var(--glass-strong)', color: 'var(--acc)', fontSize: 15 }}>◌</button>
      {open ? (
        <div className="dr-tile" style={{ position: 'fixed', right: 12, bottom: 54, zIndex: 2000, width: 230, padding: '10px 14px 14px', maxHeight: '80vh', overflow: 'auto', boxShadow: '0 18px 50px rgba(0,0,0,0.6)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
            <span className="stencil" style={{ fontSize: 9, color: 'var(--acc)' }}>dev · bench</span>
            <span className="mono" style={{ fontSize: 7.5, color: sim ? 'var(--cyan)' : 'var(--teal)' }}>{sim ? 'SIM FEED' : 'LIVE FEED'}</span>
          </div>
          <Section label="Skin" />
          <Row label="theme"><Seg value={t.theme} options={['uncaged', 'nightrun', 'mapline', 'daylight']} onChange={(v) => setTweak('theme', v)} /></Row>
          <Row label="density"><Seg value={t.density} options={['regular', 'compact']} onChange={(v) => setTweak('density', v)} /></Row>
          <Row label="scanlines"><Seg value={t.scanlines ? 'on' : 'off'} options={['on', 'off']} onChange={(v) => setTweak('scanlines', v === 'on')} /></Row>
          <Section label="Mode" />
          <Row label="operating"><Seg value={t.mode} options={['drive', 'foot', 'both', 'diag']} onChange={(v) => setTweak('mode', v)} /></Row>
          <Section label="Honest states (bench override)" />
          <Row label="ecu"><Seg value={dev.ecu ? 'connected' : 'pending'} options={['connected', 'pending']} onChange={(v) => setD('ecu', v === 'connected')} /></Row>
          <Row label="gps"><Seg value={dev.gps} options={['fix', 'acquiring', 'none']} onChange={(v) => setD('gps', v)} /></Row>
          <Row label="link"><Seg value={dev.linkLost ? 'lost' : 'live'} options={['live', 'lost']} onChange={(v) => setD('linkLost', v === 'lost')} /></Row>
          {sim ? (
            <React.Fragment>
              <Section label="Sim feed" />
              <Row label="scenario"><Seg value={dev.scenario} options={['idle', 'drive', 'spirited']} onChange={(v) => setD('scenario', v)} /></Row>
              <button type="button" onClick={() => DrifterSim.fireAlert()} className="dr-ghost" style={{ marginTop: 6 }}>fire test alert</button>
            </React.Fragment>
          ) : (
            <div className="mono" style={{ fontSize: 7.5, color: 'var(--fg-deep)', marginTop: 8, lineHeight: 1.6 }}>live feed · overrides above are local-only and do not change the vehicle</div>
          )}
        </div>
      ) : null}
    </React.Fragment>
  );
}

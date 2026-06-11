// ════════════════════════════════════════════════════════════════
// COMPONENT LIBRARY patterns — CmGatedAction (ARM→CONFIRM, brief §2.8)
// and CmVivi (single-flight ask, brief §2.5). Ported 1:1.
// ════════════════════════════════════════════════════════════════
import React from 'react';
import { DrifterSim } from '../data/adapter.js';

// ── ARM → CONFIRM gated action ──────────────────────────────────
export function CmGatedAction() {
  const [phase, setPhase] = React.useState('idle'); // idle | armed | running
  const [left, setLeft] = React.useState(120);
  React.useEffect(() => {
    if (phase !== 'armed') return;
    setLeft(120);
    const iv = setInterval(() => setLeft((s) => {
      if (s <= 1) { setPhase('idle'); return 120; }
      return s - 1;
    }), 1000);
    return () => clearInterval(iv);
  }, [phase]);
  const btn = (txt, col, onClick, solid) => (
    <button type="button" onClick={onClick} className="mono"
      style={{
        cursor: 'pointer', fontSize: 10, letterSpacing: '0.12em', padding: '8px 16px', borderRadius: 5,
        border: `1px solid ${col}`, color: solid ? 'var(--bg-0)' : col,
        background: solid ? col : 'transparent', userSelect: 'none',
        boxShadow: solid ? `0 0 18px ${col}55` : 'none',
      }}>{txt}</button>
  );
  return (
    <div className="dr-tile" style={{ padding: '12px 14px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 8 }}>
        <span className="dr-label">marauder · deauth burst</span>
        <span className="mono" style={{ fontSize: 8, color: 'var(--red)' }}>OFFENSIVE · gated</span>
      </div>
      <div className="mono" style={{ fontSize: 9, color: 'var(--fg-mute)', marginBottom: 10 }}>
        scope · allowlist <b style={{ color: 'var(--fg)' }}>2 APs</b> (MZ-LAB, MZ-TEST) · channel 6 · 30 s burst
      </div>
      {phase === 'idle' ? (
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {btn('ARM', 'var(--acc)', () => setPhase('armed'))}
          <span className="mono" style={{ fontSize: 8.5, color: 'var(--fg-dim)' }}>two taps required · nothing fires from one</span>
        </div>
      ) : phase === 'armed' ? (
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          {btn(`CONFIRM · token ${left}s`, 'var(--red)', () => setPhase('running'), true)}
          {btn('STAND DOWN', 'var(--fg-dim)', () => setPhase('idle'))}
        </div>
      ) : (
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <span className="mono" style={{ fontSize: 10, color: 'var(--red)', animation: 'drPulse 1s infinite' }}>● RUNNING · deauth ch6 · 22 s left</span>
          {btn('ABORT', 'var(--red)', () => setPhase('idle'))}
        </div>
      )}
      <div className="mono" style={{ fontSize: 8, color: 'var(--fg-deep)', marginTop: 9, borderTop: '1px dotted var(--edge)', paddingTop: 7 }}>
        no optimistic flip — state changes only on backend confirm · disabled when allowlist empty or hw absent
      </div>
    </div>
  );
}

// ── Single-flight Vivi ──────────────────────────────────────────
export function CmVivi() {
  const [state, setState] = React.useState('cold'); // cold | thinking | ready
  const ask = () => {
    if (state === 'thinking') return;
    setState('thinking');
    if (DrifterSim.viviQuery) DrifterSim.viviQuery('why is the RR tire low?');
    setTimeout(() => setState('ready'), 2600);
  };
  return (
    <div className="dr-tile" style={{ padding: '12px 14px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 9, marginBottom: 9 }}>
        <div style={{ width: 24, height: 24, borderRadius: '50%', background: 'radial-gradient(circle at 32% 30%, var(--teal), rgba(94,234,212,0.06) 70%)', boxShadow: '0 0 12px rgba(94,234,212,0.4)' }}></div>
        <span className="stencil" style={{ fontSize: 9, color: 'var(--teal)' }}>vivi</span>
        <span className="mono" style={{ fontSize: 8, color: state === 'cold' ? 'var(--acc)' : 'var(--fg-dim)', marginLeft: 'auto' }}>
          {state === 'cold' ? '⚠ model not resident — first query cold-loads (slow + power spike)' : state === 'thinking' ? 'qwen2.5:3b · thinking…' : 'qwen2.5:3b · warm'}
        </span>
      </div>
      <div style={{ display: 'flex', gap: 6 }}>
        <div className="mono" style={{ flex: 1, border: '1px solid var(--stroke-2)', borderRadius: 5, padding: '7px 10px', fontSize: 9.5, color: 'var(--fg-dim)' }}>
          why is the RR tire low?
        </div>
        <button type="button" onClick={ask}
          style={{
            cursor: state === 'thinking' ? 'not-allowed' : 'pointer', fontSize: 9.5, letterSpacing: '0.1em', padding: '7px 14px',
            borderRadius: 5, border: '1px solid var(--stroke-acc)', userSelect: 'none', background: 'transparent',
            color: state === 'thinking' ? 'var(--fg-deep)' : 'var(--acc)',
            opacity: state === 'thinking' ? 0.55 : 1,
          }}>{state === 'thinking' ? '◌ THINKING' : 'ASK'}</button>
      </div>
      <div className="mono" style={{ fontSize: 8, color: 'var(--fg-deep)', marginTop: 8 }}>
        every inference is a deliberate tap · single-flight (send disabled while pending) · publishes drifter/vivi2/query · never fires on load/focus/poll
      </div>
    </div>
  );
}

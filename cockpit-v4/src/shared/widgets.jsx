// ════════════════════════════════════════════════════════════════
// DR WIDGETS — shared building blocks (ported 1:1 from dr-widgets.jsx).
// ES module: exports useSim, Spark, TapeGauge, ArcGauge, ShiftLights,
// BigVal, DrMap, SpectrumStrip, PpiRadar, HonestState, ThottyAvatar,
// drFmt. Data comes from the adapter (real WS+REST, or ?sim mock).
// ════════════════════════════════════════════════════════════════
import React, { useState, useEffect, useRef, useMemo } from 'react';
import { DrifterSim } from '../data/adapter.js';

export function useSim() {
  const [s, setS] = useState(() => DrifterSim.getState());
  useEffect(() => DrifterSim.subscribe((st) => setS({ ...st })), []);
  return s;
}

export const fmt = {
  n0: (v) => (v == null ? '—' : Math.round(v).toLocaleString('en-AU')),
  n1: (v) => (v == null ? '—' : (Math.round(v * 10) / 10).toFixed(1)),
  n2: (v) => (v == null ? '—' : (Math.round(v * 100) / 100).toFixed(2)),
  dur: (s) => {
    if (s == null) return '—';
    const m = Math.floor(s / 60), h = Math.floor(m / 60);
    return h > 0 ? `${h}h${String(m % 60).padStart(2, '0')}` : `${m}m${String(Math.floor(s % 60)).padStart(2, '0')}`;
  },
  age: (s) => (s == null ? '' : s < 60 ? `${Math.round(s)}s` : s < 3600 ? `${Math.round(s / 60)}m` : `${Math.round(s / 3600)}h`),
};
export const drFmt = fmt;

// ── Sparkline ───────────────────────────────────────────────────
export function Spark({ data, color = 'var(--acc)', w = 200, h = 26, min, max }) {
  const pts = useMemo(() => {
    if (!data || data.length < 2) return '';
    const lo = min ?? Math.min(...data), hi = max ?? Math.max(...data);
    const span = hi - lo || 1;
    return data.map((v, i) =>
      `${(i / (data.length - 1)) * w},${h - 2 - ((v - lo) / span) * (h - 4)}`
    ).join(' ');
  }, [data, w, h, min, max]);
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" style={{ width: '100%', height: h, display: 'block', opacity: 0.85 }}>
      <polyline points={pts} fill="none" stroke={color} strokeWidth="1.4"></polyline>
    </svg>
  );
}

// ── Tape gauge ──────────────────────────────────────────────────
export function TapeGauge({ value, min, max, ghosts = [], band, ladder = [], color = 'var(--acc)' }) {
  const pct = Math.max(0, Math.min(1, ((value ?? min) - min) / (max - min))) * 100;
  return (
    <div style={{ padding: '0 2px' }}>
      <div style={{ position: 'relative', height: 6, borderRadius: 3, background: 'rgba(255,255,255,0.05)', border: '1px solid var(--stroke)' }}>
        {band ? (
          <div style={{ position: 'absolute', top: -1, bottom: -1, left: `${((band[0] - min) / (max - min)) * 100}%`, width: `${((band[1] - band[0]) / (max - min)) * 100}%`, background: 'rgba(var(--red-rgb),0.16)', borderLeft: '1px solid rgba(var(--red-rgb),0.5)' }}></div>
        ) : null}
        {ghosts.map((g, i) => (
          <div key={i} style={{ position: 'absolute', top: -2, bottom: -2, left: `${((g - min) / (max - min)) * 100}%`, width: 1, background: 'var(--fg-deep)' }}></div>
        ))}
        <div style={{ position: 'absolute', top: -3, bottom: -3, left: `${pct}%`, width: 2, background: color, boxShadow: `0 0 8px ${color}`, transition: 'left 140ms linear' }}></div>
      </div>
      {ladder.length > 0 ? (
        <div className="mono" style={{ display: 'flex', justifyContent: 'space-between', fontSize: 8, color: 'var(--fg-dim)', marginTop: 4, letterSpacing: '0.06em' }}>
          {ladder.map((l, i) => (
            <span key={i} style={{ whiteSpace: 'nowrap', ...(l.hot ? { color: 'var(--acc)' } : null) }}>{l.t}</span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

// ── Arc gauge ───────────────────────────────────────────────────
export function ArcGauge({ value, min, max, size = 160, stroke = 7, color = 'var(--acc)', redFrom, ticks = 8, children }) {
  const a0 = -220, a1 = 40;
  const r = (size - stroke) / 2 - 6;
  const c = size / 2;
  const frac = Math.max(0, Math.min(1, ((value ?? min) - min) / (max - min)));
  const arc = (f0, f1) => {
    const s = ((a0 + (a1 - a0) * f0) * Math.PI) / 180;
    const e = ((a0 + (a1 - a0) * f1) * Math.PI) / 180;
    const large = (f1 - f0) * 260 > 180 ? 1 : 0;
    return `M ${c + r * Math.cos(s)} ${c + r * Math.sin(s)} A ${r} ${r} 0 ${large} 1 ${c + r * Math.cos(e)} ${c + r * Math.sin(e)}`;
  };
  const tickEls = [];
  for (let i = 0; i <= ticks; i++) {
    const f = i / ticks;
    const a = ((a0 + (a1 - a0) * f) * Math.PI) / 180;
    const r1 = r + 4, r2 = r + (i % 2 === 0 ? 11 : 7);
    tickEls.push(<line key={i} x1={c + r1 * Math.cos(a)} y1={c + r1 * Math.sin(a)} x2={c + r2 * Math.cos(a)} y2={c + r2 * Math.sin(a)} stroke="var(--fg-deep)" strokeWidth="1"></line>);
  }
  return (
    <div style={{ position: 'relative', width: size, height: size }}>
      <svg width={size} height={size} style={{ display: 'block', overflow: 'visible' }}>
        <path d={arc(0, 1)} fill="none" stroke="rgba(255,255,255,0.07)" strokeWidth={stroke} strokeLinecap="round"></path>
        {redFrom != null ? (
          <path d={arc((redFrom - min) / (max - min), 1)} fill="none" stroke="rgba(var(--red-rgb),0.35)" strokeWidth={stroke} strokeLinecap="round"></path>
        ) : null}
        <path d={arc(0, Math.max(frac, 0.004))} fill="none" stroke={color} strokeWidth={stroke} strokeLinecap="round" style={{ filter: `drop-shadow(0 0 6px ${color})`, transition: 'd 120ms linear' }}></path>
        {tickEls}
      </svg>
      <div style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center' }}>{children}</div>
    </div>
  );
}

// ── Shift lights ────────────────────────────────────────────────
export function ShiftLights({ rpm, count = 5, redline = 6500 }) {
  const lit = Math.floor((((rpm ?? 0) - 2200) / (redline - 2200)) * (count + 1));
  return (
    <div style={{ display: 'flex', gap: 5, justifyContent: 'center' }}>
      {Array.from({ length: count }).map((_, i) => {
        const on = i < lit;
        const hot = i >= count - 2;
        const col = hot ? 'var(--red)' : 'var(--acc)';
        return <div key={i} style={{ width: 7, height: 7, borderRadius: '50%', border: '1px solid var(--stroke-2)', background: on ? col : 'transparent', boxShadow: on ? `0 0 8px ${col}` : 'none', transition: 'background 100ms' }}></div>;
      })}
    </div>
  );
}

// ── Big numeric value ───────────────────────────────────────────
export function BigVal({ num, unit, size = 44, color = 'var(--fg)', glow, sub }) {
  return (
    <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, whiteSpace: 'nowrap' }}>
      <span className="mono" style={{ fontSize: size, fontWeight: 500, lineHeight: 1, color, textShadow: glow ? 'var(--acc-glow)' : 'none', letterSpacing: '-0.02em' }}>{num}</span>
      <span className="mono" style={{ fontSize: Math.max(10, size * 0.26), color: 'var(--fg-dim)', letterSpacing: '0.06em' }}>{unit}</span>
      {sub}
    </div>
  );
}

// ── Stylized vector map ─────────────────────────────────────────
export function DrMap({ sim, showRoute = true, zoom = 1, dim = false }) {
  const off = useRef({ x: 0, y: 0, t: null });
  const [, force] = useState(0);
  useEffect(() => {
    let raf;
    const tick = (now) => {
      if (document.hidden) { off.current.t = now; raf = requestAnimationFrame(tick); return; }
      const o = off.current;
      if (o.t != null) {
        const dt = Math.min((now - o.t) / 1000, 0.1);
        const st = DrifterSim.getState();
        const mps = (st.speed || 0) / 3.6;
        const a = (((st.heading || 0) - 90) / 360) * Math.PI * 2;
        o.x -= Math.cos(a) * mps * dt * 1.6;
        o.y -= Math.sin(a) * mps * dt * 1.6;
        force((n) => n + 1);
      }
      o.t = now;
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);
  const o = off.current;
  const T = 220 * zoom;
  const HT = T * 2;
  const pt = `translate(${((o.x % T) + T) % T} ${((o.y % T) + T) % T}) scale(${zoom})`;
  const pth = `translate(${((o.x % HT) + HT) % HT} ${((o.y % HT) + HT) % HT}) scale(${zoom * 2})`;
  const hdg = sim ? (sim.heading || 0) : 0;
  return (
    <svg width="100%" height="100%" preserveAspectRatio="xMidYMid slice" style={{ display: 'block', position: 'absolute', inset: 0 }}>
      <defs>
        <pattern id="dr-map-tile" width={T} height={T} patternUnits="userSpaceOnUse" patternTransform={pt}>
          <rect width="220" height="220" fill="var(--map-land)"></rect>
          <rect x="8" y="8" width="92" height="64" fill="var(--map-block)" rx="2"></rect>
          <rect x="120" y="8" width="92" height="64" fill="var(--map-block)" rx="2"></rect>
          <rect x="8" y="92" width="64" height="58" fill="var(--map-block)" rx="2"></rect>
          <rect x="92" y="92" width="120" height="58" fill="var(--map-block)" rx="2"></rect>
          <rect x="8" y="170" width="120" height="42" fill="var(--map-block)" rx="2"></rect>
          <rect x="148" y="170" width="64" height="42" fill="var(--map-park)" rx="2"></rect>
          <g stroke="var(--map-road)" strokeWidth="5">
            <line x1="0" y1="81" x2="220" y2="81"></line>
            <line x1="0" y1="160" x2="220" y2="160"></line>
            <line x1="110" y1="0" x2="110" y2="81"></line>
            <line x1="81" y1="81" x2="81" y2="160"></line>
            <line x1="138" y1="160" x2="138" y2="220"></line>
          </g>
          <line x1="0" y1="0" x2="0" y2="220" stroke="var(--map-arterial)" strokeWidth="9" transform="translate(218 0)"></line>
          <line x1="0" y1="218" x2="220" y2="218" stroke="var(--map-arterial)" strokeWidth="9"></line>
        </pattern>
        <pattern id="dr-map-hwy" width={HT} height={HT} patternUnits="userSpaceOnUse" patternTransform={pth}>
          <line x1="-20" y1="240" x2="240" y2="-20" stroke="var(--map-hwy)" strokeWidth="11"></line>
          <line x1="-20" y1="240" x2="240" y2="-20" stroke="var(--map-hwy-stroke)" strokeWidth="1.4" opacity="0.65"></line>
        </pattern>
        <radialGradient id="dr-map-vin" cx="50%" cy="50%" r="75%">
          <stop offset="60%" stopColor="transparent"></stop>
          <stop offset="100%" stopColor="rgba(0,0,0,0.55)"></stop>
        </radialGradient>
      </defs>
      <rect width="100%" height="100%" fill="var(--map-bg)"></rect>
      <rect width="100%" height="100%" fill="url(#dr-map-tile)" opacity={dim ? 0.55 : 1}></rect>
      <rect width="100%" height="100%" fill="url(#dr-map-hwy)" opacity={dim ? 0.45 : 0.9}></rect>
      <rect width="100%" height="100%" fill="url(#dr-map-vin)"></rect>
      {showRoute ? (
        <g style={{ transform: 'translate(50%, 50%)' }}>
          <path d="M 0 6 C 0 -40, 26 -60, 30 -110 S 10 -190, 38 -250" fill="none" stroke="var(--map-hwy-stroke)" strokeWidth="3" strokeDasharray="2 7" strokeLinecap="round" opacity="0.85"></path>
        </g>
      ) : null}
      <g style={{ transform: 'translate(50%, 50%)' }}>
        <circle r="17" fill="rgba(var(--acc-rgb),0.10)"></circle>
        <g transform={`rotate(${hdg})`}>
          <path d="M 0 -9 L 7 8 L 0 4 L -7 8 Z" fill="var(--acc)" style={{ filter: 'drop-shadow(0 0 7px var(--acc))' }}></path>
        </g>
      </g>
    </svg>
  );
}

// ── RF spectrum strip (fixed dark scope) ────────────────────────
export function SpectrumStrip({ spectrum, h = 44, peakLabel }) {
  const n = spectrum.length;
  return (
    <div style={{ position: 'relative', border: '1px solid var(--stroke-2)', borderRadius: 4, background: '#0a0e14', overflow: 'hidden' }}>
      <svg viewBox={`0 0 ${n * 4} 50`} preserveAspectRatio="none" style={{ width: '100%', height: h, display: 'block' }}>
        {spectrum.map((db, i) => {
          const v = Math.max(0, Math.min(1, (db + 82) / 50));
          const hot = v > 0.6;
          return <rect key={i} x={i * 4 + 0.6} y={50 - v * 48} width="2.8" height={v * 48} fill={hot ? 'var(--scope-hot)' : 'var(--scope-bar)'} opacity={hot ? 0.95 : 0.5}></rect>;
        })}
      </svg>
      <div className="mono" style={{ position: 'absolute', top: 3, left: 6, fontSize: 7.5, letterSpacing: '0.12em', color: '#5a6471' }}>SWEEP · LIVE</div>
      {peakLabel ? <div className="mono" style={{ position: 'absolute', top: 3, right: 6, fontSize: 7.5, color: 'var(--scope-hot)' }}>{peakLabel}</div> : null}
    </div>
  );
}

// ── ADS-B PPI radar ─────────────────────────────────────────────
export function PpiRadar({ sim, size = 168, showRings = true }) {
  const [sweep, setSweep] = useState(0);
  useEffect(() => {
    let raf, last = performance.now();
    const tick = (now) => {
      if (!document.hidden) setSweep((s) => (s + (now - last) * 0.05) % 360);
      last = now;
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);
  const c = size / 2, R = size / 2 - 4;
  const blip = (ac, i) => {
    const a = ((ac.brg - 90) / 180) * Math.PI;
    const x = c + Math.cos(a) * ac.rng * R;
    const y = c + Math.sin(a) * ac.rng * R;
    let d = (sweep - ac.brg + 360) % 360;
    const op = Math.max(0.18, 1 - d / 200);
    const col = ac.ghost ? 'var(--red)' : 'var(--teal)';
    return (
      <g key={i} opacity={op}>
        <rect x={x - 2.5} y={y - 2.5} width="5" height="5" fill={col} transform={ac.ghost ? `rotate(45 ${x} ${y})` : undefined}></rect>
        <text x={x + 6} y={y + 3} fill={col} fontSize="7" fontFamily="var(--f-mono)" opacity="0.9">{ac.ghost ? 'GHOST' : ac.id}</text>
      </g>
    );
  };
  return (
    <svg width={size} height={size} style={{ display: 'block' }}>
      <circle cx={c} cy={c} r={R} fill="rgba(0,0,0,0.35)" stroke="var(--stroke-2)"></circle>
      {showRings ? [0.33, 0.66, 1].map((f) => (
        <circle key={f} cx={c} cy={c} r={R * f} fill="none" stroke="rgba(var(--cyan-rgb),0.14)" strokeWidth="1"></circle>
      )) : null}
      <line x1={c} y1={4} x2={c} y2={size - 4} stroke="rgba(var(--cyan-rgb),0.10)"></line>
      <line x1={4} y1={c} x2={size - 4} y2={c} stroke="rgba(var(--cyan-rgb),0.10)"></line>
      <g transform={`rotate(${sweep} ${c} ${c})`}>
        <path d={`M ${c} ${c} L ${c + R} ${c} A ${R} ${R} 0 0 0 ${c + R * Math.cos(-0.55)} ${c + R * Math.sin(-0.55)} Z`} fill="rgba(var(--cyan-rgb),0.10)"></path>
        <line x1={c} y1={c} x2={c + R} y2={c} stroke="var(--cyan)" strokeWidth="1.2" opacity="0.8"></line>
      </g>
      {(sim ? sim.rf.aircraft : []).map(blip)}
      <circle cx={c} cy={c} r="2.4" fill="var(--acc)" style={{ filter: 'drop-shadow(0 0 5px var(--acc))' }}></circle>
    </svg>
  );
}

// ── Honest empty/degraded state (brief §2.4) ────────────────────
export function HonestState({ kind, label, hint, compact }) {
  const KINDS = {
    'no-hw':     { sym: '⊘', col: 'var(--fg-dim)',  tag: 'no hardware' },
    'acquiring': { sym: '◌', col: 'var(--cyan)',    tag: 'acquiring',  spin: true },
    'no-key':    { sym: '⊝', col: 'var(--fg-dim)',  tag: 'no api key' },
    'conn-err':  { sym: '⚠', col: 'var(--red)',     tag: 'link error' },
    'zero':      { sym: '0',  col: 'var(--fg)',      tag: 'real zero' },
  };
  const k = KINDS[kind] || KINDS['no-hw'];
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 9, padding: compact ? '6px 4px' : '12px 6px', opacity: kind === 'zero' ? 1 : 0.92 }}>
      <span className="mono" style={{ fontSize: compact ? 13 : 17, color: k.col, animation: k.spin ? 'drSpin 2.4s linear infinite' : 'none', display: 'inline-block', flex: 'none' }}>{k.sym}</span>
      <div style={{ minWidth: 0, lineHeight: 1.35 }}>
        <div className="mono" style={{ fontSize: compact ? 9 : 10.5, color: k.col, letterSpacing: '0.1em', textTransform: 'uppercase', whiteSpace: 'nowrap' }}>{label || k.tag}</div>
        {hint ? <div style={{ fontSize: compact ? 9 : 10, color: 'var(--fg-dim)', marginTop: 1 }}>{hint}</div> : null}
      </div>
    </div>
  );
}

// ── Thotty — opt-in 2D avatar ───────────────────────────────────
export function ThottyAvatar({ size = 88, state = 'idle' }) {
  const teal = 'var(--teal)';
  return (
    <div data-thotty={state} style={{ width: size, height: size, position: 'relative' }}>
      <style>{`
        @keyframes thBlink { 0%, 92%, 100% { transform: scaleY(1); } 95% { transform: scaleY(0.08); } }
        @keyframes thMouth { 0%,100% { d: path('M 26 62 Q 35 60 44 62 Q 53 64 62 62'); } 25% { d: path('M 26 62 Q 35 54 44 62 Q 53 70 62 62'); } 50% { d: path('M 26 62 Q 35 68 44 58 Q 53 60 62 62'); } 75% { d: path('M 26 62 Q 35 58 44 66 Q 53 56 62 62'); } }
        @keyframes thPulse { 0%,100% { opacity: 0.25; transform: scale(1); } 50% { opacity: 0.6; transform: scale(1.06); } }
        [data-thotty] .th-eye { transform-origin: center; animation: thBlink 4.2s ease-in-out infinite; }
        [data-thotty="listening"] .th-ring { animation: thPulse 1.6s ease-in-out infinite; }
        [data-thotty="talking"] .th-mouth { animation: thMouth 0.55s linear infinite; }
        @media (prefers-reduced-motion: reduce) { [data-thotty] .th-eye, [data-thotty] .th-ring, [data-thotty] .th-mouth { animation: none !important; } }
      `}</style>
      <svg viewBox="0 0 88 88" width={size} height={size} style={{ display: 'block' }}>
        <circle className="th-ring" cx="44" cy="44" r="42" fill="none" stroke={teal} strokeWidth="1.5" opacity="0.25"></circle>
        <circle cx="44" cy="44" r="36" fill="rgba(0,0,0,0.55)" stroke="var(--stroke-2)"></circle>
        {[30, 38, 46, 54, 62].map((y) => (
          <line key={y} x1="14" y1={y} x2="74" y2={y} stroke="rgba(255,255,255,0.045)" strokeWidth="1"></line>
        ))}
        <rect className="th-eye" x="27" y="34" width="7" height={state === 'listening' ? 14 : 11} rx="2" fill={teal} style={{ filter: `drop-shadow(0 0 6px ${teal})` }}></rect>
        <rect className="th-eye" x="54" y="34" width="7" height={state === 'listening' ? 14 : 11} rx="2" fill={teal} style={{ filter: `drop-shadow(0 0 6px ${teal})`, animationDelay: '0.12s' }}></rect>
        <path className="th-mouth" d="M 26 62 Q 35 60 44 62 Q 53 64 62 62" fill="none" stroke={teal} strokeWidth="2.2" strokeLinecap="round" opacity="0.9"></path>
        <circle cx="20" cy="50" r="1.6" fill={teal} opacity="0.4"></circle>
        <circle cx="68" cy="50" r="1.6" fill={teal} opacity="0.4"></circle>
      </svg>
    </div>
  );
}

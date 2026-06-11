// ════════════════════════════════════════════════════════════════
// DIRECTION A · LEDGER — ported 1:1 from directions/ledger.jsx.
// ES module: chrome (LgTile/LgRail/LgTop), hero gauges, alerts, RF
// tile, trip + vivi strips, and the grouped-ledger right drawer.
// ════════════════════════════════════════════════════════════════
import React from 'react';
import { Spark, TapeGauge, ShiftLights, BigVal, DrMap, SpectrumStrip, HonestState, drFmt } from '../shared/widgets.jsx';

export function LgTile({ label, meta, children, style, bracketed = true, pad = true, live }) {
  return (
    <div className={`dr-tile${bracketed ? ' bracketed' : ''}`} style={{ display: 'flex', flexDirection: 'column', minHeight: 0, minWidth: 0, ...style }}>
      {live ? <div className="dr-live" style={{ position: 'absolute', top: 10, right: 10 }}></div> : null}
      {label ? (
        <div className="dr-head">
          <div className="dr-label">{label}</div>
          {meta ? <div className="dr-meta" style={live ? { marginRight: 14 } : null}>{meta}</div> : null}
        </div>
      ) : null}
      <div style={{ flex: 1, minHeight: 0, padding: pad ? '6px 12px 12px' : 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>{children}</div>
    </div>
  );
}

export function LgRail({ active, onPick }) {
  const items = [
    { k: 'cockpit', g: '⊞', l: 'cock' }, { k: 'map', g: '⌖', l: 'map' },
    { k: 'hw', g: '▤', l: 'hw' }, { k: 'trip', g: '∿', l: 'trip' },
    { k: 'rf', g: '⊚', l: 'rf' }, { k: 'set', g: '◌', l: 'set' }, { k: 'arms', g: '⊗', l: 'arms' },
  ];
  return (
    <nav style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6, padding: '10px 0', borderRight: '1px solid var(--stroke)', background: 'var(--inset-bg)' }}>
      {items.map((it) => (
        <button type="button" key={it.k} onClick={() => onPick(it.k)} title={it.k}
          style={{
            width: 44, height: 44, borderRadius: 8, display: 'grid', placeItems: 'center', gap: 0, cursor: 'pointer',
            color: active === it.k ? 'var(--acc)' : 'var(--fg-dim)',
            border: `1px solid ${active === it.k ? 'var(--stroke-acc)' : 'transparent'}`,
            background: active === it.k ? 'rgba(var(--acc-rgb),0.08)' : 'transparent',
            textShadow: active === it.k ? 'var(--acc-glow)' : 'none',
          }}>
          <div style={{ fontSize: 16, lineHeight: 1 }}>{it.g}</div>
          <div className="stencil" style={{ fontSize: 6.5, letterSpacing: '0.14em' }}>{it.l}</div>
        </button>
      ))}
      <div style={{ flex: 1 }}></div>
      <button type="button" title="Talk to Vivi" onClick={() => onPick('vivi')} style={{ width: 34, height: 34, borderRadius: '50%', cursor: 'pointer', background: 'radial-gradient(circle at 32% 30%, var(--teal), rgba(94,234,212,0.08) 65%)', boxShadow: '0 0 16px rgba(94,234,212,0.4)', border: '1px solid var(--stroke-2)' }}></button>
    </nav>
  );
}

export function LgTop({ sim, narrow }) {
  const clock = new Date(Date.now()).toTimeString().slice(0, 8);
  const gpsPill = {
    fix: <span className="dr-pill hot"><span className="dot"></span>gps · 3d <b className="mono" style={{ color: 'var(--fg)' }}>{sim.gps.sats} sat</b></span>,
    acquiring: <span className="dr-pill" style={{ color: 'var(--cyan)', borderColor: 'rgba(var(--cyan-rgb),0.4)' }}><span className="dot" style={{ background: 'var(--cyan)', boxShadow: '0 0 8px var(--cyan)' }}></span>gps · acquiring…</span>,
    none: <span className="dr-pill" style={{ color: 'var(--fg-dim)' }}><span className="dot" style={{ background: 'var(--fg-deep)', boxShadow: 'none' }}></span>gps · no device</span>,
  }[sim.hw.gps];
  const pill = (cls, label, v) => (
    <span className={`dr-pill ${cls || ''}`}><span className="dot"></span>{label}{v ? <b className="mono" style={{ color: 'var(--fg)', fontWeight: 500 }}>{v}</b> : null}</span>
  );
  return (
    <header style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '0 14px', borderBottom: '1px solid var(--stroke)', background: 'linear-gradient(180deg, rgba(var(--acc-rgb),0.05), transparent 60%)', position: 'relative' }}>
      <div style={{ width: 28, height: 28, display: 'grid', placeItems: 'center', border: '1px solid var(--stroke-acc)', borderRadius: 7, color: 'var(--acc)', fontFamily: 'var(--f-stencil)', fontSize: 14, textShadow: 'var(--acc-glow)' }}>m</div>
      <div style={{ lineHeight: 1.15, whiteSpace: 'nowrap' }}>
        <div className="stencil" style={{ fontSize: 11, color: 'var(--fg)' }}>mz1312 · drifter</div>
        <div className="mono" style={{ fontSize: 7.5, color: 'var(--fg-dim)', letterSpacing: '0.18em' }}>UNCAGED TECH / EST 1991</div>
      </div>
      {!narrow ? <span className="dr-pill" style={{ marginLeft: 10 }}>REG <b className="mono" style={{ color: 'var(--fg)' }}>1MZ-1312</b></span> : null}
      <div style={{ flex: 1 }}></div>
      {pill('', 'operator')}
      {gpsPill}
      <span className={`dr-pill${sim.mode !== 'drive' ? ' hot' : ''}`} title="operating mode — gates the arsenal"><span className="dot"></span>mode · {sim.mode}</span>
      <span className="dr-pill" style={sim.power.undervoltSinceBoot ? { color: 'var(--acc)', borderColor: 'var(--stroke-acc)' } : null} title="undervoltage occurred since boot (sticky bit)">
        pwr {sim.power.undervoltSinceBoot ? <b className="mono" style={{ color: 'var(--acc)' }}>⚡uv!</b> : <b className="mono" style={{ color: 'var(--fg)' }}>ok</b>}
      </span>
      {!narrow ? <span className="dr-pill">cpu <b className="mono" style={{ color: 'var(--fg)' }}>{sim.sysCpu == null ? '—' : Math.round(sim.sysCpu) + '%'}</b></span> : null}
      {!narrow ? <span className="dr-pill">temp <b className="mono" style={{ color: 'var(--fg)' }}>{sim.sysTemp == null ? '—' : Math.round(sim.sysTemp) + '°'}</b></span> : null}
      <span className="mono" style={{ fontSize: 10, color: 'var(--fg-mute)' }}>{clock}</span>
      <div style={{ position: 'absolute', left: 0, right: 0, bottom: -1, height: 1, background: 'linear-gradient(90deg, transparent, var(--acc) 35%, var(--acc) 65%, transparent)', opacity: 0.3 }}></div>
    </header>
  );
}

// ── Hero gauges ─────────────────────────────────────────────────
export function LgSpeed({ sim, big = true }) {
  const noGps = sim.hw.gps !== 'fix';
  const stale = sim.link === 'lost';
  return (
    <LgTile label="speed · gps" meta="+ obd reconcile" live={!noGps && !stale}>
      {noGps ? (
        <div style={{ flex: 1, display: 'grid', placeItems: 'center' }}>
          <HonestState kind={sim.hw.gps === 'acquiring' ? 'acquiring' : 'no-hw'}
            label={sim.hw.gps === 'acquiring' ? 'gps acquiring' : 'gps · no device'}
            hint={sim.hw.gps === 'acquiring' ? 'awaiting 3D fix — nothing shown until real' : 'plug in the usb gps dongle'} />
        </div>
      ) : (
        <React.Fragment>
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: 18, opacity: stale ? 0.4 : 1, transition: 'opacity 600ms' }}>
            <BigVal num={drFmt.n0(sim.speed)} unit="km/h" size={big ? 88 : 64} glow color="var(--fg)" />
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2, marginLeft: 'auto', paddingRight: 6 }}>
              <span className="mono" style={{ fontSize: 30, color: 'var(--acc)', textShadow: 'var(--acc-glow)', fontWeight: 500 }}>{sim.gear}</span>
              <span className="stencil" style={{ fontSize: 7, color: 'var(--fg-dim)' }}>gear</span>
            </div>
          </div>
          <Spark data={sim.hist.speed} color="var(--cyan)" min={0} max={130} h={22} />
          <TapeGauge value={sim.speed} min={0} max={130} ghosts={[60, 100]} color="var(--cyan)"
            ladder={[{ t: '0' }, { t: '60 zone', hot: true }, { t: '110' }, { t: '130' }]} />
        </React.Fragment>
      )}
    </LgTile>
  );
}

export function LgGauge({ label, meta, num, unit, spark, sparkColor, tape, top, alarm, noHw, stale }) {
  return (
    <LgTile label={label} meta={meta} live={!noHw && !stale}>
      {noHw ? (
        <div style={{ flex: 1, display: 'grid', placeItems: 'center' }}>
          <HonestState kind="no-hw" label="ecu not connected" hint="can0 idle — plug in obd-ii" />
        </div>
      ) : (
        <React.Fragment>
          {top}
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', opacity: stale ? 0.4 : 1, transition: 'opacity 600ms' }}>
            <BigVal num={num} unit={unit} size={40} color={alarm ? 'var(--red)' : 'var(--fg)'} />
          </div>
          <Spark data={spark} color={sparkColor} h={20} />
          {tape}
        </React.Fragment>
      )}
    </LgTile>
  );
}

// ── Alerts ──────────────────────────────────────────────────────
export function LgAlerts({ sim }) {
  const [acked, setAcked] = React.useState({});
  const sevC = { crit: 'var(--red)', warn: 'var(--acc)', info: 'var(--cyan)' };
  const live = sim.alerts.filter((a) => !acked[a.id]);
  const counts = {
    active: live.length,
    crit: live.filter((a) => a.sev === 'crit').length,
    anomaly: live.filter((a) => a.code === 'ANOMALY').length,
    dtcs: sim.dtcs.length,
  };
  const chip = (n, l, c) => (
    <span className="dr-pill" style={{ gap: 5, color: 'var(--fg-mute)' }}>
      <b className="mono" style={{ color: c || 'var(--fg)', fontSize: 11 }}>{n}</b>{l}
    </span>
  );
  return (
    <LgTile label={"alerts & dtc bus"} meta="retain · 5min">
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 8 }}>
        {chip(counts.active, 'active', counts.active ? 'var(--acc)' : null)}
        {chip(counts.crit, 'critical', counts.crit ? 'var(--red)' : null)}
        {chip(counts.anomaly, 'anomaly', 'var(--cyan)')}
        {chip(counts.dtcs, 'stored dtc')}
      </div>
      <div style={{ flex: 1, minHeight: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column', gap: 5 }}>
        {live.length === 0 ? (
          <div className="mono" style={{ color: 'var(--fg-dim)', fontSize: 10, padding: '14px 4px', letterSpacing: '0.1em' }}>NO ALERTS THIS SESSION · bus quiet</div>
        ) : live.slice(0, 4).map((a) => (
          <button type="button" key={a.id} onClick={() => setAcked((p) => ({ ...p, [a.id]: true }))} title="tap to acknowledge"
            style={{ textAlign: 'left', display: 'flex', alignItems: 'center', gap: 9, padding: '6px 9px', borderRadius: 6, cursor: 'pointer', background: 'var(--inset-bg)', border: '1px solid var(--stroke)', borderLeft: `2px solid ${sevC[a.sev]}` }}>
            <span className="mono" style={{ fontSize: 9, color: sevC[a.sev], minWidth: 62, letterSpacing: '0.06em' }}>{a.code}</span>
            <span style={{ fontSize: 11.5, color: 'var(--fg)', flex: 1, overflow: 'hidden', whiteSpace: 'nowrap', textOverflow: 'ellipsis' }}>{a.msg}</span>
            <span className="mono" style={{ fontSize: 8.5, color: 'var(--fg-dim)' }}>{drFmt.age(a.ageS)}</span>
            <span className="mono" style={{ fontSize: 8.5, color: 'var(--fg-deep)' }}>ACK ✕</span>
          </button>
        ))}
      </div>
    </LgTile>
  );
}

// ── RF intelligence ─────────────────────────────────────────────
export function LgRf({ sim }) {
  const mods = Object.entries(sim.rf.mods);
  return (
    <LgTile label="rf intelligence" meta="rtl-sdr · 24M–1766M" live>
      <div style={{ display: 'flex', gap: 5, marginBottom: 7, flexWrap: 'wrap' }}>
        {mods.map(([k, st]) => (
          <span key={k} className="dr-pill" style={{ padding: '2px 8px', color: st === 'on' ? 'var(--acc)' : 'var(--fg-dim)', borderColor: st === 'on' ? 'var(--stroke-acc)' : 'var(--stroke)' }}>
            {k} <b className="mono">{st === 'on' ? '●' : '○'}</b>
          </span>
        ))}
      </div>
      <SpectrumStrip spectrum={sim.rf.spectrum} h={64} peakLabel={`peak ${sim.rf.peakMhz}M · ${Math.round(sim.rf.peakDb)}dB`} />
      <div className="mono" style={{ display: 'flex', justifyContent: 'space-between', fontSize: 8.5, color: 'var(--fg-dim)', margin: '5px 1px 8px' }}>
        <span>433.92 ISM</span><span>868</span><span>1090</span><span>1766M</span>
      </div>
      <div style={{ border: '1px solid var(--stroke)', borderRadius: 6, padding: '7px 10px', background: 'var(--inset-bg)', marginBottom: 8 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
          <span className="stencil" style={{ fontSize: 7.5, color: 'var(--fg-dim)' }}>signal · intel</span>
          <span className="mono" style={{ fontSize: 7.5, color: 'var(--fg-deep)' }}>drifter/rf/classification</span>
        </div>
        <div className="mono" style={{ fontSize: 9.5, color: 'var(--fg-mute)', marginTop: 4 }}>
          keyfob-like burst ×{1 + (sim.rf.hits % 4)} @ 433.92M · OOK · <span style={{ color: 'var(--cyan)' }}>conf 0.81</span>
        </div>
      </div>
      <div style={{ display: 'flex', gap: 6, marginTop: 'auto', flexWrap: 'wrap' }}>
        <span className="dr-ghost">pause rtl_433</span>
        <span className="dr-ghost">scan emergency</span>
        <span className="dr-ghost">force spectrum</span>
        <span className="mono" style={{ marginLeft: 'auto', alignSelf: 'center', fontSize: 9, color: 'var(--teal)' }}>TPMS · {sim.rf.tpmsSeen}/4 · {sim.rf.hits} pkts</span>
      </div>
    </LgTile>
  );
}

// ── Trip + Vivi strips ──────────────────────────────────────────
export function LgTrip({ sim }) {
  const slot = (v, l) => (
    <div style={{ display: 'flex', alignItems: 'baseline', gap: 5 }}>
      <b className="mono" style={{ fontSize: 13, color: 'var(--fg)', fontWeight: 500 }}>{v}</b>
      <span className="stencil" style={{ fontSize: 7.5, color: 'var(--fg-dim)' }}>{l}</span>
    </div>
  );
  return (
    <div className="dr-tile" style={{ display: 'flex', alignItems: 'center', gap: 26, padding: '0 16px', height: 38, flex: 'none' }}>
      <span className="dr-label" style={{ fontSize: 9 }}>trip</span>
      {slot(drFmt.n1(sim.trip.km), 'km')}
      {slot(drFmt.n2(sim.trip.fuel), 'L')}
      {slot('$' + drFmt.n2(sim.trip.cost), 'aud')}
      {slot(sim.trip.l100 ? drFmt.n1(sim.trip.l100) : '—', 'L/100')}
      {slot(drFmt.dur(sim.trip.durS), 'dur')}
      <span className="mono" style={{ marginLeft: 'auto', fontSize: 9, color: 'var(--fg-dim)', whiteSpace: 'nowrap' }}>ODO {drFmt.n0(sim.odo)} km</span>
    </div>
  );
}

export function LgVivi({ sim }) {
  return (
    <div className="dr-tile bracketed" style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '10px 14px', flex: 'none' }}>
      <div style={{ width: 36, height: 36, borderRadius: '50%', flex: 'none', background: 'radial-gradient(circle at 32% 30%, var(--teal), rgba(94,234,212,0.06) 70%)', boxShadow: '0 0 18px rgba(94,234,212,0.35)' }}></div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', gap: 10, alignItems: 'baseline' }}>
          <span className="stencil" style={{ fontSize: 9, color: 'var(--teal)' }}>vivi</span>
          <span className="mono" style={{ fontSize: 8.5, color: 'var(--fg-dim)' }}>{sim.vivi.status}</span>
        </div>
        <div style={{ fontSize: 12.5, color: 'var(--fg)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{sim.vivi.lastSaid || '—'}</div>
      </div>
      <div style={{ display: 'flex', gap: 6, flex: 'none' }}>
        <span className="dr-ghost live"><span className="dr-live" style={{ width: 5, height: 5 }}></span>hold to talk <span className="kbd">SPC</span></span>
        <span className="dr-ghost">ask vivi <span className="kbd">M</span></span>
        <span className="dr-ghost">conv</span>
      </div>
    </div>
  );
}

// ── Right column: map + grouped ledger drawer ───────────────────
export const LG_GROUPS = [
  { g: 'vehicle', tabs: ['sensors', 'tires', 'dtcs', 'trip'] },
  { g: 'radio', tabs: ['rf', 'ble', 'ads-b', 'wardrive', 'weather'] },
  { g: 'arsenal', tabs: ['kismet', 'marauder', 'alpr', 'vision', 'sentry'] },
  { g: 'system', tabs: ['sessions', 'audit', 'health'] },
];

export function LgDrawerBody({ tab, sim }) {
  const row = (l, v, age, hot) => (
    <div key={l} style={{ display: 'flex', alignItems: 'baseline', gap: 8, padding: '5px 2px', borderBottom: '1px dotted var(--edge)' }}>
      <span className="mono" style={{ fontSize: 9.5, color: 'var(--fg-mute)', flex: 1, letterSpacing: '0.04em', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{l}</span>
      <b className="mono" style={{ fontSize: 11, color: hot ? 'var(--acc)' : 'var(--fg)', fontWeight: 500, whiteSpace: 'nowrap' }}>{v}</b>
      <span className="mono" style={{ fontSize: 8, color: 'var(--fg-deep)', width: 26, textAlign: 'right', flex: 'none' }}>{age}</span>
    </div>
  );
  if (tab === 'tires') {
    return (
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6, paddingTop: 4 }}>
        {sim.tpms.map((t) => {
          const low = t.kpa != null && t.kpa < 222;
          return (
            <div key={t.pos} style={{ border: '1px solid var(--stroke)', borderRadius: 6, padding: '8px 10px', background: 'var(--inset-bg)', borderLeftColor: low ? 'var(--acc)' : undefined, borderLeftWidth: low ? 2 : 1 }}>
              <div className="stencil" style={{ fontSize: 8, color: 'var(--fg-dim)' }}>{t.pos.toLowerCase()}</div>
              <div className="mono" style={{ fontSize: 16, color: low ? 'var(--acc)' : 'var(--fg)' }}>{t.kpa == null ? '—' : t.kpa}<span style={{ fontSize: 8, color: 'var(--fg-dim)' }}> kPa</span></div>
              <div className="mono" style={{ fontSize: 8.5, color: 'var(--fg-dim)' }}>{t.c == null ? 'no sensor' : t.c + '°C'}</div>
            </div>
          );
        })}
      </div>
    );
  }
  if (tab === 'dtcs') {
    return (
      <div style={{ paddingTop: 4 }}>
        {sim.dtcs.length === 0 ? <div className="mono" style={{ fontSize: 9.5, color: 'var(--fg-dim)', padding: '8px 2px' }}>no stored codes</div> : sim.dtcs.map((d) => row(d.code, d.desc || '—', d.state))}
        <span className="dr-ghost" style={{ marginTop: 10 }}>clear stored dtcs</span>
      </div>
    );
  }
  if (tab === 'trip') {
    return (
      <div style={{ paddingTop: 4 }}>
        {row('distance', drFmt.n1(sim.trip.km) + ' km')}
        {row('fuel', drFmt.n2(sim.trip.fuel) + ' L')}
        {row('cost · AUD', '$' + drFmt.n2(sim.trip.cost))}
        {row('economy', (sim.trip.l100 ? drFmt.n1(sim.trip.l100) : '—') + ' L/100')}
        {row('duration', drFmt.dur(sim.trip.durS))}
        {row('odometer', drFmt.n0(sim.odo) + ' km')}
      </div>
    );
  }
  const noEcu = sim.hw.ecu !== 'ok';
  const noGps = sim.hw.gps !== 'fix';
  const dash = '⊘ no source';
  return (
    <div style={{ paddingTop: 2 }}>
      {row('coolant · primary', noEcu ? dash : drFmt.n1(sim.coolant) + ' °C', noEcu ? '' : '1s', !noEcu && sim.coolant > 100)}
      {row('voltage · alternator', noEcu ? dash : drFmt.n1(sim.voltage) + ' V', noEcu ? '' : '1s')}
      {row('rpm · crank', noEcu ? dash : drFmt.n0(sim.rpm), noEcu ? '' : '50ms')}
      {row('speed · gps', noGps ? '◌ awaiting fix' : drFmt.n0(sim.speed) + ' km/h', noGps ? '' : '1s')}
      {row('throttle', noEcu ? dash : drFmt.n0(sim.throttle * 100) + ' %', noEcu ? '' : '50ms')}
      {row('gps accuracy', noGps ? '◌ awaiting fix' : drFmt.n1(sim.gps.acc) + ' m', noGps ? '' : '1s')}
      {row('heading', noGps ? '◌ awaiting fix' : drFmt.n0(sim.heading) + ' °', noGps ? '' : '1s')}
    </div>
  );
}

export function LgRight({ sim, short }) {
  const [tab, setTab] = React.useState('sensors');
  const armed = sim.mode === 'foot' || sim.mode === 'both';
  const mapH = short ? 196 : 252;
  const hasFix = sim.hw.gps === 'fix';
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10, minHeight: 0 }}>
      <div className="dr-tile bracketed" style={{ height: mapH, flex: 'none', overflow: 'hidden', position: 'relative' }}>
        <DrMap sim={sim} zoom={1} dim={!hasFix} showRoute={hasFix} />
        {!hasFix ? (
          <div style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center', background: 'rgba(0,0,0,0.55)' }}>
            <HonestState kind={sim.hw.gps === 'acquiring' ? 'acquiring' : 'no-hw'} label={sim.hw.gps === 'acquiring' ? 'awaiting gps fix' : 'gps · no device'} hint="never a fake position" compact />
          </div>
        ) : null}
        <div style={{ position: 'absolute', top: 8, left: 10, right: 10, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span className="dr-label" style={{ textShadow: 'var(--map-ink-shadow)' }}>map · live nav</span>
          {hasFix ? <span className="dr-pill hot" style={{ background: 'var(--glass-strong)' }}><span className="dot"></span>{(sim.gps.acc ?? 0).toFixed(0)}m</span> : <span className="dr-pill" style={{ background: 'var(--glass-strong)', color: 'var(--fg-dim)' }}>no fix</span>}
        </div>
        <div style={{ position: 'absolute', bottom: 8, left: 10, right: 10, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span className="mono" style={{ fontSize: 8.5, color: 'var(--fg-mute)', textShadow: 'var(--map-ink-shadow)', whiteSpace: 'nowrap' }}>{hasFix ? `${sim.gps.lat.toFixed(4)} / ${sim.gps.lon.toFixed(4)}` : '— / —'}</span>
          <span className="dr-ghost" style={{ background: 'var(--glass-strong)' }}>expand ⛶</span>
        </div>
      </div>
      <div className="dr-tile" style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
        <div style={{ padding: '10px 12px 8px', borderBottom: '1px solid var(--stroke)' }}>
          {LG_GROUPS.map((grp) => {
            const locked = grp.g === 'arsenal' && !armed;
            return (
            <div key={grp.g} style={{ display: 'flex', alignItems: 'baseline', gap: 6, marginBottom: 5 }}>
              <span className="stencil" style={{ fontSize: 7.5, color: 'var(--fg-deep)', width: 46, flex: 'none' }}>{grp.g}</span>
              {locked ? (
                <span className="mono" style={{ fontSize: 8, color: 'var(--fg-deep)', letterSpacing: '0.08em', padding: '2px 0' }}>⊘ LOCKED · drive mode suppresses recon — switch to foot/both</span>
              ) : (
              <div style={{ display: 'flex', gap: 3, flexWrap: 'wrap' }}>
                {grp.tabs.map((tb) => (
                  <button type="button" key={tb} onClick={() => setTab(tb)}
                    className="mono"
                    style={{
                      fontSize: 8, letterSpacing: '0.06em', textTransform: 'uppercase', cursor: 'pointer', whiteSpace: 'nowrap',
                      padding: '2px 6px', borderRadius: 4,
                      color: tab === tb ? 'var(--acc)' : 'var(--fg-dim)',
                      background: tab === tb ? 'rgba(var(--acc-rgb),0.10)' : 'transparent',
                      border: `1px solid ${tab === tb ? 'var(--stroke-acc)' : 'transparent'}`,
                    }}>{tb}</button>
                ))}
              </div>
              )}
            </div>
          );})}
        </div>
        <div style={{ flex: 1, minHeight: 0, overflow: 'hidden', padding: '4px 12px' }}>
          <LgDrawerBody tab={tab} sim={sim} />
        </div>
        <div className="mono" style={{ display: 'flex', justifyContent: 'space-between', padding: '7px 12px', borderTop: '1px solid var(--stroke)', fontSize: 8.5, color: 'var(--fg-dim)' }}>
          <span>driver · <b style={{ color: 'var(--acc)' }}>OPERATOR</b></span>
          <span>session 0611-A</span>
        </div>
      </div>
    </div>
  );
}

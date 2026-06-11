// ════════════════════════════════════════════════════════════════
// SYSTEM SURFACE (service grid, power decode, radio toggle, broker/
// home-sync) + VIVI SURFACE (chat, single-flight composer, avatar
// opt-in). Ported 1:1; power pills + ASK wired to live data.
// ════════════════════════════════════════════════════════════════
import React from 'react';
import { HonestState, ThottyAvatar } from '../shared/widgets.jsx';
import { DrifterSim } from '../data/adapter.js';
import { LgTile } from './ledger.jsx';

function SyLamp({ s }) {
  const col = s.state === 'ok' ? 'var(--teal)' : s.state === 'gave-up' ? 'var(--red)' : 'var(--acc)';
  return (
    <button type="button" title={`${s.name} · ${s.state} · ${s.restarts} restarts — tap to restart`}
      onClick={() => DrifterSim.restartService && DrifterSim.restartService(s.name)}
      style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '4px 7px', borderRadius: 4, border: '1px solid var(--stroke)', background: 'var(--inset-bg)', minWidth: 0, cursor: 'pointer', textAlign: 'left' }}>
      <span style={{ width: 5, height: 5, borderRadius: '50%', flex: 'none', background: col, boxShadow: s.state !== 'ok' ? `0 0 7px ${col}` : 'none' }}></span>
      <span className="mono" style={{ fontSize: 7.5, color: s.state === 'ok' ? 'var(--fg-dim)' : col, letterSpacing: '0.03em', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{s.name}</span>
      {s.restarts > 0 ? <span className="mono" style={{ fontSize: 7, color: 'var(--fg-deep)', marginLeft: 'auto', flex: 'none' }}>↻{s.restarts}</span> : null}
    </button>
  );
}

export function SyMain({ sim }) {
  const sys = sim.system;
  const pwr = sim.power;
  const [radioPending, setRadioPending] = React.useState(false);
  const bad = sys.services.filter((s) => s.state !== 'ok');
  const onPill = (active, hot, txt) => (
    <span className={`dr-pill${hot ? ' hot' : ''}`} style={!hot ? { color: 'var(--teal)' } : null}>{txt}</span>
  );
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1.6fr 1fr', gridTemplateRows: 'auto 1fr', gap: 10, padding: 10, minHeight: 0, minWidth: 0 }}>

      <LgTile label="power · throttle decode" meta={`vcgencmd get_throttled → ${sys.throttle?.raw ?? '—'}`} style={{ gridColumn: '1 / -1' }}>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          {onPill(false, pwr.undervoltNow, `undervolt now · ${pwr.undervoltNow ? 'YES' : 'no'}`)}
          {onPill(false, pwr.undervoltSinceBoot, `undervolt since boot · ${pwr.undervoltSinceBoot ? 'YES' : 'no'}`)}
          {onPill(false, sys.throttle?.capNow, `freq-cap now · ${sys.throttle?.capNow ? 'YES' : 'no'}`)}
          {onPill(false, sys.throttle?.capBoot, `freq-capped since boot · ${sys.throttle?.capBoot ? 'YES' : 'no'}`)}
          <span className="mono" style={{ fontSize: 8.5, color: 'var(--fg-dim)', marginLeft: 'auto' }}>
            5V rail marginal · usb_max_current_enable=1 suppresses OS warning — this tile is the honest indicator
          </span>
        </div>
      </LgTile>

      <LgTile label="services · watchdog" meta={`${sys.services.length} units · ${bad.length} unhealthy`}>
        {sys.services.length === 0 ? <HonestState kind="acquiring" label="awaiting watchdog" hint="drifter/system/watchdog not yet received" /> : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 4, overflow: 'hidden', alignContent: 'start' }}>
            {sys.services.map((s) => <SyLamp key={s.name} s={s} />)}
          </div>
        )}
        <div className="mono" style={{ fontSize: 7.5, color: 'var(--fg-deep)', marginTop: 'auto', paddingTop: 8 }}>
          ● ok · <span style={{ color: 'var(--acc)' }}>● flapping/waiting</span> · <span style={{ color: 'var(--red)' }}>● watchdog gave up</span> — tap a unit to restart (POST /api/service/&lt;unit&gt;)
        </div>
      </LgTile>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 10, minHeight: 0 }}>
        <LgTile label="radio · one wi-fi, explicit owner" meta="§6.9 conflict surfaced">
          <div style={{ display: 'flex', gap: 6, marginBottom: 7 }}>
            {['ap · tether', 'client · internet'].map((m, i) => {
              const active = (sys.radio === 'ap') === (i === 0);
              return (
                <button type="button" key={m} className="mono"
                  onClick={() => { if (!radioPending) { setRadioPending(true); setTimeout(() => setRadioPending(false), 1500); } }}
                  style={{
                    flex: 1, textAlign: 'center', fontSize: 9, letterSpacing: '0.08em', padding: '7px 0', borderRadius: 5, cursor: 'pointer',
                    color: active ? 'var(--bg-0)' : 'var(--fg-mute)',
                    background: active ? 'var(--acc)' : 'transparent',
                    border: `1px solid ${active ? 'var(--acc)' : 'var(--stroke-2)'}`,
                    opacity: radioPending ? 0.5 : 1,
                  }}>{m}</button>
              );
            })}
          </div>
          <div className="mono" style={{ fontSize: 8.5, color: radioPending ? 'var(--cyan)' : 'var(--fg-dim)' }}>
            {radioPending ? '◌ switching — hotspot will drop for ~20 s…' : 'owner: hotspot (MZ1312_DRIFTER) · autoconnect suppressed'}
          </div>
        </LgTile>
        <LgTile label="hardware presence" meta="drifter/hw/*" style={{ flex: 1 }}>
          {sys.hwPresence.length === 0 ? <HonestState kind="acquiring" label="probing hardware" compact /> : sys.hwPresence.map(([name, st]) => (
            <div key={name} style={{ display: 'flex', justifyContent: 'space-between', gap: 8, padding: '3.5px 0', borderBottom: '1px dotted var(--edge)' }}>
              <span className="mono" style={{ fontSize: 9, color: 'var(--fg-mute)', whiteSpace: 'nowrap' }}>{name}</span>
              <b className="mono" style={{ fontSize: 9, fontWeight: 500, whiteSpace: 'nowrap', color: st === 'ok' ? 'var(--teal)' : st === 'down' ? 'var(--red)' : 'var(--fg-deep)' }}>{st}</b>
            </div>
          ))}
        </LgTile>
        <LgTile label="broker · sync">
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, padding: '3.5px 0', borderBottom: '1px dotted var(--edge)' }}>
            <span className="mono" style={{ fontSize: 9, color: 'var(--fg-mute)', whiteSpace: 'nowrap' }}>mqtt broker</span>
            <b className="mono" style={{ fontSize: 9, color: sys.broker.ok ? 'var(--teal)' : 'var(--fg-dim)', whiteSpace: 'nowrap' }}>{sys.broker.ok ? `ok · ${sys.broker.clients} cl · ${sys.broker.msgs}/s` : 'awaiting'}</b>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, padding: '3.5px 0' }}>
            <span className="mono" style={{ fontSize: 9, color: 'var(--fg-mute)', whiteSpace: 'nowrap' }}>home-sync</span>
            <b className="mono" style={{ fontSize: 9, color: 'var(--acc)', whiteSpace: 'nowrap' }}>{sys.homeSync}</b>
          </div>
        </LgTile>
      </div>
    </div>
  );
}

// ── VIVI SURFACE ────────────────────────────────────────────────
export function VvMain({ sim }) {
  const [avatarOn, setAvatarOn] = React.useState(false);
  const [avState, setAvState] = React.useState('idle');
  const [thinking, setThinking] = React.useState(false);
  const [draft, setDraft] = React.useState('');
  const ask = () => {
    if (thinking || !draft.trim()) return;
    setThinking(true);
    if (DrifterSim.viviQuery) DrifterSim.viviQuery(draft.trim());
    setDraft('');
    setTimeout(() => setThinking(false), 2600);
  };
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 300px', gridTemplateAreas: '"main side"', minHeight: 0, minWidth: 0 }}>

      <div style={{ gridArea: 'main', display: 'flex', flexDirection: 'column', padding: 10, gap: 10, minHeight: 0, minWidth: 0 }}>
        <LgTile label="vivi · intercom" meta="drifter/vivi2/query · sse stream" style={{ flex: 1, minHeight: 0 }}>
          <div style={{ flex: 1, minHeight: 0, overflow: 'auto', display: 'flex', flexDirection: 'column', gap: 10, paddingTop: 4 }}>
            {sim.chat.length === 0 ? <HonestState kind="zero" label="no conversation yet" hint="ask vivi anything — every reply is a deliberate inference" /> : sim.chat.map((m, i) => (
              <div key={i} style={{ display: 'flex', gap: 10, flexDirection: m.who === 'op' ? 'row-reverse' : 'row' }}>
                {m.who === 'vivi' ? <div style={{ width: 22, height: 22, borderRadius: '50%', flex: 'none', background: 'radial-gradient(circle at 32% 30%, var(--teal), rgba(94,234,212,0.06) 70%)', boxShadow: '0 0 12px rgba(94,234,212,0.35)' }}></div> : null}
                <div style={{ maxWidth: '74%' }}>
                  <div className="mono" style={{ fontSize: 7.5, color: 'var(--fg-deep)', marginBottom: 2, textAlign: m.who === 'op' ? 'right' : 'left' }}>{m.who === 'op' ? 'OPERATOR' : 'VIVI'} · {m.t}</div>
                  <div style={{
                    fontSize: 12, color: 'var(--fg)', lineHeight: 1.45, padding: '8px 12px', borderRadius: 8,
                    background: m.who === 'op' ? 'rgba(var(--acc-rgb),0.10)' : 'rgba(0,0,0,0.25)',
                    border: `1px solid ${m.who === 'op' ? 'var(--stroke-acc)' : 'var(--stroke)'}`,
                  }}>{m.msg}</div>
                </div>
              </div>
            ))}
            {thinking ? <div className="mono" style={{ fontSize: 9, color: 'var(--cyan)', paddingLeft: 32 }}>◌ thinking — single-flight, send disabled…</div> : null}
          </div>
          <div style={{ display: 'flex', gap: 8, paddingTop: 10, borderTop: '1px solid var(--stroke)' }}>
            <input className="mono" value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && ask()}
              placeholder="ask vivi… (every inference is a deliberate tap)"
              style={{ flex: 1, border: '1px solid var(--stroke-2)', borderRadius: 6, padding: '10px 12px', fontSize: 10.5, color: 'var(--fg)', background: 'transparent', outline: 'none' }} />
            <button type="button" onClick={ask}
              style={{ fontSize: 10, letterSpacing: '0.1em', padding: '10px 18px', borderRadius: 6, border: '1px solid var(--stroke-acc)', background: 'transparent', color: thinking ? 'var(--fg-deep)' : 'var(--acc)', cursor: thinking ? 'not-allowed' : 'pointer', opacity: thinking ? 0.55 : 1, alignSelf: 'center', userSelect: 'none' }}>
              {thinking ? '◌' : 'ASK'}</button>
            <span className="dr-ghost live" style={{ alignSelf: 'center', padding: '9px 12px' }}>hold to talk</span>
          </div>
        </LgTile>
      </div>

      <div style={{ gridArea: 'side', display: 'flex', flexDirection: 'column', gap: 10, padding: '10px 10px 10px 0', minHeight: 0 }}>
        <LgTile label="model · power" meta="§2.5 guard">
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, padding: '3.5px 0', borderBottom: '1px dotted var(--edge)' }}>
            <span className="mono" style={{ fontSize: 9, color: 'var(--fg-mute)', whiteSpace: 'nowrap' }}>qwen2.5:3b</span>
            <b className="mono" style={{ fontSize: 9, color: 'var(--acc)', whiteSpace: 'nowrap' }}>not resident</b>
          </div>
          <div className="mono" style={{ fontSize: 8.5, color: 'var(--fg-dim)', margin: '6px 0' }}>⚠ first query cold-loads — slow + power spike on the marginal 5 V rail</div>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', paddingTop: 4 }}>
            <span className="mono" style={{ fontSize: 9, color: 'var(--fg-mute)', whiteSpace: 'nowrap' }}>keep-warm timer</span>
            <span className="mono" style={{ fontSize: 8.5, color: 'var(--fg-deep)', border: '1px solid var(--stroke-2)', borderRadius: 999, padding: '2px 10px', whiteSpace: 'nowrap' }}>OFF · default</span>
          </div>
        </LgTile>
        <LgTile label="voice">
          {[['wake word', 'vivi'], ['stt', 'whisper-tiny'], ['tts out', 'ws :8082'], ['conv mode', 'off']].map(([l, v]) => (
            <div key={l} style={{ display: 'flex', justifyContent: 'space-between', gap: 8, padding: '3.5px 0', borderBottom: '1px dotted var(--edge)' }}>
              <span className="mono" style={{ fontSize: 9, color: 'var(--fg-mute)', whiteSpace: 'nowrap' }}>{l}</span>
              <b className="mono" style={{ fontSize: 9, color: 'var(--fg)', fontWeight: 500, whiteSpace: 'nowrap' }}>{v}</b>
            </div>
          ))}
        </LgTile>
        <LgTile label="avatar · thotty · opt-in" meta="glb dropped · 2d vector">
          <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
            {avatarOn ? (
              <ThottyAvatar size={84} state={avState} />
            ) : (
              <div style={{ width: 84, height: 84, borderRadius: '50%', flex: 'none', background: 'radial-gradient(circle at 32% 30%, var(--teal), rgba(94,234,212,0.06) 70%)', boxShadow: '0 0 16px rgba(94,234,212,0.35)', border: '1px solid var(--stroke-2)' }}></div>
            )}
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className="mono" style={{ fontSize: 8.5, color: 'var(--fg-mute)', whiteSpace: 'nowrap' }}>{avatarOn ? `thotty · ${avState}` : 'orb · zero-cost default'}</div>
              {avatarOn ? (
                <div style={{ display: 'flex', gap: 4, marginTop: 6, flexWrap: 'wrap' }}>
                  {['idle', 'listening', 'talking'].map((st) => (
                    <button type="button" key={st} className="mono" onClick={() => setAvState(st)}
                      style={{ fontSize: 7.5, letterSpacing: '0.06em', padding: '3px 8px', borderRadius: 3, cursor: 'pointer', userSelect: 'none', whiteSpace: 'nowrap', border: `1px solid ${avState === st ? 'var(--stroke-acc)' : 'var(--stroke)'}`, background: 'transparent', color: avState === st ? 'var(--acc)' : 'var(--fg-dim)' }}>{st}</button>
                  ))}
                </div>
              ) : null}
              <button type="button" className="mono" onClick={() => setAvatarOn(!avatarOn)}
                style={{ display: 'inline-block', marginTop: 6, fontSize: 8.5, letterSpacing: '0.08em', padding: '4px 10px', borderRadius: 4, whiteSpace: 'nowrap', border: `1px solid ${avatarOn ? 'var(--stroke-acc)' : 'var(--stroke-2)'}`, background: 'transparent', color: avatarOn ? 'var(--acc)' : 'var(--fg-dim)', cursor: 'pointer', userSelect: 'none' }}>
                {avatarOn ? 'avatar ON · tap to stop' : 'enable 2d avatar'}</button>
            </div>
          </div>
          <div className="mono" style={{ fontSize: 7.5, color: 'var(--fg-deep)', marginTop: 8 }}>not mounted unless enabled — no animation loop, no draw cost on the pi by default · lip-sync drives the mouth from tts amplitude</div>
        </LgTile>
      </div>
    </div>
  );
}

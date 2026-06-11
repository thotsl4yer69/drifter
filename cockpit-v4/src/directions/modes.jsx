// ════════════════════════════════════════════════════════════════
// LEDGER MODE SURFACES — FOOT (arsenal) + DIAG. Ported 1:1.
// ════════════════════════════════════════════════════════════════
import React from 'react';
import { SpectrumStrip, PpiRadar, HonestState, drFmt } from '../shared/widgets.jsx';
import { DrifterSim } from '../data/adapter.js';
import { LgTile } from './ledger.jsx';
import { CmGatedAction, CmVivi } from './components.jsx';

function MdRow({ l, v, hot, dim, extra }) {
  return (
    <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, padding: '4.5px 2px', borderBottom: '1px dotted var(--edge)' }}>
      <span className="mono" style={{ fontSize: 9, color: dim ? 'var(--fg-deep)' : 'var(--fg-mute)', flex: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{l}</span>
      {extra}
      <b className="mono" style={{ fontSize: 10.5, color: hot ? 'var(--acc)' : dim ? 'var(--fg-dim)' : 'var(--fg)', fontWeight: 500, whiteSpace: 'nowrap' }}>{v}</b>
    </div>
  );
}

// ── FOOT · arsenal surface ──────────────────────────────────────
function FtWardrive({ sim }) {
  return (
    <LgTile label="wi-fi recon · wardrive" meta="source: drifter/wardrive — kismet chain dead" live>
      <div className="mono" style={{ fontSize: 7.5, color: 'var(--fg-deep)', marginBottom: 5, letterSpacing: '0.06em' }}>SSID · CH · ENC · RSSI</div>
      {sim.recon.wardrive.length === 0 ? <HonestState kind="acquiring" label="scanning wlan0" hint="no networks decoded yet" compact /> : sim.recon.wardrive.map((n) => (
        <div key={n.bssid} style={{ display: 'flex', alignItems: 'baseline', gap: 8, padding: '4px 2px', borderBottom: '1px dotted var(--edge)' }}>
          <span className="mono" style={{ fontSize: 9.5, color: n.own ? 'var(--teal)' : 'var(--fg)', flex: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{n.ssid}{n.own ? ' ·ours' : ''}</span>
          <span className="mono" style={{ fontSize: 8.5, color: 'var(--fg-dim)', width: 20, textAlign: 'right' }}>{n.ch}</span>
          <span className="mono" style={{ fontSize: 8.5, color: n.enc === 'OPEN' ? 'var(--acc)' : 'var(--fg-dim)', width: 34, textAlign: 'right' }}>{n.enc}</span>
          <span className="mono" style={{ fontSize: 9, color: 'var(--fg-mute)', width: 30, textAlign: 'right' }}>{n.rssi}</span>
        </div>
      ))}
      <div className="mono" style={{ fontSize: 7.5, color: 'var(--fg-deep)', marginTop: 'auto', paddingTop: 6 }}>kismet datasource dead (§6.6) — wardrive is the honest source</div>
    </LgTile>
  );
}

function FtBle({ sim }) {
  const p = sim.recon.blePersist;
  return (
    <LgTile label="ble surveillance" meta="hci0">
      <HonestState kind="no-hw" label="bt adapter down" hint="hci0 DOWN — live surveillance blind until reset" compact />
      {p ? (
        <div style={{ border: '1px solid var(--stroke)', borderRadius: 6, padding: '7px 10px', background: 'var(--inset-bg)', marginTop: 4 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
            <span className="stencil" style={{ fontSize: 7.5, color: 'var(--acc)' }}>persistent follower · history</span>
            <span className="mono" style={{ fontSize: 7.5, color: 'var(--fg-deep)' }}>last session</span>
          </div>
          <div className="mono" style={{ fontSize: 9.5, color: 'var(--fg)', marginTop: 4 }}>{p.mac} · {p.kind}</div>
          <div className="mono" style={{ fontSize: 8.5, color: 'var(--fg-mute)', marginTop: 2 }}>{p.sightings} sightings over {p.spanMin} min · last {Math.round(p.lastSeenMin / 60)}h ago</div>
        </div>
      ) : null}
      <span className="dr-ghost" style={{ marginTop: 8, alignSelf: 'flex-start' }}>reset hci0</span>
    </LgTile>
  );
}

function FtSentry() {
  const [state, setState] = React.useState('disarmed'); // disarmed | pending | armed
  return (
    <LgTile label="sentry" meta="cabin watch · no optimistic flip">
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flex: 1 }}>
        <span className="mono" style={{ fontSize: 12, color: state === 'armed' ? 'var(--red)' : state === 'pending' ? 'var(--fg-dim)' : 'var(--teal)', letterSpacing: '0.1em', whiteSpace: 'nowrap' }}>
          {state === 'armed' ? '● ARMED' : state === 'pending' ? '◌ awaiting backend confirm…' : '○ DISARMED'}
        </span>
        <button type="button" className="dr-ghost" style={{ marginLeft: 'auto', opacity: state === 'pending' ? 0.5 : 1, cursor: state === 'pending' ? 'not-allowed' : 'pointer' }}
          onClick={() => {
            if (state === 'pending') return;
            const target = state === 'armed' ? 'disarmed' : 'armed';
            setState('pending');
            setTimeout(() => setState(target), 1400);
          }}>{state === 'armed' ? 'disarm' : 'arm sentry'}</button>
      </div>
      <div className="mono" style={{ fontSize: 7.5, color: 'var(--fg-deep)' }}>state flips only on drifter/sentry/status confirm — never optimistically</div>
    </LgTile>
  );
}

function FtHid() {
  const [armedId, setArmedId] = React.useState(null);
  const payloads = DrifterSim.getState().recon.hidPayloads;
  return (
    <LgTile label="hid · badusb" meta="arm → confirm → run">
      {payloads.length === 0 ? <HonestState kind="no-hw" label="no payloads loaded" hint="native gadget unconfigured (dr_mode=host)" compact /> : payloads.map((p) => (
        <div key={p.id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '5px 2px', borderBottom: '1px dotted var(--edge)' }}>
          <span className="mono" style={{ fontSize: 9.5, color: 'var(--fg)' }}>{p.id}</span>
          <span className="mono" style={{ fontSize: 8, color: 'var(--fg-dim)', flex: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{p.desc}</span>
          {armedId === p.id ? (
            <React.Fragment>
              <button type="button" className="mono" onClick={() => setArmedId(null)} style={{ fontSize: 8.5, color: 'var(--bg-0)', background: 'var(--red)', border: 0, borderRadius: 4, padding: '3px 9px', cursor: 'pointer', letterSpacing: '0.08em' }}>CONFIRM RUN</button>
              <button type="button" className="mono" onClick={() => setArmedId(null)} style={{ fontSize: 8.5, color: 'var(--fg-dim)', border: '1px solid var(--stroke-2)', background: 'transparent', borderRadius: 4, padding: '3px 7px', cursor: 'pointer' }}>✕</button>
            </React.Fragment>
          ) : (
            <button type="button" className="mono" onClick={() => setArmedId(p.id)} style={{ fontSize: 8.5, color: 'var(--acc)', border: '1px solid var(--stroke-acc)', background: 'transparent', borderRadius: 4, padding: '3px 9px', cursor: 'pointer', letterSpacing: '0.08em' }}>ARM</button>
          )}
        </div>
      ))}
      <div className="mono" style={{ fontSize: 7.5, color: 'var(--fg-deep)', marginTop: 'auto', paddingTop: 6 }}>high-risk · target keyboard HID · two taps minimum, no payload edits while armed</div>
    </LgTile>
  );
}

// ── RF · passive intelligence surface (rail ⊚) ──────────────────
// Receive-only: spectrum, ADS-B, Wi-Fi wardrive, BLE surveillance.
// No transmit, no offense — that lives on the ARSENAL surface.
function RfGhost({ sim }) {
  return (
    <LgTile label="counter-surveillance" meta="drifter/ghost · passive correlator">
      <MdRow l="tracker followers" v="0" dim />
      <MdRow l="imsi-catcher suspects" v="0" dim />
      <MdRow l="alpr awareness" v="idle" dim />
      <div className="mono" style={{ fontSize: 7.5, color: 'var(--fg-deep)', marginTop: 'auto', paddingTop: 6 }}>energy-anomaly heuristic only · needs sdr + bt feeds to flag</div>
    </LgTile>
  );
}

export function RfMain({ sim }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 1fr 1fr', gridTemplateRows: 'auto 1fr 1fr', gap: 10, padding: 10, minHeight: 0, minWidth: 0 }}>
      <div className="mono" style={{ gridColumn: '1 / -1', display: 'flex', alignItems: 'center', gap: 10, padding: '6px 12px', borderRadius: 6, border: '1px solid rgba(var(--cyan-rgb),0.4)', background: 'rgba(var(--cyan-rgb),0.07)', color: 'var(--cyan)', fontSize: 10, letterSpacing: '0.12em' }}>
        ⊚ RF INTELLIGENCE · passive · receive-only — spectrum, ads-b, wi-fi &amp; ble recon. no transmit.
      </div>

      <LgTile label="rf · spectrum" meta="rtl-sdr · live sweep" live style={{ gridRow: '2' }}>
        <SpectrumStrip spectrum={sim.rf.spectrum} h={56} peakLabel={`peak ${sim.rf.peakMhz}M · ${Math.round(sim.rf.peakDb)}dB`} />
        <div className="mono" style={{ display: 'flex', justifyContent: 'space-between', fontSize: 8, color: 'var(--fg-dim)', margin: '4px 1px 0' }}>
          <span>24M</span><span>433.92</span><span>868</span><span>1090</span><span>1766M</span>
        </div>
        <div style={{ display: 'flex', gap: 6, marginTop: 'auto', paddingTop: 8 }}>
          <span className="dr-ghost">force sweep</span>
          <span className="dr-ghost">scan emergency</span>
          <span className="dr-ghost">rfaudio listen</span>
        </div>
      </LgTile>
      <LgTile label="ads-b · ppi" meta={`${sim.rf.adsb} aircraft · 1090M`} live style={{ gridRow: '3', alignItems: 'center' }}>
        <div style={{ display: 'flex', gap: 14, alignItems: 'center', flex: 1 }}>
          <PpiRadar sim={sim} size={148} />
          <div className="mono" style={{ fontSize: 8.5, color: 'var(--fg-dim)', lineHeight: 1.9 }}>
            <span style={{ color: 'var(--teal)' }}>■</span> callsign ok<br />
            <span style={{ color: 'var(--red)' }}>◆</span> ghost flag<br />
            rings 10/20/30 km
          </div>
        </div>
      </LgTile>

      <div style={{ gridRow: '2', gridColumn: '2', display: 'grid', minHeight: 0 }}><FtWardrive sim={sim} /></div>
      <div style={{ gridRow: '3', gridColumn: '2', display: 'grid', minHeight: 0 }}><FtBle sim={sim} /></div>

      <div style={{ gridRow: '2 / 4', gridColumn: '3', display: 'grid', minHeight: 0 }}><RfGhost sim={sim} /></div>
    </div>
  );
}

// ── ARSENAL · offensive surface (rail ⊗) ────────────────────────
// Active-transmit / intrusive: every action ARM→CONFIRM gated.
export function FtMain({ sim }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gridTemplateRows: 'auto 1fr 1fr', gap: 10, padding: 10, minHeight: 0, minWidth: 0 }}>
      <div className="mono" style={{ gridColumn: '1 / -1', display: 'flex', alignItems: 'center', gap: 10, padding: '6px 12px', borderRadius: 6, border: '1px solid var(--stroke-acc)', background: 'rgba(var(--acc-rgb),0.08)', color: 'var(--acc)', fontSize: 10, letterSpacing: '0.12em' }}>
        ⊗ ARSENAL · offensive · parked — every action is ARM→CONFIRM gated &amp; allowlist-scoped
      </div>

      <div style={{ gridRow: '2', gridColumn: '1', display: 'grid', minHeight: 0 }}><CmGatedAction /></div>
      <div style={{ gridRow: '2', gridColumn: '2', display: 'grid', minHeight: 0 }}>
        <LgTile label="wi-fi audit" meta="allowlist-gated · handshake/pmkid">
          <MdRow l="allowlist scope" v={sim.recon.auditAllowlist.length ? sim.recon.auditAllowlist.join(' · ') : 'empty'} />
          <MdRow l="handshake capture" v="idle" dim />
          <MdRow l="monitor iface" v="wlan1 · absent" dim />
          <div className="mono" style={{ fontSize: 7.5, color: 'var(--fg-deep)', marginTop: 'auto', paddingTop: 6 }}>disabled when allowlist is empty — shown plainly, never grayed mystery · passive capture only, no deauth-to-force</div>
        </LgTile>
      </div>
      <div style={{ gridRow: '3', gridColumn: '1', display: 'grid', minHeight: 0 }}><FtHid /></div>
      <div style={{ gridRow: '3', gridColumn: '2', display: 'grid', minHeight: 0 }}><FtSentry /></div>
    </div>
  );
}

// ── DIAG · diagnostics surface ──────────────────────────────────
function DgSensorRow({ l, v, unit, frac, band, tick, hot }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '4.5px 2px', borderBottom: '1px dotted var(--edge)' }}>
      <span className="mono" style={{ fontSize: 9, color: 'var(--fg-mute)', width: 118, flex: 'none', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{l}</span>
      <div style={{ flex: 1, height: 4, borderRadius: 2, background: 'rgba(255,255,255,0.05)', position: 'relative' }}>
        {band ? <div style={{ position: 'absolute', top: -1, bottom: -1, left: `${band[0] * 100}%`, width: `${(band[1] - band[0]) * 100}%`, background: 'rgba(var(--cyan-rgb),0.15)', borderRadius: 2 }} title="30s min–max"></div> : null}
        {tick != null ? <div style={{ position: 'absolute', top: -2, bottom: -2, left: `${tick * 100}%`, width: 1, background: 'var(--cyan)' }} title="learned threshold"></div> : null}
        <div style={{ position: 'absolute', top: -2, bottom: -2, left: `${Math.max(0, Math.min(1, frac)) * 100}%`, width: 2, background: hot ? 'var(--acc)' : 'var(--fg-mute)', transition: 'left 200ms linear' }}></div>
      </div>
      <b className="mono" style={{ fontSize: 10, color: hot ? 'var(--acc)' : 'var(--fg)', fontWeight: 500, width: 74, textAlign: 'right', whiteSpace: 'nowrap', flex: 'none' }}>{v}<span style={{ color: 'var(--fg-dim)', fontSize: 7.5 }}> {unit}</span></b>
    </div>
  );
}

export function DgMain({ sim, showBanner = true }) {
  const n = (x) => drFmt.n1(x);
  const noEcu = sim.hw.ecu !== 'ok';
  const s = sim;
  const maf = 2.4 + s.throttle * 14, iat = 31 + s.throttle * 9, load = s.throttle * 88,
    stft = 2.1 + Math.sin(s.t) * 1.8, ltft = 5.8, o2 = 0.45 + Math.sin(s.t * 3) * 0.3,
    timing = 12 + s.throttle * 22, baro = 99.4, fuel = 64 - s.trip.fuel;
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1.25fr 1fr 1.05fr', gridTemplateRows: 'auto 1fr', gap: 10, padding: 10, minHeight: 0, minWidth: 0 }}>
      {showBanner ? (
        <div className="mono" style={{ gridColumn: '1 / -1', display: 'flex', alignItems: 'center', gap: 10, padding: '6px 12px', borderRadius: 6, border: '1px solid var(--stroke-acc)', background: 'rgba(var(--acc-rgb),0.10)', color: 'var(--acc)', fontSize: 10, letterSpacing: '0.12em' }}>
          ⚠ WATCHDOG AUTO-DEMOTED → DIAG · mem 91% · arsenal suspended <span style={{ color: 'var(--fg-mute)' }}>· mode pill restores</span>
        </div>
      ) : <div style={{ gridColumn: '1 / -1', display: 'none' }}></div>}

      <LgTile label="obd · full sensor table" meta="learned ticks · 30s min–max bands" live={!noEcu}>
        {noEcu ? <HonestState kind="no-hw" label="ecu not connected" hint="can0 idle — plug in obd-ii" /> : (
          <React.Fragment>
            <DgSensorRow l="coolant · primary" v={n(s.coolant)} unit="°C" frac={(s.coolant - 40) / 80} band={[(s.coolant - 44) / 80, (s.coolant - 36) / 80]} tick={(104 - 40) / 80} hot={s.coolant > 104} />
            <DgSensorRow l="voltage · alt" v={n(s.voltage)} unit="V" frac={(s.voltage - 11) / 4} band={[(s.voltage - 11.3) / 4, (s.voltage - 10.8) / 4]} tick={(12 - 11) / 4} hot={s.voltage < 12} />
            <DgSensorRow l="maf" v={n(maf)} unit="g/s" frac={maf / 30} band={[maf / 34, maf / 26]} />
            <DgSensorRow l="intake air temp" v={n(iat)} unit="°C" frac={(iat - 10) / 50} tick={(45 - 10) / 50} />
            <DgSensorRow l="engine load" v={n(load)} unit="%" frac={load / 100} band={[Math.max(0, load - 9) / 100, Math.min(100, load + 9) / 100]} />
            <DgSensorRow l="throttle pos" v={n(s.throttle * 100)} unit="%" frac={s.throttle} />
            <DgSensorRow l="stft · b1" v={n(stft)} unit="%" frac={(stft + 10) / 20} tick={0.75} hot={Math.abs(stft) > 8} />
            <DgSensorRow l="ltft · b1" v={n(ltft)} unit="%" frac={(ltft + 10) / 20} tick={0.75} hot={ltft > 8} />
            <DgSensorRow l="o2 · b1s1" v={drFmt.n2(o2)} unit="V" frac={o2} />
            <DgSensorRow l="timing advance" v={n(timing)} unit="°" frac={timing / 40} />
            <DgSensorRow l="barometric" v={n(baro)} unit="kPa" frac={(baro - 90) / 20} />
            <DgSensorRow l="fuel level" v={n(fuel)} unit="%" frac={fuel / 100} />
            <DgSensorRow l="run time" v={drFmt.dur(s.trip.durS)} unit="" frac={0} />
          </React.Fragment>
        )}
      </LgTile>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 10, minHeight: 0 }}>
        <LgTile label="adaptive learning" meta="per-pid envelopes">
          <MdRow l="readiness" v="learning · 7 drives" hot />
          <MdRow l="pids tracked" v="14 / 18" />
          <MdRow l="last envelope update" v="2 min ago" dim />
        </LgTile>
        <LgTile label="dtcs" meta="stored">
          {sim.dtcs.length === 0 ? <MdRow l="no stored codes" v="clear" dim /> : sim.dtcs.map((d) => <MdRow key={d.code} l={`${d.code} · ${d.desc || ''}`} v={d.state} hot />)}
          <span className="dr-ghost" style={{ marginTop: 8, alignSelf: 'flex-start' }}>clear stored dtcs</span>
        </LgTile>
        <LgTile label="anomaly stream" meta="learned-envelope deviations" style={{ flex: 1 }}>
          {[
            ['voltage ripple 0.3V @ idle', '19m', 'edge'],
            ['stft drift +4% over 10 min', '41m', 'watch'],
            ['iat sensor lag vs ambient', '2h', 'cleared'],
          ].map(([m, age, tag]) => (
            <div key={m} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '5px 2px', borderBottom: '1px dotted var(--edge)' }}>
              <span style={{ fontSize: 10.5, color: 'var(--fg)', flex: 1 }}>{m}</span>
              <span className="mono" style={{ fontSize: 7.5, color: tag === 'cleared' ? 'var(--fg-deep)' : 'var(--cyan)', border: '1px solid var(--stroke)', borderRadius: 3, padding: '1px 5px' }}>{tag}</span>
              <span className="mono" style={{ fontSize: 8, color: 'var(--fg-dim)' }}>{age}</span>
            </div>
          ))}
        </LgTile>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 10, minHeight: 0 }}>
        <LgTile label="session report" meta="post-drive · llm · on demand">
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
            <span className="mono" style={{ fontSize: 8.5, color: 'var(--red)', border: '1px solid rgba(var(--red-rgb),0.5)', borderRadius: 3, padding: '1px 6px', whiteSpace: 'nowrap' }}>SAFETY FLAG</span>
            <span className="mono" style={{ fontSize: 8.5, color: 'var(--fg-dim)', whiteSpace: 'nowrap' }}>session 0611-A · conf 0.74</span>
          </div>
          <MdRow l="suspect · 1" v="lean condition b1 (P0171)" hot />
          <MdRow l="suspect · 2" v="RR tire 6% under" />
          <MdRow l="action" v="smoke-test intake · re-torque RR" />
          <div className="mono" style={{ fontSize: 7.5, color: 'var(--fg-deep)', marginTop: 7 }}>generated only on tap, post-drive — never auto-runs (§2.5)</div>
        </LgTile>
        <CmVivi />
        <LgTile label="voice · stt" meta="wake word: vivi">
          <MdRow l="mic link" v="ok" />
          <MdRow l="stt engine" v="whisper-tiny · resident" />
          <MdRow l="tts out" v="ws :8082 · phone speaker" dim />
        </LgTile>
      </div>
    </div>
  );
}

// ════════════════════════════════════════════════════════════════
// PHONE MODE SURFACES — pocket FOOT + DIAG + the bottom dock. 1:1.
// ════════════════════════════════════════════════════════════════
import React from 'react';
import { useSim, SpectrumStrip, HonestState, drFmt } from '../shared/widgets.jsx';

function PmHead({ sim, mode }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <span className="stencil" style={{ fontSize: 10, color: 'var(--acc)', textShadow: 'var(--acc-glow)' }}>drifter</span>
      <span className="mono" style={{ fontSize: 7, color: 'var(--fg-dim)', letterSpacing: '0.16em' }}>MZ1312</span>
      <div style={{ flex: 1 }}></div>
      <span className="dr-pill hot" style={{ fontSize: 8 }}><span className="dot"></span>{mode}</span>
      {sim.power.undervoltSinceBoot ? <span className="dr-pill hot" style={{ fontSize: 8 }}>⚡uv</span> : null}
      <span className="mono" style={{ fontSize: 9, color: 'var(--fg-mute)' }}>{new Date().toTimeString().slice(0, 5)}</span>
    </div>
  );
}

function PmTile({ label, meta, children, style }) {
  return (
    <div className="dr-tile" style={{ padding: '10px 12px', display: 'flex', flexDirection: 'column', ...style }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 6 }}>
        <span className="dr-label" style={{ fontSize: 9 }}>{label}</span>
        {meta ? <span className="dr-meta">{meta}</span> : null}
      </div>
      {children}
    </div>
  );
}

export function PmDock({ active, onPick }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-around', borderTop: '1px solid var(--stroke)', padding: '10px 0 14px', background: 'var(--inset-bg)', margin: '0 -12px' }}>
      {[['cock', '⊞'], ['map', '⌖'], ['rf', '⊚'], ['data', '▤'], ['arms', '⊗']].map(([k, g]) => (
        <button type="button" key={k} onClick={() => onPick && onPick(k)} style={{ background: 'transparent', border: 0, textAlign: 'center', cursor: 'pointer', color: active === k ? 'var(--acc)' : 'var(--fg-dim)', textShadow: active === k ? 'var(--acc-glow)' : 'none', minWidth: 44 }}>
          <div style={{ fontSize: 17 }}>{g}</div>
          <div className="stencil" style={{ fontSize: 6.5 }}>{k}</div>
        </button>
      ))}
    </div>
  );
}

// ── PHONE · RF (passive intel) ──────────────────────────────────
export function DirPhoneRf({ t, onNav }) {
  const simRaw = useSim();
  const sim = { ...simRaw, mode: 'foot' };
  return (
    <div style={{ position: 'absolute', inset: 0, zIndex: 1, display: 'flex', flexDirection: 'column', padding: '12px 12px 0', gap: 9 }} data-screen-label="A′ · POCKET RF">
      <PmHead sim={sim} mode="rf · passive" />

      <PmTile label="rf · sweep" meta={`peak ${sim.rf.peakMhz}M · receive-only`}>
        <SpectrumStrip spectrum={sim.rf.spectrum} h={40} />
      </PmTile>

      <PmTile label="wi-fi · wardrive" meta="kismet dead — honest source">
        {sim.recon.wardrive.length === 0 ? <HonestState kind="acquiring" label="scanning wlan0" compact /> : sim.recon.wardrive.slice(0, 4).map((n) => (
          <div key={n.bssid} style={{ display: 'flex', alignItems: 'baseline', gap: 8, padding: '4px 0', borderBottom: '1px dotted var(--edge)' }}>
            <span className="mono" style={{ fontSize: 9.5, color: n.own ? 'var(--teal)' : 'var(--fg)', flex: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{n.ssid}</span>
            <span className="mono" style={{ fontSize: 8, color: n.enc === 'OPEN' ? 'var(--acc)' : 'var(--fg-dim)' }}>{n.enc}</span>
            <span className="mono" style={{ fontSize: 9, color: 'var(--fg-mute)', width: 28, textAlign: 'right' }}>{n.rssi}</span>
          </div>
        ))}
      </PmTile>

      <PmTile label="ble" meta="hci0">
        <HonestState kind="no-hw" label="bt adapter down" hint="surveillance blind — tap to reset hci0" compact />
      </PmTile>

      <PmTile label="counter-surveillance" meta="ghost · passive">
        <div className="mono" style={{ fontSize: 9, color: 'var(--fg-mute)' }}>trackers 0 · imsi-suspects 0 · alpr idle</div>
        <div className="mono" style={{ fontSize: 7.5, color: 'var(--fg-deep)', marginTop: 4 }}>needs sdr + bt feeds to flag</div>
      </PmTile>

      <div style={{ flex: 1 }}></div>
      <PmDock active="rf" onPick={onNav} />
    </div>
  );
}

// ── PHONE · ARSENAL (offensive, gated) ──────────────────────────
export function DirPhoneFoot({ t, onNav }) {
  const simRaw = useSim();
  const sim = { ...simRaw, mode: 'foot' };
  const [armed, setArmed] = React.useState(false);
  return (
    <div style={{ position: 'absolute', inset: 0, zIndex: 1, display: 'flex', flexDirection: 'column', padding: '12px 12px 0', gap: 9 }} data-screen-label="A′ · POCKET ARSENAL">
      <PmHead sim={sim} mode="arms · gated" />

      <PmTile label="marauder · deauth" meta="offensive · gated">
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span className="mono" style={{ fontSize: 8.5, color: 'var(--fg-mute)', flex: 1 }}>allowlist 2 APs · ch 6 · 30 s</span>
          {armed ? (
            <React.Fragment>
              <button type="button" className="mono" onClick={() => setArmed(false)} style={{ fontSize: 9, color: 'var(--bg-0)', background: 'var(--red)', border: 0, borderRadius: 5, padding: '8px 12px', cursor: 'pointer', letterSpacing: '0.08em' }}>CONFIRM 118s</button>
              <button type="button" className="mono" onClick={() => setArmed(false)} style={{ fontSize: 9, color: 'var(--fg-dim)', border: '1px solid var(--stroke-2)', background: 'transparent', borderRadius: 5, padding: '8px 10px', cursor: 'pointer' }}>✕</button>
            </React.Fragment>
          ) : (
            <button type="button" className="mono" onClick={() => setArmed(true)} style={{ fontSize: 9, color: 'var(--acc)', border: '1px solid var(--stroke-acc)', background: 'transparent', borderRadius: 5, padding: '8px 16px', cursor: 'pointer', letterSpacing: '0.1em' }}>ARM</button>
          )}
        </div>
        <div className="mono" style={{ fontSize: 7.5, color: 'var(--fg-deep)', marginTop: 6 }}>two-leg confirm-token · 120s expiry · no optimistic flip</div>
      </PmTile>

      <PmTile label="wi-fi audit" meta="allowlist-gated">
        <div className="mono" style={{ fontSize: 9, color: 'var(--fg-mute)' }}>{sim.recon.auditAllowlist.length ? sim.recon.auditAllowlist.join(' · ') : 'allowlist empty — disabled'}</div>
        <div className="mono" style={{ fontSize: 7.5, color: 'var(--fg-deep)', marginTop: 4 }}>passive handshake/pmkid capture · no deauth-to-force</div>
      </PmTile>

      <PmTile label="hid · badusb" meta="arm → confirm → run">
        <HonestState kind="no-hw" label="native gadget unconfigured" hint="dr_mode=host — nothing can be typed" compact />
      </PmTile>

      <PmTile label="sentry" meta="no optimistic flip">
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span className="mono" style={{ fontSize: 11, color: 'var(--teal)', letterSpacing: '0.1em', whiteSpace: 'nowrap' }}>○ DISARMED</span>
          <span className="dr-ghost" style={{ marginLeft: 'auto', padding: '8px 14px' }}>arm sentry</span>
        </div>
      </PmTile>

      <div style={{ flex: 1 }}></div>
      <PmDock active="arms" onPick={onNav} />
    </div>
  );
}

// ── PHONE · DIAG ────────────────────────────────────────────────
export function DirPhoneDiag({ t, onNav }) {
  const simRaw = useSim();
  const sim = { ...simRaw, mode: 'diag' };
  const s = sim;
  const noEcu = sim.hw.ecu !== 'ok';
  const rows = [
    ['coolant', drFmt.n1(s.coolant) + ' °C', (s.coolant - 40) / 80, s.coolant > 104],
    ['voltage', drFmt.n1(s.voltage) + ' V', (s.voltage - 11) / 4, s.voltage < 12],
    ['maf', drFmt.n1(2.4 + s.throttle * 14) + ' g/s', (2.4 + s.throttle * 14) / 30],
    ['engine load', drFmt.n0(s.throttle * 88) + ' %', s.throttle * 0.88],
    ['stft · b1', drFmt.n1(2.1 + Math.sin(s.t) * 1.8) + ' %', 0.6],
    ['ltft · b1', '5.8 %', 0.79, false],
    ['fuel level', drFmt.n0(64 - s.trip.fuel) + ' %', (64 - s.trip.fuel) / 100],
  ];
  return (
    <div style={{ position: 'absolute', inset: 0, zIndex: 1, display: 'flex', flexDirection: 'column', padding: '12px 12px 0', gap: 9 }} data-screen-label="A′ · POCKET DIAG">
      <PmHead sim={sim} mode="diag" />

      <PmTile label="obd · sensors" meta="learned ticks">
        {noEcu ? <HonestState kind="no-hw" label="ecu not connected" hint="can0 idle — plug in obd-ii" compact /> : rows.map(([l, v, frac, hot]) => (
          <div key={l} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4.5px 0', borderBottom: '1px dotted var(--edge)' }}>
            <span className="mono" style={{ fontSize: 9, color: 'var(--fg-mute)', width: 78, flex: 'none' }}>{l}</span>
            <div style={{ flex: 1, height: 4, borderRadius: 2, background: 'rgba(255,255,255,0.05)', position: 'relative' }}>
              <div style={{ position: 'absolute', top: -2, bottom: -2, left: `${Math.max(0, Math.min(1, frac)) * 100}%`, width: 2, background: hot ? 'var(--acc)' : 'var(--fg-mute)' }}></div>
            </div>
            <b className="mono" style={{ fontSize: 10, color: hot ? 'var(--acc)' : 'var(--fg)', fontWeight: 500, whiteSpace: 'nowrap' }}>{v}</b>
          </div>
        ))}
      </PmTile>

      <PmTile label="session report" meta="on demand">
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
          <span className="mono" style={{ fontSize: 8, color: 'var(--red)', border: '1px solid rgba(var(--red-rgb),0.5)', borderRadius: 3, padding: '1px 6px', whiteSpace: 'nowrap' }}>SAFETY FLAG</span>
          <span className="mono" style={{ fontSize: 8, color: 'var(--fg-dim)' }}>conf 0.74</span>
        </div>
        <div style={{ fontSize: 11, color: 'var(--fg)' }}>lean condition b1 (P0171) · RR tire 6% under</div>
        <div className="mono" style={{ fontSize: 8.5, color: 'var(--fg-mute)', marginTop: 3 }}>→ smoke-test intake · re-torque RR</div>
      </PmTile>

      <PmTile label="vivi" meta="single-flight">
        <div style={{ display: 'flex', gap: 6 }}>
          <div className="mono" style={{ flex: 1, border: '1px solid var(--stroke-2)', borderRadius: 5, padding: '9px 10px', fontSize: 9.5, color: 'var(--fg-dim)' }}>ask about this session…</div>
          <span className="mono" style={{ fontSize: 9.5, color: 'var(--acc)', border: '1px solid var(--stroke-acc)', borderRadius: 5, padding: '9px 14px', letterSpacing: '0.1em', cursor: 'pointer' }}>ASK</span>
        </div>
        <div className="mono" style={{ fontSize: 7.5, color: 'var(--fg-deep)', marginTop: 6 }}>⚠ model not resident — first query cold-loads</div>
      </PmTile>

      <PmTile label="anomalies" meta="envelope">
        <div style={{ fontSize: 10.5, color: 'var(--fg)' }}>voltage ripple 0.3V @ idle <span className="mono" style={{ fontSize: 7.5, color: 'var(--cyan)' }}>edge</span></div>
        <div style={{ fontSize: 10.5, color: 'var(--fg)', marginTop: 4 }}>stft drift +4% / 10 min <span className="mono" style={{ fontSize: 7.5, color: 'var(--cyan)' }}>watch</span></div>
      </PmTile>

      <div style={{ flex: 1 }}></div>
      <PmDock active="data" onPick={onNav} />
    </div>
  );
}

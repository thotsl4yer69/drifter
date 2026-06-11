// ════════════════════════════════════════════════════════════════
// COCKPIT SHELL — responsive app (phone <700 · mid 700–1100 · full
// ≥1100), mode-aware rail, all LEDGER surfaces. Ported from app/
// shell.jsx; the canvas tweaks-protocol is replaced by useSettings +
// DevPanel (persisted theme/density/scanlines; bench overrides).
// Data is the real adapter (WS+REST) or ?sim mock — same interface.
// ════════════════════════════════════════════════════════════════
import React from 'react';
import { useSim, Spark, TapeGauge, ShiftLights, drFmt } from '../shared/widgets.jsx';
import { DrifterSim } from '../data/adapter.js';
import { useSettings, DevPanel } from '../shared/settings.jsx';
import { LgTile, LgRail, LgTop, LgSpeed, LgGauge, LgAlerts, LgRf, LgTrip, LgVivi, LgRight } from '../directions/ledger.jsx';
import { RfMain, FtMain, DgMain } from '../directions/modes.jsx';
import { MpMain } from '../directions/map.jsx';
import { SyMain, VvMain } from '../directions/system.jsx';
import { DirPhone } from '../directions/phone.jsx';
import { DirPhoneRf, DirPhoneFoot, DirPhoneDiag, PmDock } from '../directions/phone-modes.jsx';

function useViewport() {
  const [v, setV] = React.useState({ w: window.innerWidth, h: window.innerHeight });
  React.useEffect(() => {
    const on = () => setV({ w: window.innerWidth, h: window.innerHeight });
    window.addEventListener('resize', on);
    return () => window.removeEventListener('resize', on);
  }, []);
  return v;
}

function LinkBanner({ sim }) {
  if (sim.link !== 'lost') return null;
  return (
    <div className="mono" style={{ flex: 'none', display: 'flex', alignItems: 'center', gap: 10, padding: '6px 12px', borderRadius: 6, border: '1px solid rgba(var(--red-rgb),0.5)', background: 'rgba(var(--red-rgb),0.10)', color: 'var(--red)', fontSize: 10, letterSpacing: '0.12em' }}>
      <span style={{ animation: 'drPulse 1.2s ease-in-out infinite' }}>●</span> LINK LOST — ws :8081 down · retry backoff · values frozen
    </div>
  );
}
function DemoteBanner({ sim }) {
  if (!sim.autoDemoted) return null;
  return (
    <div className="mono" style={{ flex: 'none', display: 'flex', alignItems: 'center', gap: 10, padding: '6px 12px', borderRadius: 6, border: '1px solid var(--stroke-acc)', background: 'rgba(var(--acc-rgb),0.10)', color: 'var(--acc)', fontSize: 10, letterSpacing: '0.12em' }}>
      ⚠ WATCHDOG AUTO-DEMOTED → DIAG · memory/thermal pressure · arsenal suspended
    </div>
  );
}

function ShDriveMain({ sim, short }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10, padding: 10, minHeight: 0, minWidth: 0 }}>
      <LinkBanner sim={sim} />
      <DemoteBanner sim={sim} />
      <div style={{ display: 'grid', gridTemplateColumns: '1.55fr 1fr 1fr 1fr', gap: 10, height: short ? 184 : 218, flex: 'none' }}>
        <LgSpeed sim={sim} big={!short} />
        <LgGauge label="rpm · crank" meta="CAN · 50ms" noHw={sim.hw.ecu !== 'ok'} stale={sim.link === 'lost'}
          num={drFmt.n1(sim.rpm / 1000)} unit="×1000"
          spark={sim.hist.rpm} sparkColor="var(--cyan)"
          top={<ShiftLights rpm={sim.rpm} />}
          alarm={sim.rpm > 6300}
          tape={<TapeGauge value={sim.rpm} min={0} max={7000} band={[6500, 7000]} ghosts={[3500]} color="var(--cyan)" ladder={[{ t: '0' }, { t: '3.5' }, { t: 'redline 6.5', hot: true }]} />} />
        <LgGauge label="coolant · prim" meta="B1 · 1s" noHw={sim.hw.ecu !== 'ok'} stale={sim.link === 'lost'}
          num={drFmt.n1(sim.coolant)} unit="°C"
          spark={sim.hist.coolant} sparkColor="var(--acc)"
          alarm={sim.coolant > 104}
          tape={<TapeGauge value={sim.coolant} min={40} max={120} band={[108, 120]} ghosts={[104]} color="var(--acc)" ladder={[{ t: '40' }, { t: 'amber 104', hot: true }, { t: '120' }]} />} />
        <LgGauge label="voltage · alt" meta="power · 1s" noHw={sim.hw.ecu !== 'ok'} stale={sim.link === 'lost'}
          num={drFmt.n1(sim.voltage)} unit="V"
          spark={sim.hist.voltage} sparkColor="var(--teal)"
          alarm={sim.voltage < 12}
          tape={<TapeGauge value={sim.voltage} min={11} max={15} ghosts={[12, 14.4]} color="var(--teal)" ladder={[{ t: '11.0' }, { t: '12.0 crit', hot: true }, { t: '14.4' }]} />} />
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1.25fr 1fr', gap: 10, flex: 1, minHeight: 0 }}>
        <LgAlerts sim={sim} />
        <LgRf sim={sim} />
      </div>
      <LgTrip sim={sim} />
      <LgVivi sim={sim} />
    </div>
  );
}

function ShSurface({ surf, sim, short, onNav }) {
  const armed = sim.mode === 'foot' || sim.mode === 'both';
  if (surf === 'map') return <MpMain sim={sim} />;
  if (surf === 'hw') return <DgMain sim={sim} showBanner={sim.autoDemoted} />;
  if (surf === 'set') return <SyMain sim={sim} />;
  if (surf === 'vivi') return <VvMain sim={sim} />;
  if (surf === 'rf' || surf === 'arms') {
    if (!armed) {
      const offensive = surf === 'arms';
      return (
        <div style={{ display: 'grid', placeItems: 'center', padding: 20 }}>
          <div className="dr-tile bracketed" style={{ padding: '22px 28px', maxWidth: 420, textAlign: 'center' }}>
            <div className="mono" style={{ fontSize: 13, color: 'var(--fg-dim)', marginBottom: 8 }}>⊘</div>
            <div className="stencil" style={{ fontSize: 10, color: 'var(--fg)' }}>{offensive ? 'arsenal locked' : 'rf intel locked'}</div>
            <div className="mono" style={{ fontSize: 9, color: 'var(--fg-dim)', marginTop: 8, lineHeight: 1.7 }}>
              drive mode suppresses {offensive ? 'offensive' : 'recon'} surfaces.<br />switch the MODE pill to foot or both when parked.
            </div>
            <div style={{ marginTop: 14, display: 'flex', justifyContent: 'center' }}>
              <button type="button" className="dr-ghost" onClick={() => onNav('mode-foot')}>switch to foot mode</button>
            </div>
          </div>
        </div>
      );
    }
    return surf === 'rf' ? <RfMain sim={sim} /> : <FtMain sim={sim} />;
  }
  if (surf === 'trip') {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10, padding: 10, minHeight: 0 }}>
        <LgTrip sim={sim} />
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, flex: 'none' }}>
          <LgTile label="session" meta="0611-A · recording" live>
            <div style={{ padding: '4px 0' }}>
              <Spark data={sim.hist.speed} color="var(--cyan)" min={0} max={130} h={54} />
              <div className="mono" style={{ fontSize: 8, color: 'var(--fg-dim)', marginTop: 4 }}>speed trace · this session</div>
            </div>
          </LgTile>
          <LgTile label="session report" meta="post-drive · on demand">
            <div className="mono" style={{ fontSize: 9.5, color: 'var(--fg-mute)', lineHeight: 1.7 }}>
              report generates after the drive ends — one tap, never automatic.
            </div>
            <span className="dr-ghost" style={{ marginTop: 8, alignSelf: 'flex-start' }}>generate report</span>
          </LgTile>
        </div>
      </div>
    );
  }
  return <ShDriveMain sim={sim} short={short} />;
}

class ShBoundary extends React.Component {
  constructor(p) { super(p); this.state = { err: null }; }
  static getDerivedStateFromError(err) { return { err }; }
  componentDidUpdate(prev) { if (prev.surfKey !== this.props.surfKey && this.state.err) this.setState({ err: null }); }
  render() {
    if (this.state.err) {
      return (
        <div style={{ display: 'grid', placeItems: 'center', padding: 20 }}>
          <div className="dr-tile bracketed" style={{ padding: '20px 26px', maxWidth: 440 }}>
            <div className="mono" style={{ fontSize: 10, color: 'var(--red)', letterSpacing: '0.12em', marginBottom: 6 }}>⚠ SURFACE RENDER FAULT</div>
            <div className="mono" style={{ fontSize: 9, color: 'var(--fg-mute)', lineHeight: 1.7, wordBreak: 'break-word' }}>{String(this.state.err && this.state.err.message || this.state.err)}</div>
            <button type="button" className="dr-ghost" style={{ marginTop: 12 }} onClick={() => this.setState({ err: null })}>retry render</button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

export function CockpitApp() {
  const [t, setTweak] = useSettings();
  const sim = useSim();
  const { w, h } = useViewport();
  const forced = new URLSearchParams(location.search).get('layout');
  const layout = forced || (w < 700 ? 'phone' : w < 1100 ? 'mid' : 'full');
  const [surf, setSurfRaw] = React.useState(() => localStorage.getItem('dr-cockpit-surf') || 'cockpit');
  const [pocket, setPocket] = React.useState(() => localStorage.getItem('dr-cockpit-pocket') || 'cock');
  const [sheet, setSheet] = React.useState(false);
  const setSurf = (k) => { setSurfRaw(k); localStorage.setItem('dr-cockpit-surf', k); };
  const onNavPocket = (k) => { setPocket(k); localStorage.setItem('dr-cockpit-pocket', k); };

  // mode is a real control — sync the persisted/desired mode to the node.
  React.useEffect(() => { DrifterSim.setMode(t.mode); }, [t.mode]);

  const onNav = (k) => {
    if (k === 'mode-foot') { setTweak('mode', 'foot'); return; }
    setSurf(k);
  };

  const short = h < 760;
  const showDrawer = layout === 'full' && surf === 'cockpit';

  const frame = (children) => (
    <div className="dr"
      data-dr-theme={t.theme === 'uncaged' ? undefined : t.theme}
      data-dr-scan={t.scanlines ? 'on' : 'off'}
      data-dr-density={t.density}
      style={{ position: 'fixed', inset: 0 }}>
      <div className="dr-atmo"></div>
      {children}
      <div className="dr-scan"></div>
      <DevPanel t={t} setTweak={setTweak} />
    </div>
  );

  if (layout === 'phone') {
    const pocketSurf = {
      cock: <DirPhone t={t} onNav={onNavPocket} />,
      arms: <DirPhoneFoot t={t} onNav={onNavPocket} />,
      rf: <DirPhoneRf t={t} onNav={onNavPocket} />,
      data: <DirPhoneDiag t={t} onNav={onNavPocket} />,
      map: (
        <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', zIndex: 1 }} data-screen-label="pocket map">
          <div style={{ flex: 1, position: 'relative', minHeight: 0 }}><MpMain sim={sim} /></div>
          <div style={{ padding: '0 12px' }}><PmDock active="map" onPick={onNavPocket} /></div>
        </div>
      ),
    }[pocket] || <DirPhone t={t} onNav={onNavPocket} />;
    return frame(<div style={{ position: 'absolute', inset: 0, zIndex: 1 }}><ShBoundary surfKey={pocket}>{pocketSurf}</ShBoundary></div>);
  }

  return frame(
    <div style={{
      position: 'absolute', inset: 0, zIndex: 1, display: 'grid',
      gridTemplateRows: '44px 1fr',
      gridTemplateColumns: showDrawer ? '58px 1fr 322px' : '58px 1fr',
      gridTemplateAreas: showDrawer ? '"top top top" "rail main right"' : '"top top" "rail main"',
    }} data-screen-label={`cockpit · ${surf}`}>
      <div style={{ gridArea: 'top', display: 'grid' }}><LgTop sim={sim} narrow={layout === 'mid'} /></div>
      <div style={{ gridArea: 'rail', display: 'grid' }}><LgRail active={surf === 'vivi' ? 'cockpit' : surf} onPick={onNav} /></div>
      <div style={{ gridArea: 'main', display: 'grid', minHeight: 0, minWidth: 0 }}>
        <ShBoundary surfKey={surf}><ShSurface surf={surf} sim={sim} short={short} onNav={onNav} /></ShBoundary>
      </div>
      {showDrawer ? (
        <div style={{ gridArea: 'right', padding: '10px 10px 10px 0', display: 'grid', minHeight: 0 }}>
          <LgRight sim={sim} short={short} />
        </div>
      ) : null}
      {layout === 'mid' && surf === 'cockpit' ? (
        <React.Fragment>
          <button onClick={() => setSheet(!sheet)} className="mono"
            style={{ position: 'absolute', right: 14, bottom: 14, zIndex: 40, fontSize: 10, letterSpacing: '0.14em', padding: '10px 16px', borderRadius: 999, border: '1px solid var(--stroke-acc)', background: 'var(--glass-strong)', color: 'var(--acc)', cursor: 'pointer' }}>
            {sheet ? 'CLOSE' : 'DATA'}</button>
          {sheet ? (
            <div style={{ position: 'absolute', right: 14, bottom: 60, width: 330, height: 'min(72%, 560px)', zIndex: 39, display: 'grid', boxShadow: '0 18px 50px rgba(0,0,0,0.6)' }}>
              <LgRight sim={sim} short />
            </div>
          ) : null}
        </React.Fragment>
      ) : null}
    </div>
  );
}

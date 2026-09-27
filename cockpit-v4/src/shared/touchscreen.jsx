import React from 'react';
import { hardwareView, metricView, perceptionView } from '../data/display-state.js';
import '../styles/touchscreen.css';

export function TouchRail({ active, onPick }) {
  const items = [
    { k: 'cockpit', g: '◈', l: 'drive' }, { k: 'map', g: '⌖', l: 'map' },
    { k: 'hw', g: '▤', l: 'diag' }, { k: 'rf', g: '⊚', l: 'rf' },
    { k: 'arms', g: '⊗', l: 'foot' }, { k: 'vivi', g: '●', l: 'vivi' },
    { k: 'set', g: '◌', l: 'system' },
  ];
  return <nav className="dr-touch-rail" aria-label="DRIFTER workspaces">
    {items.map(it => <button type="button" key={it.k} onClick={() => onPick(it.k)}
      className={active === it.k ? 'active' : ''} aria-current={active === it.k ? 'page' : undefined}>
      <span aria-hidden="true" style={{fontSize:18}}>{it.g}</span><span className="stencil">{it.l}</span>
    </button>)}
  </nav>;
}

export function TouchHeader({ sim, theme, onTheme, onData, sheet, demo }) {
  const nextTheme = theme === 'daylight' ? 'nightrun' : theme === 'nightrun' ? 'uncaged' : 'daylight';
  const nextLabel = nextTheme === 'daylight' ? 'DAY' : nextTheme === 'nightrun' ? 'NIGHT' : 'AMBER';
  return <header className="dr-touch-header">
    <div className="dr-touch-brand"><strong>DRIFTER</strong><span className="mono">VEHICLE / FIELD NODE</span></div>
    <span className={`dr-touch-link mono ${sim.link === 'live' ? '' : 'lost'}`} role="status">
      {demo ? 'TEST DATA' : sim.link === 'live' ? 'NODE CONNECTED' : 'RECONNECTING'}
    </span>
    <span className="dr-touch-mode mono">{String(sim.mode || 'unknown').toUpperCase()}</span>
    <button type="button" className="dr-touch-action mono" onClick={() => onTheme(nextTheme)} aria-label={`Switch display to ${nextLabel.toLowerCase()} theme`}>{nextLabel}</button>
    <button type="button" className="dr-touch-action mono" onClick={onData} aria-haspopup="dialog" aria-controls="dr-data-sheet" aria-expanded={sheet}>DATA</button>
  </header>;
}

export function HwStrip({ sim, now, demo }) {
  return <div className="dr-hw-strip" aria-label="Reported hardware status">
    {hardwareView(sim, now, demo).map(([label, value]) => <span key={label}
      className={['DATA', 'FIX', 'LIVE', 'RECORDING'].includes(value) ? 'ok' : 'off'}>
      <i aria-hidden="true" />{label}<b>{value}</b>
    </span>)}
  </div>;
}

function MetricTile({ sim, metric, label, unit, digits = 0, divisor = 1, now, demo }) {
  const view = metricView(sim, metric, now, demo);
  const valid = view.value !== null;
  const history = (sim.hist?.[metric] || []).filter(Number.isFinite).slice(-40);
  const lo = history.length ? Math.min(...history) : 0;
  const hi = history.length ? Math.max(...history) : 0;
  const points = history.map((v, i) => `${i * 100 / Math.max(1, history.length - 1)},${20 - 16 * (v - lo) / (hi - lo || 1)}`).join(' ');
  return <section className={`dr-metric dr-tile bracketed ${metric === 'speed' ? 'speed' : ''}`} aria-label={`${label} ${view.status}`} data-metric={metric}>
    <div className="dr-metric-label mono">{label}</div>
    <div className={`dr-metric-value mono ${valid ? '' : 'empty'}`}>{valid ? (view.value / divisor).toFixed(digits) : '—'}</div>
    <div className="dr-metric-unit mono">{unit}</div>
    <svg className="dr-metric-spark" viewBox="0 0 100 24" preserveAspectRatio="none" aria-hidden="true">
      {valid && history.length > 1 ? <polyline points={points} fill="none" stroke="currentColor" strokeWidth="1" vectorEffect="non-scaling-stroke" /> : null}
    </svg>
    <div className={`dr-metric-state mono ${valid ? '' : 'waiting'}`}>{view.status}</div>
  </section>;
}

export function PerceptionTile({ sim, now }) {
  const view = perceptionView(sim, now);
  return <section className={`dr-tile bracketed dr-touch-perception ${view.tone}`} aria-label="Experimental perception">
    <div className="dr-touch-card-head mono"><span>PERCEPTION</span><b>{view.badge}</b></div>
    <strong className="dr-touch-card-title">{view.headline}</strong>
    <p>{view.detail}</p>
  </section>;
}

function AlertSummary({ sim, onDiag }) {
  const severity = { crit: 0, warn: 1, info: 2 };
  const alerts = (Array.isArray(sim.alerts) ? sim.alerts : []).filter(a => a && typeof a === 'object')
    .slice().sort((a, b) => (severity[a.sev] ?? 3) - (severity[b.sev] ?? 3));
  const first = alerts[0];
  const lost = sim.link !== 'live';
  return <button type="button" className={`dr-tile bracketed dr-alert-summary ${!lost && first?.sev === 'crit' ? 'critical' : ''}`} onClick={onDiag} aria-label="Open diagnostics and complete alert list">
    <span className="dr-touch-card-head mono"><span>DIAGNOSTICS</span><b>{lost ? 'LINK LOST' : `${alerts.length} REPORTED`}</b></span>
    <strong className="dr-touch-card-title">{lost ? 'LIVE ALERTS UNAVAILABLE' : first ? String(first.code || 'VEHICLE ALERT') : 'NO ALERTS REPORTED'}</strong>
    <span className="dr-alert-message">{lost ? 'Waiting for the node to reconnect.' : first ? String(first.msg || 'Open diagnostics for details.') : 'Open DIAG for fault codes and connection checks.'}</span>
    <span className="dr-alert-cta mono">OPEN DIAG →</span>
  </button>;
}

// One local half-second clock expires gauges even when WS stops sending data.
// No network polling; cleaned up when leaving DRIVE.
export class DrivePanel extends React.Component {
  constructor(props) { super(props); this.state = { now: Date.now() }; }
  componentDidMount() { this.clock = setInterval(() => this.setState({ now: Date.now() }), 500); }
  componentWillUnmount() { clearInterval(this.clock); }
  render() {
    const { sim, demo, onDiag, children } = this.props;
    // Read the clock on every render: props may arrive between timer ticks.
    const now = Date.now();
    return <div className="dr-drive-screen">
      {sim.autoDemoted ? <div className="dr-drive-warning mono" role="status">WATCHDOG DEMOTED TO DIAG · FIELD TOOLS SUSPENDED</div> : null}
      <HwStrip sim={sim} now={now} demo={demo} />
      <div className="dr-primary-gauges">
        <MetricTile sim={sim} metric="speed" label="SPEED · OBD" unit="km/h" now={now} demo={demo} />
        <MetricTile sim={sim} metric="rpm" label="ENGINE RPM" unit="× 1000 rpm" divisor={1000} digits={1} now={now} demo={demo} />
        <MetricTile sim={sim} metric="coolant" label="COOLANT" unit="°C" digits={1} now={now} demo={demo} />
        <MetricTile sim={sim} metric="voltage" label="SUPPLY" unit="V" digits={1} now={now} demo={demo} />
      </div>
      <div className="dr-drive-context"><AlertSummary sim={sim} onDiag={onDiag} /><PerceptionTile sim={sim} now={now} /></div>
      <div className="dr-drive-extras">{children}</div>
    </div>;
  }
}

// Native modal supplies Escape and an inert background. Explicit Tab wrapping
// also keeps kiosk keyboard focus out of browser chrome.
export class DataSheet extends React.Component {
  componentDidMount() { this.returnFocus = document.activeElement; this.dialog.showModal(); }
  componentWillUnmount() {
    if (this.dialog?.open) this.dialog.close();
    if (this.returnFocus?.isConnected) this.returnFocus.focus();
  }
  render() {
    return <dialog id="dr-data-sheet" className="dr-data-sheet" ref={el => { this.dialog = el; }} aria-labelledby="dr-data-title" aria-modal="true" onClose={this.props.onClose}
      onKeyDown={event => {
        if (event.key !== 'Tab') return;
        const targets = Array.from(this.dialog.querySelectorAll('button, [href], input, select, textarea, [tabindex]'))
          .filter(el => !el.disabled && el.tabIndex >= 0 && el.getClientRects().length && !el.closest('[inert]'));
        const first = targets[0], last = targets[targets.length - 1];
        if (!first) { event.preventDefault(); return; }
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }}
      onClick={event => {
        if (event.target !== this.dialog) return;
        const r = this.dialog.getBoundingClientRect();
        if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) this.dialog.close();
      }}>
      <div className="dr-data-sheet-head"><h2 id="dr-data-title" className="mono">NODE DATA</h2>
        <button type="button" className="dr-touch-action mono" onClick={() => this.dialog.close()}>CLOSE</button>
      </div>
      <div className="dr-data-sheet-body">{this.props.children}</div>
    </dialog>;
  }
}

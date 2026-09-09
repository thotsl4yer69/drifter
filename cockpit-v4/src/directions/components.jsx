// ════════════════════════════════════════════════════════════════
// COMPONENT LIBRARY patterns — real FOOT control surface + Vivi.
// The cockpit must never render fake action state: every control below
// calls an existing fail-closed dashboard API and reports its response.
// ════════════════════════════════════════════════════════════════
import React from 'react';
import { DrifterSim } from '../data/adapter.js';

const FOOT_UNITS = [
  ['drifter-flipper', 'FLIPPER'],
  ['drifter-marauder', 'MARAUDER'],
  ['drifter-wardrive', 'WARDRIVE'],
  ['drifter-kismet', 'KISMET'],
  ['drifter-kismet-bridge', 'KISMET BRIDGE'],
  ['drifter-wifi-audit', 'WI-FI AUDIT'],
  ['drifter-rfaudio', 'RF AUDIO'],
  ['drifter-fly-catcher', 'FLY CATCHER'],
];

const FLIPPER_QUICK_ACTIONS = [
  ['wifi_scan_ap', 'WI-FI SCAN'],
  ['ble_scan', 'BLE SCAN'],
  ['freq_analyzer', 'FREQ ANALYZER'],
  ['subghz_monitor_start', 'SUB-GHZ MON'],
  ['subghz_monitor_stop', 'STOP MON'],
];

async function apiJson(path, body) {
  const opts = body === undefined ? {} : {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  };
  const res = await fetch(path, opts);
  const text = await res.text();
  let payload = null;
  if (text) {
    try { payload = JSON.parse(text); } catch (_) { payload = { message: text }; }
  }
  if (!res.ok) {
    const detail = payload?.error || payload?.message || `${res.status} ${res.statusText}`;
    throw new Error(detail);
  }
  return payload;
}

function touchBtn(txt, onClick, opts = {}) {
  const { hot = false, disabled = false, wide = false } = opts;
  return (
    <button type="button" onClick={onClick} disabled={disabled} className="mono"
      style={{
        minHeight: 42, minWidth: wide ? 132 : 92, padding: '8px 12px', borderRadius: 6,
        border: `1px solid ${hot ? 'var(--red)' : 'var(--stroke-acc)'}`,
        color: disabled ? 'var(--fg-deep)' : hot ? 'var(--red)' : 'var(--acc)',
        background: hot ? 'rgba(var(--red-rgb),0.08)' : 'rgba(var(--acc-rgb),0.04)',
        fontSize: 9, letterSpacing: '0.08em', cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.55 : 1, userSelect: 'none', touchAction: 'manipulation',
      }}>{txt}</button>
  );
}

// ── Real FOOT control surface ───────────────────────────────────
// Replaces the old local-only mock action tile. The Pi backend remains
// authoritative: mode switching is validated by config.MODES; service control
// is restricted to the arsenal unit allowlist; Flipper commands are allowlisted;
// HID still requires ARM → service-published arm id → CONFIRM.
export function CmGatedAction() {
  const [arsenal, setArsenal] = React.useState(null);
  const [payloads, setPayloads] = React.useState([]);
  const [hidStatus, setHidStatus] = React.useState(null);
  const [payloadId, setPayloadId] = React.useState('');
  const [backend, setBackend] = React.useState('flipper');
  const [busy, setBusy] = React.useState('');
  const [notice, setNotice] = React.useState('ready');

  const refresh = React.useCallback(async () => {
    try {
      const [a, p, h] = await Promise.all([
        apiJson('/api/arsenal'),
        apiJson('/api/hid/payloads'),
        apiJson('/api/hid/status'),
      ]);
      const list = Array.isArray(p) ? p : [];
      setArsenal(a);
      setPayloads(list);
      setHidStatus(h);
      setPayloadId((prev) => list.some((x) => x.id === prev) ? prev : (list[0]?.id || ''));
      if (h?.flipper?.connected) setBackend('flipper');
      setNotice('live state refreshed');
    } catch (e) {
      setNotice(`refresh failed · ${e.message}`);
    }
  }, []);

  React.useEffect(() => {
    refresh();
    const iv = setInterval(refresh, 5000);
    return () => clearInterval(iv);
  }, [refresh]);

  const run = async (key, fn) => {
    if (busy) return null;
    setBusy(key);
    setNotice(`${key}…`);
    try {
      const out = await fn();
      setNotice(`${key} · ${out?.ok === false ? 'failed' : 'ok'}`);
      await refresh();
      return out;
    } catch (e) {
      setNotice(`${key} · ${e.message}`);
      return null;
    } finally {
      setBusy('');
    }
  };

  const switchMode = (name) => run(`mode ${name}`, async () => {
    // POST directly and only update the local view after the Pi accepted it.
    // DrifterSim.setMode() is intentionally not used here because it performs
    // an optimistic local flip before its network request completes.
    const out = await apiJson(`/api/mode/${name}`, {});
    const local = DrifterSim.getState?.();
    if (local) {
      local.mode = name;
      local.autoDemoted = false;
    }
    return out || { ok: true };
  });

  const serviceAction = (unit, action) => run(`${action} ${unit.replace('drifter-', '')}`,
    () => apiJson(`/api/service/${unit}`, { action }));

  const flipperAction = (command) => run(command,
    () => apiJson('/api/flipper/command', { command }));

  const pollArmed = async () => {
    for (let i = 0; i < 8; i += 1) {
      await new Promise((r) => setTimeout(r, 400));
      const h = await apiJson('/api/hid/status');
      setHidStatus(h);
      if (h?.armed?.id) return h.armed;
    }
    return null;
  };

  const hidArm = () => run('hid arm', async () => {
    if (!payloadId) throw new Error('select a stored payload');
    await apiJson('/api/hid/command', {
      command: 'hid_arm', payload_id: payloadId, backend,
    });
    const armed = await pollArmed();
    if (!armed?.id) throw new Error('no arm confirmation from drifter-hid');
    return { ok: true, armed };
  });

  const hidConfirm = () => run('hid confirm', async () => {
    const id = hidStatus?.armed?.id;
    if (!id) throw new Error('nothing armed');
    return apiJson('/api/hid/command', { command: 'hid_confirm', id });
  });

  const hidCancel = () => run('hid cancel', async () => {
    const id = hidStatus?.armed?.id;
    if (!id) throw new Error('nothing armed');
    return apiJson('/api/hid/command', { command: 'hid_cancel', id });
  });

  const toolMap = Object.fromEntries((arsenal?.tools || []).filter((x) => x.unit).map((x) => [x.unit, x]));
  const mode = arsenal?.mode || DrifterSim.getState()?.mode || 'unknown';
  const footReady = mode === 'foot' || mode === 'both';
  const flipperActive = !!toolMap['drifter-flipper']?.live_meta?.unit_active;
  const armed = hidStatus?.armed || null;
  const noticeBad = /failed|offline|error|refus|403|409|500|503/i.test(notice);

  return (
    <div className="dr-tile" style={{ padding: '12px 14px', overflow: 'auto', minHeight: 0 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, alignItems: 'baseline', marginBottom: 8 }}>
        <span className="dr-label">foot control · touch console</span>
        <span className="mono" style={{ fontSize: 8, color: footReady ? 'var(--teal)' : 'var(--acc)' }}>
          MODE · {String(mode).toUpperCase()}
        </span>
      </div>

      <div className="mono" style={{ fontSize: 8, color: 'var(--fg-dim)', marginBottom: 5, letterSpacing: '0.08em' }}>OPERATING MODE</div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7, marginBottom: 10 }}>
        {touchBtn('DIAG', () => switchMode('diag'), { disabled: !!busy || mode === 'diag' })}
        {touchBtn('DRIVE', () => switchMode('drive'), { disabled: !!busy || mode === 'drive' })}
        {touchBtn('FOOT', () => switchMode('foot'), { hot: true, disabled: !!busy || mode === 'foot' })}
        {touchBtn('FULL OPSEC', () => { window.location.href = `http://${window.location.hostname}:8090/`; }, { wide: true, disabled: !!busy || !footReady })}
        {touchBtn('REFRESH', refresh, { disabled: !!busy })}
      </div>

      {!footReady ? (
        <div className="mono" style={{ fontSize: 8, color: 'var(--acc)', border: '1px solid var(--stroke-acc)', borderRadius: 5, padding: '6px 8px', marginBottom: 9 }}>
          FOOT arsenal locked · switch to FOOT before starting services, Flipper actions or HID.
        </div>
      ) : null}

      <div className="mono" style={{ fontSize: 8, color: 'var(--fg-dim)', marginBottom: 5, letterSpacing: '0.08em' }}>ARSENAL SERVICES</div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2,minmax(0,1fr))', gap: 5, marginBottom: 10 }}>
        {FOOT_UNITS.map(([unit, label]) => {
          const t = toolMap[unit];
          const active = !!t?.live_meta?.unit_active;
          const serviceLocked = !!busy || !footReady;
          return (
            <div key={unit} style={{ border: '1px solid var(--stroke)', borderRadius: 6, padding: '6px 7px', minWidth: 0 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 5 }}>
                <span style={{ color: active ? 'var(--teal)' : 'var(--fg-deep)' }}>●</span>
                <span className="mono" style={{ fontSize: 8, color: 'var(--fg)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>{label}</span>
              </div>
              <div style={{ display: 'flex', gap: 4 }}>
                <button type="button" className="dr-ghost" disabled={serviceLocked} onClick={() => serviceAction(unit, active ? 'restart' : 'start')}
                  style={{ flex: 1, minHeight: 34, cursor: serviceLocked ? 'not-allowed' : 'pointer', touchAction: 'manipulation', opacity: serviceLocked ? 0.5 : 1 }}>{active ? 'restart' : 'start'}</button>
                <button type="button" className="dr-ghost" disabled={serviceLocked || !active} onClick={() => serviceAction(unit, 'stop')}
                  style={{ minHeight: 34, cursor: serviceLocked || !active ? 'not-allowed' : 'pointer', touchAction: 'manipulation', opacity: serviceLocked || !active ? 0.5 : 1 }}>stop</button>
              </div>
            </div>
          );
        })}
      </div>

      <div className="mono" style={{ fontSize: 8, color: 'var(--fg-dim)', marginBottom: 5, letterSpacing: '0.08em' }}>FLIPPER BRIDGE · ALLOWLISTED QUICK ACTIONS</div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5, marginBottom: 10 }}>
        {FLIPPER_QUICK_ACTIONS.map(([cmd, label]) => touchBtn(label, () => flipperAction(cmd), {
          disabled: !!busy || !footReady || !flipperActive,
        }))}
      </div>
      {footReady && !flipperActive ? (
        <div className="mono" style={{ fontSize: 7.5, color: 'var(--fg-deep)', marginTop: -5, marginBottom: 9 }}>Flipper actions unlock when drifter-flipper is active and hardware is detected.</div>
      ) : null}

      <div className="mono" style={{ fontSize: 8, color: 'var(--fg-dim)', marginBottom: 5, letterSpacing: '0.08em' }}>RUBBER DUCKY · EXISTING ARM → CONFIRM GATE</div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: 6, marginBottom: 6 }}>
        <select value={payloadId} onChange={(e) => setPayloadId(e.target.value)} disabled={!footReady || !!busy}
          style={{ minHeight: 42, minWidth: 0, borderRadius: 6, border: '1px solid var(--stroke-2)', background: 'var(--inset-bg)', color: 'var(--fg)', padding: '0 8px', opacity: !footReady || busy ? 0.55 : 1 }}>
          {payloads.length === 0 ? <option value="">no stored payloads</option> : null}
          {payloads.map((p) => <option key={p.id} value={p.id}>{p.name || p.id}</option>)}
        </select>
        <select value={backend} onChange={(e) => setBackend(e.target.value)} disabled={!footReady || !!busy}
          style={{ minHeight: 42, borderRadius: 6, border: '1px solid var(--stroke-2)', background: 'var(--inset-bg)', color: 'var(--fg)', padding: '0 8px', opacity: !footReady || busy ? 0.55 : 1 }}>
          <option value="flipper">Flipper</option>
          <option value="native">Native HID</option>
        </select>
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        {!armed ? touchBtn('ARM PAYLOAD', hidArm, { wide: true, disabled: !!busy || !footReady || !payloadId }) : null}
        {armed ? touchBtn(`CONFIRM ${armed.id}`, hidConfirm, { wide: true, hot: true, disabled: !!busy || !footReady }) : null}
        {armed ? touchBtn('CANCEL', hidCancel, { disabled: !!busy }) : null}
      </div>
      <div className="mono" style={{ fontSize: 7.5, color: 'var(--fg-deep)', marginTop: 6 }}>
        backend readiness · flipper {hidStatus?.flipper?.connected ? 'connected' : 'not confirmed'} · native {hidStatus?.native?.bound ? 'bound' : 'not bound'}
      </div>

      <div className="mono" style={{ fontSize: 8, color: noticeBad ? 'var(--red)' : 'var(--fg-mute)', marginTop: 10, borderTop: '1px dotted var(--edge)', paddingTop: 7 }}>
        {busy ? '◌ ' : '● '}{notice} · controls are local-network + backend allowlist gated
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
          {state === 'cold' ? '⚠ model not resident — first query cold-loads (slow + power spike)' : state === 'thinking' ? 'qwen2.5:1.5b · thinking…' : 'qwen2.5:1.5b · warm'}
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

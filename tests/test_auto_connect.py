"""Tests for auto_connect: terse parsing + the connector-owned AP state machine."""
from __future__ import annotations

import json
import sys
import types

sys.path.insert(0, 'src')

import auto_connect
from config import (
    AP_FALLBACK_CONNECTION,
    AUTOCONNECT_AP_BACKOFF_MAX_SEC,
    AUTOCONNECT_AP_PROBE_SETTLE_SEC,
    AUTOCONNECT_AP_RESCAN_SEC,
)
from auto_connect import (
    next_probe_backoff,
    parse_active_connections,
    parse_active_ssid,
    parse_wifi_scan,
    pick_target_ssid,
)


def test_parse_wifi_scan_basic():
    out = "HomeNet\nCafe Wifi\n\nMZ1312_DRIFTER\n"
    assert parse_wifi_scan(out) == {"HomeNet", "Cafe Wifi", "MZ1312_DRIFTER"}


def test_parse_wifi_scan_unescapes_colon_ssid():
    # nmcli -t escapes ':' in an SSID as '\:'
    out = "Net\\:5G\nPlain\n"
    visible = parse_wifi_scan(out)
    assert "Net:5G" in visible
    assert pick_target_ssid(visible, ["Net:5G"]) == "Net:5G"


def test_parse_active_ssid_simple():
    out = "no:HomeNet\nyes:Cafe Wifi\n"
    assert parse_active_ssid(out) == "Cafe Wifi"


def test_parse_active_ssid_with_colon_in_ssid():
    # ACTIVE:SSID terse line; the SSID itself contains an (escaped) colon.
    out = "no:Other\nyes:Net\\:5G\n"
    assert parse_active_ssid(out) == "Net:5G"


def test_parse_active_ssid_none_when_no_active():
    assert parse_active_ssid("no:A\nno:B\n") is None


# ── Connector-owned AP state machine (_service_loop) ───────────────────
#
# The 2026-08 bug cluster: auto_connect classified the Pi's OWN MZ1312_DRIFTER
# beacon as a client connection (ACTIVE,SSID ambiguity + the `not ap_active`
# term lost on restart), latching a permanent fake 'connected' state that
# never scanned and never recovered. These tests pin the replacement:
# authoritative NAME,DEVICE detection + hysteresic teardown/rescan probes.

AP = AP_FALLBACK_CONNECTION
IFACE = 'wlan0'
PHONE = 'Drifter'


class _FakeCompleted:
    def __init__(self, stdout='', returncode=0):
        self.stdout = stdout
        self.stderr = ''
        self.returncode = returncode


class _FakeClient:
    def __init__(self, sink):
        self._sink = sink

    def publish(self, topic, payload, retain=True):
        self._sink.append(json.loads(payload))


def _loop(monkeypatch, known, *, conn=None, active_ssid=None, scans=(),
          connect_ok=True, steps=()):
    """Drive _service_loop for N iterations with the nmcli boundary faked.

    Per-iteration scripts (a bare value repeats every iteration; a list is
    consumed one entry per iteration and its last entry then repeats):
      conn        -- profile owning wlan0 per NAME,DEVICE --active
                     (None = no active Wi-Fi connection)
      active_ssid -- ACTIVE,SSID dev wifi answer (None = nothing active)
      scans       -- SSIDs returned by each `dev wifi list`
      steps       -- fake-clock seconds advanced between iterations
    """
    ac = auto_connect

    h = types.SimpleNamespace()
    h.ac = ac
    h.published = []
    h.commands = []
    h.sleeps = []
    h.connects = []
    h.connect_ok = connect_ok
    conn_q = list(conn) if isinstance(conn, list) else [conn]
    ssid_q = list(active_ssid) if isinstance(active_ssid, list) else [active_ssid]
    scan_q = [list(s) for s in scans] if isinstance(scans, list) else [list(scans)]
    steps_q = list(steps)

    class _Clock:                       # stands in for the time module
        now = 1000.0

        @staticmethod
        def time():
            return _Clock.now

    h.clock = _Clock

    def _pop(q):
        if not q:
            return None
        return q.pop(0) if len(q) > 1 else q[0]

    def fake_run(cmd, timeout=20.0):
        cmd = list(cmd)
        h.commands.append(cmd)
        if cmd[:4] == ['nmcli', '-t', '-f', 'NAME,DEVICE']:
            c = _pop(conn_q)
            return _FakeCompleted(stdout=f'{c}:{IFACE}\n' if c else '')
        if cmd[:4] == ['nmcli', '-t', '-f', 'ACTIVE,SSID']:
            s = _pop(ssid_q)
            return _FakeCompleted(stdout=f'yes:{s}\n' if s else '')
        if cmd[:4] == ['nmcli', '-t', '-f', 'SSID']:
            return _FakeCompleted(stdout='\n'.join(_pop(scan_q) or []))
        if cmd[0] == 'ip':
            return _FakeCompleted(stdout=f'{IFACE} UP 192.168.43.20/24\n')
        if cmd[0] == 'ping':
            return _FakeCompleted(returncode=0)
        return _FakeCompleted()

    monkeypatch.setattr(ac, 'shutil',
                        types.SimpleNamespace(which=lambda n: n in ('nmcli', 'ip', 'ping')))
    monkeypatch.setattr(ac, '_run', fake_run)
    monkeypatch.setattr(ac, '_sleep', lambda secs, keep: h.sleeps.append(secs))
    monkeypatch.setattr(ac, 'time', _Clock)

    def fake_connect(ssid):
        h.connects.append(ssid)
        return h.connect_ok

    monkeypatch.setattr(ac, 'connect_ssid', fake_connect)

    def run(iterations):
        left = [iterations]

        def keep_running():
            if left[0] <= 0:
                return False
            left[0] -= 1
            if steps_q:
                _Clock.now += steps_q.pop(0)
            return True

        ac._service_loop(known, _FakeClient(h.published), keep_running)

    h.run = run
    h.nmcli = lambda: h.commands
    h.states = lambda: [p['state'] for p in h.published]
    return h


def test_own_ap_connection_is_ap_fallback_not_connected(monkeypatch):
    """Our own beacon must read as ap_fallback -- never as a client join."""
    # NM reports MZ1312_DRIFTER as an ACTIVE client SSID too (the ambiguity).
    h = _loop(monkeypatch, known=[PHONE], conn=AP, active_ssid=AP)
    h.run(2)
    assert set(h.states()) <= {'ap_fallback'}
    assert 'connected' not in h.states()
    assert all(p['ap_fallback'] is True for p in h.published)
    assert all(p['ssid'] == AP for p in h.published)


def test_fresh_restart_with_ap_up_stays_ap_fallback(monkeypatch):
    """Pins the stuck-forever regression: cold in-process state (ap_active
    False after Restart=on-failure) plus AP-up must still be ap_fallback,
    and the loop must immediately probe instead of latching."""
    h = _loop(monkeypatch, known=[], conn=AP, active_ssid=AP)
    h.run(3)
    assert 'connected' not in h.states()
    downs = [c for c in h.nmcli() if c[1:3] == ['connection', 'down']]
    assert downs, "restart with AP up must trigger a recovery probe"


def test_empty_known_list_ignores_own_ap_beacon(monkeypatch):
    """known=[] used to match ANY active SSID via `not known` -- including our
    own beacon. The authoritative check must win first."""
    h = _loop(monkeypatch, known=[], conn=AP, active_ssid=AP)
    h.run(2)
    assert 'connected' not in h.states()
    assert h.states()[-1] == 'ap_fallback'


def test_real_client_join_still_connected(monkeypatch):
    """A genuine phone-tether join still publishes the full connected payload."""
    h = _loop(monkeypatch, known=[PHONE], conn=None, active_ssid=PHONE)
    h.run(2)
    assert h.states() == ['connected', 'connected']
    p = h.published[0]
    assert p['ssid'] == PHONE
    assert p['ip'] == '192.168.43.20'
    assert p['internet'] is True
    assert p['ap_fallback'] is False
    assert h.connects == []          # already joined; no re-join attempted
    assert not [c for c in h.nmcli() if c[1:3] == ['connection', 'up']]


def test_manual_unknown_network_join_is_respected(monkeypatch):
    """Operator hand-joins CoffeeShop — not in `known`. The connector must
    treat it as connected: no teardown hunting known SSIDs, no fallback AP,
    no re-join attempts. (The old `joined in known` gate destroyed manual
    joins by tearing them down to chase configured networks.)"""
    h = _loop(monkeypatch, known=['DG2144-B817', 'Drifter-Phone'],
              conn=None, active_ssid='CoffeeShop')
    h.run(3)
    assert h.states() == ['connected'] * 3
    assert all(p['ssid'] == 'CoffeeShop' for p in h.published)
    assert all(p['ap_fallback'] is False for p in h.published)
    downs = [c for c in h.nmcli() if c[1:3] == ['connection', 'down']]
    ups = [c for c in h.nmcli() if c[1:3] == ['connection', 'up']]
    assert downs == [] and ups == []
    assert h.connects == []          # never re-joins over the operator
    # A connected pass must not even scan for better networks.
    assert [c for c in h.nmcli() if c[-2:] == ['wifi', 'list']] == []


def test_manual_join_without_internet_stays_connected_flag_honest(monkeypatch):
    """connected reflects operator intent; internet=True/False keeps
    following the PING_HOST probe regardless of known-list membership."""
    h = _loop(monkeypatch, known=['DG2144-B817'], conn=None,
              active_ssid='CoffeeShop')
    monkeypatch.setattr(auto_connect, 'internet_ok', lambda: False)
    h.run(1)
    p = h.published[0]
    assert p['state'] == 'connected'
    assert p['ssid'] == 'CoffeeShop'
    assert p['internet'] is False


def test_parse_active_connections_maps_iface_to_profile():
    rows = ('lo:lo\n'
            'Wired connection 1:eth0\n'
            'tailscale0:tailscale0\n'
            f'{AP}:wlan0\n')
    assert parse_active_connections(rows, 'wlan0') == AP
    assert parse_active_connections(rows, 'eth0') == 'Wired connection 1'
    assert parse_active_connections(rows, 'tailscale0') == 'tailscale0'
    # escaped colon inside a profile name survives the terse split
    assert parse_active_connections('My\\:AP:wlan0\n', 'wlan0') == 'My:AP'
    # other devices' rows never leak through; empty output -> None
    assert parse_active_connections('wlan1stuff:wlan1\n', IFACE) is None
    assert parse_active_connections('', IFACE) is None


def test_probe_teardown_order_then_rebuild(monkeypatch):
    """Probe cycle: tear AP down -> settle -> fresh rescan/list -> no target ->
    rebuild the AP immediately."""
    h = _loop(monkeypatch, known=[PHONE], conn=AP, scans=[])
    h.run(1)

    cmds = h.nmcli()
    idx_down = next(i for i, c in enumerate(cmds)
                    if c[1:3] == ['connection', 'down'])
    idx_rescan = next(i for i, c in enumerate(cmds) if c[1:3] == ['dev', 'wifi'])
    idx_list = next(i for i, c in enumerate(cmds) if c[-2:] == ['wifi', 'list'])
    ups = [c for c in cmds if c[1:3] == ['connection', 'up']]
    assert idx_down < idx_rescan < idx_list
    assert ups == [['nmcli', 'connection', 'up', AP]]
    assert AUTOCONNECT_AP_PROBE_SETTLE_SEC in h.sleeps
    assert h.states()[-1] == 'ap_fallback'
    assert h.connects == []


def test_probe_finds_target_joins_without_raising_ap(monkeypatch):
    """Target visible after teardown: join it; the AP stays DOWN."""
    h = _loop(monkeypatch, known=[PHONE], conn=AP, scans=[['HomeNet', PHONE]])
    h.run(1)
    assert h.connects == [PHONE]
    assert not [c for c in h.nmcli() if c[1:3] == ['connection', 'up']], \
        "a successful join must NOT rebuild the rescue AP"
    assert any(s == 'connecting' for s in h.states())
    assert h.states()[-1] == 'connected'
    assert h.published[-1]['ap_fallback'] is False


def test_probe_backoff_doubles_and_caps():
    from config import AUTOCONNECT_AP_BACKOFF_MAX_SEC as MAX
    assert next_probe_backoff(AUTOCONNECT_AP_RESCAN_SEC) == min(
        AUTOCONNECT_AP_RESCAN_SEC * 2, MAX)
    assert next_probe_backoff(MAX) == MAX
    assert next_probe_backoff(MAX + 12345) == MAX


def test_successful_client_reset_of_backoff(monkeypatch):
    """After a successful client join the probe cadence returns to base.
    Timeline (fake-clock steps between iterations): probe@i1 rebuilds and
    doubles to 600s; i2 skips (inside backoff); i3 probes past 600s, finds
    the phone, joins (backoff reset); i4 is a normal client pass; i5 has the
    AP up again and must probe after only ~335s -- which only happens at
    BASE cadence (the unreset doubled interval would still be 600s)."""
    h = _loop(
        monkeypatch,
        known=[PHONE],
        conn=[AP, AP, AP, None, AP],
        # ACTIVE,SSID is only consulted on non-AP iterations (i4 here)
        active_ssid=PHONE,
        # scan queue is consumed per SCAN EVENT, not per iteration:
        # probe-scans happen at i1 (empty), i3 (phone visible), i5 (empty)
        scans=[[], [PHONE], []],
        steps=[325, 325, 325, 335],
    )
    h.run(5)
    downs = [c for c in h.nmcli() if c[1:3] == ['connection', 'down']]
    ups = [c for c in h.nmcli() if c[1:3] == ['connection', 'up']]
    assert len(downs) == 3, f"expected probes at i1/i3/i5, got {len(downs)}"
    assert len(ups) == 2, "rebuild only on failed probes (i1, i5)"
    assert h.connects == [PHONE]
    assert 'connected' in h.states()


def test_no_scan_while_ap_up_between_probes(monkeypatch):
    """Between probe windows the loop must not scan -- dev wifi list only
    serves NM's stale pre-AP cache while the radio is in AP mode."""
    h = _loop(monkeypatch, known=[PHONE], conn=AP)
    h.run(3)
    rescans = [c for c in h.nmcli() if c[1:3] == ['dev', 'wifi']]
    lists = [c for c in h.nmcli() if c[-2:] == ['wifi', 'list']]
    assert len(rescans) == 1 and len(lists) == 1, \
        ("exactly one trusted probe-scan expected, "
         f"got rescan={len(rescans)} list={len(lists)}")
    assert set(h.states()) == {'ap_fallback'}

import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import marauder_bridge as mb


class TestCommandLock:
    def test_lock_acquire_release(self):
        lock = mb.CommandLock()
        assert lock.try_acquire("scan_ap", "op-uuid-1") is True
        assert lock.held_by() == ("scan_ap", "op-uuid-1")

        # Second acquire fails while held
        assert lock.try_acquire("scan_sta", "op-uuid-2") is False

        lock.release()
        assert lock.held_by() is None

        # Now another acquire works
        assert lock.try_acquire("scan_sta", "op-uuid-2") is True


class TestPendingConfirms:
    def test_register_and_pop_within_window(self):
        store = mb.PendingConfirms(ttl_s=120)
        token = store.register("deauth_attack", {"target": "aa:..."})
        assert isinstance(token, str) and len(token) >= 16
        popped = store.pop(token)
        assert popped == ("deauth_attack", {"target": "aa:..."})

    def test_pop_unknown_token_returns_none(self):
        store = mb.PendingConfirms(ttl_s=120)
        assert store.pop("not-a-real-token") is None

    def test_pop_returns_single_use(self):
        store = mb.PendingConfirms(ttl_s=120)
        token = store.register("x", {})
        assert store.pop(token) == ("x", {})
        assert store.pop(token) is None  # already consumed

    def test_sweep_expires_old_entries(self):
        store = mb.PendingConfirms(ttl_s=0.05)
        token = store.register("x", {})
        time.sleep(0.1)
        store.sweep()
        assert store.pop(token) is None


import json
from unittest.mock import MagicMock


class TestDispatch:
    def _make_bridge(self, transport_mode="direct"):
        transport = MagicMock()
        transport.mode = transport_mode
        transport.hw_detail = "fake"
        mqtt = MagicMock()
        bridge = mb.Bridge(transport=transport, mqtt_client=mqtt,
                           allowlist_scope={"wifi": [], "ble": [], "evilportal": []},
                           session_writer=MagicMock())
        return bridge, transport, mqtt

    def test_dispatch_scan_ap_publishes_event_with_id(self):
        bridge, _, mqtt = self._make_bridge()
        payload = {"id": "op-uuid-1", "command": "scan_ap",
                   "args": {"mode": "ap", "duration_s": 30}}
        bridge.dispatch(payload)
        # Look for a publish to drifter/marauder/event with id echo
        found = False
        for call in mqtt.publish.call_args_list:
            topic, body = call.args[0], call.args[1]
            if topic == "drifter/marauder/event":
                ev = json.loads(body)
                if ev.get("id") == "op-uuid-1":
                    found = True
                    assert ev["ok"] is True
        assert found, f"No matching event publish: {mqtt.publish.call_args_list}"

    def test_dispatch_high_risk_without_token_returns_confirm_required(self):
        bridge, _, mqtt = self._make_bridge()
        payload = {"id": "op-uuid-2", "command": "deauth_attack",
                   "args": {"target_bssid": "aa:bb:cc:dd:ee:ff"}}
        bridge.dispatch(payload)
        for call in mqtt.publish.call_args_list:
            topic, body = call.args[0], call.args[1]
            if topic == "drifter/marauder/event":
                ev = json.loads(body)
                if ev["id"] == "op-uuid-2":
                    assert ev["ok"] is False
                    assert "confirm" in ev["response"].lower()
                    assert "confirm_token" in ev
                    return
        raise AssertionError("no event for op-uuid-2")

    def test_dispatch_high_risk_empty_allowlist_refuses(self):
        bridge, _, mqtt = self._make_bridge()
        # First call gets confirm token
        bridge.dispatch({"id": "a", "command": "deauth_attack",
                         "args": {"target_bssid": "aa:bb:cc:dd:ee:ff"}})
        token = None
        for call in mqtt.publish.call_args_list:
            if call.args[0] == "drifter/marauder/event":
                ev = json.loads(call.args[1])
                if ev["id"] == "a":
                    token = ev.get("confirm_token")
                    break
        assert token, "expected token on first call"

        mqtt.publish.reset_mock()
        # Second call with token still refuses (allowlist empty)
        bridge.dispatch({"id": "b", "command": "deauth_attack",
                         "args": {"target_bssid": "aa:bb:cc:dd:ee:ff"},
                         "confirm_token": token})
        found = False
        for call in mqtt.publish.call_args_list:
            if call.args[0] == "drifter/marauder/event":
                ev = json.loads(call.args[1])
                if ev["id"] == "b":
                    found = True
                    assert ev["ok"] is False
                    assert "allowlist" in ev["response"].lower()
        assert found


class TestActiveWifiDispatch:
    def test_deauth_detect_dispatches_without_confirm(self):
        bridge, transport, mqtt = TestDispatch()._make_bridge()
        bridge.dispatch({"id": "x", "command": "deauth_detect",
                         "args": {"duration_s": 30}})
        transport.send.assert_called_once_with("attack -t deauth -d\r\n")

    def test_deauth_attack_in_scope_executes_after_confirm(self):
        bridge, transport, mqtt = TestDispatch()._make_bridge()
        bridge.allowlist = {"wifi": [{"bssid": "aa:bb:cc:dd:ee:ff"}],
                            "ble": [], "evilportal": []}

        # First call → token
        bridge.dispatch({"id": "a", "command": "deauth_attack",
                         "args": {"bssid": "aa:bb:cc:dd:ee:ff",
                                  "ssid": "ACME"}})
        token = None
        for call in mqtt.publish.call_args_list:
            if call.args[0] == "drifter/marauder/event":
                ev = json.loads(call.args[1])
                if ev["id"] == "a":
                    token = ev.get("confirm_token")
        assert token

        # Second call with token → executes
        mqtt.publish.reset_mock()
        bridge.dispatch({"id": "b", "command": "deauth_attack",
                         "args": {"bssid": "aa:bb:cc:dd:ee:ff",
                                  "ssid": "ACME"},
                         "confirm_token": token})
        # find the matching event with id=b
        found = False
        for call in mqtt.publish.call_args_list:
            if call.args[0] == "drifter/marauder/event":
                ev = json.loads(call.args[1])
                if ev["id"] == "b":
                    found = True
                    assert ev["ok"] is True
        assert found
        transport.send.assert_called_with("attack -t deauth\r\n")

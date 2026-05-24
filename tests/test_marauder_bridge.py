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

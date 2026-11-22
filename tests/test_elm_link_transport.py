"""Transport-level tests for elm_link against a fake ELM327 on a real socket."""
import os
import socket
import sys
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import elm_link  # noqa: E402


class FakeElm:
    """Minimal ELM327 over TCP: answers each CR-terminated command with
    '<reply>\\r\\r>' like a real adapter's prompt."""

    def __init__(self, replies=None):
        self.replies = replies or {b"ATZ": b"ELM327 v1.5", b"0100": b"41 00 BE 3E B8 11"}
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        self.received = []
        self._t = threading.Thread(target=self._serve, daemon=True)
        self._t.start()

    def _serve(self):
        try:
            conn, _ = self.srv.accept()
        except OSError:
            return
        buf = b""
        with conn:
            while True:
                try:
                    data = conn.recv(256)
                except OSError:
                    return
                if not data:
                    return
                buf += data
                while b"\r" in buf:
                    cmd, buf = buf.split(b"\r", 1)
                    self.received.append(cmd)
                    conn.sendall(self.replies.get(cmd, b"?") + b"\r\r>")

    def close(self):
        self.srv.close()


@pytest.fixture
def elm():
    f = FakeElm()
    yield f
    f.close()


def test_wifi_link_roundtrip(elm):
    cfg = elm_link.LinkConfig(mode="wifi", wifi_host="127.0.0.1", wifi_port=elm.port, timeout=2.0)
    stream, label = elm_link.open_elm_link(cfg)
    try:
        assert label == f"wifi://127.0.0.1:{elm.port}"
        stream.reset_input_buffer()
        assert stream.write(b"ATZ\r") == 4
        assert b"ELM327" in stream.read(64)
        stream.write(b"0100\r")
        out = stream.read(64)
        assert out.endswith(b">") and b"41 00" in out
    finally:
        stream.close()
    assert elm.received[:2] == [b"ATZ", b"0100"]


def test_read_stops_at_prompt_without_waiting_for_n_bytes(elm):
    cfg = elm_link.LinkConfig(mode="wifi", wifi_host="127.0.0.1", wifi_port=elm.port, timeout=5.0)
    stream, _ = elm_link.open_elm_link(cfg)
    try:
        stream.write(b"ATZ\r")
        # Asking for far more than the reply must still return promptly at '>'
        assert stream.read(4096).endswith(b">")
    finally:
        stream.close()


def test_read_returns_partial_on_timeout():
    a, b = socket.socketpair()
    a.settimeout(0.2)
    s = elm_link.SocketStream(a, "pair")
    b.sendall(b"NO PROMPT")
    assert s.read(64) == b"NO PROMPT"
    s.close()
    b.close()


def test_reset_input_buffer_drains_and_restores_timeout():
    a, b = socket.socketpair()
    a.settimeout(1.5)
    s = elm_link.SocketStream(a, "pair")
    b.sendall(b"stale junk>")
    import time
    time.sleep(0.05)
    s.reset_input_buffer()
    assert a.gettimeout() == 1.5
    b.sendall(b"fresh>")
    assert s.read(64) == b"fresh>"
    s.close()
    b.close()


def test_close_is_safe_after_peer_gone():
    a, b = socket.socketpair()
    b.close()
    elm_link.SocketStream(a, "pair").close()


def test_auto_falls_back_from_serial_to_wifi(elm, monkeypatch):
    def boom(cfg):
        raise OSError("no tty")
    monkeypatch.setattr(elm_link, "_open_serial", boom)
    cfg = elm_link.LinkConfig(mode="auto", serial_dev="/dev/does-not-exist",
                              wifi_host="127.0.0.1", wifi_port=elm.port, timeout=2.0)
    stream, label = elm_link.open_elm_link(cfg)
    stream.close()
    assert label.startswith("wifi://")


def test_all_candidates_failing_reports_each(monkeypatch):
    monkeypatch.setattr(elm_link, "_open_serial", lambda cfg: (_ for _ in ()).throw(OSError("no tty")))
    cfg = elm_link.LinkConfig(mode="auto", serial_dev="/dev/nope",
                              wifi_host="127.0.0.1", wifi_port=1, timeout=0.5)
    with pytest.raises(RuntimeError) as e:
        elm_link.open_elm_link(cfg)
    msg = str(e.value)
    assert "wifi:" in msg and "serial: no tty" in msg


def test_wifi_and_bt_require_configuration():
    with pytest.raises(RuntimeError, match="ELM_WIFI_HOST"):
        elm_link._open_wifi(elm_link.LinkConfig(mode="wifi"))
    with pytest.raises(RuntimeError, match="ELM_BT_MAC"):
        elm_link._open_bluetooth(elm_link.LinkConfig(mode="bluetooth"))


def test_unknown_mode_rejected():
    with pytest.raises(ValueError):
        elm_link.candidate_modes(elm_link.LinkConfig(mode="carrier-pigeon"))


@pytest.mark.parametrize("mode,expected", [
    ("serial", ["serial"]), ("usb", ["serial"]), ("rfcomm", ["serial"]),
    ("bt", ["bluetooth"]), ("tcp", ["wifi"]), ("network", ["wifi"]),
])
def test_mode_aliases(mode, expected):
    assert elm_link.candidate_modes(elm_link.LinkConfig(mode=mode)) == expected


def test_from_env_overrides(monkeypatch):
    monkeypatch.setenv("DRIFTER_ELM_LINK", " WIFI ")
    monkeypatch.setenv("OBD_SERIAL_DEV", "/dev/drifter-obd")
    monkeypatch.setenv("OBD_SERIAL_BAUD", "115200")
    monkeypatch.setenv("ELM_WIFI_HOST", "192.168.0.10")
    monkeypatch.setenv("ELM_WIFI_PORT", "35001")
    monkeypatch.setenv("ELM_TIMEOUT", "2.5")
    cfg = elm_link.LinkConfig.from_env(serial_dev="/dev/ttyUSB0", serial_baud=38400)
    assert (cfg.mode, cfg.serial_dev, cfg.serial_baud) == ("wifi", "/dev/drifter-obd", 115200)
    assert (cfg.wifi_host, cfg.wifi_port, cfg.timeout) == ("192.168.0.10", 35001, 2.5)


def test_from_env_defaults(monkeypatch):
    for k in ("DRIFTER_ELM_LINK", "OBD_SERIAL_DEV", "OBD_SERIAL_BAUD", "ELM_WIFI_HOST",
              "ELM_BT_MAC", "ELM_WIFI_PORT", "ELM_TIMEOUT", "ELM_BT_CHANNEL"):
        monkeypatch.delenv(k, raising=False)
    cfg = elm_link.LinkConfig.from_env(serial_dev="/dev/drifter-obd", serial_baud=38400)
    assert cfg.mode == "auto" and cfg.serial_dev == "/dev/drifter-obd" and cfg.wifi_port == 35000


@pytest.mark.parametrize("cfg,expected", [
    (elm_link.LinkConfig(mode="wifi"), False),
    (elm_link.LinkConfig(mode="bt"), False),
    (elm_link.LinkConfig(mode="serial", serial_dev="/dev/definitely-missing"), False),
    (elm_link.LinkConfig(mode="auto", serial_dev="/dev/definitely-missing"), False),
    (elm_link.LinkConfig(mode="auto", serial_dev="", wifi_host="x"), True),
])
def test_configured_elm_available(cfg, expected):
    assert elm_link.configured_elm_available(cfg) is expected

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from unittest.mock import MagicMock, patch

import marauder_transport as mt


class TestEnumerateCandidates:
    def test_known_vidpids_includes_esp32_s2(self):
        assert ("303a", "1001") in mt.KNOWN_MARAUDER_VIDPIDS

    def test_known_vidpids_includes_esp32_s3(self):
        assert ("303a", "1014") in mt.KNOWN_MARAUDER_VIDPIDS

    def test_known_vidpids_includes_cp210x(self):
        assert ("10c4", "ea60") in mt.KNOWN_MARAUDER_VIDPIDS

    def test_enumerate_returns_list_of_paths_when_dir_empty(self, tmp_path):
        """No serial devices → empty list, no crash."""
        result = mt.enumerate_serial_candidates(by_id_dir=tmp_path)
        assert result == []

    def test_enumerate_finds_matching_vidpid_symlinks(self, tmp_path):
        """Symlink names contain VID:PID in the form 'usb-VVVV_PPPP_*'."""
        # Marauder ESP32-S2 fake symlink
        (tmp_path / "usb-Espressif_USB_JTAG_serial_debug_unit_303a_1001_FF-if00").symlink_to(
            "/dev/null"
        )
        # CP210x fake symlink
        (tmp_path / "usb-Silicon_Labs_CP2102N_USB_to_UART_Bridge_Controller_10c4_ea60_AB-if00-port0").symlink_to(
            "/dev/null"
        )
        # Non-matching (Logitech receiver)
        (tmp_path / "usb-Logitech_046d_c534-event-mouse").symlink_to(
            "/dev/null"
        )
        result = mt.enumerate_serial_candidates(by_id_dir=tmp_path)
        assert len(result) == 2
        # Returns absolute path strings, sorted for determinism
        assert all("/dev/null" not in p for p in result)  # symlink target stripped
        names = {Path(p).name for p in result}
        assert any("303a_1001" in n for n in names)
        assert any("10c4_ea60" in n for n in names)


import os
import pty
import queue
import threading
import time


class TestProbeDirect:
    def _pty_pair(self, fake_response: bytes, delay: float = 0.05):
        """Open a pty pair. Spawn a thread that, when the test side reads
        a request line, writes fake_response after a small delay."""
        master, slave = pty.openpty()
        slave_path = os.ttyname(slave)

        def responder():
            # Wait for any write from device side (the probe sends stopscan\r\n)
            try:
                os.read(master, 256)
            except OSError:
                return
            time.sleep(delay)
            os.write(master, fake_response)

        t = threading.Thread(target=responder, daemon=True)
        t.start()
        return slave_path, master, t

    def test_probe_direct_finds_marauder_banner(self):
        slave_path, master, _ = self._pty_pair(
            b"Marauder v0.13.4 ready\r\n>\r\n"
        )
        try:
            ok, detail = mt.probe_direct(slave_path, timeout=1.0)
            assert ok is True
            assert "Marauder" in detail or "ESP32" in detail
        finally:
            os.close(master)

    def test_probe_direct_finds_esp32_banner(self):
        slave_path, master, _ = self._pty_pair(b"ESP32 chip waking up\r\n>")
        try:
            ok, _ = mt.probe_direct(slave_path, timeout=1.0)
            assert ok is True
        finally:
            os.close(master)

    def test_probe_direct_rejects_unrelated_device(self):
        slave_path, master, _ = self._pty_pair(b"GPS fix: $GPGGA,...\r\n")
        try:
            ok, _ = mt.probe_direct(slave_path, timeout=1.0)
            assert ok is False
        finally:
            os.close(master)

    def test_probe_direct_handles_no_response(self):
        slave_path, master, _ = self._pty_pair(b"", delay=2.0)
        try:
            ok, detail = mt.probe_direct(slave_path, timeout=0.5)
            assert ok is False
            assert "timeout" in detail.lower() or "no response" in detail.lower()
        finally:
            os.close(master)


class TestProbeFlipperProxy:
    def test_probe_proxy_finds_module_via_hardware_endpoint(self):
        fake_response = MagicMock(status_code=200)
        fake_response.json.return_value = {"marauder_module_present": True,
                                            "module": "marauder",
                                            "capabilities": ["wifi", "ble"]}
        with patch("marauder_transport.requests.get", return_value=fake_response):
            ok, detail = mt.probe_flipper_proxy("http://127.0.0.1:8080")
            assert ok is True
            assert "marauder" in detail.lower()

    def test_probe_proxy_module_absent(self):
        fake_response = MagicMock(status_code=200)
        fake_response.json.return_value = {"marauder_module_present": False,
                                            "module": "none"}
        with patch("marauder_transport.requests.get", return_value=fake_response):
            ok, _ = mt.probe_flipper_proxy("http://127.0.0.1:8080")
            assert ok is False

    def test_probe_proxy_dashboard_unreachable(self):
        with patch("marauder_transport.requests.get",
                   side_effect=ConnectionError("refused")):
            ok, detail = mt.probe_flipper_proxy("http://127.0.0.1:8080")
            assert ok is False
            assert "unreachable" in detail.lower() or "refused" in detail.lower()

    def test_probe_proxy_dashboard_http_error(self):
        fake_response = MagicMock(status_code=500)
        with patch("marauder_transport.requests.get", return_value=fake_response):
            ok, detail = mt.probe_flipper_proxy("http://127.0.0.1:8080")
            assert ok is False


class TestAutodetect:
    def test_autodetect_picks_direct_when_present(self, tmp_path):
        """Direct USB wins over proxy when both present."""
        # Create a fake by-id symlink with a real pty backend
        slave_path, master, _ = TestProbeDirect()._pty_pair(b"Marauder ready>")
        try:
            (tmp_path / "usb-Espressif_303a_1001_FF-if00").symlink_to(slave_path)
            t = mt.MarauderTransport(
                by_id_dir=tmp_path,
                dashboard_url="http://127.0.0.1:8080",
                probe_timeout=1.0,
            )
            t.autodetect()
            assert t.mode == "direct"
            assert t.port_path == str(tmp_path / "usb-Espressif_303a_1001_FF-if00")
        finally:
            os.close(master)

    def test_autodetect_falls_back_to_proxy(self, tmp_path):
        """No direct hardware → tries flipper proxy."""
        fake_response = MagicMock(status_code=200)
        fake_response.json.return_value = {"marauder_module_present": True}
        with patch("marauder_transport.requests.get", return_value=fake_response):
            t = mt.MarauderTransport(by_id_dir=tmp_path,
                                     dashboard_url="http://127.0.0.1:8080")
            t.autodetect()
            assert t.mode == "proxy"

    def test_autodetect_no_hardware(self, tmp_path):
        """Neither direct nor proxy → mode='none'."""
        with patch("marauder_transport.requests.get",
                   side_effect=ConnectionError("refused")):
            t = mt.MarauderTransport(by_id_dir=tmp_path,
                                     dashboard_url="http://127.0.0.1:8080")
            t.autodetect()
            assert t.mode == "none"
            assert t.port_path is None


class TestSendReceiveDirect:
    def test_send_command_and_receive_lines(self):
        """send_command writes bytes; reader thread parses lines into queue."""
        master, slave = pty.openpty()
        slave_path = os.ttyname(slave)

        t = mt.MarauderTransport(probe_timeout=0.2)
        t.mode = "direct"
        t.port_path = slave_path
        line_q: queue.Queue[str] = queue.Queue()

        t.start(line_callback=lambda l: line_q.put(l))

        # Simulate Marauder pushing two lines after our command
        def feeder():
            os.read(master, 256)  # consume the command we sent
            time.sleep(0.05)
            os.write(master, b"RSSI: -67 Ch: 6 BSSID: aa:bb:cc:dd:ee:ff ESSID: X\r\n")
            os.write(master, b"RSSI: -55 Ch: 11 BSSID: 11:22:33:44:55:66 ESSID: Y\r\n")

        threading.Thread(target=feeder, daemon=True).start()

        t.send("scanap\r\n")

        # Reader should produce 2 lines
        lines = []
        for _ in range(2):
            try:
                lines.append(line_q.get(timeout=1.0))
            except queue.Empty:
                break

        t.stop()
        os.close(master)

        assert len(lines) == 2
        assert "BSSID: aa:bb:cc:dd:ee:ff" in lines[0]
        assert "BSSID: 11:22:33:44:55:66" in lines[1]

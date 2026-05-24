import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

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

"""Regression coverage for the redacted DRIFTER hardware report."""
import hardware_report as hr


def test_redact_removes_mac_and_ip():
    value = hr._redact("OBDII 11:22:33:44:55:66 host 192.168.1.112")
    assert "11:22:33:44:55:66" not in value
    assert "192.168.1.112" not in value
    assert "<REDACTED_MAC>" in value
    assert "<REDACTED_IP>" in value


def test_report_declares_sensitive_fields_not_collected(monkeypatch):
    monkeypatch.setattr(hr, "_serial_devices", lambda: [])
    monkeypatch.setattr(hr, "_framebuffers", lambda: [])
    monkeypatch.setattr(hr, "_git_state", lambda: {"present": False})
    monkeypatch.setattr(
        hr,
        "_run",
        lambda argv, timeout=10.0: {
            "available": False,
            "rc": 127,
            "stdout": "",
            "stderr": f"{argv[0]} unavailable",
        },
    )
    report = hr.build_report()
    assert report["privacy"]["vin_collected"] is False
    assert report["privacy"]["wifi_credentials_collected"] is False
    assert report["privacy"]["api_keys_collected"] is False
    assert report["privacy"]["usb_serial_numbers_collected"] is False
    assert report["privacy"]["bluetooth_mac_redacted"] is True


def test_udev_allowlist_excludes_unique_serial_fields():
    assert "ID_SERIAL" not in hr._UDEV_KEYS
    assert "ID_SERIAL_SHORT" not in hr._UDEV_KEYS
    assert "ID_VENDOR_ID" in hr._UDEV_KEYS
    assert "ID_MODEL_ID" in hr._UDEV_KEYS

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import elm_link


def test_explicit_wifi_mode():
    cfg = elm_link.LinkConfig(mode="wifi", wifi_host="192.168.0.10")
    assert elm_link.candidate_modes(cfg) == ["wifi"]
    assert elm_link.configured_elm_available(cfg)


def test_explicit_bluetooth_mode():
    cfg = elm_link.LinkConfig(mode="bluetooth", bt_mac="AA:BB:CC:DD:EE:FF")
    assert elm_link.candidate_modes(cfg) == ["bluetooth"]
    assert elm_link.configured_elm_available(cfg)


def test_auto_uses_configured_network_links(monkeypatch):
    monkeypatch.setattr(elm_link.os.path, "exists", lambda _: False)
    cfg = elm_link.LinkConfig(
        mode="auto",
        serial_dev="/dev/drifter-obd",
        bt_mac="AA:BB:CC:DD:EE:FF",
        wifi_host="192.168.0.10",
    )
    assert elm_link.candidate_modes(cfg) == ["bluetooth", "wifi", "serial"]


def test_auto_prefers_present_serial(monkeypatch):
    monkeypatch.setattr(elm_link.os.path, "exists", lambda _: True)
    cfg = elm_link.LinkConfig(
        mode="auto",
        serial_dev="/dev/drifter-obd",
        bt_mac="AA:BB:CC:DD:EE:FF",
        wifi_host="192.168.0.10",
    )
    assert elm_link.candidate_modes(cfg) == ["serial", "bluetooth", "wifi"]


def test_env_config(monkeypatch):
    monkeypatch.setenv("DRIFTER_ELM_LINK", "wifi")
    monkeypatch.setenv("ELM_WIFI_HOST", "10.0.0.8")
    monkeypatch.setenv("ELM_WIFI_PORT", "12345")
    cfg = elm_link.LinkConfig.from_env(serial_dev="/dev/x", serial_baud=38400)
    assert cfg.mode == "wifi"
    assert cfg.wifi_host == "10.0.0.8"
    assert cfg.wifi_port == 12345

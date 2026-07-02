"""config.resolve_device — stable serial-device path resolution.

Guards the precedence: env override > stable udev symlink (if present) > raw
path fallback. The fallback is what keeps a node that hasn't installed the
udev rules working exactly as before.
"""
import os

import config


def test_env_override_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("DRIFTER_TEST_DEV", "/dev/ttyOVERRIDE")
    got = config.resolve_device("DRIFTER_TEST_DEV", str(tmp_path / "stable"), "/dev/ttyUSB0")
    assert got == "/dev/ttyOVERRIDE"


def test_stable_symlink_preferred_when_present(tmp_path, monkeypatch):
    monkeypatch.delenv("DRIFTER_TEST_DEV", raising=False)
    real = tmp_path / "real"
    real.write_text("")
    stable = tmp_path / "stable"
    os.symlink(real, stable)
    got = config.resolve_device("DRIFTER_TEST_DEV", str(stable), "/dev/ttyUSB0")
    assert got == str(stable)


def test_raw_fallback_when_stable_absent(tmp_path, monkeypatch):
    monkeypatch.delenv("DRIFTER_TEST_DEV", raising=False)
    got = config.resolve_device(
        "DRIFTER_TEST_DEV", str(tmp_path / "does-not-exist"), "/dev/ttyUSB0"
    )
    assert got == "/dev/ttyUSB0"


def test_device_constants_resolve_to_a_path():
    # On a bench with no dongles + no udev symlinks, the constants must still be
    # the historical raw paths (never empty / None).
    assert config.OBD_SERIAL_DEV
    assert config.NAV_GPS_DEVICE
    assert config.COMMS_MODEM_DEV

"""Regression coverage for field LCD health/recovery semantics."""
from __future__ import annotations

import subprocess

import display_recover as display


def _tree(tmp_path, monkeypatch, *, service_active=True, bound=True):
    graphics = tmp_path / 'graphics'
    fb = graphics / 'fb1'
    fb.mkdir(parents=True)
    (fb / 'name').write_text('ili9486\n')
    (fb / 'virtual_size').write_text('480,320\n')
    (fb / 'bits_per_pixel').write_text('16\n')

    dev = tmp_path / 'dev' / 'fb1'
    dev.parent.mkdir(parents=True)
    dev.touch()

    drivers = tmp_path / 'drivers'
    driver = drivers / 'fb_ili9486'
    driver.mkdir(parents=True)
    (driver / 'bind').touch()
    (driver / 'unbind').touch()
    if bound:
        (driver / 'spi0.0').mkdir()

    devices = tmp_path / 'devices'
    (devices / 'spi0.0').mkdir(parents=True)

    monkeypatch.setattr(display, 'SYS_GRAPHICS_ROOT', graphics)
    monkeypatch.setattr(display, 'SPI_DRIVER_ROOT', drivers)
    monkeypatch.setattr(display, 'SPI_DEVICE_ROOT', devices)
    monkeypatch.setattr(display, 'LCD_FB_DEVICE', dev)
    monkeypatch.setattr(display, 'LCD_SPI_DEVICE', 'spi0.0')
    monkeypatch.setattr(display.shutil, 'which', lambda name: '/bin/systemctl')

    state = 'active' if service_active else 'failed'
    sub = 'running' if service_active else 'failed'

    def fake_run(argv, **kwargs):
        if 'show' in argv:
            stdout = (
                f'ActiveState={state}\nSubState={sub}\nNRestarts=0\n'
                f'ExecMainStatus={0 if service_active else 1}\nResult={'success' if service_active else 'exit-code'}\n'
            )
            return subprocess.CompletedProcess(argv, 0, stdout, '')
        return subprocess.CompletedProcess(argv, 0, '', '')

    monkeypatch.setattr(display.subprocess, 'run', fake_run)
    return driver


def test_status_requires_expected_framebuffer_binding_and_running_service(tmp_path, monkeypatch):
    _tree(tmp_path, monkeypatch, service_active=True, bound=True)
    assert display.status() == 0


def test_status_fails_when_lcd_service_is_not_running(tmp_path, monkeypatch):
    _tree(tmp_path, monkeypatch, service_active=False, bound=True)
    assert display.status() == 2


def test_status_fails_when_spi_driver_has_no_bound_display(tmp_path, monkeypatch):
    _tree(tmp_path, monkeypatch, service_active=True, bound=False)
    assert display.status() == 2


def test_recover_refuses_to_guess_missing_spi_device(tmp_path, monkeypatch):
    driver = _tree(tmp_path, monkeypatch, service_active=True, bound=False)
    (display.SPI_DEVICE_ROOT / 'spi0.0').rmdir()
    monkeypatch.setattr(display.os, 'geteuid', lambda: 0)

    assert display.recover() == 2
    assert (driver / 'bind').read_text() == ''


def test_recover_uses_configured_spi_target_and_requires_post_check(tmp_path, monkeypatch):
    driver = _tree(tmp_path, monkeypatch, service_active=True, bound=False)
    monkeypatch.setattr(display.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(display.time, 'sleep', lambda _seconds: None)
    monkeypatch.setattr(display, 'status', lambda: 0)

    assert display.recover() == 0
    assert (driver / 'bind').read_text() == 'spi0.0'

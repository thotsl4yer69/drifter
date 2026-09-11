#!/usr/bin/env python3
"""DRIFTER ELM327 transport links.

Provides one small stream interface for generic ELM327-compatible readers over:
  * serial / USB / RFCOMM tty
  * Bluetooth Classic RFCOMM (direct socket)
  * Wi-Fi TCP

Configuration is environment-driven so existing installs remain compatible:

  DRIFTER_ELM_LINK=auto|serial|bluetooth|wifi
  OBD_SERIAL_DEV=/dev/drifter-obd
  OBD_SERIAL_BAUD=38400
  ELM_BT_MAC=AA:BB:CC:DD:EE:FF
  ELM_BT_CHANNEL=1
  ELM_WIFI_HOST=192.168.0.10
  ELM_WIFI_PORT=35000

`auto` tries a present serial device first, then configured Bluetooth, then
configured Wi-Fi.  The returned object implements the subset of pyserial used by
obd_bridge.py: write(), read(), reset_input_buffer(), close().
"""
from __future__ import annotations

import math
import os
import re
import socket
from dataclasses import dataclass


@dataclass(frozen=True)
class LinkConfig:
    mode: str = "auto"
    serial_dev: str = "/dev/ttyUSB0"
    serial_baud: int = 38400
    bt_mac: str = ""
    bt_channel: int = 1
    wifi_host: str = ""
    wifi_port: int = 35000
    timeout: float = 1.0

    def __post_init__(self):
        if self.mode not in {'auto', 'serial', 'usb', 'tty', 'rfcomm', 'bluetooth', 'bt', 'wifi', 'tcp', 'network'}:
            raise ValueError(f'unsupported DRIFTER_ELM_LINK={self.mode!r}')
        if not 1 <= self.bt_channel <= 30:
            raise ValueError('ELM_BT_CHANNEL must be 1..30')
        if not 1 <= self.wifi_port <= 65535:
            raise ValueError('ELM_WIFI_PORT must be 1..65535')
        if self.serial_baud <= 0:
            raise ValueError('OBD_SERIAL_BAUD must be positive')
        if not math.isfinite(self.timeout) or not 0.05 <= self.timeout <= 30:
            raise ValueError('ELM_TIMEOUT must be between 0.05 and 30 seconds')
        if self.bt_mac and not re.fullmatch(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}', self.bt_mac):
            raise ValueError('ELM_BT_MAC must contain six hexadecimal octets')

    @classmethod
    def from_env(cls, *, serial_dev: str, serial_baud: int) -> LinkConfig:
        return cls(
            mode=(os.getenv("DRIFTER_ELM_LINK", "auto") or "auto").strip().lower(),
            serial_dev=os.getenv("OBD_SERIAL_DEV", serial_dev),
            serial_baud=int(os.getenv("OBD_SERIAL_BAUD", str(serial_baud))),
            bt_mac=(os.getenv("ELM_BT_MAC", "") or "").strip(),
            bt_channel=int(os.getenv("ELM_BT_CHANNEL", "1")),
            wifi_host=(os.getenv("ELM_WIFI_HOST", "") or "").strip(),
            wifi_port=int(os.getenv("ELM_WIFI_PORT", "35000")),
            timeout=float(os.getenv("ELM_TIMEOUT", "1.0")),
        )


class SocketStream:
    """Serial-like wrapper around an already-connected socket."""

    def __init__(self, sock: socket.socket, label: str):
        self.sock = sock
        self.label = label

    def write(self, data: bytes) -> int:
        self.sock.sendall(data)
        return len(data)

    def read(self, n: int = 128) -> bytes:
        try:
            chunk = self.sock.recv(max(1, n))
        except TimeoutError:
            return b''
        if not chunk:
            raise ConnectionError('ELM327 socket disconnected')
        return chunk

    def reset_input_buffer(self) -> None:
        old_timeout = self.sock.gettimeout()
        try:
            self.sock.setblocking(False)
            while True:
                try:
                    if not self.sock.recv(4096):
                        raise ConnectionError("ELM327 socket disconnected")
                except (BlockingIOError, InterruptedError):
                    break
        finally:
            self.sock.settimeout(old_timeout)

    def close(self) -> None:
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()


def _open_serial(cfg: LinkConfig):
    import serial
    return serial.Serial(cfg.serial_dev, cfg.serial_baud, timeout=min(cfg.timeout, 0.1), write_timeout=cfg.timeout, exclusive=True)


def _open_wifi(cfg: LinkConfig):
    if not cfg.wifi_host:
        raise RuntimeError("ELM_WIFI_HOST is not configured")
    sock = socket.create_connection((cfg.wifi_host, cfg.wifi_port), timeout=cfg.timeout)
    sock.settimeout(min(cfg.timeout, 0.1))
    return SocketStream(sock, f"wifi://{cfg.wifi_host}:{cfg.wifi_port}")


def _open_bluetooth(cfg: LinkConfig):
    if not cfg.bt_mac:
        raise RuntimeError("ELM_BT_MAC is not configured")
    if not hasattr(socket, "AF_BLUETOOTH"):
        raise RuntimeError("Python/Linux build does not expose AF_BLUETOOTH")
    sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
    sock.settimeout(cfg.timeout)
    try:
        sock.connect((cfg.bt_mac, cfg.bt_channel))
    except Exception:
        sock.close()
        raise
    sock.settimeout(min(cfg.timeout, 0.1))
    return SocketStream(sock, f"bluetooth://{cfg.bt_mac}:{cfg.bt_channel}")


def candidate_modes(cfg: LinkConfig) -> list[str]:
    mode = cfg.mode
    if mode in {"serial", "usb", "tty", "rfcomm"}:
        return ["serial"]
    if mode in {"bluetooth", "bt"}:
        return ["bluetooth"]
    if mode in {"wifi", "tcp", "network"}:
        return ["wifi"]
    if mode != "auto":
        raise ValueError(f"unsupported DRIFTER_ELM_LINK={mode!r}")

    modes: list[str] = []
    if cfg.serial_dev and os.path.exists(cfg.serial_dev):
        modes.append("serial")
    if cfg.bt_mac:
        modes.append("bluetooth")
    if cfg.wifi_host:
        modes.append("wifi")
    # Preserve historical behaviour: even if the tty is not currently present,
    # try serial once so hot-plugging and stable udev paths still work.
    if "serial" not in modes:
        modes.append("serial")
    return modes


def open_elm_link(cfg: LinkConfig, initializer=None):
    """Try each configured link through initialization, closing failures."""
    errors = []
    openers = {'serial': _open_serial, 'bluetooth': _open_bluetooth, 'wifi': _open_wifi}
    for mode in candidate_modes(cfg):
        stream = None
        try:
            stream = openers[mode](cfg)
            label = getattr(stream, 'label', f'serial://{cfg.serial_dev}@{cfg.serial_baud}')
            if initializer is not None:
                initializer(stream)
            return stream, label
        except Exception as exc:
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
            errors.append(f'{mode}: {exc}')
    raise RuntimeError('; '.join(errors) or 'no ELM327 link candidates configured')


def configured_elm_available(cfg: LinkConfig) -> bool:
    """Cheap configuration/presence hint used by transport arbitration."""
    if cfg.mode in {"bluetooth", "bt"}:
        return bool(cfg.bt_mac)
    if cfg.mode in {"wifi", "tcp", "network"}:
        return bool(cfg.wifi_host)
    if cfg.mode in {"serial", "usb", "tty", "rfcomm"}:
        return bool(cfg.serial_dev and os.path.exists(cfg.serial_dev))
    return bool(
        (cfg.serial_dev and os.path.exists(cfg.serial_dev))
        or cfg.bt_mac
        or cfg.wifi_host
    )

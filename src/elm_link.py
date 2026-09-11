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

import os
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

    @classmethod
    def from_env(cls, *, serial_dev: str, serial_baud: int) -> "LinkConfig":
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
        chunks = []
        remaining = max(1, n)
        try:
            while remaining > 0:
                chunk = self.sock.recv(remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
                if b">" in chunk:
                    break
        except socket.timeout:
            pass
        return b"".join(chunks)

    def reset_input_buffer(self) -> None:
        old_timeout = self.sock.gettimeout()
        try:
            self.sock.setblocking(False)
            while True:
                try:
                    if not self.sock.recv(4096):
                        break
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
    return serial.Serial(cfg.serial_dev, cfg.serial_baud, timeout=cfg.timeout)


def _open_wifi(cfg: LinkConfig):
    if not cfg.wifi_host:
        raise RuntimeError("ELM_WIFI_HOST is not configured")
    sock = socket.create_connection((cfg.wifi_host, cfg.wifi_port), timeout=cfg.timeout)
    sock.settimeout(cfg.timeout)
    return SocketStream(sock, f"wifi://{cfg.wifi_host}:{cfg.wifi_port}")


def _open_bluetooth(cfg: LinkConfig):
    if not cfg.bt_mac:
        raise RuntimeError("ELM_BT_MAC is not configured")
    if not hasattr(socket, "AF_BLUETOOTH"):
        raise RuntimeError("Python/Linux build does not expose AF_BLUETOOTH")
    sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
    sock.settimeout(cfg.timeout)
    sock.connect((cfg.bt_mac, cfg.bt_channel))
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


def open_elm_link(cfg: LinkConfig):
    """Open the first configured ELM link and return (stream, description).

    Raises RuntimeError only after every candidate has failed.  The caller owns
    retry/backoff so vehicle-node services can degrade to hardware-pending.
    """
    errors = []
    for mode in candidate_modes(cfg):
        try:
            if mode == "serial":
                stream = _open_serial(cfg)
                return stream, f"serial://{cfg.serial_dev}@{cfg.serial_baud}"
            if mode == "bluetooth":
                stream = _open_bluetooth(cfg)
                return stream, stream.label
            if mode == "wifi":
                stream = _open_wifi(cfg)
                return stream, stream.label
        except Exception as exc:
            errors.append(f"{mode}: {exc}")
    raise RuntimeError("; ".join(errors) or "no ELM327 link candidates configured")


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

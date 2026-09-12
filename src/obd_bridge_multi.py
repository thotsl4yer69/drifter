#!/usr/bin/env python3
"""DRIFTER multi-link ELM327 bridge entrypoint.

Keeps the mature polling/PID/MQTT logic in obd_bridge.py and replaces only the
physical ELM327 stream opener. Bluetooth Classic RFCOMM and Wi-Fi TCP adapters
therefore behave exactly like the established serial/K-line path.
"""
from __future__ import annotations

import logging
import os
import time

import elm_link

# If an operator explicitly configures Bluetooth/Wi-Fi but does not set the
# high-level transport selector, make ELM327 the owner of OBD telemetry. This
# prevents transport arbitration from falling back to raw CAN merely because
# there is no /dev/ttyUSB* node.
if not os.getenv("DRIFTER_TRANSPORT"):
    mode = (os.getenv("DRIFTER_ELM_LINK", "auto") or "auto").strip().lower()
    if mode in {"bluetooth", "bt", "wifi", "tcp", "network"} or (
        os.getenv("ELM_BT_MAC") or os.getenv("ELM_WIFI_HOST")
    ):
        os.environ["DRIFTER_TRANSPORT"] = "elm327"

import obd_bridge  # noqa: E402  (env must be normalised first)
from config import OBD_SERIAL_BAUD, OBD_SERIAL_DEV  # noqa: E402

log = logging.getLogger("drifter.elm.multi")

_last_link_description = ""


def _configured_description(cfg: elm_link.LinkConfig) -> str:
    mode = (cfg.mode or "auto").lower()
    if mode in {"bluetooth", "bt"} and cfg.bt_mac:
        return f"bluetooth:{cfg.bt_mac}:ch{cfg.bt_channel}"
    if mode in {"wifi", "tcp", "network"} and cfg.wifi_host:
        return f"wifi:{cfg.wifi_host}:{cfg.wifi_port}"
    if mode in {"serial", "usb", "tty", "rfcomm"}:
        return f"serial:{cfg.serial_dev}@{cfg.serial_baud}"
    if cfg.bt_mac:
        return f"bluetooth:{cfg.bt_mac}:ch{cfg.bt_channel}"
    if cfg.wifi_host:
        return f"wifi:{cfg.wifi_host}:{cfg.wifi_port}"
    return f"serial:{cfg.serial_dev}@{cfg.serial_baud}"


def _open_multi_elm():
    global _last_link_description
    cfg = elm_link.LinkConfig.from_env(
        serial_dev=OBD_SERIAL_DEV,
        serial_baud=OBD_SERIAL_BAUD,
    )

    # obd_bridge.main publishes its module-global OBD_SERIAL_DEV as the status
    # device. Keep that field truthful for Bluetooth/Wi-Fi instead of claiming
    # /dev/drifter-obd even when no serial device is involved.
    obd_bridge.OBD_SERIAL_DEV = _configured_description(cfg)

    try:
        stream, description = elm_link.open_elm_link(cfg)
    except Exception as exc:
        log.warning("ELM327 link open failed (%s): %s", obd_bridge.OBD_SERIAL_DEV, exc)
        return None

    # Same adapter initialisation sequence as the established serial bridge.
    # ATSP0 lets the ELM negotiate ISO9141/KWP, J1850 or CAN per vehicle.
    for cmd in ("ATZ", "ATE0", "ATH0", "ATL0", "ATS0", "ATSP0"):
        try:
            stream.write(f"{cmd}\r".encode("ascii"))
            time.sleep(0.45 if cmd == "ATZ" else 0.15)
            stream.read(256)
        except Exception as exc:
            log.warning("ELM init %s failed on %s: %s", cmd, description, exc)
            try:
                stream.close()
            except Exception:
                pass
            return None

    proto = obd_bridge.detect_protocol(stream)
    _last_link_description = description
    obd_bridge.OBD_SERIAL_DEV = description
    log.info("ELM327 ready via %s — protocol: %s", description, proto)
    return stream


# Patch only the physical opener. All query parsing, PID support probing,
# per-vehicle applicability, MQTT publication and retry semantics remain in the
# original bridge.
obd_bridge._open_elm = _open_multi_elm


if __name__ == "__main__":
    obd_bridge.main()

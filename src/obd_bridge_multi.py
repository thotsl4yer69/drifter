#!/usr/bin/env python3
"""DRIFTER multi-link ELM327 bridge entrypoint.

Serial, Bluetooth Classic RFCOMM and Wi-Fi TCP all use the same parser-safe,
K-line-aware initialisation in obd_bridge.py. The transport wrapper only opens
the physical stream; it does not duplicate ELM AT setup policy.
"""
from __future__ import annotations

import logging
import os

import elm_link

# If an operator explicitly configures Bluetooth/Wi-Fi but does not set the
# high-level transport selector, make ELM327 the owner of OBD telemetry.
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
    configured = _configured_description(cfg)
    obd_bridge.OBD_SERIAL_DEV = configured
    # Make socket/serial timeout slightly more tolerant for K-line first init.
    cfg = elm_link.LinkConfig(
        mode=cfg.mode,
        serial_dev=cfg.serial_dev,
        serial_baud=cfg.serial_baud,
        bt_mac=cfg.bt_mac,
        bt_channel=cfg.bt_channel,
        wifi_host=cfg.wifi_host,
        wifi_port=cfg.wifi_port,
        timeout=max(cfg.timeout, obd_bridge.ELM_IO_TIMEOUT),
    )

    try:
        stream, description = elm_link.open_elm_link(cfg)
    except Exception as exc:
        obd_bridge._last_elm_meta = {
            "adapter_ok": False,
            "ecu_ok": False,
            "device": configured,
            "protocol": "unknown",
            "reason": f"link_open_failed: {exc}",
        }
        log.warning("ELM327 link open failed (%s): %s", configured, exc)
        return None

    # Critical invariant: all transports share ONE ELM setup path. In
    # particular this keeps ATS1 enabled; the old multi-link wrapper sent ATS0
    # while obd_bridge's parser expected spaced bytes, making a connected ELM
    # look alive while PIDs silently failed to decode.
    meta = obd_bridge.initialise_elm(stream)
    meta["device"] = description
    obd_bridge._last_elm_meta = meta
    if not meta.get("adapter_ok"):
        try:
            stream.close()
        except Exception:
            pass
        log.warning("ELM327 init failed via %s: %s", description, meta.get("reason"))
        return None

    _last_link_description = description
    obd_bridge.OBD_SERIAL_DEV = description
    log.info(
        "ELM327 ready via %s — ECU=%s protocol=%s",
        description,
        "online" if meta.get("ecu_ok") else "waiting",
        meta.get("protocol"),
    )
    return stream


obd_bridge._open_elm = _open_multi_elm


if __name__ == "__main__":
    obd_bridge.main()

#!/usr/bin/env python3
"""DRIFTER multi-link ELM327 bridge entrypoint.

Serial, Bluetooth Classic RFCOMM and Wi-Fi TCP all use the same parser-safe,
K-line-aware initialisation in obd_bridge.py. The transport wrapper only opens
the physical stream; it does not duplicate ELM AT setup policy.
"""
from __future__ import annotations

import logging
import os
from dataclasses import replace

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
    """Open and prove each configured candidate before selecting the link.

    Merely opening a tty/socket is not sufficient: in AUTO mode a present but
    unrelated serial device must not permanently mask a valid Bluetooth/Wi-Fi
    ELM327 configured behind it.
    """
    global _last_link_description
    cfg = elm_link.LinkConfig.from_env(
        serial_dev=OBD_SERIAL_DEV,
        serial_baud=OBD_SERIAL_BAUD,
    )
    configured = _configured_description(cfg)
    obd_bridge.OBD_SERIAL_DEV = configured
    cfg = replace(cfg, timeout=max(cfg.timeout, obd_bridge.ELM_IO_TIMEOUT))

    errors: list[str] = []
    try:
        modes = elm_link.candidate_modes(cfg)
    except Exception as exc:
        modes = []
        errors.append(str(exc))

    for mode in modes:
        candidate_cfg = replace(cfg, mode=mode)
        try:
            stream, description = elm_link.open_elm_link(candidate_cfg)
        except Exception as exc:
            errors.append(f"{mode}: open failed: {exc}")
            continue

        # Critical invariant: all transports share ONE ELM setup path. In
        # particular this keeps ATS1 enabled; the old multi-link wrapper sent
        # ATS0 while obd_bridge's parser expected spaced bytes.
        meta = obd_bridge.initialise_elm(stream)
        meta["device"] = description
        if meta.get("adapter_ok"):
            obd_bridge._last_elm_meta = meta
            _last_link_description = description
            obd_bridge.OBD_SERIAL_DEV = description
            log.info(
                "ELM327 ready via %s — ECU=%s protocol=%s",
                description,
                "online" if meta.get("ecu_ok") else "waiting",
                meta.get("protocol"),
            )
            return stream

        errors.append(f"{mode}: ELM proof failed: {meta.get('reason', 'unknown')}")
        try:
            stream.close()
        except Exception:
            pass

    reason = "; ".join(errors) or "no ELM327 link candidates configured"
    obd_bridge._last_elm_meta = {
        "adapter_ok": False,
        "ecu_ok": False,
        "device": configured,
        "protocol": "unknown",
        "reason": f"all_candidates_failed: {reason}",
    }
    log.warning("ELM327 candidates exhausted (%s): %s", configured, reason)
    return None


obd_bridge._open_elm = _open_multi_elm


if __name__ == "__main__":
    obd_bridge.main()

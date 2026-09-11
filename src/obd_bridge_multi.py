#!/usr/bin/env python3
"""DRIFTER multi-link ELM327 bridge entrypoint.

Keeps the mature polling/PID/MQTT logic in obd_bridge.py and replaces only the
physical ELM327 stream opener.  This makes Bluetooth Classic RFCOMM and Wi-Fi
TCP adapters behave exactly like the existing serial/K-line path.
"""
from __future__ import annotations

import json
import logging
import os
import time

import elm_link

# If an operator explicitly configures Bluetooth/Wi-Fi but does not set the
# high-level transport selector, make ELM327 the owner of OBD telemetry.  This
# prevents obd_transport's historical serial-device heuristic from falling
# back to raw CAN merely because there is no /dev/ttyUSB* node.
if not os.getenv("DRIFTER_TRANSPORT"):
    mode = (os.getenv("DRIFTER_ELM_LINK", "auto") or "auto").strip().lower()
    if mode in {"bluetooth", "bt", "wifi", "tcp", "network"}:
        os.environ["DRIFTER_TRANSPORT"] = "elm327"
    elif (os.getenv("ELM_BT_MAC") or os.getenv("ELM_WIFI_HOST")):
        os.environ["DRIFTER_TRANSPORT"] = "elm327"

import obd_bridge  # noqa: E402  (env must be normalised first)
from config import OBD_SERIAL_BAUD, OBD_SERIAL_DEV, TOPICS  # noqa: E402

log = logging.getLogger("drifter.elm.multi")

_last_link_description = ""


def _open_multi_elm():
    global _last_link_description
    cfg = elm_link.LinkConfig.from_env(
        serial_dev=OBD_SERIAL_DEV,
        serial_baud=OBD_SERIAL_BAUD,
    )
    try:
        stream, description = elm_link.open_elm_link(cfg)
    except Exception as exc:
        log.warning("ELM327 link open failed: %s", exc)
        return None

    # Same adapter initialisation sequence as the established serial bridge.
    # ATSP0 is essential on the X-Type because it lets the adapter negotiate
    # ISO9141/KWP K-line as well as CAN/J1850 where applicable.
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
    log.info("ELM327 ready via %s — protocol: %s", description, proto)
    return stream


def _publish_link_metadata(client) -> None:
    if not _last_link_description:
        return
    try:
        client.publish(
            TOPICS["obd_status"],
            json.dumps({
                "state": "online",
                "device": _last_link_description,
                "link": _last_link_description.split(":", 1)[0],
                "ts": time.time(),
            }),
            retain=True,
        )
    except Exception:
        pass


# Patch only the physical opener.  All query parsing, PID support probing,
# per-vehicle applicability, MQTT publication and retry semantics remain in the
# original tested bridge.
obd_bridge._open_elm = _open_multi_elm


if __name__ == "__main__":
    obd_bridge.main()

#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Supervised Peykan MCP launcher.
Refuses unsafe or implicit vehicle connections. Never starts the simulator on CAN.
UNCAGED TECHNOLOGY — EST 1991
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def command(env: dict[str, str]) -> list[str]:
    mode = env.get("DRIFTER_PEYKAN_MODE")
    interface = env.get("PEYKAN_CAN_INTERFACE")
    channel = env.get("PEYKAN_CAN_CHANNEL", "")
    if (env.get("PEYKAN_ALLOW_TRANSMIT") != "false"
            or env.get("PEYKAN_ALLOW_WRITE_SERVICES") != "false"):
        raise ValueError("Peykan must be configured as read-only (false/false)")
    if env.get("PEYKAN_MCP_HOST") != "127.0.0.1":
        raise ValueError("Peykan MCP must bind only to 127.0.0.1")
    executable = str(Path(sys.executable).with_name("peykan"))
    args = [executable]
    if mode == "demo" and interface == "virtual":
        args.append("demo")
    elif mode == "socketcan" and interface == "socketcan":
        if not channel or "/" in channel or not channel.isidentifier():
            raise ValueError("Valid PEYKAN_CAN_CHANNEL required")
        dbc = Path(env.get("PEYKAN_DBC_PATH") or "")
        if not dbc.is_file() or not dbc.is_absolute():
            raise ValueError("Real CAN requires an explicit, readable DBC file")
        args.append("server")
    else:
        raise ValueError("Refusing Peykan mode/interface mismatch")
    return args + ["--host", "127.0.0.1", "--port", "6278",
                   "--transport", "streamable-http"]


if __name__ == "__main__":
    try:
        cmd = command(dict(os.environ))
        os.execv(cmd[0], cmd)
    except (OSError, ValueError) as exc:
        print(f"Peykan startup blocked: {exc}", file=sys.stderr)
        sys.exit(2)

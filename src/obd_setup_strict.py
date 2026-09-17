#!/usr/bin/env python3
"""Strict field-facing wrapper for DRIFTER ELM327 setup.

The underlying ``obd_setup`` module deliberately distinguishes adapter
reachability from ECU reachability. This wrapper makes that distinction part
of the operator CLI contract and uses the exact production ELM initializer for
vehicle acceptance, including the ISO 9141/KWP fallback ladder.

* exit 0: adapter and ECU both proved;
* exit 3: adapter proved/configured, but ECU not proved yet;
* exit 2: adapter/transport itself not proved.

An adapter-only result may still be persisted so an operator can configure a
reader with ignition off, but FIELD OPS must not report that as a completed
vehicle connection.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time

import obd_bridge
import obd_setup as base

EXIT_OK = 0
EXIT_ADAPTER_ONLY = 3
EXIT_ADAPTER_UNREACHABLE = 2


def result_code(result: dict[str, object]) -> int:
    if result.get("ok"):
        return EXIT_OK
    if result.get("adapter_ok"):
        return EXIT_ADAPTER_ONLY
    return EXIT_ADAPTER_UNREACHABLE


def runtime_probe(cfg) -> dict[str, object]:
    """Probe one configured link through the same initializer used in service.

    This deliberately avoids a second field-only protocol implementation. The
    production bridge owns adapter setup, ATS1, slow K-line timing and the
    auto -> ISO9141/KWP -> CAN fallback ladder; acceptance must exercise that
    same path or it can disagree with the running service.
    """
    result: dict[str, object] = {
        "ok": False,
        "adapter_ok": False,
        "ecu_ok": False,
        "mode": getattr(cfg, "mode", "unknown"),
        "link": "",
        "adapter": "",
        "protocol": "",
        "protocol_attempt": "",
        "rpm": None,
        "error": "",
    }
    stream = None
    try:
        stream, description = base.elm_link.open_elm_link(cfg)
        result["link"] = description
        meta = obd_bridge.initialise_elm(stream)
        result["adapter_ok"] = bool(meta.get("adapter_ok"))
        result["ecu_ok"] = bool(meta.get("ecu_ok"))
        result["adapter"] = str(meta.get("identity") or "")
        result["protocol"] = str(meta.get("protocol") or "")
        result["protocol_attempt"] = str(meta.get("protocol_attempt") or "")

        if result["adapter_ok"] and result["ecu_ok"]:
            rpm_payload = obd_bridge._query_pid(stream, "010C")
            if rpm_payload and len(rpm_payload) >= 2:
                result["rpm"] = round(((rpm_payload[0] * 256) + rpm_payload[1]) / 4.0, 1)
        elif result["adapter_ok"]:
            attempt = result["protocol_attempt"] or "runtime protocol ladder"
            result["error"] = (
                "ELM327 connected, but ECU communication was not proved after "
                f"{attempt}; ignition may be off or the vehicle bus is not responding"
            )
        else:
            reason = str(meta.get("reason") or "adapter initialization failed")
            result["error"] = f"ELM adapter was not proved ({reason})"

        result["ok"] = bool(result["adapter_ok"] and result["ecu_ok"])
    except Exception as exc:
        result["error"] = str(exc)
    finally:
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass
    return result


def _print_result(result: dict[str, object], *, as_json: bool = False) -> None:
    if as_json:
        payload = dict(result)
        payload["rc"] = result_code(result)
        print(json.dumps(payload, indent=2))
    else:
        base._print_probe(result)
        if result.get("protocol_attempt"):
            print(f"[INFO] protocol attempt = {result['protocol_attempt']}")


def cmd_test(args) -> int:
    result = runtime_probe(base._configured_cfg())
    _print_result(result, as_json=bool(args.json))
    return result_code(result)


def _strict_activate(cfg) -> int:
    base._require_root()
    base._service("stop")
    result: dict[str, object]
    try:
        result = runtime_probe(cfg)
        _print_result(result)
        if not result.get("adapter_ok"):
            print("Configuration NOT saved because the adapter itself did not answer.")
            return EXIT_ADAPTER_UNREACHABLE

        base._persist_cfg(cfg)
        if result.get("ecu_ok"):
            print(f"[PASS] saved {cfg.mode} ELM327 and proved ECU communication")
        else:
            print(
                f"[DEGRADED] saved {cfg.mode} ELM327, but ECU communication was NOT proved"
            )
            print(f"           {result.get('error') or 'No Mode 01 ECU response'}")
    finally:
        base._service("restart")

    time.sleep(1.0)
    status = base._mqtt_status(3)
    if status:
        print(f"[INFO] bridge status: {status}")
    return result_code(result)


def cmd_pair(args) -> int:
    base._require_root()
    if not base.shutil.which("bluetoothctl"):
        print("bluetoothctl is not installed", file=sys.stderr)
        return EXIT_ADAPTER_UNREACHABLE
    mac = args.mac.upper()
    print(f"Pairing {mac}. If prompted, common ELM327 PINs are 1234 or 0000.")
    returncode = subprocess.run(["bluetoothctl", "pair", mac], check=False).returncode
    if returncode != 0:
        print("Pairing failed. Confirm the adapter is powered and visible with `drifter obd scan`.")
        return returncode or EXIT_ADAPTER_UNREACHABLE
    subprocess.run(["bluetoothctl", "trust", mac], check=False)
    return _strict_activate(base._cfg_bt(mac, args.channel))


def cmd_setup(args) -> int:
    """Run discovery, then require a production-path final ECU proof.

    ``obd_setup.cmd_setup`` may intentionally save an adapter that is reachable
    while ignition is off. Re-probe the persisted configuration with the
    service stopped and convert that state to exit 3 rather than false success.
    """
    rc = base.cmd_setup(args)
    if rc != 0:
        return rc

    base._service("stop")
    try:
        result = runtime_probe(base._configured_cfg())
        print("Final vehicle-link acceptance probe:")
        _print_result(result)
    finally:
        base._service("restart")

    code = result_code(result)
    if code == EXIT_ADAPTER_ONLY:
        print("[DEGRADED] adapter is configured, but setup is NOT complete until the ECU responds")
    return code


def main(argv: list[str] | None = None) -> int:
    args = base.build_parser().parse_args(argv)
    command = args.command

    if command == "test":
        return cmd_test(args)
    if command == "setup":
        return cmd_setup(args)
    if command == "pair":
        return cmd_pair(args)
    if command == "use-serial":
        return _strict_activate(base._cfg_serial(args.device, args.baud))
    if command == "use-bt":
        return _strict_activate(base._cfg_bt(args.mac, args.channel))
    if command == "use-wifi":
        return _strict_activate(base._cfg_wifi(args.host, args.port))

    # status/scan are read-only and keep their existing behavior.
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

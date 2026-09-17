#!/usr/bin/env python3
"""DRIFTER vehicle-link setup and verification.

Field-facing ELM327 helper for serial/USB, Bluetooth Classic RFCOMM and Wi-Fi
TCP readers. A candidate is accepted only after an ELM AT-command handshake;
ECU communication is separately verified with read-only OBD-II Mode 01 PID 00.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

try:
    import elm_link
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import elm_link  # type: ignore

ENV_PATH = Path(os.getenv("DRIFTER_ENV_FILE", "/opt/drifter/.env"))
SERVICE = "drifter-obdbridge"
MQTT_TOPIC = "drifter/obd/status"

ELM_KEYS = {
    "DRIFTER_TRANSPORT",
    "DRIFTER_ELM_LINK",
    "OBD_SERIAL_DEV",
    "OBD_SERIAL_BAUD",
    "ELM_BT_MAC",
    "ELM_BT_CHANNEL",
    "ELM_WIFI_HOST",
    "ELM_WIFI_PORT",
    "ELM_TIMEOUT",
}

_BT_NAME_HINTS = (
    "obd",
    "elm",
    "v-link",
    "vlink",
    "vgate",
    "konwei",
    "kw902",
    "viecar",
    "icar",
    "obdii",
    "mini obd",
)
_WIFI_NAME_HINTS = ("obd", "elm", "v-link", "vlink", "vgate", "wifi_obd")


def _run(cmd: list[str], timeout: float = 8.0) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


def _load_env(path: Path = ENV_PATH) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def _cfg_from_values(values: dict[str, str]) -> elm_link.LinkConfig:
    return elm_link.LinkConfig(
        mode=(values.get("DRIFTER_ELM_LINK") or "auto").strip().lower(),
        serial_dev=(values.get("OBD_SERIAL_DEV") or "/dev/drifter-obd").strip(),
        serial_baud=int(values.get("OBD_SERIAL_BAUD") or "38400"),
        bt_mac=(values.get("ELM_BT_MAC") or "").strip(),
        bt_channel=int(values.get("ELM_BT_CHANNEL") or "1"),
        wifi_host=(values.get("ELM_WIFI_HOST") or "").strip(),
        wifi_port=int(values.get("ELM_WIFI_PORT") or "35000"),
        timeout=float(values.get("ELM_TIMEOUT") or "3.0"),
    )


def _configured_cfg() -> elm_link.LinkConfig:
    values = _load_env()
    for key in ELM_KEYS:
        if key in os.environ:
            values[key] = os.environ[key]
    return _cfg_from_values(values)


def _require_root() -> None:
    if os.geteuid() != 0 and str(ENV_PATH).startswith("/opt/"):
        raise SystemExit("This action changes the live vehicle link. Run it with sudo.")


def _write_env(updates: dict[str, str], path: Path = ENV_PATH) -> None:
    _require_root()
    try:
        original = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        original = []

    remaining = dict(updates)
    output: list[str] = []
    for raw in original:
        stripped = raw.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in remaining:
                output.append(f"{key}={remaining.pop(key)}")
                continue
        output.append(raw)

    if remaining:
        if output and output[-1].strip():
            output.append("")
        output.append("# DRIFTER vehicle link — managed by `drifter obd`")
        output.extend(f"{key}={value}" for key, value in remaining.items())

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _service(action: str) -> None:
    if shutil.which("systemctl"):
        subprocess.run(
            ["systemctl", action, SERVICE],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )


def _service_state() -> dict[str, object]:
    if not shutil.which("systemctl"):
        return {"active": None, "detail": "systemctl unavailable"}
    result = _run(
        [
            "systemctl",
            "show",
            SERVICE,
            "--no-pager",
            "-p",
            "ActiveState",
            "-p",
            "SubState",
            "-p",
            "NRestarts",
            "-p",
            "ExecMainStatus",
            "-p",
            "Result",
        ],
        timeout=4,
    )
    values: dict[str, object] = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    values["active"] = values.get("ActiveState") == "active"
    return values


def _mqtt_status(timeout: int = 2) -> str:
    if not shutil.which("mosquitto_sub"):
        return ""
    result = _run(
        [
            "mosquitto_sub",
            "-h",
            "127.0.0.1",
            "-t",
            MQTT_TOPIC,
            "-C",
            "1",
            "-W",
            str(timeout),
        ],
        timeout=timeout + 2,
    )
    return result.stdout.strip()


def _serial_candidates() -> list[str]:
    paths: list[str] = []
    candidates = (
        ["/dev/drifter-obd"]
        + sorted(glob.glob("/dev/serial/by-id/*"))
        + sorted(glob.glob("/dev/ttyUSB*"))
        + sorted(glob.glob("/dev/ttyACM*"))
    )
    for path in candidates:
        if os.path.exists(path) and path not in paths:
            paths.append(path)
    return paths


def _bt_info(mac: str) -> dict[str, object]:
    result = _run(["bluetoothctl", "info", mac], timeout=4)
    info: dict[str, object] = {"mac": mac, "paired": False, "connected": False}
    for raw in result.stdout.splitlines():
        line = raw.strip()
        if line.startswith("Name:") or (line.startswith("Alias:") and not info.get("name")):
            info["name"] = line.split(":", 1)[1].strip()
        elif line.startswith("Paired:"):
            info["paired"] = line.split(":", 1)[1].strip().lower() == "yes"
        elif line.startswith("Connected:"):
            info["connected"] = line.split(":", 1)[1].strip().lower() == "yes"
        elif line.startswith("Trusted:"):
            info["trusted"] = line.split(":", 1)[1].strip().lower() == "yes"
    return info


def _bt_devices(scan_seconds: int = 0) -> list[dict[str, object]]:
    if not shutil.which("bluetoothctl"):
        return []
    if scan_seconds > 0:
        _run(
            ["bluetoothctl", "--timeout", str(scan_seconds), "scan", "on"],
            timeout=scan_seconds + 3,
        )
    result = _run(["bluetoothctl", "devices"], timeout=4)
    devices: list[dict[str, object]] = []
    seen: set[str] = set()
    for line in result.stdout.splitlines():
        match = re.match(r"^Device\s+([0-9A-Fa-f:]{17})\s*(.*)$", line.strip())
        if not match:
            continue
        mac, name = match.group(1).upper(), match.group(2).strip()
        if mac in seen:
            continue
        seen.add(mac)
        info = _bt_info(mac)
        info["name"] = info.get("name") or name or "unknown"
        info["likely_elm"] = any(hint in str(info["name"]).lower() for hint in _BT_NAME_HINTS)
        devices.append(info)
    return devices


def _wifi_networks() -> list[dict[str, object]]:
    if not shutil.which("nmcli"):
        return []
    result = _run(
        ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list", "--rescan", "yes"],
        timeout=12,
    )
    networks: list[dict[str, object]] = []
    seen: set[str] = set()
    for raw in result.stdout.splitlines():
        parts = re.split(r"(?<!\\):", raw)
        if not parts:
            continue
        ssid = parts[0].replace("\\:", ":").strip()
        if not ssid or ssid in seen:
            continue
        seen.add(ssid)
        networks.append(
            {
                "ssid": ssid,
                "signal": parts[1] if len(parts) > 1 else "",
                "security": parts[2] if len(parts) > 2 else "",
                "likely_elm": any(hint in ssid.lower() for hint in _WIFI_NAME_HINTS),
            }
        )
    return networks


def _current_gateway() -> str:
    if not shutil.which("ip"):
        return ""
    result = _run(["ip", "route", "show", "default"], timeout=3)
    match = re.search(r"\bvia\s+(\d+\.\d+\.\d+\.\d+)", result.stdout)
    return match.group(1) if match else ""


def _read_prompt(stream, max_reads: int = 5) -> str:
    data = bytearray()
    for _ in range(max_reads):
        chunk = stream.read(512)
        if chunk:
            data.extend(chunk)
        if b">" in data:
            break
    return bytes(data).decode("ascii", errors="ignore")


def _command(stream, command: str, delay: float = 0.12, reads: int = 5) -> str:
    try:
        stream.reset_input_buffer()
    except Exception:
        pass
    stream.write((command + "\r").encode("ascii"))
    time.sleep(delay)
    return _read_prompt(stream, reads)


def _clean_response(text: str, command: str = "") -> str:
    lines = [line.strip() for line in text.replace("\r", "\n").replace(">", "").splitlines() if line.strip()]
    if command:
        wanted = command.replace(" ", "").upper()
        lines = [line for line in lines if line.replace(" ", "").upper() != wanted]
    return " | ".join(lines)


def _normal_hex(text: str) -> str:
    return re.sub(r"[^0-9A-F]", "", text.upper())


def probe(cfg: elm_link.LinkConfig) -> dict[str, object]:
    """Prove adapter reachability and ECU communication independently."""
    result: dict[str, object] = {
        "ok": False,
        "adapter_ok": False,
        "ecu_ok": False,
        "mode": cfg.mode,
        "link": "",
        "adapter": "",
        "protocol": "",
        "rpm": None,
        "error": "",
    }
    stream = None
    try:
        stream, description = elm_link.open_elm_link(cfg)
        result["link"] = description
        _command(stream, "ATZ", delay=0.8, reads=7)
        # Keep spaces enabled here as well as in the runtime bridge. The
        # runtime parser is intentionally spacing-aware, and leaving a reader
        # in ATS0 after setup can produce an apparently healthy adapter with
        # undecodable PID frames on the next hand-off.
        for command in ("ATE0", "ATL0", "ATS1", "ATH0", "ATSP0"):
            _command(stream, command)

        ident = _clean_response(_command(stream, "ATI", delay=0.2), "ATI")
        if not ident or ident == "?":
            raise RuntimeError("transport opened but ATI did not identify an ELM-compatible adapter")
        result["adapter"] = ident
        result["adapter_ok"] = True

        pids = _clean_response(_command(stream, "0100", delay=0.4, reads=10), "0100")
        protocol = _clean_response(_command(stream, "ATDP", delay=0.15), "ATDP")
        result["protocol"] = protocol
        pid_hex = _normal_hex(pids)
        result["ecu_ok"] = "4100" in pid_hex

        if result["ecu_ok"]:
            rpm_text = _clean_response(_command(stream, "010C", delay=0.2, reads=8), "010C")
            rpm_hex = _normal_hex(rpm_text)
            pos = rpm_hex.find("410C")
            if pos >= 0 and len(rpm_hex) >= pos + 8:
                a = int(rpm_hex[pos + 4 : pos + 6], 16)
                b = int(rpm_hex[pos + 6 : pos + 8], 16)
                result["rpm"] = round(((a * 256) + b) / 4.0, 1)
        elif "NO DATA" in pids.upper():
            result["error"] = "ELM327 connected, but ECU returned NO DATA — ignition may be off"
        elif "UNABLE TO CONNECT" in pids.upper():
            result["error"] = "ELM327 connected, but it could not establish an OBD-II protocol with the ECU"
        else:
            result["error"] = f"ELM327 connected, but no Mode 01 ECU response ({pids or 'empty response'})"
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


def _cfg_serial(device: str, baud: int = 38400) -> elm_link.LinkConfig:
    return elm_link.LinkConfig(mode="serial", serial_dev=device, serial_baud=baud, timeout=3.0)


def _cfg_bt(mac: str, channel: int = 1) -> elm_link.LinkConfig:
    return elm_link.LinkConfig(mode="bluetooth", bt_mac=mac.upper(), bt_channel=channel, timeout=3.0)


def _cfg_wifi(host: str, port: int = 35000) -> elm_link.LinkConfig:
    return elm_link.LinkConfig(mode="wifi", wifi_host=host, wifi_port=port, timeout=3.0)


def _persist_cfg(cfg: elm_link.LinkConfig) -> None:
    _write_env(
        {
            "DRIFTER_TRANSPORT": "elm327",
            "DRIFTER_ELM_LINK": cfg.mode,
            "OBD_SERIAL_DEV": cfg.serial_dev,
            "OBD_SERIAL_BAUD": str(cfg.serial_baud),
            "ELM_BT_MAC": cfg.bt_mac,
            "ELM_BT_CHANNEL": str(cfg.bt_channel),
            "ELM_WIFI_HOST": cfg.wifi_host,
            "ELM_WIFI_PORT": str(cfg.wifi_port),
            "ELM_TIMEOUT": str(cfg.timeout),
        }
    )


def _print_probe(result: dict[str, object]) -> None:
    if result.get("adapter_ok"):
        print(f"[PASS] adapter  {result.get('adapter') or 'ELM-compatible'}")
        print(f"[PASS] link     {result.get('link')}")
    else:
        print(f"[FAIL] adapter  {result.get('error') or 'not reachable'}")
        return
    if result.get("ecu_ok"):
        print(f"[PASS] ECU      responding via {result.get('protocol') or 'auto protocol'}")
        if result.get("rpm") is not None:
            print(f"[PASS] sample   engine RPM = {result['rpm']}")
        else:
            print("[PASS] sample   Mode 01 PID support reply received")
    else:
        print(f"[WAIT] ECU      {result.get('error')}")


def cmd_status(args) -> int:
    cfg = _configured_cfg()
    payload = {
        "config": {key: _load_env().get(key, "") for key in sorted(ELM_KEYS)},
        "effective_link": asdict(cfg),
        "service": _service_state(),
        "mqtt": _mqtt_status(),
        "serial_candidates": _serial_candidates(),
    }
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0
    print("DRIFTER vehicle link")
    print(f"  configured mode : {cfg.mode}")
    if cfg.bt_mac:
        print(f"  bluetooth       : {cfg.bt_mac} channel {cfg.bt_channel}")
    if cfg.wifi_host:
        print(f"  wifi tcp        : {cfg.wifi_host}:{cfg.wifi_port}")
    print(f"  serial          : {cfg.serial_dev} @ {cfg.serial_baud}")
    service = payload["service"]
    print(
        "  service         : "
        f"{service.get('ActiveState', 'unknown')}/{service.get('SubState', 'unknown')} "
        f"restarts={service.get('NRestarts', '?')}"
    )
    print(f"  live OBD state  : {payload['mqtt'] or 'no retained status received'}")
    return 0


def cmd_scan(args) -> int:
    serials = _serial_candidates()
    bluetooth = _bt_devices(args.seconds)
    wifi = _wifi_networks()
    if args.json:
        print(json.dumps({"serial": serials, "bluetooth": bluetooth, "wifi": wifi}, indent=2))
        return 0
    print("SERIAL")
    print("  " + "\n  ".join(serials) if serials else "  none")
    print("BLUETOOTH")
    if bluetooth:
        for device in sorted(bluetooth, key=lambda item: (not bool(item.get("likely_elm")), str(item.get("name")))):
            hint = " likely-ELM" if device.get("likely_elm") else ""
            state = "paired" if device.get("paired") else "unpaired"
            print(f"  {device['mac']}  {device.get('name', 'unknown')}  [{state}{hint}]")
    else:
        print("  none")
    print("WI-FI")
    if wifi:
        for network in sorted(wifi, key=lambda item: (not bool(item.get("likely_elm")), str(item.get("ssid")))):
            hint = " likely-ELM" if network.get("likely_elm") else ""
            print(
                f"  {network['ssid']}  signal={network['signal']} "
                f"security={network['security'] or 'open'}{hint}"
            )
    else:
        print("  none")
    return 0


def cmd_test(args) -> int:
    result = probe(_configured_cfg())
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        _print_probe(result)
    return 0 if result.get("adapter_ok") else 2


def _activate(cfg: elm_link.LinkConfig) -> int:
    _require_root()
    _service("stop")
    try:
        result = probe(cfg)
        _print_probe(result)
        if not result.get("adapter_ok"):
            print("Configuration NOT saved because the adapter itself did not answer.")
            return 2
        _persist_cfg(cfg)
        print(f"[PASS] saved {cfg.mode} ELM327 as the active DRIFTER vehicle transport")
    finally:
        _service("restart")
    time.sleep(1.0)
    status = _mqtt_status(3)
    if status:
        print(f"[INFO] bridge status: {status}")
    return 0


def cmd_use_serial(args) -> int:
    return _activate(_cfg_serial(args.device, args.baud))


def cmd_use_bt(args) -> int:
    return _activate(_cfg_bt(args.mac, args.channel))


def cmd_use_wifi(args) -> int:
    return _activate(_cfg_wifi(args.host, args.port))


def cmd_pair(args) -> int:
    _require_root()
    if not shutil.which("bluetoothctl"):
        print("bluetoothctl is not installed", file=sys.stderr)
        return 2
    mac = args.mac.upper()
    print(f"Pairing {mac}. If prompted, common ELM327 PINs are 1234 or 0000.")
    returncode = subprocess.run(["bluetoothctl", "pair", mac], check=False).returncode
    if returncode != 0:
        print("Pairing failed. Confirm the adapter is powered and visible with `drifter obd scan`.")
        return returncode or 2
    subprocess.run(["bluetoothctl", "trust", mac], check=False)
    return _activate(_cfg_bt(mac, args.channel))


def _configured(cfg: elm_link.LinkConfig) -> bool:
    return bool(
        (cfg.mode in {"bluetooth", "bt"} and cfg.bt_mac)
        or (cfg.mode in {"wifi", "tcp", "network"} and cfg.wifi_host)
        or (cfg.mode in {"serial", "usb", "tty", "rfcomm"} and os.path.exists(cfg.serial_dev))
    )


def cmd_setup(args) -> int:
    _require_root()
    _service("stop")
    selected: elm_link.LinkConfig | None = None
    visible_bt: list[dict[str, object]] = []
    try:
        current = _configured_cfg()
        if _configured(current):
            print(f"Trying configured {current.mode} link...")
            result = probe(current)
            _print_probe(result)
            if result.get("adapter_ok"):
                selected = current

        if selected is None:
            for device in _serial_candidates():
                print(f"Trying serial candidate {device}...")
                cfg = _cfg_serial(device)
                result = probe(cfg)
                if result.get("adapter_ok"):
                    _print_probe(result)
                    selected = cfg
                    break

        if selected is None:
            visible_bt = _bt_devices(args.seconds)
            paired = [item for item in visible_bt if item.get("paired")]
            paired.sort(key=lambda item: (not bool(item.get("likely_elm")), str(item.get("name"))))
            for device in paired:
                print(f"Trying paired Bluetooth candidate {device['name']} ({device['mac']})...")
                cfg = _cfg_bt(str(device["mac"]))
                result = probe(cfg)
                if result.get("adapter_ok"):
                    _print_probe(result)
                    selected = cfg
                    break

        if selected is None:
            gateway = _current_gateway()
            if gateway:
                try:
                    with socket.create_connection((gateway, 35000), timeout=0.8):
                        pass
                    print(f"Trying ELM-style TCP service on current gateway {gateway}:35000...")
                    cfg = _cfg_wifi(gateway, 35000)
                    result = probe(cfg)
                    if result.get("adapter_ok"):
                        _print_probe(result)
                        selected = cfg
                except OSError:
                    pass

        if selected is None:
            unpaired = [
                item for item in visible_bt if item.get("likely_elm") and not item.get("paired")
            ]
            print("[FAIL] No usable ELM327 link was proved.")
            if unpaired:
                device = unpaired[0]
                print(f"[NEXT] Bluetooth adapter found but not paired: {device['name']} {device['mac']}")
                print(f"       Run: sudo drifter obd pair {device['mac']}")
            else:
                likely_wifi = [item for item in _wifi_networks() if item.get("likely_elm")]
                if likely_wifi:
                    print(f"[NEXT] ELM-style Wi-Fi network visible: {likely_wifi[0]['ssid']}")
                    print("       Connect the OBD Wi-Fi interface, then rerun `sudo drifter obd setup`.")
                else:
                    print("[NEXT] Confirm ELM327 power + ignition ON, then run `drifter obd scan`.")
            return 2

        _persist_cfg(selected)
        print(f"[PASS] DRIFTER will use {selected.mode} ELM327 on subsequent boots.")
        return 0
    finally:
        _service("restart")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="drifter obd", description=__doc__)
    parser.add_argument("--json", action="store_true", help="machine-readable output where supported")
    subparsers = parser.add_subparsers(dest="command", required=True)

    command = subparsers.add_parser("status", help="show configured link and live OBD state")
    command.set_defaults(func=cmd_status)

    command = subparsers.add_parser("scan", help="scan serial, Bluetooth and Wi-Fi candidates")
    command.add_argument("--seconds", type=int, default=8, help="Bluetooth scan time")
    command.set_defaults(func=cmd_scan)

    command = subparsers.add_parser("test", help="probe the configured ELM327 and ECU")
    command.set_defaults(func=cmd_test)

    command = subparsers.add_parser("setup", help="find, prove and persist a usable ELM327")
    command.add_argument("--seconds", type=int, default=10, help="Bluetooth discovery time")
    command.set_defaults(func=cmd_setup)

    command = subparsers.add_parser("pair", help="pair/trust a Bluetooth ELM327 and verify it")
    command.add_argument("mac")
    command.add_argument("--channel", type=int, default=1)
    command.set_defaults(func=cmd_pair)

    command = subparsers.add_parser("use-serial", help="verify/select a serial ELM327")
    command.add_argument("device")
    command.add_argument("--baud", type=int, default=38400)
    command.set_defaults(func=cmd_use_serial)

    command = subparsers.add_parser("use-bt", help="verify/select a paired Bluetooth ELM327")
    command.add_argument("mac")
    command.add_argument("--channel", type=int, default=1)
    command.set_defaults(func=cmd_use_bt)

    command = subparsers.add_parser("use-wifi", help="verify/select a Wi-Fi TCP ELM327")
    command.add_argument("host")
    command.add_argument("--port", type=int, default=35000)
    command.set_defaults(func=cmd_use_wifi)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
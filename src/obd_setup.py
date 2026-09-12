#!/usr/bin/env python3
"""DRIFTER vehicle-link setup and verification.

Field-facing ELM327 helper.  The telemetry bridge already supports serial,
Bluetooth Classic RFCOMM and Wi-Fi TCP, but historically required an operator
to edit /opt/drifter/.env by hand.  This module turns that into a deterministic
workflow:

    drifter obd status
    drifter obd scan
    drifter obd test
    sudo drifter obd setup
    sudo drifter obd pair AA:BB:CC:DD:EE:FF
    sudo drifter obd use-bt AA:BB:CC:DD:EE:FF
    sudo drifter obd use-wifi 192.168.0.10

A candidate is not accepted merely because its name contains "OBD".  We open
the transport and issue ELM AT commands.  ECU communication is then verified
with the standard read-only Mode 01 PID 00 query.  ATSP0 leaves physical-layer
selection to the ELM327 so the same workflow works with ISO9141/KWP2000,
J1850, CAN and other OBD-II protocols supported by the adapter.
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
except ImportError:  # development from repository root
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
    "obd", "elm", "v-link", "vlink", "vgate", "konwei", "kw902",
    "viecar", "icar", "obdii", "mini obd",
)
_WIFI_NAME_HINTS = ("obd", "elm", "v-link", "vlink", "vgate", "wifi_obd")


def _run(cmd: list[str], timeout: float = 8.0, check: bool = False) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=check)
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


def _load_env(path: Path = ENV_PATH) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key] = value
    return out


def _cfg_from_values(values: dict[str, str]) -> elm_link.LinkConfig:
    return elm_link.LinkConfig(
        mode=(values.get("DRIFTER_ELM_LINK") or "auto").strip().lower(),
        serial_dev=(values.get("OBD_SERIAL_DEV") or "/dev/drifter-obd").strip(),
        serial_baud=int(values.get("OBD_SERIAL_BAUD") or "38400"),
        bt_mac=(values.get("ELM_BT_MAC") or "").strip(),
        bt_channel=int(values.get("ELM_BT_CHANNEL") or "1"),
        wifi_host=(values.get("ELM_WIFI_HOST") or "").strip(),
        wifi_port=int(values.get("ELM_WIFI_PORT") or "35000"),
        # Initial protocol search on an older K-line vehicle can take >1 s.
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
        for key, value in remaining.items():
            output.append(f"{key}={value}")

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _service(action: str) -> None:
    if not shutil.which("systemctl"):
        return
    subprocess.run(["systemctl", action, SERVICE], stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, check=False)


def _service_state() -> dict:
    if not shutil.which("systemctl"):
        return {"active": None, "detail": "systemctl unavailable"}
    r = _run(["systemctl", "show", SERVICE, "--no-pager",
              "-p", "ActiveState", "-p", "SubState", "-p", "NRestarts",
              "-p", "ExecMainStatus", "-p", "Result"], timeout=4)
    vals = {}
    for line in r.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            vals[k] = v
    vals["active"] = vals.get("ActiveState") == "active"
    return vals


def _mqtt_status(timeout: int = 2) -> str:
    if not shutil.which("mosquitto_sub"):
        return ""
    r = _run(["mosquitto_sub", "-h", "127.0.0.1", "-t", MQTT_TOPIC,
              "-C", "1", "-W", str(timeout)], timeout=timeout + 2)
    return r.stdout.strip()


def _serial_candidates() -> list[str]:
    paths: list[str] = []
    for p in ["/dev/drifter-obd"] + sorted(glob.glob("/dev/serial/by-id/*")) \
            + sorted(glob.glob("/dev/ttyUSB*")) + sorted(glob.glob("/dev/ttyACM*")):
        if os.path.exists(p) and p not in paths:
            paths.append(p)
    return paths


def _bt_info(mac: str) -> dict:
    r = _run(["bluetoothctl", "info", mac], timeout=4)
    out: dict[str, object] = {"mac": mac, "paired": False, "connected": False}
    for raw in r.stdout.splitlines():
        line = raw.strip()
        if line.startswith("Name:"):
            out["name"] = line.split(":", 1)[1].strip()
        elif line.startswith("Alias:") and not out.get("name"):
            out["name"] = line.split(":", 1)[1].strip()
        elif line.startswith("Paired:"):
            out["paired"] = line.split(":", 1)[1].strip().lower() == "yes"
        elif line.startswith("Connected:"):
            out["connected"] = line.split(":", 1)[1].strip().lower() == "yes"
        elif line.startswith("Trusted:"):
            out["trusted"] = line.split(":", 1)[1].strip().lower() == "yes"
    return out


def _bt_devices(scan_seconds: int = 0) -> list[dict]:
    if not shutil.which("bluetoothctl"):
        return []
    if scan_seconds > 0:
        _run(["bluetoothctl", "--timeout", str(scan_seconds), "scan", "on"],
             timeout=scan_seconds + 3)
    r = _run(["bluetoothctl", "devices"], timeout=4)
    devices: list[dict] = []
    seen = set()
    for line in r.stdout.splitlines():
        m = re.match(r"^Device\s+([0-9A-Fa-f:]{17})\s*(.*)$", line.strip())
        if not m:
            continue
        mac, name = m.group(1).upper(), m.group(2).strip()
        if mac in seen:
            continue
        seen.add(mac)
        info = _bt_info(mac)
        info["name"] = info.get("name") or name or "unknown"
        info["likely_elm"] = any(h in str(info["name"]).lower() for h in _BT_NAME_HINTS)
        devices.append(info)
    return devices


def _wifi_networks() -> list[dict]:
    if not shutil.which("nmcli"):
        return []
    r = _run(["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi",
              "list", "--rescan", "yes"], timeout=12)
    nets: list[dict] = []
    seen = set()
    for raw in r.stdout.splitlines():
        # nmcli -t escapes literal ':' as '\:'. Split only unescaped separators.
        parts = re.split(r"(?<!\\):", raw)
        if not parts:
            continue
        ssid = parts[0].replace("\\:", ":").strip()
        if not ssid or ssid in seen:
            continue
        seen.add(ssid)
        signal = parts[1] if len(parts) > 1 else ""
        security = parts[2] if len(parts) > 2 else ""
        nets.append({
            "ssid": ssid,
            "signal": signal,
            "security": security,
            "likely_elm": any(h in ssid.lower() for h in _WIFI_NAME_HINTS),
        })
    return nets


def _current_gateway() -> str:
    if not shutil.which("ip"):
        return ""
    r = _run(["ip", "route", "show", "default"], timeout=3)
    m = re.search(r"\bvia\s+(\d+\.\d+\.\d+\.\d+)", r.stdout)
    return m.group(1) if m else ""


def _read_prompt(stream, max_reads: int = 5) -> str:
    data = bytearray()
    for _ in range(max_reads):
        chunk = stream.read(512)
        if chunk:
            data.extend(chunk)
        if b">" in data:
            break
    return bytes(data).decode("ascii", errors="ignore")


def _command(stream, cmd: str, delay: float = 0.12, reads: int = 5) -> str:
    try:
        stream.reset_input_buffer()
    except Exception:
        pass
    stream.write((cmd + "\r").encode("ascii"))
    time.sleep(delay)
    return _read_prompt(stream, reads)


def _clean_response(text: str, command: str = "") -> str:
    text = text.replace("\r", "\n").replace(">", "")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if command:
        lines = [ln for ln in lines if ln.replace(" ", "").upper() != command.replace(" ", "").upper()]
    return " | ".join(lines)


def _normal_hex(text: str) -> str:
    return re.sub(r"[^0-9A-F]", "", text.upper())


def probe(cfg: elm_link.LinkConfig) -> dict:
    """Open one configured ELM transport and prove adapter + ECU independently."""
    result: dict[str, object] = {
        "ok": False, "adapter_ok": False, "ecu_ok": False,
        "mode": cfg.mode, "link": "", "adapter": "", "protocol": "",
        "rpm": None, "error": "",
    }
    stream = None
    try:
        stream, description = elm_link.open_elm_link(cfg)
        result["link"] = description
        # Reset can take a moment; older clone firmware is especially slow.
        _command(stream, "ATZ", delay=0.8, reads=7)
        _command(stream, "ATE0")
        _command(stream, "ATL0")
        _command(stream, "ATS0")
        _command(stream, "ATH0")
        _command(stream, "ATSP0")

        ident = _clean_response(_command(stream, "ATI", delay=0.2), "ATI")
        if not ident or ident == "?":
            raise RuntimeError("transport opened but ATI did not identify an ELM-compatible adapter")
        result["adapter"] = ident
        result["adapter_ok"] = True

        # Ask for supported PIDs. On ATSP0 this also forces actual protocol
        # discovery, which may take several seconds on ISO9141/KWP2000.
        pids = _clean_response(_command(stream, "0100", delay=0.4, reads=10), "0100")
        proto = _clean_response(_command(stream, "ATDP", delay=0.15), "ATDP")
        result["protocol"] = proto
        hx = _normal_hex(pids)
        result["ecu_ok"] = "4100" in hx

        if result["ecu_ok"]:
            rpm_text = _clean_response(_command(stream, "010C", delay=0.2, reads=8), "010C")
            rpm_hex = _normal_hex(rpm_text)
            pos = rpm_hex.find("410C")
            if pos >= 0 and len(rpm_hex) >= pos + 8:
                a = int(rpm_hex[pos + 4:pos + 6], 16)
                b = int(rpm_hex[pos + 6:pos + 8], 16)
                result["rpm"] = round(((a * 256) + b) / 4.0, 1)
        else:
            upper = pids.upper()
            if "NO DATA" in upper:
                result["error"] = "ELM327 connected, but ECU returned NO DATA — ignition may be off or this ECU/protocol is not responding"
            elif "UNABLE TO CONNECT" in upper:
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


def _cfg_serial(dev: str, baud: int = 38400) -> elm_link.LinkConfig:
    return elm_link.LinkConfig(mode="serial", serial_dev=dev, serial_baud=baud, timeout=3.0)


def _cfg_bt(mac: str, channel: int = 1) -> elm_link.LinkConfig:
    return elm_link.LinkConfig(mode="bluetooth", bt_mac=mac.upper(), bt_channel=channel, timeout=3.0)


def _cfg_wifi(host: str, port: int = 35000) -> elm_link.LinkConfig:
    return elm_link.LinkConfig(mode="wifi", wifi_host=host, wifi_port=port, timeout=3.0)


def _persist_cfg(cfg: elm_link.LinkConfig) -> None:
    updates = {
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
    _write_env(updates)


def _status_payload() -> dict:
    cfg = _configured_cfg()
    values = _load_env()
    return {
        "config": {k: values.get(k, "") for k in sorted(ELM_KEYS)},
        "effective_link": asdict(cfg),
        "service": _service_state(),
        "mqtt": _mqtt_status(),
        "serial_candidates": _serial_candidates(),
    }


def _print_probe(result: dict) -> None:
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
    payload = _status_payload()
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0
    cfg = payload["effective_link"]
    print("DRIFTER vehicle link")
    print(f"  configured mode : {cfg['mode']}")
    if cfg.get("bt_mac"):
        print(f"  bluetooth       : {cfg['bt_mac']} channel {cfg['bt_channel']}")
    if cfg.get("wifi_host"):
        print(f"  wifi tcp        : {cfg['wifi_host']}:{cfg['wifi_port']}")
    print(f"  serial          : {cfg['serial_dev']} @ {cfg['serial_baud']}")
    svc = payload["service"]
    print(f"  service         : {svc.get('ActiveState', 'unknown')}/{svc.get('SubState', 'unknown')} restarts={svc.get('NRestarts', '?')}")
    if payload["mqtt"]:
        print(f"  live OBD state  : {payload['mqtt']}")
    else:
        print("  live OBD state  : no retained status received")
    return 0


def cmd_scan(args) -> int:
    serials = _serial_candidates()
    bt = _bt_devices(args.seconds)
    wifi = _wifi_networks()
    payload = {"serial": serials, "bluetooth": bt, "wifi": wifi}
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0
    print("SERIAL")
    print("  " + "\n  ".join(serials) if serials else "  none")
    print("BLUETOOTH")
    if bt:
        for d in sorted(bt, key=lambda x: (not bool(x.get("likely_elm")), str(x.get("name")))):
            hint = " likely-ELM" if d.get("likely_elm") else ""
            state = "paired" if d.get("paired") else "unpaired"
            print(f"  {d['mac']}  {d.get('name', 'unknown')}  [{state}{hint}]")
    else:
        print("  none")
    print("WI-FI")
    if wifi:
        for n in sorted(wifi, key=lambda x: (not bool(x.get("likely_elm")), str(x.get("ssid")))):
            hint = " likely-ELM" if n.get("likely_elm") else ""
            print(f"  {n['ssid']}  signal={n['signal']} security={n['security'] or 'open'}{hint}")
    else:
        print("  none")
    return 0


def cmd_test(args) -> int:
    cfg = _configured_cfg()
    result = probe(cfg)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        _print_probe(result)
    return 0 if result.get("adapter_ok") else 2


def _activate(cfg: elm_link.LinkConfig, *, verify: bool = True) -> int:
    _require_root()
    _service("stop")
    try:
        result = probe(cfg) if verify else {"adapter_ok": True, "ecu_ok": False}
        if verify:
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
    print(f"Pairing {mac}. If the adapter asks for a PIN, common ELM327 PINs are 1234 or 0000.")
    # Keep stdin/stdout attached so BlueZ can ask for a PIN interactively.
    rc = subprocess.run(["bluetoothctl", "pair", mac], check=False).returncode
    if rc != 0:
        print("Pairing failed. Run `drifter obd scan` and confirm the adapter is powered and visible.")
        return rc or 2
    subprocess.run(["bluetoothctl", "trust", mac], check=False)
    return _activate(_cfg_bt(mac, args.channel))


def cmd_setup(args) -> int:
    """Best-effort zero-config setup: prove configured link, serial, then paired BT."""
    _require_root()
    _service("stop")
    selected: elm_link.LinkConfig | None = None
    visible_bt: list[dict] = []
    try:
        current = _configured_cfg()
        # A genuinely configured link gets first chance.
        configured = (
            (current.mode in {"bluetooth", "bt"} and current.bt_mac)
            or (current.mode in {"wifi", "tcp", "network"} and current.wifi_host)
            or (current.mode in {"serial", "usb", "tty", "rfcomm"} and os.path.exists(current.serial_dev))
        )
        if configured:
            print(f"Trying configured {current.mode} link...")
            res = probe(current)
            _print_probe(res)
            if res.get("adapter_ok"):
                selected = current

        if selected is None:
            for dev in _serial_candidates():
                print(f"Trying serial candidate {dev}...")
                cfg = _cfg_serial(dev)
                res = probe(cfg)
                if res.get("adapter_ok"):
                    _print_probe(res)
                    selected = cfg
                    break

        if selected is None:
            visible_bt = _bt_devices(args.seconds)
            paired = [d for d in visible_bt if d.get("paired")]
            paired.sort(key=lambda d: (not bool(d.get("likely_elm")), str(d.get("name"))))
            for dev in paired:
                print(f"Trying paired Bluetooth candidate {dev['name']} ({dev['mac']})...")
                cfg = _cfg_bt(str(dev["mac"]))
                res = probe(cfg)
                if res.get("adapter_ok"):
                    _print_probe(res)
                    selected = cfg
                    break

        # If the Pi is already associated to an ELM Wi-Fi adapter, its gateway
        # is normally the TCP endpoint. Probe that without inventing an address.
        if selected is None:
            gateway = _current_gateway()
            if gateway:
                try:
                    with socket.create_connection((gateway, 35000), timeout=0.8):
                        pass
                    print(f"Trying ELM-style TCP service on current gateway {gateway}:35000...")
                    cfg = _cfg_wifi(gateway, 35000)
                    res = probe(cfg)
                    if res.get("adapter_ok"):
                        _print_probe(res)
                        selected = cfg
                except OSError:
                    pass

        if selected is None:
            likely_unpaired = [d for d in visible_bt if d.get("likely_elm") and not d.get("paired")]
            print("[FAIL] No usable ELM327 link was proved.")
            if likely_unpaired:
                d = likely_unpaired[0]
                print(f"[NEXT] Bluetooth adapter found but not paired: {d['name']} {d['mac']}")
                print(f"       Run: sudo drifter obd pair {d['mac']}")
            else:
                likely_wifi = [n for n in _wifi_networks() if n.get("likely_elm")]
                if likely_wifi:
                    print(f"[NEXT] ELM-style Wi-Fi network visible: {likely_wifi[0]['ssid']}")
                    print("       Connect the DRIFTER OBD Wi-Fi interface to it, then rerun `sudo drifter obd setup`.")
                else:
                    print("[NEXT] Confirm the ELM327 is powered in the OBD-II socket, ignition is ON, then rerun `drifter obd scan`.")
            return 2

        _persist_cfg(selected)
        print(f"[PASS] DRIFTER will use {selected.mode} ELM327 on subsequent boots.")
        return 0
    finally:
        _service("restart")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="drifter obd", description=__doc__)
    p.add_argument("--json", action="store_true", help="machine-readable output where supported")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("status", help="show configured link, bridge service and retained OBD state")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("scan", help="scan serial, Bluetooth and Wi-Fi candidates")
    sp.add_argument("--seconds", type=int, default=8, help="Bluetooth scan time (default: 8)")
    sp.set_defaults(func=cmd_scan)

    sp = sub.add_parser("test", help="probe the currently configured ELM327 and ECU")
    sp.set_defaults(func=cmd_test)

    sp = sub.add_parser("setup", help="find, prove and persist a usable ELM327 link")
    sp.add_argument("--seconds", type=int, default=10, help="Bluetooth discovery time")
    sp.set_defaults(func=cmd_setup)

    sp = sub.add_parser("pair", help="pair/trust a Bluetooth Classic ELM327, then verify it")
    sp.add_argument("mac")
    sp.add_argument("--channel", type=int, default=1)
    sp.set_defaults(func=cmd_pair)

    sp = sub.add_parser("use-serial", help="verify and select a serial/USB ELM327")
    sp.add_argument("device")
    sp.add_argument("--baud", type=int, default=38400)
    sp.set_defaults(func=cmd_use_serial)

    sp = sub.add_parser("use-bt", help="verify and select a paired Bluetooth Classic ELM327")
    sp.add_argument("mac")
    sp.add_argument("--channel", type=int, default=1)
    sp.set_defaults(func=cmd_use_bt)

    sp = sub.add_parser("use-wifi", help="verify and select a Wi-Fi TCP ELM327")
    sp.add_argument("host")
    sp.add_argument("--port", type=int, default=35000)
    sp.set_defaults(func=cmd_use_wifi)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

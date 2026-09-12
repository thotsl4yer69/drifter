#!/usr/bin/env python3
"""Field-first HTTP routes layered over the existing DRIFTER dashboard.

Routes are local/hotspot-only and provide touch-safe structured operations for
vehicle-link setup, passive RF field work and display recovery. Existing
DashboardHandler routes remain the fallback for the full cockpit.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import asdict
from urllib.parse import urlparse

import obd_setup
import web_dashboard_state as state
from web_dashboard_handlers import DashboardHandler

FIELD_MAX_BODY = 32 * 1024
_MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}$")
_HOST_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_DEV_RE = re.compile(r"^/dev/[A-Za-z0-9_./:-]{1,180}$")
_RF_ACTIONS = {
    "survey", "survey_stop", "hunt_start", "hunt_stop",
    "capture", "zoom", "listen", "listen_stop",
    "baseline_save", "recover",
}


def _local(peer: str) -> bool:
    return peer == "127.0.0.1" or peer == "::1" or peer.startswith("10.42.0.")


def _run(argv: list[str], timeout: float = 20.0, *, input_text: str | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            argv,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(argv, 127, "", str(exc))


def _json_from_stdout(result: subprocess.CompletedProcess) -> dict:
    try:
        payload = json.loads(result.stdout)
        if isinstance(payload, dict):
            payload.setdefault("ok", result.returncode == 0)
            payload.setdefault("rc", result.returncode)
            return payload
    except (ValueError, TypeError):
        pass
    if result.returncode != 0:
        return {
            "ok": False,
            "rc": result.returncode,
            "error": (result.stderr or result.stdout or "command failed").strip()[:1200],
        }
    return {"ok": True, "rc": 0, "output": result.stdout.strip()[:4000]}


def _list_count(value) -> int:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        for key in ("devices", "networks", "rows", "items"):
            if isinstance(value.get(key), list):
                return len(value[key])
    return 0


def _preferred_obd_wifi_iface() -> str:
    """Prefer a secondary Wi-Fi NIC so joining an ELM AP doesn't kill control.

    DRIFTER normally owns wlan0 for client/hotspot resilience. When the D-Link
    or another second adapter exists, use it for a Wi-Fi ELM327. If there is
    only one radio nmcli may still use it; the Pi-local touchscreen remains
    available but the API tells the operator which interface was selected.
    """
    result = _run(["/usr/bin/nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device"], timeout=5)
    wifi = []
    for line in result.stdout.splitlines():
        parts = line.split(":", 2)
        if len(parts) >= 2 and parts[1] == "wifi" and parts[0]:
            wifi.append(parts[0])
    for preferred in ("wlan1", "wlan2", "wlx"):
        for iface in wifi:
            if iface == preferred or iface.startswith(preferred):
                return iface
    for iface in wifi:
        if iface != "wlan0":
            return iface
    return wifi[0] if wifi else ""


class FieldDashboardHandler(DashboardHandler):
    """Adds appliance-style field controls without duplicating the dashboard."""

    def _field_json(self, payload, code: int = 200) -> None:
        raw = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _field_body(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            length = 0
        if length < 0 or length > FIELD_MAX_BODY:
            self._field_json({"ok": False, "error": "request too large"}, 413)
            return None
        try:
            raw = self.rfile.read(length) if length else b"{}"
            body = json.loads(raw or b"{}")
        except (ValueError, UnicodeDecodeError):
            self._field_json({"ok": False, "error": "invalid JSON"}, 400)
            return None
        if not isinstance(body, dict):
            self._field_json({"ok": False, "error": "JSON object required"}, 400)
            return None
        return body

    def _field_allowed(self) -> bool:
        peer = self.client_address[0] if self.client_address else ""
        if _local(peer):
            return True
        self._field_json({"ok": False, "error": "field controls are local-network only"}, 403)
        return False

    def _obd_status(self) -> dict:
        """Cheap status snapshot: no mosquitto_sub or Bluetooth scan per poll."""
        cfg = obd_setup._configured_cfg()
        env = obd_setup._load_env()
        return {
            "ok": True,
            "config": {key: env.get(key, "") for key in sorted(obd_setup.ELM_KEYS)},
            "effective_link": asdict(cfg),
            "service": obd_setup._service_state(),
            "mqtt": state.latest_state.get("obd_status") or {},
            "serial_candidates": obd_setup._serial_candidates(),
            "ts": time.time(),
        }

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if not path.startswith("/api/field/"):
            return super().do_GET()
        if not self._field_allowed():
            return

        if path == "/api/field/obd/status":
            return self._field_json(self._obd_status())

        if path == "/api/field/rf/status":
            return self._field_json({
                "ok": True,
                "ops": state.latest_state.get("rf_ops") or {},
                "findings": state.latest_state.get("rf_findings") or {"findings": []},
                "hunt": state.latest_state.get("rf_hunt") or {},
                "capture": state.latest_state.get("rf_capture") or {},
                "zoom": state.latest_state.get("rf_spectrum_zoom") or {},
                "spectrum": state.latest_state.get("rf_spectrum_summary") or {},
                "rfaudio": state.latest_state.get("rfaudio_status") or {},
                "hardware": state.latest_state.get("hw_rtl_sdr") or {},
                "correlation": {
                    "wifi_devices": _list_count(state.latest_state.get("wifi_devices")),
                    "ble_devices": _list_count(state.latest_state.get("ble_devices")),
                    "wardrive_wifi": _list_count(state.latest_state.get("wardrive_wifi")),
                    "gps": state.latest_state.get("gps_fix") or {},
                },
                "ts": time.time(),
            })

        if path == "/api/field/system/status":
            return self._field_json({
                "ok": True,
                "boot": state.latest_state.get("boot_status") or {},
                "watchdog": state.latest_state.get("system_watchdog") or {},
                "lcd": state.latest_state.get("lcd_status") or {},
                "network": state.latest_state.get("network_status") or {},
                "ts": time.time(),
            })

        self._field_json({"ok": False, "error": "unknown field route"}, 404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if not path.startswith("/api/field/"):
            return super().do_POST()
        if not self._field_allowed():
            return
        body = self._field_body()
        if body is None:
            return

        if path == "/api/field/obd/scan":
            try:
                seconds = max(2, min(12, int(body.get("seconds", 6) or 6)))
            except (TypeError, ValueError):
                seconds = 6
            result = _run(
                ["/usr/local/bin/drifter", "obd", "--json", "scan", "--seconds", str(seconds)],
                timeout=seconds + 8,
            )
            return self._field_json(_json_from_stdout(result),
                                    200 if result.returncode == 0 else 503)

        if path == "/api/field/obd/test":
            result = _run(["/usr/local/bin/drifter", "obd", "--json", "test"], timeout=18)
            return self._field_json(_json_from_stdout(result))

        if path == "/api/field/obd/auto":
            result = _run(
                ["sudo", "-n", "/usr/local/bin/drifter", "obd", "setup", "--seconds", "7"],
                timeout=35,
            )
            return self._field_json({
                "ok": result.returncode == 0,
                "rc": result.returncode,
                "output": result.stdout.strip()[-5000:],
                "error": result.stderr.strip()[-1500:],
            })

        if path == "/api/field/obd/select":
            mode = str(body.get("mode") or "").lower()
            argv = ["sudo", "-n", "/usr/local/bin/drifter", "obd"]
            if mode == "bluetooth":
                mac = str(body.get("mac") or "").upper()
                if not _MAC_RE.fullmatch(mac):
                    return self._field_json({"ok": False, "error": "valid Bluetooth MAC required"}, 400)
                argv += ["use-bt", mac]
            elif mode == "wifi":
                host = str(body.get("host") or "")
                try:
                    port = int(body.get("port", 35000))
                except (TypeError, ValueError):
                    port = 0
                if not _HOST_RE.fullmatch(host) or not 1 <= port <= 65535:
                    return self._field_json({"ok": False, "error": "valid ELM Wi-Fi host/port required"}, 400)
                argv += ["use-wifi", host, "--port", str(port)]
            elif mode == "serial":
                device = str(body.get("device") or "")
                try:
                    baud = int(body.get("baud", 38400))
                except (TypeError, ValueError):
                    baud = 0
                if not _DEV_RE.fullmatch(device) or baud not in {9600, 38400, 57600, 115200}:
                    return self._field_json({"ok": False, "error": "valid serial device/baud required"}, 400)
                argv += ["use-serial", device, "--baud", str(baud)]
            else:
                return self._field_json({"ok": False, "error": "mode must be bluetooth, wifi or serial"}, 400)
            result = _run(argv, timeout=25)
            return self._field_json({
                "ok": result.returncode == 0,
                "rc": result.returncode,
                "output": result.stdout.strip()[-5000:],
                "error": result.stderr.strip()[-1500:],
            })

        if path == "/api/field/obd/pair":
            mac = str(body.get("mac") or "").upper()
            pin = str(body.get("pin") or "1234")
            if not _MAC_RE.fullmatch(mac) or not re.fullmatch(r"\d{4,8}", pin):
                return self._field_json({"ok": False, "error": "valid MAC and numeric PIN required"}, 400)
            script = "\n".join([
                "power on", "agent KeyboardOnly", "default-agent",
                f"pair {mac}", pin, f"trust {mac}", f"connect {mac}", "quit", "",
            ])
            result = _run(
                ["sudo", "-n", "/usr/bin/bluetoothctl"],
                timeout=25,
                input_text=script,
            )
            text = (result.stdout + "\n" + result.stderr).strip()
            paired = (
                "Pairing successful" in text
                or "Paired: yes" in text
                or "already paired" in text.lower()
            )
            return self._field_json({"ok": paired, "rc": result.returncode,
                                     "output": text[-5000:]})

        if path == "/api/field/obd/wifi-connect":
            ssid = str(body.get("ssid") or "")
            password = str(body.get("password") or "")
            if not ssid or len(ssid) > 64 or "\n" in ssid or "\r" in ssid:
                return self._field_json({"ok": False, "error": "valid SSID required"}, 400)
            iface = _preferred_obd_wifi_iface()
            argv = ["sudo", "-n", "/usr/bin/nmcli", "device", "wifi", "connect", ssid]
            if password:
                argv += ["password", password]
            if iface:
                argv += ["ifname", iface]
            result = _run(argv, timeout=25)
            return self._field_json({
                "ok": result.returncode == 0,
                "rc": result.returncode,
                "interface": iface or None,
                "output": result.stdout.strip()[-2000:],
                "error": result.stderr.strip()[-1000:],
            })

        if path == "/api/field/rf/command":
            action = str(body.get("action") or "").lower().strip()
            if action not in _RF_ACTIONS:
                return self._field_json({"ok": False, "error": "unsupported RF field action"}, 400)
            payload = {"action": action, "ts": time.time()}
            if action in {"hunt_start", "capture", "zoom", "listen"}:
                try:
                    freq = float(body.get("freq_mhz"))
                except (TypeError, ValueError):
                    return self._field_json({"ok": False, "error": "freq_mhz required"}, 400)
                if not 24.0 <= freq <= 1766.0:
                    return self._field_json({"ok": False, "error": "freq_mhz outside RTL-SDR range"}, 400)
                payload["freq_mhz"] = freq
            if action == "capture":
                try:
                    payload["duration_s"] = max(1.0, min(30.0, float(body.get("duration_s", 15))))
                except (TypeError, ValueError):
                    payload["duration_s"] = 15.0
            if action == "zoom":
                try:
                    payload["span_khz"] = max(50, min(5000, int(body.get("span_khz", 500))))
                except (TypeError, ValueError):
                    payload["span_khz"] = 500
            if action == "listen":
                mode = str(body.get("mode") or "nfm").lower()
                payload["mode"] = mode if mode in {"am", "nfm", "wfm", "fm", "usb", "lsb", "raw"} else "nfm"
            if state.mqtt_client is None:
                return self._field_json({"ok": False, "error": "MQTT not ready"}, 503)
            state.mqtt_client.publish("drifter/rf/ops/command", json.dumps(payload), qos=1)
            return self._field_json({"ok": True, "queued": payload})

        if path == "/api/field/display/recover":
            result = _run(
                ["sudo", "-n", "/usr/local/bin/drifter", "display", "recover"],
                timeout=20,
            )
            return self._field_json({
                "ok": result.returncode == 0,
                "rc": result.returncode,
                "output": result.stdout.strip()[-3000:],
                "error": result.stderr.strip()[-1000:],
            })

        self._field_json({"ok": False, "error": "unknown field route"}, 404)

"""MZ1312 DRIFTER — Marauder bridge module: rogue AP + portal + cred capture."""

import hashlib
import uuid
from pathlib import Path

import marauder_allowlist as ma
import marauder_protocol as mp

MAX_PORTAL_DURATION_S = 1800  # 30 minutes
MAX_TEMPLATE_BYTES = 64 * 1024

REQUIRED_PLACEHOLDER = "{{captive_post_url}}"
BLOCKED_PATTERNS = ['<script src="http']


def load_and_validate_template(template_dir: Path | str) -> bytes:
    """Read portal.html from template_dir, validate against three rules:
      - contains {{captive_post_url}} placeholder
      - <= MAX_TEMPLATE_BYTES
      - no external <script src="http..."> exfil tags
    Returns the HTML as bytes (raw, unsubstituted)."""
    d = Path(template_dir)
    html_path = d / "portal.html"
    raw = html_path.read_bytes()
    if len(raw) > MAX_TEMPLATE_BYTES:
        raise ValueError(f"portal template too large ({len(raw)}B > 64KB cap)")
    text = raw.decode("utf-8", errors="replace")
    if REQUIRED_PLACEHOLDER not in text:
        raise ValueError(f"portal template missing {REQUIRED_PLACEHOLDER} placeholder")
    for pat in BLOCKED_PATTERNS:
        if pat in text.lower() or pat in text:
            raise ValueError("portal template contains blocked pattern: external script src")
    return raw


def start(transport, allowlist_scope: dict, *,
          template_root: Path | str,
          ssid: str, template_name: str,
          duration_s: int) -> dict:
    """Start a rogue-AP + captive-portal session."""
    if transport.mode == "none":
        return {"ok": False, "response": "no transport available"}

    # Allowlist gate — (ssid, template) pair must match a single entry
    ok, reason = ma.is_target_allowed(allowlist_scope, "evilportal",
                                      ssid=ssid, template=template_name)
    if not ok:
        return {"ok": False, "response": reason}

    template_dir = Path(template_root) / template_name
    if not template_dir.is_dir():
        return {"ok": False, "response": f"template dir not found: {template_dir}"}

    try:
        raw_html = load_and_validate_template(template_dir)
    except (OSError, ValueError) as e:
        return {"ok": False, "response": f"template invalid: {e}"}

    template_sha = hashlib.sha256(raw_html).hexdigest()
    session_id = uuid.uuid4().hex

    # Upload template (chunked) then start
    for chunk in mp.cmd_evilportal_load_template(raw_html):
        transport.send(chunk)
    transport.send(mp.cmd_evilportal_start(ssid))

    capped = min(int(duration_s), MAX_PORTAL_DURATION_S)
    return {"ok": True,
            "response": f"portal started ssid={ssid} template={template_name}",
            "session_id": session_id,
            "ssid": ssid,
            "template_name": template_name,
            "template_sha256": template_sha,
            "duration_s": capped}


def stop(transport) -> dict:
    if transport.mode == "none":
        return {"ok": False, "response": "no transport"}
    transport.send(mp.cmd_evilportal_stop())
    return {"ok": True, "response": "portal stop sent"}

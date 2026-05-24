# Marauder Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `drifter-marauder` service — Python bridge to ESP32 Marauder firmware (direct USB or Flipper-proxy), covering passive Wi-Fi/BLE recon, active Wi-Fi (deauth/beacon), BLE recon+spam, and EvilPortal, all gated by a three-layer authorization model.

**Architecture:** New systemd service `drifter-marauder.service` (runs as `drifter` user with `dialout` group for USB serial). Transport autodetects between `/dev/serial/by-id/*` (direct ESP32) and proxying through existing `drifter-flipper` (via Flipper Zero's GPIO ESP32 module). MQTT topics under `drifter/marauder/`. HTTP API mounted on `:8090` OPSEC dashboard (foot-mode only). Every HIGH-risk command passes local-peer gate + per-command confirm token + per-target allowlist (extends `audit_targets.yaml`).

**Tech Stack:** Python 3.11+, `paho-mqtt`, `pyserial`, `pyyaml`, systemd `User=drifter` + `SupplementaryGroups=dialout`, Marauder firmware ~v0.13 CLI.

**Spec:** `docs/superpowers/specs/2026-05-24-marauder-bridge-design.md` (read this first; the plan references its section numbers).

---

## File Structure

**New files:**

| Path | Responsibility |
|---|---|
| `src/marauder_bridge.py` | Service entry; main loop; MQTT client; command lock; dispatch |
| `src/marauder_transport.py` | Autodetect direct-USB vs Flipper-proxy; serial I/O; reader thread |
| `src/marauder_protocol.py` | Marauder CLI command builders; line-event parser; regex table |
| `src/marauder_allowlist.py` | Loads `audit_targets.yaml`; gates per category (wifi/ble/evilportal) |
| `src/marauder_features/__init__.py` | Package marker |
| `src/marauder_features/passive.py` | Phase 1: passive scan dispatch + result handling |
| `src/marauder_features/active_wifi.py` | Phase 2: deauth/beacon/probe-flood dispatch + audit |
| `src/marauder_features/ble.py` | Phase 3: BLE scan/AirTag/skimmer/spam dispatch + audit |
| `src/marauder_features/evilportal.py` | Phase 4: rogue AP + portal + cred capture path |
| `services/drifter-marauder.service` | Systemd unit |
| `config/audit_targets.sample.yaml` | Sample allowlist (empty `marauder:` block) |
| `tests/test_marauder_protocol.py` | Unit: command builders + event parser |
| `tests/test_marauder_allowlist.py` | Unit: allowlist gating per category |
| `tests/test_marauder_classify_risk.py` | Unit: every command → documented risk level |
| `tests/test_marauder_session_record.py` | Unit: audit JSON invariants |
| `tests/integration/test_marauder_transport.py` | pty-loopback integration |
| `tests/fixtures/marauder/scanap_output.txt` | Captured Marauder serial fixture |
| `tests/fixtures/marauder/scansta_output.txt` | Captured Marauder serial fixture |
| `tests/fixtures/marauder/blescan_output.txt` | Captured Marauder serial fixture |
| `tests/fixtures/marauder/portals/test-portal/portal.html` | Dryrun-only portal template |
| `scripts/test-bench-marauder.sh` | Real-hardware bench modes (probe/passive/...) |

**Modified files:**

| Path | Change |
|---|---|
| `src/config.py` | Add `drifter-marauder` to `FOOT_ONLY_SERVICES`; add `BEACON_SPAM_RANDOM_REFUSE = True` |
| `src/opsec_dashboard.py` | Mount `/api/marauder/*` routes |
| `install.sh` | Add `drifter-marauder` to `SERVICES`; create `/opt/drifter/etc/marauder/` tree; seed `audit_targets.yaml` if absent |
| `docs/MARAUDER.md` | Rewrite to point at new spec; rescind passive-only constraint |

---

## Conventions used in this plan

- `pytest tests/test_marauder_X.py::test_Y -v` is the standard test invocation.
- Every commit uses Conventional Commits prefix matching the repo style (`feat(marauder):`, `fix(marauder):`, `test(marauder):`, `docs(marauder):`, `chore(marauder):`).
- Every commit message ends with `Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>`.
- `git add` lists specific files — never `git add -A` or `git add .` (project rule).
- Each task is self-contained; you can read tasks out of order. Code is repeated where needed.

---

## Phase 0 — Prep & supersede

Lands the doc + scaffolding changes before any service code. Five tasks.

---

### Task 0.1: Rewrite `docs/MARAUDER.md` to point at the new spec

**Files:**
- Modify: `docs/MARAUDER.md` (full rewrite)

- [ ] **Step 1: Write the new doc content**

Replace the entire file with:

```markdown
# MARAUDER — ESP32 Wi-Fi/BLE Bridge

DRIFTER's foot-mode arsenal includes a Marauder bridge that talks to ESP32
Marauder firmware over USB serial, either directly via a Marauder-flashed
ESP32 dev board or via the Flipper Zero GPIO ESP32 module. The bridge surfaces
**both passive and active** Marauder capabilities; the offensive surface is
gated by the three-layer authorization model defined in the design spec.

**Design spec:** [`superpowers/specs/2026-05-24-marauder-bridge-design.md`](superpowers/specs/2026-05-24-marauder-bridge-design.md)

**Implementation plan:** [`superpowers/plans/2026-05-24-marauder-bridge.md`](superpowers/plans/2026-05-24-marauder-bridge.md)

## Change of stance (2026-05-24)

This doc previously stated:

> "DRIFTER deliberately surfaces ONLY the passive Marauder commands. The
> firmware can do more, but the cockpit must not surface DEAUTH, BEACON
> SPAM, or EVIL TWIN — those are kept out of the operator surface by spec."

**That constraint is rescinded.** The new spec surfaces offensive features
(deauth attack, beacon spam, BLE proximity spam, EvilPortal) behind a
three-layer gate:

1. Local-peer network check (127.0.0.1 + 10.42.0.0/24 only).
2. Per-command confirmation token (HIGH-risk commands return a token,
   operator must POST it back within 120 s).
3. Per-target allowlist match in `/opt/drifter/etc/audit_targets.yaml`
   under the `marauder:` top-level key. Empty allowlist → refused.

The gating model is considered sufficient to surface offensive features
safely for authorized pentest engagements.

## How to authorize a target

Edit `/opt/drifter/etc/audit_targets.yaml` and add entries under `marauder:`:

```yaml
marauder:
  wifi:
    - ssid: "ACME-Pentest-Guest"
    - bssid: "aa:bb:cc:dd:ee:ff"
  ble:
    - mac: "11:22:33:44:55:66"
    # OR for indiscriminate-spam areas:
    - area_authorized: true
      area_label: "ACME HQ pen-test lab room 204"
  evilportal:
    - ssid: "ACME-Pentest-Guest"
      template: "acme-guest"
      max_captures: 50
      authorized_use: "ACME contract #1234 valid 2026-05-01 → 2026-06-30"
```

## Firmware flash (operator-managed, unchanged)

The firmware itself is not vendored — operator downloads the matching `.bin`
once per board and drops it in `/opt/drifter/state/marauder_fw/` before
running the flash script:

```bash
sudo mkdir -p /opt/drifter/state/marauder_fw
sudo chown "$USER:$USER" /opt/drifter/state/marauder_fw

# Pick the right binary for your board from:
#   https://github.com/justcallmekoko/ESP32Marauder/releases
# For the operator's default DevKit_v4:
curl -L -o /opt/drifter/state/marauder_fw/marauder_devkit_v4.bin \
  https://github.com/justcallmekoko/ESP32Marauder/releases/latest/download/esp32_marauder_vX.Y.Z_DevKit_v4.bin

sudo apt install python3-esptool

# Flash (defaults to newest .bin in marauder_fw/ at address 0x10000):
~/drifter/tools/flash_marauder.sh /dev/ttyUSB0

# To use a specific firmware or address:
MARAUDER_FLASH_ADDR=0x0 ~/drifter/tools/flash_marauder.sh /dev/ttyUSB0 /path/to/full_image.bin
```

After flashing: unplug + replug the ESP32. The new `drifter-marauder` service
autodetects on next probe (or `POST /api/marauder/probe`).
```

- [ ] **Step 2: Commit**

```bash
git -C /home/kali/drifter add docs/MARAUDER.md
git -C /home/kali/drifter commit -m "$(cat <<'EOF'
docs(marauder): rescind passive-only constraint; point at new spec

The marauder bridge implementation lands the full Marauder feature
surface (passive + active Wi-Fi + BLE + EvilPortal) gated by the
three-layer authorization model. Update MARAUDER.md to reflect the
change of stance and route readers to the design spec.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 0.2: Add `audit_targets.sample.yaml` with empty `marauder:` block

**Files:**
- Create: `config/audit_targets.sample.yaml`

- [ ] **Step 1: Write the sample file**

```yaml
# MZ1312 DRIFTER — audit target allowlist (sample)
#
# Copy to /opt/drifter/etc/audit_targets.yaml and populate before running
# any HIGH-risk drifter-wifi-audit or drifter-marauder commands. Empty
# lists mean every offensive command will be refused with "allowlist
# empty" until you add entries.
#
# This file is the SINGLE source of truth for what targets the operator
# has authorization to attack. The Pi has no other authorization mechanism.

# Used by drifter-wifi-audit for handshake/PMKID capture.
networks: []
#  - ssid: "ACME-Pentest-Guest"
#    bssid: "aa:bb:cc:dd:ee:ff"

# Used by drifter-marauder for deauth / beacon spam / BLE / EvilPortal.
# See docs/superpowers/specs/2026-05-24-marauder-bridge-design.md §5.3
# for the full schema.
marauder:
  wifi: []
  #  - ssid: "ACME-Pentest-Guest"
  #  - bssid: "aa:bb:cc:dd:ee:ff"

  ble: []
  #  - mac: "11:22:33:44:55:66"
  #  - area_authorized: true
  #    area_label: "ACME HQ pen-test lab room 204"

  evilportal: []
  #  - ssid: "ACME-Pentest-Guest"
  #    template: "acme-guest"
  #    max_captures: 50
  #    authorized_use: "ACME contract #1234 valid 2026-05-01 → 2026-06-30"
```

- [ ] **Step 2: Commit**

```bash
git -C /home/kali/drifter add config/audit_targets.sample.yaml
git -C /home/kali/drifter commit -m "$(cat <<'EOF'
chore(marauder): add audit_targets.sample.yaml with empty marauder block

Deployed by install.sh to /opt/drifter/etc/audit_targets.yaml if no
allowlist exists yet. Operator must populate before any HIGH-risk
command works.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 0.3: Register `drifter-marauder` in `config.py`

**Files:**
- Modify: `src/config.py` (add to `FOOT_ONLY_SERVICES`, add `BEACON_SPAM_RANDOM_REFUSE`)

- [ ] **Step 1: Add to `FOOT_ONLY_SERVICES`**

Find the existing `FOOT_ONLY_SERVICES` list (around line 950) and append:

```python
FOOT_ONLY_SERVICES = [
    "drifter-wardrive",
    "drifter-flipper",
    "drifter-opsec",
    "drifter-kismet",
    "drifter-kismet-bridge",
    "drifter-wifi-audit",
    "drifter-marauder",      # NEW
]
```

Also add to `SERVICES` (the master list, usually defined above the mode-specific lists). Find the existing `SERVICES = [...]` block and append `"drifter-marauder"` in alphabetical-ish order (between `drifter-logger` and `drifter-opsec` works).

- [ ] **Step 2: Add hard-refusal config constants**

At the end of `config.py`, just before the file ends:

```python
# ── Marauder bridge feature flags ─────────────────────────────────────
# Random-SSID beacon spam is refused outright by the bridge — random
# SSIDs cannot be allowlisted and the firmware-level command is purely
# disruptive. Flip to False + redeploy to enable (deliberate friction).
BEACON_SPAM_RANDOM_REFUSE = True

# Same reasoning for Rick Astley beacon spam. Flip plus add a wildcard
# `marauder.wifi[].ssid: "*"` allowlist entry to enable.
BEACON_SPAM_RICKROLL_REFUSE = True
```

- [ ] **Step 3: Run existing config tests to verify sanity-check still passes**

Run: `pytest tests/test_config.py -v 2>/dev/null || python3 -c "import sys; sys.path.insert(0,'src'); import config; print('SERVICES count:', len(config.SERVICES)); print('FOOT_ONLY count:', len(config.FOOT_ONLY_SERVICES))"`

Expected: `SERVICES count` increases by 1; the `_classified` assertion in `config.py:977` does not fire.

- [ ] **Step 4: Commit**

```bash
git -C /home/kali/drifter add src/config.py
git -C /home/kali/drifter commit -m "$(cat <<'EOF'
feat(marauder): register drifter-marauder in config.py FOOT_ONLY_SERVICES

Plus BEACON_SPAM_RANDOM_REFUSE and BEACON_SPAM_RICKROLL_REFUSE feature
flags (default True — see spec §7). /healthz auto-picks up the new
service as expected-when-foot, hw_pending-when-no-hardware.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 0.4: Wire `install.sh` to deploy Marauder config tree + enable service

**Files:**
- Modify: `install.sh` (two blocks — config deployment, SERVICES register)

- [ ] **Step 1: Add the Marauder config tree block**

After the kismet site config block (around line 365 — find the `KISMET_SITE_SRC` block added in commit `bcbfa64`), add:

```bash
# Marauder config tree — operator allowlist + portal templates + beacon lists
mkdir -p /opt/drifter/etc/marauder/portals \
         /opt/drifter/etc/marauder/beacon_lists
chown -R drifter:drifter /opt/drifter/etc/marauder
ok "Marauder config tree at /opt/drifter/etc/marauder/"

# Seed audit_targets.yaml ONLY if it doesn't already exist — never
# overwrite operator scope.
if [ ! -f /opt/drifter/etc/audit_targets.yaml ]; then
    mkdir -p /opt/drifter/etc
    install -m 0640 -o drifter -g drifter \
        "${REPO_DIR}/config/audit_targets.sample.yaml" \
        /opt/drifter/etc/audit_targets.yaml
    ok "audit_targets.yaml seeded (EMPTY — populate before HIGH-risk commands work)"
else
    ok "audit_targets.yaml already present — not overwriting"
fi
```

- [ ] **Step 2: Add `drifter-marauder` to the SERVICES enable list**

Find line 472 (the `SERVICES="drifter-canbridge ..."` block). Append `drifter-marauder`:

```bash
SERVICES="drifter-canbridge drifter-alerts drifter-dashboard drifter-logger drifter-voice drifter-vivi drifter-hotspot drifter-homesync drifter-watchdog drifter-realdash drifter-rf drifter-rfaudio drifter-wardrive drifter-fbmirror drifter-anomaly drifter-analyst drifter-voicein drifter-flipper drifter-opsec drifter-bleconv drifter-gps drifter-batcher drifter-trip drifter-thresholds drifter-reporter drifter-db-checkpoint drifter-boot-reason drifter-marauder"
```

- [ ] **Step 3: Lint shellcheck (if installed)**

Run: `shellcheck install.sh || echo "shellcheck not installed; skipping"`
Expected: no new errors introduced by the additions (existing errors OK).

- [ ] **Step 4: Commit**

```bash
git -C /home/kali/drifter add install.sh
git -C /home/kali/drifter commit -m "$(cat <<'EOF'
feat(marauder): install.sh deploys config tree + enables service

- Creates /opt/drifter/etc/marauder/{portals,beacon_lists}/
- Seeds /opt/drifter/etc/audit_targets.yaml from sample if absent
  (never overwrites operator scope)
- Adds drifter-marauder to SERVICES so systemctl enable picks it up

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 0.5: Create empty source package skeleton (commit a no-op tree so subsequent tasks are pure additions)

**Files:**
- Create: `src/marauder_bridge.py` (placeholder)
- Create: `src/marauder_transport.py` (placeholder)
- Create: `src/marauder_protocol.py` (placeholder)
- Create: `src/marauder_allowlist.py` (placeholder)
- Create: `src/marauder_features/__init__.py` (empty)
- Create: `src/marauder_features/passive.py` (placeholder)
- Create: `src/marauder_features/active_wifi.py` (placeholder)
- Create: `src/marauder_features/ble.py` (placeholder)
- Create: `src/marauder_features/evilportal.py` (placeholder)

- [ ] **Step 1: Create each module with a docstring and a single `raise NotImplementedError` exit guard**

For each `src/marauder_*.py` and `src/marauder_features/*.py` (except `__init__.py`), write:

```python
"""MZ1312 DRIFTER — Marauder bridge module: <one-line purpose>.

See docs/superpowers/specs/2026-05-24-marauder-bridge-design.md for
the contract this module implements. Implementation lands across the
plan in docs/superpowers/plans/2026-05-24-marauder-bridge.md.
"""

if __name__ == "__main__":
    raise NotImplementedError(
        "drifter-marauder scaffold — module not yet implemented; see plan"
    )
```

Tailor the one-line purpose per module:

- `marauder_bridge.py`: `service entry point, main loop, command dispatch`
- `marauder_transport.py`: `transport autodetect + serial I/O`
- `marauder_protocol.py`: `Marauder CLI command builders + line-event parser`
- `marauder_allowlist.py`: `allowlist load + per-category scope gating`
- `marauder_features/passive.py`: `passive recon (scanap/scansta/sniffprobe)`
- `marauder_features/active_wifi.py`: `active Wi-Fi (deauth/beacon/probe-flood)`
- `marauder_features/ble.py`: `BLE recon + spam`
- `marauder_features/evilportal.py`: `rogue AP + portal + cred capture`

For `marauder_features/__init__.py`: empty file (zero bytes).

- [ ] **Step 2: Verify all modules import without error**

Run:
```bash
PYTHONPATH=/home/kali/drifter/src python3 -c "
import marauder_bridge, marauder_transport, marauder_protocol, marauder_allowlist
import marauder_features
from marauder_features import passive, active_wifi, ble, evilportal
print('all imports OK')
"
```
Expected: `all imports OK`

- [ ] **Step 3: Commit**

```bash
git -C /home/kali/drifter add src/marauder_bridge.py src/marauder_transport.py src/marauder_protocol.py src/marauder_allowlist.py src/marauder_features/__init__.py src/marauder_features/passive.py src/marauder_features/active_wifi.py src/marauder_features/ble.py src/marauder_features/evilportal.py
git -C /home/kali/drifter commit -m "$(cat <<'EOF'
chore(marauder): scaffold empty source modules

Placeholder modules + features package. Each module raises
NotImplementedError if run directly. Subsequent tasks fill them in
test-first.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Phase 1 — Core infrastructure + passive recon

Builds the protocol layer, transport autodetect, allowlist module, MQTT bridge, HTTP API, and the passive scan feature. End state: `drifter-marauder` runs, autodetects hardware (or stays idle), and operator can `POST /api/marauder/cmd {command:"scan",mode:"ap"}` to get real AP events on `drifter/marauder/scan/ap`.

---

### Task 1.1: Protocol — passive command builders (test-first)

**Files:**
- Modify: `src/marauder_protocol.py`
- Create: `tests/test_marauder_protocol.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_marauder_protocol.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import marauder_protocol as mp


class TestPassiveBuilders:
    def test_cmd_scan_ap(self):
        assert mp.cmd_scan_ap() == "scanap\r\n"

    def test_cmd_scan_sta(self):
        assert mp.cmd_scan_sta() == "scansta\r\n"

    def test_cmd_scan_probes(self):
        assert mp.cmd_scan_probes() == "sniffprobe\r\n"

    def test_cmd_stop(self):
        assert mp.cmd_stop() == "stopscan\r\n"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_marauder_protocol.py -v`
Expected: 4 FAIL with `AttributeError: module 'marauder_protocol' has no attribute 'cmd_scan_ap'`

- [ ] **Step 3: Implement the builders**

Replace the body of `src/marauder_protocol.py` with:

```python
"""MZ1312 DRIFTER — Marauder bridge module: Marauder CLI command builders + line-event parser.

See docs/superpowers/specs/2026-05-24-marauder-bridge-design.md §3.
"""

# ── Command builders ──────────────────────────────────────────────────
# Pure functions. No I/O. The transport layer is responsible for
# actually writing these strings to the serial port.

def cmd_scan_ap() -> str:
    return "scanap\r\n"


def cmd_scan_sta() -> str:
    return "scansta\r\n"


def cmd_scan_probes() -> str:
    return "sniffprobe\r\n"


def cmd_stop() -> str:
    return "stopscan\r\n"


if __name__ == "__main__":
    raise NotImplementedError("marauder_protocol is a library; import don't run")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_marauder_protocol.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git -C /home/kali/drifter add tests/test_marauder_protocol.py src/marauder_protocol.py
git -C /home/kali/drifter commit -m "$(cat <<'EOF'
feat(marauder): protocol — passive command builders

scanap, scansta, sniffprobe, stopscan. Pure string builders, no I/O.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 1.2: Protocol — event parser regex table + parse_event scaffold

**Files:**
- Modify: `src/marauder_protocol.py`
- Modify: `tests/test_marauder_protocol.py`

- [ ] **Step 1: Write the failing tests for parser scaffold**

Append to `tests/test_marauder_protocol.py`:

```python
class TestEventParserScaffold:
    def test_parse_event_unknown_line_returns_unknown_type(self):
        """Unknown lines must return {type:'unknown', raw:...}, NOT None.
        This makes firmware drift observable instead of silent."""
        result = mp.parse_event("some line we have never seen before xyzzy")
        assert result == {"type": "unknown", "raw": "some line we have never seen before xyzzy"}

    def test_parse_event_empty_line_returns_none(self):
        """Empty / whitespace-only lines are pure noise — return None."""
        assert mp.parse_event("") is None
        assert mp.parse_event("   ") is None
        assert mp.parse_event("\r\n") is None

    def test_parse_event_strips_trailing_whitespace(self):
        result = mp.parse_event("some line we have never seen before xyzzy\r\n")
        assert result == {"type": "unknown", "raw": "some line we have never seen before xyzzy"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_marauder_protocol.py::TestEventParserScaffold -v`
Expected: 3 FAIL with `AttributeError: module 'marauder_protocol' has no attribute 'parse_event'`

- [ ] **Step 3: Implement parser scaffold**

Append to `src/marauder_protocol.py` (before the `if __name__` block):

```python
# ── Event parser ──────────────────────────────────────────────────────
# Single regex table for all known Marauder line shapes. A firmware
# bump that changes line format is a one-place edit here.
#
# Patterns are (compiled_regex, type_label, group_to_event_func).
# Order matters — first match wins. Put more specific patterns first.

import re
import time

# Filled in by subsequent tasks (parse_ap, parse_sta, parse_probe...).
_PARSERS: list[tuple[re.Pattern, str, "callable"]] = []


def parse_event(line: str) -> dict | None:
    """Parse one line of Marauder serial output.

    Returns:
        - dict with at least {'type': ..., 'ts': float} for known lines
        - {'type': 'unknown', 'raw': line} for lines that match no pattern
        - None for empty / whitespace-only lines
    """
    if line is None:
        return None
    stripped = line.strip()
    if not stripped:
        return None

    for pattern, type_label, builder in _PARSERS:
        m = pattern.match(stripped)
        if m:
            ev = builder(m)
            ev.setdefault("type", type_label)
            ev.setdefault("ts", time.time())
            return ev

    return {"type": "unknown", "raw": stripped}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_marauder_protocol.py -v`
Expected: 7 PASS (4 from Task 1.1 + 3 scaffold)

- [ ] **Step 5: Commit**

```bash
git -C /home/kali/drifter add tests/test_marauder_protocol.py src/marauder_protocol.py
git -C /home/kali/drifter commit -m "$(cat <<'EOF'
feat(marauder): protocol — event parser scaffold

parse_event() returns 'unknown' for unmatched lines (never None) so
firmware drift is observable. Empty lines return None. _PARSERS table
is empty for now; each event type adds a row in subsequent tasks.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 1.3: Protocol — parse AP scan events

**Files:**
- Modify: `src/marauder_protocol.py`
- Modify: `tests/test_marauder_protocol.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_marauder_protocol.py`:

```python
class TestParseAP:
    def test_parse_ap_typical_line(self):
        line = "RSSI: -67 Ch: 6 BSSID: aa:bb:cc:dd:ee:ff ESSID: CoffeeShop"
        ev = mp.parse_event(line)
        assert ev["type"] == "ap"
        assert ev["rssi"] == -67
        assert ev["ch"] == 6
        assert ev["bssid"] == "aa:bb:cc:dd:ee:ff"
        assert ev["ssid"] == "CoffeeShop"
        assert "ts" in ev

    def test_parse_ap_ssid_with_spaces(self):
        line = "RSSI: -45 Ch: 11 BSSID: 11:22:33:44:55:66 ESSID: My Home Wi-Fi 5GHz"
        ev = mp.parse_event(line)
        assert ev["type"] == "ap"
        assert ev["ssid"] == "My Home Wi-Fi 5GHz"

    def test_parse_ap_hidden_ssid(self):
        """Marauder shows hidden SSIDs as empty string after ESSID:"""
        line = "RSSI: -82 Ch: 1 BSSID: 99:88:77:66:55:44 ESSID: "
        ev = mp.parse_event(line)
        assert ev["type"] == "ap"
        assert ev["ssid"] == ""

    def test_parse_ap_negative_rssi_bounds(self):
        ev = mp.parse_event("RSSI: -100 Ch: 13 BSSID: aa:bb:cc:dd:ee:ff ESSID: x")
        assert ev["rssi"] == -100
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_marauder_protocol.py::TestParseAP -v`
Expected: 4 FAIL (currently returns `{type:"unknown"}`)

- [ ] **Step 3: Implement the AP parser**

In `src/marauder_protocol.py`, replace `_PARSERS: list[...] = []` with:

```python
_RE_AP = re.compile(
    r"^RSSI:\s*(?P<rssi>-?\d+)\s+"
    r"Ch:\s*(?P<ch>\d+)\s+"
    r"BSSID:\s*(?P<bssid>[0-9a-fA-F:]{17})\s+"
    r"ESSID:\s?(?P<ssid>.*?)$"
)


def _build_ap(m: re.Match) -> dict:
    return {
        "rssi": int(m.group("rssi")),
        "ch": int(m.group("ch")),
        "bssid": m.group("bssid").lower(),
        "ssid": m.group("ssid"),
    }


_PARSERS: list[tuple[re.Pattern, str, "callable"]] = [
    (_RE_AP, "ap", _build_ap),
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_marauder_protocol.py -v`
Expected: 11 PASS

- [ ] **Step 5: Commit**

```bash
git -C /home/kali/drifter add tests/test_marauder_protocol.py src/marauder_protocol.py
git -C /home/kali/drifter commit -m "$(cat <<'EOF'
feat(marauder): protocol — parse AP scan events

scanap output: RSSI/Ch/BSSID/ESSID. Hidden SSIDs surface as empty
string. BSSIDs normalized to lowercase.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 1.4: Protocol — parse station scan events

**Files:**
- Modify: `src/marauder_protocol.py`
- Modify: `tests/test_marauder_protocol.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_marauder_protocol.py`:

```python
class TestParseSTA:
    def test_parse_sta_typical_line(self):
        line = "RSSI: -82 BSSID: aa:bb:cc:dd:ee:ff STA: 11:22:33:44:55:66 ESSID: CoffeeShop"
        ev = mp.parse_event(line)
        assert ev["type"] == "station"
        assert ev["rssi"] == -82
        assert ev["ap_bssid"] == "aa:bb:cc:dd:ee:ff"
        assert ev["sta_mac"] == "11:22:33:44:55:66"
        assert ev["ssid"] == "CoffeeShop"

    def test_parse_sta_does_not_match_ap_pattern(self):
        """STA lines lack 'Ch:' — must not be claimed by the AP parser."""
        line = "RSSI: -82 BSSID: aa:bb:cc:dd:ee:ff STA: 11:22:33:44:55:66 ESSID: x"
        ev = mp.parse_event(line)
        assert ev["type"] == "station"  # not 'ap'
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_marauder_protocol.py::TestParseSTA -v`
Expected: 2 FAIL

- [ ] **Step 3: Implement the STA parser**

In `src/marauder_protocol.py`, add the regex + builder before the `_PARSERS` list and insert into the list:

```python
_RE_STA = re.compile(
    r"^RSSI:\s*(?P<rssi>-?\d+)\s+"
    r"BSSID:\s*(?P<ap_bssid>[0-9a-fA-F:]{17})\s+"
    r"STA:\s*(?P<sta_mac>[0-9a-fA-F:]{17})\s+"
    r"ESSID:\s?(?P<ssid>.*?)$"
)


def _build_sta(m: re.Match) -> dict:
    return {
        "rssi": int(m.group("rssi")),
        "ap_bssid": m.group("ap_bssid").lower(),
        "sta_mac": m.group("sta_mac").lower(),
        "ssid": m.group("ssid"),
    }
```

Update the `_PARSERS` list — STA goes BEFORE AP because both start with `RSSI:` and STA has a more specific shape:

```python
_PARSERS: list[tuple[re.Pattern, str, "callable"]] = [
    (_RE_STA, "station", _build_sta),
    (_RE_AP, "ap", _build_ap),
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_marauder_protocol.py -v`
Expected: 13 PASS (no regression in AP tests — STA's pattern requires `STA:` which AP lines don't have)

- [ ] **Step 5: Commit**

```bash
git -C /home/kali/drifter add tests/test_marauder_protocol.py src/marauder_protocol.py
git -C /home/kali/drifter commit -m "$(cat <<'EOF'
feat(marauder): protocol — parse station scan events

scansta output: RSSI/BSSID/STA/ESSID. Listed before AP in _PARSERS
because both start with RSSI: but STA requires the STA: token.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 1.5: Protocol — parse probe-request events

**Files:**
- Modify: `src/marauder_protocol.py`
- Modify: `tests/test_marauder_protocol.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_marauder_protocol.py`:

```python
class TestParseProbe:
    def test_parse_probe_typical_line(self):
        line = 'Probe req: 11:22:33:44:55:66 -> "MyHomeWifi"'
        ev = mp.parse_event(line)
        assert ev["type"] == "probe"
        assert ev["sta_mac"] == "11:22:33:44:55:66"
        assert ev["looking_for_ssid"] == "MyHomeWifi"

    def test_parse_probe_with_arrow_unicode(self):
        """Some Marauder builds emit U+2192 (→), others ASCII ->. Both must parse."""
        line = 'Probe req: aa:bb:cc:dd:ee:ff → "Starbucks WiFi"'
        ev = mp.parse_event(line)
        assert ev["type"] == "probe"
        assert ev["looking_for_ssid"] == "Starbucks WiFi"

    def test_parse_probe_empty_ssid(self):
        line = 'Probe req: aa:bb:cc:dd:ee:ff -> ""'
        ev = mp.parse_event(line)
        assert ev["type"] == "probe"
        assert ev["looking_for_ssid"] == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_marauder_protocol.py::TestParseProbe -v`
Expected: 3 FAIL

- [ ] **Step 3: Implement the probe parser**

Add to `src/marauder_protocol.py`:

```python
_RE_PROBE = re.compile(
    r"^Probe req:\s*(?P<sta_mac>[0-9a-fA-F:]{17})\s*"
    r"(?:->|→)\s*"
    r'"(?P<ssid>.*)"\s*$'
)


def _build_probe(m: re.Match) -> dict:
    return {
        "sta_mac": m.group("sta_mac").lower(),
        "looking_for_ssid": m.group("ssid"),
    }
```

Add `(_RE_PROBE, "probe", _build_probe)` to `_PARSERS` (order with the others doesn't matter — different prefix).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_marauder_protocol.py -v`
Expected: 16 PASS

- [ ] **Step 5: Commit**

```bash
git -C /home/kali/drifter add tests/test_marauder_protocol.py src/marauder_protocol.py
git -C /home/kali/drifter commit -m "$(cat <<'EOF'
feat(marauder): protocol — parse probe-request events

sniffprobe output: "Probe req: MAC -> 'SSID'". Accepts both ASCII ->
and Unicode → arrow (firmware build variants).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 1.6: Allowlist — load YAML

**Files:**
- Modify: `src/marauder_allowlist.py`
- Create: `tests/test_marauder_allowlist.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_marauder_allowlist.py
import sys
import textwrap
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import marauder_allowlist as ma


class TestLoadAllowlist:
    def test_load_missing_file_returns_empty(self, tmp_path):
        result = ma.load_marauder_allowlist(tmp_path / "nonexistent.yaml")
        assert result == {"wifi": [], "ble": [], "evilportal": []}

    def test_load_empty_marauder_block(self, tmp_path):
        p = tmp_path / "audit.yaml"
        p.write_text("networks: []\nmarauder:\n  wifi: []\n  ble: []\n  evilportal: []\n")
        result = ma.load_marauder_allowlist(p)
        assert result == {"wifi": [], "ble": [], "evilportal": []}

    def test_load_no_marauder_block_at_all(self, tmp_path):
        """If audit_targets.yaml has only the legacy wifi-audit 'networks'
        key (older deploys), marauder treats it as fully-empty scope."""
        p = tmp_path / "audit.yaml"
        p.write_text("networks: []\n")
        result = ma.load_marauder_allowlist(p)
        assert result == {"wifi": [], "ble": [], "evilportal": []}

    def test_load_populated_wifi(self, tmp_path):
        p = tmp_path / "audit.yaml"
        p.write_text(textwrap.dedent("""
            marauder:
              wifi:
                - ssid: "ACME-Pentest"
                - bssid: "aa:bb:cc:dd:ee:ff"
              ble: []
              evilportal: []
        """))
        result = ma.load_marauder_allowlist(p)
        assert result["wifi"] == [
            {"ssid": "ACME-Pentest"},
            {"bssid": "aa:bb:cc:dd:ee:ff"},
        ]

    def test_load_malformed_yaml_returns_empty(self, tmp_path):
        """Malformed YAML must NOT crash the service. Empty scope is safe."""
        p = tmp_path / "audit.yaml"
        p.write_text("marauder:\n  wifi:\n    - {malformed")
        result = ma.load_marauder_allowlist(p)
        assert result == {"wifi": [], "ble": [], "evilportal": []}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_marauder_allowlist.py -v`
Expected: 5 FAIL with `AttributeError: module 'marauder_allowlist' has no attribute 'load_marauder_allowlist'`

- [ ] **Step 3: Implement the loader**

Replace the body of `src/marauder_allowlist.py`:

```python
"""MZ1312 DRIFTER — Marauder bridge module: allowlist load + per-category scope gating.

See docs/superpowers/specs/2026-05-24-marauder-bridge-design.md §5.3.
"""

import logging
import os
from pathlib import Path

log = logging.getLogger("marauder.allowlist")

ALLOWLIST_PATH = Path(os.environ.get(
    "MARAUDER_ALLOWLIST", "/opt/drifter/etc/audit_targets.yaml"
))

_EMPTY = {"wifi": [], "ble": [], "evilportal": []}


def load_marauder_allowlist(path: Path | str | None = None) -> dict:
    """Load marauder allowlist from audit_targets.yaml.

    Returns dict with keys 'wifi', 'ble', 'evilportal', each a list of
    entry dicts. Missing file / missing 'marauder:' block / malformed YAML
    all return {wifi:[], ble:[], evilportal:[]} — empty scope is safe.
    """
    p = Path(path) if path else ALLOWLIST_PATH
    if not p.exists():
        log.warning("allowlist not found at %s — treating as empty", p)
        return dict(_EMPTY)

    try:
        import yaml
    except ImportError:
        log.error("PyYAML missing — cannot parse allowlist; treating as empty")
        return dict(_EMPTY)

    try:
        with p.open() as fh:
            data = yaml.safe_load(fh) or {}
    except yaml.YAMLError as e:
        log.error("allowlist YAML parse error: %s — treating as empty", e)
        return dict(_EMPTY)

    block = (data or {}).get("marauder") or {}
    return {
        "wifi": list(block.get("wifi") or []),
        "ble": list(block.get("ble") or []),
        "evilportal": list(block.get("evilportal") or []),
    }


if __name__ == "__main__":
    raise NotImplementedError("marauder_allowlist is a library; import don't run")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_marauder_allowlist.py -v`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git -C /home/kali/drifter add tests/test_marauder_allowlist.py src/marauder_allowlist.py
git -C /home/kali/drifter commit -m "$(cat <<'EOF'
feat(marauder): allowlist — YAML loader

Reads /opt/drifter/etc/audit_targets.yaml, returns the marauder block
as {wifi, ble, evilportal} lists. Missing file / missing block /
malformed YAML all return empty scope (fail-safe — refuses every HIGH
command rather than crashing the service).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 1.7: Allowlist — `is_target_allowed` for wifi category

**Files:**
- Modify: `src/marauder_allowlist.py`
- Modify: `tests/test_marauder_allowlist.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_marauder_allowlist.py`:

```python
class TestIsTargetAllowedWifi:
    def _scope(self, wifi_entries):
        return {"wifi": wifi_entries, "ble": [], "evilportal": []}

    def test_empty_wifi_refuses_everything(self):
        ok, reason = ma.is_target_allowed(
            self._scope([]), "wifi", ssid="anything", bssid="aa:bb:cc:dd:ee:ff"
        )
        assert ok is False
        assert "empty" in reason.lower()

    def test_ssid_match_allows(self):
        ok, reason = ma.is_target_allowed(
            self._scope([{"ssid": "ACME-Pentest"}]),
            "wifi", ssid="ACME-Pentest", bssid="aa:bb:cc:dd:ee:ff",
        )
        assert ok is True
        assert reason == "matched ssid=ACME-Pentest"

    def test_bssid_match_allows(self):
        ok, _ = ma.is_target_allowed(
            self._scope([{"bssid": "aa:bb:cc:dd:ee:ff"}]),
            "wifi", ssid="WhateverSSID", bssid="aa:bb:cc:dd:ee:ff",
        )
        assert ok is True

    def test_bssid_match_is_case_insensitive(self):
        ok, _ = ma.is_target_allowed(
            self._scope([{"bssid": "AA:BB:CC:DD:EE:FF"}]),
            "wifi", ssid="x", bssid="aa:bb:cc:dd:ee:ff",
        )
        assert ok is True

    def test_no_match_refuses(self):
        ok, reason = ma.is_target_allowed(
            self._scope([{"ssid": "ACME-Pentest"}]),
            "wifi", ssid="SomeoneElsesWiFi", bssid="99:88:77:66:55:44",
        )
        assert ok is False
        assert "no match" in reason.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_marauder_allowlist.py::TestIsTargetAllowedWifi -v`
Expected: 5 FAIL

- [ ] **Step 3: Implement `is_target_allowed`**

Append to `src/marauder_allowlist.py` (before the `if __name__` block):

```python
def is_target_allowed(
    scope: dict, category: str, **fields
) -> tuple[bool, str]:
    """Check whether a target falls inside the allowlist scope.

    Args:
        scope: result of load_marauder_allowlist()
        category: 'wifi' | 'ble' | 'evilportal'
        **fields: category-specific (ssid, bssid, mac, template, ...)

    Returns:
        (allowed: bool, reason: str)
    """
    entries = scope.get(category, [])
    if not entries:
        return False, f"allowlist empty for category={category}"

    if category == "wifi":
        return _check_wifi(entries, fields)
    if category == "ble":
        return _check_ble(entries, fields)
    if category == "evilportal":
        return _check_evilportal(entries, fields)
    return False, f"unknown allowlist category={category}"


def _check_wifi(entries: list[dict], fields: dict) -> tuple[bool, str]:
    ssid = fields.get("ssid", "")
    bssid = (fields.get("bssid") or "").lower()
    for entry in entries:
        if "ssid" in entry and entry["ssid"] == ssid:
            return True, f"matched ssid={ssid}"
        if "bssid" in entry and entry["bssid"].lower() == bssid:
            return True, f"matched bssid={bssid}"
    return False, "no match in wifi allowlist"


def _check_ble(entries: list[dict], fields: dict) -> tuple[bool, str]:
    # Implemented in Task 3.x — stub returns refuse for now.
    return False, "ble allowlist check not yet implemented"


def _check_evilportal(entries: list[dict], fields: dict) -> tuple[bool, str]:
    # Implemented in Task 4.x — stub returns refuse for now.
    return False, "evilportal allowlist check not yet implemented"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_marauder_allowlist.py -v`
Expected: 10 PASS (5 loader + 5 wifi gate)

- [ ] **Step 5: Commit**

```bash
git -C /home/kali/drifter add tests/test_marauder_allowlist.py src/marauder_allowlist.py
git -C /home/kali/drifter commit -m "$(cat <<'EOF'
feat(marauder): allowlist — is_target_allowed for wifi category

SSID match, BSSID match (case-insensitive), empty-scope refuse, no-match
refuse. BLE + EvilPortal check stubs refuse-by-default until implemented
in their respective phases.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 1.8: Transport — known VID:PID table + serial port enumeration

**Files:**
- Modify: `src/marauder_transport.py`
- Create: `tests/test_marauder_transport.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_marauder_transport.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import marauder_transport as mt


class TestEnumerateCandidates:
    def test_known_vidpids_includes_esp32_s2(self):
        assert ("303a", "1001") in mt.KNOWN_MARAUDER_VIDPIDS

    def test_known_vidpids_includes_esp32_s3(self):
        assert ("303a", "1014") in mt.KNOWN_MARAUDER_VIDPIDS

    def test_known_vidpids_includes_cp210x(self):
        assert ("10c4", "ea60") in mt.KNOWN_MARAUDER_VIDPIDS

    def test_enumerate_returns_list_of_paths_when_dir_empty(self, tmp_path):
        """No serial devices → empty list, no crash."""
        result = mt.enumerate_serial_candidates(by_id_dir=tmp_path)
        assert result == []

    def test_enumerate_finds_matching_vidpid_symlinks(self, tmp_path):
        """Symlink names contain VID:PID in the form 'usb-VVVV_PPPP_*'."""
        # Marauder ESP32-S2 fake symlink
        (tmp_path / "usb-Espressif_USB_JTAG_serial_debug_unit_303a_1001_FF-if00").symlink_to(
            "/dev/null"
        )
        # CP210x fake symlink
        (tmp_path / "usb-Silicon_Labs_CP2102N_USB_to_UART_Bridge_Controller_10c4_ea60_AB-if00-port0").symlink_to(
            "/dev/null"
        )
        # Non-matching (Logitech receiver)
        (tmp_path / "usb-Logitech_046d_c534-event-mouse").symlink_to(
            "/dev/null"
        )
        result = mt.enumerate_serial_candidates(by_id_dir=tmp_path)
        assert len(result) == 2
        # Returns absolute path strings, sorted for determinism
        assert all("/dev/null" not in p for p in result)  # symlink target stripped
        names = {Path(p).name for p in result}
        assert any("303a_1001" in n for n in names)
        assert any("10c4_ea60" in n for n in names)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_marauder_transport.py -v`
Expected: 5 FAIL

- [ ] **Step 3: Implement VID:PID table + enumerator**

Replace the body of `src/marauder_transport.py`:

```python
"""MZ1312 DRIFTER — Marauder bridge module: transport autodetect + serial I/O.

See docs/superpowers/specs/2026-05-24-marauder-bridge-design.md §2.
"""

import logging
import re
from pathlib import Path

log = logging.getLogger("marauder.transport")

# Known VID:PID pairs for Marauder-flashable boards.
# Format: (vid_hex_lowercase, pid_hex_lowercase). See spec §2.
KNOWN_MARAUDER_VIDPIDS: list[tuple[str, str]] = [
    ("303a", "1001"),  # Espressif ESP32-S2
    ("303a", "1014"),  # Espressif ESP32-S3
    ("10c4", "ea60"),  # Silicon Labs CP210x (common dev-board USB-UART)
]

_BY_ID = Path("/dev/serial/by-id")

# Symlink names look like: usb-Vendor_Product_VVVV_PPPP_SERIAL-...
_RE_VIDPID = re.compile(r"_([0-9a-f]{4})_([0-9a-f]{4})_", re.IGNORECASE)


def enumerate_serial_candidates(by_id_dir: Path | None = None) -> list[str]:
    """Return absolute paths of serial devices whose VID:PID matches a
    known Marauder board. Sorted for deterministic probe order.
    """
    d = by_id_dir if by_id_dir is not None else _BY_ID
    if not d.exists():
        return []
    out: list[str] = []
    for entry in sorted(d.iterdir()):
        m = _RE_VIDPID.search(entry.name)
        if not m:
            continue
        vid, pid = m.group(1).lower(), m.group(2).lower()
        if (vid, pid) in KNOWN_MARAUDER_VIDPIDS:
            out.append(str(entry))
    return out


if __name__ == "__main__":
    raise NotImplementedError("marauder_transport is a library; import don't run")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_marauder_transport.py -v`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git -C /home/kali/drifter add tests/test_marauder_transport.py src/marauder_transport.py
git -C /home/kali/drifter commit -m "$(cat <<'EOF'
feat(marauder): transport — VID:PID table + serial enumerator

Scans /dev/serial/by-id/ for ESP32-S2 / ESP32-S3 / CP210x symlinks.
Returns sorted absolute paths for deterministic probe order. Non-
matching devices (e.g. Logitech receiver) are skipped.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 1.9: Transport — direct USB probe (open + banner check)

**Files:**
- Modify: `src/marauder_transport.py`
- Modify: `tests/test_marauder_transport.py`

- [ ] **Step 1: Write the failing tests** (pty-based loopback)

Append to `tests/test_marauder_transport.py`:

```python
import os
import pty
import threading
import time


class TestProbeDirect:
    def _pty_pair(self, fake_response: bytes, delay: float = 0.05):
        """Open a pty pair. Spawn a thread that, when the test side reads
        a request line, writes fake_response after a small delay."""
        master, slave = pty.openpty()
        slave_path = os.ttyname(slave)

        def responder():
            # Wait for any write from device side (the probe sends stopscan\r\n)
            try:
                os.read(master, 256)
            except OSError:
                return
            time.sleep(delay)
            os.write(master, fake_response)

        t = threading.Thread(target=responder, daemon=True)
        t.start()
        return slave_path, master, t

    def test_probe_direct_finds_marauder_banner(self):
        slave_path, master, _ = self._pty_pair(
            b"Marauder v0.13.4 ready\r\n>\r\n"
        )
        try:
            ok, detail = mt.probe_direct(slave_path, timeout=1.0)
            assert ok is True
            assert "Marauder" in detail or "ESP32" in detail
        finally:
            os.close(master)

    def test_probe_direct_finds_esp32_banner(self):
        slave_path, master, _ = self._pty_pair(b"ESP32 chip waking up\r\n>")
        try:
            ok, _ = mt.probe_direct(slave_path, timeout=1.0)
            assert ok is True
        finally:
            os.close(master)

    def test_probe_direct_rejects_unrelated_device(self):
        slave_path, master, _ = self._pty_pair(b"GPS fix: $GPGGA,...\r\n")
        try:
            ok, _ = mt.probe_direct(slave_path, timeout=1.0)
            assert ok is False
        finally:
            os.close(master)

    def test_probe_direct_handles_no_response(self):
        slave_path, master, _ = self._pty_pair(b"", delay=2.0)
        try:
            ok, detail = mt.probe_direct(slave_path, timeout=0.5)
            assert ok is False
            assert "timeout" in detail.lower() or "no response" in detail.lower()
        finally:
            os.close(master)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_marauder_transport.py::TestProbeDirect -v`
Expected: 4 FAIL with `AttributeError: module 'marauder_transport' has no attribute 'probe_direct'`

- [ ] **Step 3: Implement `probe_direct`**

Append to `src/marauder_transport.py`:

```python
import time

try:
    import serial  # pyserial
except ImportError:
    serial = None


def probe_direct(port_path: str, timeout: float = 0.5) -> tuple[bool, str]:
    """Open a serial port, send stopscan, look for Marauder/ESP32 banner.

    Returns (matched, detail).
    """
    if serial is None:
        return False, "pyserial not installed"

    try:
        ser = serial.Serial(port_path, baudrate=115200, timeout=timeout)
    except (OSError, serial.SerialException) as e:
        return False, f"open failed: {e}"

    try:
        ser.write(b"stopscan\r\n")
        ser.flush()
        # Read up to timeout, accumulating bytes; accept on first banner hit.
        deadline = time.monotonic() + timeout
        buf = b""
        while time.monotonic() < deadline:
            chunk = ser.read(128)
            if chunk:
                buf += chunk
                if b"Marauder" in buf or b"ESP32" in buf or b">" in buf:
                    # Strip control bytes for the detail field
                    detail = buf.decode("utf-8", errors="replace").strip()[:160]
                    return True, detail
        return False, "no response (timeout)"
    finally:
        try:
            ser.close()
        except Exception:
            pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_marauder_transport.py -v`
Expected: 9 PASS

- [ ] **Step 5: Commit**

```bash
git -C /home/kali/drifter add tests/test_marauder_transport.py src/marauder_transport.py
git -C /home/kali/drifter commit -m "$(cat <<'EOF'
feat(marauder): transport — direct USB banner probe

Opens port at 115200 8N1, sends stopscan, accepts on Marauder / ESP32
/ '>' prompt in response within timeout. Rejects unrelated devices
(GPS, etc.) and returns clear timeout reason.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 1.10: Transport — Flipper proxy probe

**Files:**
- Modify: `src/marauder_transport.py`
- Modify: `tests/test_marauder_transport.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_marauder_transport.py`:

```python
from unittest.mock import patch, MagicMock


class TestProbeFlipperProxy:
    def test_probe_proxy_finds_module_via_hardware_endpoint(self):
        fake_response = MagicMock(status_code=200)
        fake_response.json.return_value = {"marauder_module_present": True,
                                            "module": "marauder",
                                            "capabilities": ["wifi", "ble"]}
        with patch("marauder_transport.requests.get", return_value=fake_response):
            ok, detail = mt.probe_flipper_proxy("http://127.0.0.1:8080")
            assert ok is True
            assert "marauder" in detail.lower()

    def test_probe_proxy_module_absent(self):
        fake_response = MagicMock(status_code=200)
        fake_response.json.return_value = {"marauder_module_present": False,
                                            "module": "none"}
        with patch("marauder_transport.requests.get", return_value=fake_response):
            ok, _ = mt.probe_flipper_proxy("http://127.0.0.1:8080")
            assert ok is False

    def test_probe_proxy_dashboard_unreachable(self):
        with patch("marauder_transport.requests.get",
                   side_effect=ConnectionError("refused")):
            ok, detail = mt.probe_flipper_proxy("http://127.0.0.1:8080")
            assert ok is False
            assert "unreachable" in detail.lower() or "refused" in detail.lower()

    def test_probe_proxy_dashboard_http_error(self):
        fake_response = MagicMock(status_code=500)
        with patch("marauder_transport.requests.get", return_value=fake_response):
            ok, detail = mt.probe_flipper_proxy("http://127.0.0.1:8080")
            assert ok is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_marauder_transport.py::TestProbeFlipperProxy -v`
Expected: 4 FAIL with `AttributeError: module 'marauder_transport' has no attribute 'probe_flipper_proxy'`

- [ ] **Step 3: Implement `probe_flipper_proxy`**

Append to `src/marauder_transport.py`:

```python
try:
    import requests
except ImportError:
    requests = None


def probe_flipper_proxy(
    dashboard_base_url: str = "http://127.0.0.1:8080",
    timeout: float = 1.5,
) -> tuple[bool, str]:
    """Query drifter-flipper's /api/flipper/hardware. Returns (found, detail)."""
    if requests is None:
        return False, "requests not installed"
    try:
        r = requests.get(f"{dashboard_base_url}/api/flipper/hardware",
                         timeout=timeout)
    except Exception as e:
        return False, f"dashboard unreachable: {e}"
    if r.status_code != 200:
        return False, f"dashboard returned HTTP {r.status_code}"
    try:
        payload = r.json()
    except Exception as e:
        return False, f"dashboard returned non-JSON: {e}"
    if payload.get("marauder_module_present"):
        return True, f"marauder module via flipper: caps={payload.get('capabilities', [])}"
    return False, "marauder module not present on flipper"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_marauder_transport.py -v`
Expected: 13 PASS

- [ ] **Step 5: Commit**

```bash
git -C /home/kali/drifter add tests/test_marauder_transport.py src/marauder_transport.py
git -C /home/kali/drifter commit -m "$(cat <<'EOF'
feat(marauder): transport — Flipper proxy probe

Queries /api/flipper/hardware on drifter-dashboard. Returns OK when
marauder_module_present is true. Connection errors / 5xx / non-JSON
all map to refuse with clear reason.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 1.11: Transport — `MarauderTransport` class with autodetect

**Files:**
- Modify: `src/marauder_transport.py`
- Modify: `tests/test_marauder_transport.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_marauder_transport.py`:

```python
class TestAutodetect:
    def test_autodetect_picks_direct_when_present(self, tmp_path):
        """Direct USB wins over proxy when both present."""
        # Create a fake by-id symlink with a real pty backend
        slave_path, master, _ = TestProbeDirect()._pty_pair(b"Marauder ready>")
        try:
            (tmp_path / "usb-Espressif_303a_1001_FF-if00").symlink_to(slave_path)
            t = mt.MarauderTransport(
                by_id_dir=tmp_path,
                dashboard_url="http://127.0.0.1:8080",
                probe_timeout=1.0,
            )
            t.autodetect()
            assert t.mode == "direct"
            assert t.port_path == str(tmp_path / "usb-Espressif_303a_1001_FF-if00")
        finally:
            os.close(master)

    def test_autodetect_falls_back_to_proxy(self, tmp_path):
        """No direct hardware → tries flipper proxy."""
        fake_response = MagicMock(status_code=200)
        fake_response.json.return_value = {"marauder_module_present": True}
        with patch("marauder_transport.requests.get", return_value=fake_response):
            t = mt.MarauderTransport(by_id_dir=tmp_path,
                                     dashboard_url="http://127.0.0.1:8080")
            t.autodetect()
            assert t.mode == "proxy"

    def test_autodetect_no_hardware(self, tmp_path):
        """Neither direct nor proxy → mode='none'."""
        with patch("marauder_transport.requests.get",
                   side_effect=ConnectionError("refused")):
            t = mt.MarauderTransport(by_id_dir=tmp_path,
                                     dashboard_url="http://127.0.0.1:8080")
            t.autodetect()
            assert t.mode == "none"
            assert t.port_path is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_marauder_transport.py::TestAutodetect -v`
Expected: 3 FAIL with `AttributeError: module 'marauder_transport' has no attribute 'MarauderTransport'`

- [ ] **Step 3: Implement the class**

Append to `src/marauder_transport.py`:

```python
class MarauderTransport:
    """Holds the chosen transport for the session. autodetect() picks
    direct USB or Flipper proxy or 'none'. Subsequent send/receive
    operations dispatch to the chosen path.
    """

    def __init__(
        self,
        by_id_dir: Path | None = None,
        dashboard_url: str = "http://127.0.0.1:8080",
        probe_timeout: float = 1.0,
    ):
        self.by_id_dir = by_id_dir
        self.dashboard_url = dashboard_url
        self.probe_timeout = probe_timeout
        self.mode: str = "none"  # "direct" | "proxy" | "none"
        self.port_path: str | None = None
        self.hw_detail: str = ""
        self._serial = None  # opened on first send for direct mode

    def autodetect(self) -> str:
        """Probe direct first, then proxy. Sets self.mode and returns it."""
        # 1) Direct USB
        for candidate in enumerate_serial_candidates(self.by_id_dir):
            ok, detail = probe_direct(candidate, timeout=self.probe_timeout)
            if ok:
                self.mode = "direct"
                self.port_path = candidate
                self.hw_detail = detail
                log.info("transport=direct port=%s", candidate)
                return self.mode

        # 2) Flipper proxy
        ok, detail = probe_flipper_proxy(self.dashboard_url,
                                          timeout=self.probe_timeout)
        if ok:
            self.mode = "proxy"
            self.port_path = None
            self.hw_detail = detail
            log.info("transport=proxy detail=%s", detail)
            return self.mode

        # 3) Nothing
        self.mode = "none"
        self.port_path = None
        self.hw_detail = "no hardware found (no direct ESP32, no Flipper marauder module)"
        log.warning("transport=none — staying idle")
        return self.mode
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_marauder_transport.py -v`
Expected: 16 PASS

- [ ] **Step 5: Commit**

```bash
git -C /home/kali/drifter add tests/test_marauder_transport.py src/marauder_transport.py
git -C /home/kali/drifter commit -m "$(cat <<'EOF'
feat(marauder): transport — MarauderTransport with autodetect

Probes direct USB candidates first (Marauder firmware on its own dev
board), falls back to Flipper proxy via /api/flipper/hardware. Sticky
session: once mode is set, stays put until autodetect() is called again.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 1.12: Transport — send/receive + reader thread (direct mode)

**Files:**
- Modify: `src/marauder_transport.py`
- Modify: `tests/test_marauder_transport.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_marauder_transport.py`:

```python
import queue


class TestSendReceiveDirect:
    def test_send_command_and_receive_lines(self):
        """send_command writes bytes; reader thread parses lines into queue."""
        master, slave = pty.openpty()
        slave_path = os.ttyname(slave)

        t = mt.MarauderTransport(probe_timeout=0.2)
        t.mode = "direct"
        t.port_path = slave_path
        line_q: queue.Queue[str] = queue.Queue()

        t.start(line_callback=lambda l: line_q.put(l))

        # Simulate Marauder pushing two lines after our command
        def feeder():
            os.read(master, 256)  # consume the command we sent
            time.sleep(0.05)
            os.write(master, b"RSSI: -67 Ch: 6 BSSID: aa:bb:cc:dd:ee:ff ESSID: X\r\n")
            os.write(master, b"RSSI: -55 Ch: 11 BSSID: 11:22:33:44:55:66 ESSID: Y\r\n")

        threading.Thread(target=feeder, daemon=True).start()

        t.send("scanap\r\n")

        # Reader should produce 2 lines
        lines = []
        for _ in range(2):
            try:
                lines.append(line_q.get(timeout=1.0))
            except queue.Empty:
                break

        t.stop()
        os.close(master)

        assert len(lines) == 2
        assert "BSSID: aa:bb:cc:dd:ee:ff" in lines[0]
        assert "BSSID: 11:22:33:44:55:66" in lines[1]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_marauder_transport.py::TestSendReceiveDirect -v`
Expected: FAIL — `start`/`send`/`stop` not defined

- [ ] **Step 3: Implement send/receive**

Append to `src/marauder_transport.py`:

```python
import threading


class MarauderTransport(MarauderTransport):  # type: ignore[no-redef]
    # (re-declared to extend — in actual implementation, add these methods
    # to the existing class body above)
    pass


# Extend the actual class:
def _start(self, line_callback) -> None:
    """Open the serial port (direct mode) and start the reader thread.
    line_callback is invoked with each received line (no trailing \r\n).
    """
    if self.mode != "direct":
        raise RuntimeError(f"start() only supported in direct mode (got {self.mode})")
    if serial is None:
        raise RuntimeError("pyserial not installed")
    self._serial = serial.Serial(self.port_path, baudrate=115200, timeout=0.25)
    self._line_callback = line_callback
    self._stop_event = threading.Event()
    self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
    self._reader_thread.start()


def _stop(self) -> None:
    if getattr(self, "_stop_event", None):
        self._stop_event.set()
    if getattr(self, "_reader_thread", None):
        self._reader_thread.join(timeout=2.0)
    if self._serial:
        try:
            self._serial.close()
        except Exception:
            pass
        self._serial = None


def _send(self, text: str) -> None:
    if self.mode != "direct":
        raise RuntimeError(f"send() only supported in direct mode (got {self.mode})")
    if not self._serial:
        raise RuntimeError("transport not started")
    self._serial.write(text.encode("utf-8"))
    self._serial.flush()


def _read_loop(self) -> None:
    buf = b""
    while not self._stop_event.is_set():
        try:
            chunk = self._serial.read(256)
        except Exception as e:
            log.error("read error: %s", e)
            break
        if not chunk:
            continue
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            try:
                self._line_callback(line.decode("utf-8", errors="replace").rstrip("\r"))
            except Exception:
                log.exception("line callback raised")


MarauderTransport.start = _start
MarauderTransport.stop = _stop
MarauderTransport.send = _send
MarauderTransport._read_loop = _read_loop
```

(Inline note for implementer: in production code, prefer adding these methods directly inside the class body. The split above is to keep this plan task focused on the new methods without re-quoting the entire class.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_marauder_transport.py -v`
Expected: 17 PASS

- [ ] **Step 5: Commit**

```bash
git -C /home/kali/drifter add tests/test_marauder_transport.py src/marauder_transport.py
git -C /home/kali/drifter commit -m "$(cat <<'EOF'
feat(marauder): transport — send/receive + reader thread (direct mode)

start(line_callback) opens the serial port at 115200 and spawns a
daemon reader thread that splits on \n and delivers lines to the
callback. send(text) writes to the port; stop() joins the reader and
closes the port.

Proxy-mode send/receive lands when active features need it (Phase 2).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 1.13: Bridge — risk classifier

**Files:**
- Modify: `src/marauder_bridge.py`
- Create: `tests/test_marauder_classify_risk.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_marauder_classify_risk.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import marauder_bridge as mb


class TestClassifyRisk:
    def test_low_passive_scans(self):
        for cmd in ["scan_ap", "scan_sta", "scan_probes", "stop",
                    "deauth_detect", "ble_scan_all", "ble_scan_airtag",
                    "ble_scan_skim"]:
            assert mb.classify_risk(cmd) == "LOW", f"{cmd} should be LOW"

    def test_med_select_channel(self):
        for cmd in ["select_ap", "channel_hop", "scan_param"]:
            assert mb.classify_risk(cmd) == "MED", f"{cmd} should be MED"

    def test_high_active_attacks(self):
        for cmd in ["deauth_attack", "beacon_spam_list",
                    "beacon_spam_random", "beacon_spam_rickroll",
                    "probe_flood",
                    "ble_spam_swift_pair", "ble_spam_easy_setup",
                    "ble_spam_apple_proximity", "ble_spam_all",
                    "evilportal_start"]:
            assert mb.classify_risk(cmd) == "HIGH", f"{cmd} should be HIGH"

    def test_unknown_defaults_to_high(self):
        """Unknown commands fail closed — treated as HIGH so they get gated."""
        assert mb.classify_risk("totally_made_up_command") == "HIGH"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_marauder_classify_risk.py -v`
Expected: 4 FAIL

- [ ] **Step 3: Implement `classify_risk`**

Replace the body of `src/marauder_bridge.py`:

```python
"""MZ1312 DRIFTER — Marauder bridge module: service entry point, main loop, command dispatch.

See docs/superpowers/specs/2026-05-24-marauder-bridge-design.md §1, §5.2, §6.
"""

import logging
import threading
import time
import uuid

log = logging.getLogger("marauder.bridge")

# Risk tiers per spec §5.2. Unknown commands fail closed (HIGH).
_LOW_RISK = {
    "scan_ap", "scan_sta", "scan_probes", "stop",
    "deauth_detect",
    "ble_scan_all", "ble_scan_airtag", "ble_scan_skim",
    "probe", "status",
}
_MED_RISK = {
    "select_ap", "channel_hop", "scan_param",
}
_HIGH_RISK = {
    "deauth_attack", "beacon_spam_list",
    "beacon_spam_random", "beacon_spam_rickroll",
    "probe_flood",
    "ble_spam_swift_pair", "ble_spam_easy_setup",
    "ble_spam_apple_proximity", "ble_spam_all",
    "evilportal_start", "evilportal_stop",
}


def classify_risk(command: str) -> str:
    """Return 'LOW' | 'MED' | 'HIGH'. Unknown → HIGH (fail closed)."""
    if command in _LOW_RISK:
        return "LOW"
    if command in _MED_RISK:
        return "MED"
    return "HIGH"


if __name__ == "__main__":
    raise NotImplementedError("marauder_bridge main() lands in Task 1.18")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_marauder_classify_risk.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git -C /home/kali/drifter add tests/test_marauder_classify_risk.py src/marauder_bridge.py
git -C /home/kali/drifter commit -m "$(cat <<'EOF'
feat(marauder): bridge — risk classifier (LOW/MED/HIGH)

Per spec §5.2. Unknown commands default to HIGH so they pass through
the confirm + allowlist gates. Passive scans + deauth detection are
LOW (no RF emission). Selects/channel changes are MED. Everything that
emits offensive RF or captures creds is HIGH.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 1.14: Bridge — command lock + pending-confirmation store

**Files:**
- Modify: `src/marauder_bridge.py`
- Create: `tests/test_marauder_bridge.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_marauder_bridge.py
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import marauder_bridge as mb


class TestCommandLock:
    def test_lock_acquire_release(self):
        lock = mb.CommandLock()
        assert lock.try_acquire("scan_ap", "op-uuid-1") is True
        assert lock.held_by() == ("scan_ap", "op-uuid-1")

        # Second acquire fails while held
        assert lock.try_acquire("scan_sta", "op-uuid-2") is False

        lock.release()
        assert lock.held_by() is None

        # Now another acquire works
        assert lock.try_acquire("scan_sta", "op-uuid-2") is True


class TestPendingConfirms:
    def test_register_and_pop_within_window(self):
        store = mb.PendingConfirms(ttl_s=120)
        token = store.register("deauth_attack", {"target": "aa:..."})
        assert isinstance(token, str) and len(token) >= 16
        popped = store.pop(token)
        assert popped == ("deauth_attack", {"target": "aa:..."})

    def test_pop_unknown_token_returns_none(self):
        store = mb.PendingConfirms(ttl_s=120)
        assert store.pop("not-a-real-token") is None

    def test_pop_returns_single_use(self):
        store = mb.PendingConfirms(ttl_s=120)
        token = store.register("x", {})
        assert store.pop(token) == ("x", {})
        assert store.pop(token) is None  # already consumed

    def test_sweep_expires_old_entries(self):
        store = mb.PendingConfirms(ttl_s=0.05)
        token = store.register("x", {})
        time.sleep(0.1)
        store.sweep()
        assert store.pop(token) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_marauder_bridge.py -v`
Expected: 5 FAIL

- [ ] **Step 3: Implement `CommandLock` and `PendingConfirms`**

Append to `src/marauder_bridge.py`:

```python
class CommandLock:
    """Single command at a time. Marauder firmware can't run two
    scans/attacks concurrently."""

    def __init__(self):
        self._lock = threading.Lock()
        self._holder: tuple[str, str] | None = None  # (command, op_uuid)

    def try_acquire(self, command: str, op_uuid: str) -> bool:
        with self._lock:
            if self._holder is not None:
                return False
            self._holder = (command, op_uuid)
            return True

    def release(self) -> None:
        with self._lock:
            self._holder = None

    def held_by(self) -> tuple[str, str] | None:
        with self._lock:
            return self._holder


class PendingConfirms:
    """HIGH-risk command confirmation tokens. Single-use, expire after TTL."""

    def __init__(self, ttl_s: float = 120):
        self._ttl = ttl_s
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[float, str, dict]] = {}  # token → (ts, cmd, args)

    def register(self, command: str, args: dict) -> str:
        token = uuid.uuid4().hex
        with self._lock:
            self._entries[token] = (time.time(), command, args)
        return token

    def pop(self, token: str) -> tuple[str, dict] | None:
        with self._lock:
            entry = self._entries.pop(token, None)
        if entry is None:
            return None
        ts, cmd, args = entry
        if time.time() - ts > self._ttl:
            return None  # expired between register and pop
        return cmd, args

    def sweep(self) -> int:
        """Remove expired entries. Returns count removed."""
        cutoff = time.time() - self._ttl
        removed = 0
        with self._lock:
            stale = [t for t, (ts, _, _) in self._entries.items() if ts < cutoff]
            for t in stale:
                del self._entries[t]
                removed += 1
        return removed
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_marauder_bridge.py -v`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git -C /home/kali/drifter add tests/test_marauder_bridge.py src/marauder_bridge.py
git -C /home/kali/drifter commit -m "$(cat <<'EOF'
feat(marauder): bridge — CommandLock + PendingConfirms

CommandLock enforces one-at-a-time semantics (Marauder firmware
constraint). PendingConfirms stores HIGH-risk command tokens with
120s TTL, single-use semantics. Both thread-safe.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 1.15: Session storage — JSONL writer + sessions index

**Files:**
- Create: `src/marauder_storage.py`
- Create: `tests/test_marauder_storage.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_marauder_storage.py
import json
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import marauder_storage as ms


class TestSessionWriter:
    def test_start_creates_jsonl_file(self, tmp_path):
        s = ms.SessionWriter(state_root=tmp_path)
        sid = s.start(category="scans", mode="ap")
        assert isinstance(sid, str) and len(sid) >= 8
        scan_file = tmp_path / "scans" / f"{sid}.jsonl"
        assert scan_file.exists()

    def test_append_writes_one_jsonl_line_per_event(self, tmp_path):
        s = ms.SessionWriter(state_root=tmp_path)
        sid = s.start(category="scans", mode="ap")
        s.append(sid, {"type": "ap", "bssid": "aa:bb:..", "rssi": -67})
        s.append(sid, {"type": "ap", "bssid": "11:22:..", "rssi": -55})
        scan_file = tmp_path / "scans" / f"{sid}.jsonl"
        lines = scan_file.read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["bssid"] == "aa:bb:.."

    def test_end_writes_index_entry(self, tmp_path):
        s = ms.SessionWriter(state_root=tmp_path)
        sid = s.start(category="scans", mode="ap")
        s.append(sid, {"type": "ap", "bssid": "x", "rssi": -1})
        s.end(sid)
        index = json.loads((tmp_path / "sessions.json").read_text())
        assert any(e["id"] == sid for e in index["sessions"])
        entry = next(e for e in index["sessions"] if e["id"] == sid)
        assert entry["event_count"] == 1
        assert entry["mode"] == "ap"
        assert entry["ended_ts"] is not None
        assert entry["started_ts"] <= entry["ended_ts"]

    def test_double_end_is_noop(self, tmp_path):
        """Second end() must not corrupt the index entry."""
        s = ms.SessionWriter(state_root=tmp_path)
        sid = s.start(category="scans", mode="ap")
        s.end(sid)
        first_end = json.loads((tmp_path / "sessions.json").read_text())["sessions"][0]["ended_ts"]
        time.sleep(0.05)
        s.end(sid)
        second_end = json.loads((tmp_path / "sessions.json").read_text())["sessions"][0]["ended_ts"]
        assert first_end == second_end  # invariant: ended_ts immutable
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_marauder_storage.py -v`
Expected: 4 FAIL

- [ ] **Step 3: Implement `SessionWriter`**

Create `src/marauder_storage.py`:

```python
"""MZ1312 DRIFTER — Marauder bridge module: session JSONL writer + sessions.json index.

See docs/superpowers/specs/2026-05-24-marauder-bridge-design.md §4.3, §6.
"""

import json
import os
import threading
import time
import uuid
from pathlib import Path


class SessionWriter:
    """Writes per-session JSONL files + maintains the sessions.json index.

    Append-only — once a session is end()ed, its index entry's ended_ts
    is immutable (double-end is a no-op).
    """

    def __init__(self, state_root: Path | str = "/opt/drifter/state/marauder"):
        self.root = Path(state_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._open_files: dict[str, "object"] = {}  # session_id → file handle
        self._meta: dict[str, dict] = {}  # session_id → metadata dict

    def start(self, *, category: str, mode: str) -> str:
        """Open a new session. Returns the session_id."""
        sid = uuid.uuid4().hex
        cat_dir = self.root / category
        cat_dir.mkdir(parents=True, exist_ok=True)
        path = cat_dir / f"{sid}.jsonl"
        fh = path.open("a", buffering=1)  # line-buffered
        with self._lock:
            self._open_files[sid] = fh
            self._meta[sid] = {
                "id": sid,
                "category": category,
                "mode": mode,
                "started_ts": time.time(),
                "ended_ts": None,
                "event_count": 0,
                "file_path": str(path),
            }
        return sid

    def append(self, session_id: str, event: dict) -> None:
        with self._lock:
            fh = self._open_files.get(session_id)
            if fh is None:
                return  # session already closed; drop event
            fh.write(json.dumps(event, separators=(",", ":")) + "\n")
            self._meta[session_id]["event_count"] += 1

    def end(self, session_id: str) -> None:
        with self._lock:
            meta = self._meta.get(session_id)
            if not meta or meta["ended_ts"] is not None:
                return  # already ended — no-op
            fh = self._open_files.pop(session_id, None)
            if fh:
                try:
                    fh.flush()
                    fh.close()
                except Exception:
                    pass
            meta["ended_ts"] = time.time()
            self._append_to_index(meta)

    def _append_to_index(self, meta: dict) -> None:
        idx_path = self.root / "sessions.json"
        try:
            existing = json.loads(idx_path.read_text())
        except Exception:
            existing = {"sessions": []}
        # If the session is already in the index, do NOT overwrite (immutable end_ts)
        if any(e["id"] == meta["id"] for e in existing["sessions"]):
            return
        existing["sessions"].append(meta)
        # Atomic write
        tmp = idx_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(existing, indent=2))
        os.replace(tmp, idx_path)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_marauder_storage.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git -C /home/kali/drifter add tests/test_marauder_storage.py src/marauder_storage.py
git -C /home/kali/drifter commit -m "$(cat <<'EOF'
feat(marauder): storage — SessionWriter + sessions.json index

Per-session JSONL files under state_root/<category>/<id>.jsonl, plus
an append-only sessions.json index. end() is idempotent (immutable
ended_ts invariant).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 1.16: Passive feature — `passive_scan` dispatcher

**Files:**
- Modify: `src/marauder_features/passive.py`
- Create: `tests/test_marauder_passive.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_marauder_passive.py
import sys
from pathlib import Path
from unittest.mock import MagicMock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from marauder_features import passive


class TestPassiveScanStart:
    def test_start_ap_sends_correct_command(self):
        transport = MagicMock()
        transport.mode = "direct"
        result = passive.start_scan(transport, mode="ap", duration_s=30)
        assert result["ok"] is True
        transport.send.assert_called_once_with("scanap\r\n")
        assert result["mode"] == "ap"
        assert result["duration_s"] == 30

    def test_start_sta_sends_correct_command(self):
        transport = MagicMock()
        transport.mode = "direct"
        result = passive.start_scan(transport, mode="sta", duration_s=10)
        transport.send.assert_called_once_with("scansta\r\n")

    def test_start_probe_sends_correct_command(self):
        transport = MagicMock()
        transport.mode = "direct"
        passive.start_scan(transport, mode="probe", duration_s=10)
        transport.send.assert_called_once_with("sniffprobe\r\n")

    def test_unknown_mode_refused(self):
        transport = MagicMock()
        transport.mode = "direct"
        result = passive.start_scan(transport, mode="bogus", duration_s=10)
        assert result["ok"] is False
        assert "unknown mode" in result["response"].lower()
        transport.send.assert_not_called()

    def test_duration_capped_at_600(self):
        transport = MagicMock()
        transport.mode = "direct"
        result = passive.start_scan(transport, mode="ap", duration_s=9999)
        assert result["duration_s"] == 600

    def test_no_hardware_refused(self):
        transport = MagicMock()
        transport.mode = "none"
        result = passive.start_scan(transport, mode="ap", duration_s=30)
        assert result["ok"] is False
        assert "no transport" in result["response"].lower()
        transport.send.assert_not_called()


class TestPassiveScanStop:
    def test_stop_sends_stopscan(self):
        transport = MagicMock()
        transport.mode = "direct"
        passive.stop_scan(transport)
        transport.send.assert_called_once_with("stopscan\r\n")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_marauder_passive.py -v`
Expected: 7 FAIL

- [ ] **Step 3: Implement `start_scan` and `stop_scan`**

Replace the body of `src/marauder_features/passive.py`:

```python
"""MZ1312 DRIFTER — Marauder bridge module: passive recon (scanap/scansta/sniffprobe)."""

import marauder_protocol as mp

MAX_DURATION_S = 600

_MODE_TO_BUILDER = {
    "ap": mp.cmd_scan_ap,
    "sta": mp.cmd_scan_sta,
    "probe": mp.cmd_scan_probes,
}


def start_scan(transport, *, mode: str, duration_s: int) -> dict:
    """Issue a passive scan via the transport.

    Returns {ok, response, mode, duration_s}. Does NOT block — the caller
    is responsible for setting a timer to call stop_scan() after duration_s.
    """
    if transport.mode == "none":
        return {"ok": False, "response": "no transport available",
                "mode": mode, "duration_s": duration_s}

    builder = _MODE_TO_BUILDER.get(mode)
    if builder is None:
        return {"ok": False,
                "response": f"unknown mode={mode} (want ap/sta/probe)",
                "mode": mode, "duration_s": duration_s}

    capped = min(int(duration_s), MAX_DURATION_S)
    transport.send(builder())
    return {"ok": True, "response": f"scan started mode={mode}",
            "mode": mode, "duration_s": capped}


def stop_scan(transport) -> dict:
    if transport.mode == "none":
        return {"ok": False, "response": "no transport"}
    transport.send(mp.cmd_stop())
    return {"ok": True, "response": "stop sent"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_marauder_passive.py -v`
Expected: 7 PASS

- [ ] **Step 5: Commit**

```bash
git -C /home/kali/drifter add tests/test_marauder_passive.py src/marauder_features/passive.py
git -C /home/kali/drifter commit -m "$(cat <<'EOF'
feat(marauder): features.passive — start_scan + stop_scan

Dispatches scan_ap / scan_sta / scan_probe via the transport. Duration
hard-capped at 600s. Unknown mode + no-hardware both return ok=false
with explicit reason. Caller manages the duration timer.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 1.17: Bridge — MQTT client + dispatch glue

**Files:**
- Modify: `src/marauder_bridge.py`
- Modify: `tests/test_marauder_bridge.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_marauder_bridge.py`:

```python
import json
from unittest.mock import MagicMock


class TestDispatch:
    def _make_bridge(self, transport_mode="direct"):
        transport = MagicMock()
        transport.mode = transport_mode
        transport.hw_detail = "fake"
        mqtt = MagicMock()
        bridge = mb.Bridge(transport=transport, mqtt_client=mqtt,
                           allowlist_scope={"wifi": [], "ble": [], "evilportal": []},
                           session_writer=MagicMock())
        return bridge, transport, mqtt

    def test_dispatch_scan_ap_publishes_event_with_id(self):
        bridge, _, mqtt = self._make_bridge()
        payload = {"id": "op-uuid-1", "command": "scan_ap",
                   "args": {"mode": "ap", "duration_s": 30}}
        bridge.dispatch(payload)
        # Look for a publish to drifter/marauder/event with id echo
        found = False
        for call in mqtt.publish.call_args_list:
            topic, body = call.args[0], call.args[1]
            if topic == "drifter/marauder/event":
                ev = json.loads(body)
                if ev.get("id") == "op-uuid-1":
                    found = True
                    assert ev["ok"] is True
        assert found, f"No matching event publish: {mqtt.publish.call_args_list}"

    def test_dispatch_high_risk_without_token_returns_confirm_required(self):
        bridge, _, mqtt = self._make_bridge()
        payload = {"id": "op-uuid-2", "command": "deauth_attack",
                   "args": {"target_bssid": "aa:bb:cc:dd:ee:ff"}}
        bridge.dispatch(payload)
        for call in mqtt.publish.call_args_list:
            topic, body = call.args[0], call.args[1]
            if topic == "drifter/marauder/event":
                ev = json.loads(body)
                if ev["id"] == "op-uuid-2":
                    assert ev["ok"] is False
                    assert "confirm" in ev["response"].lower()
                    assert "confirm_token" in ev
                    return
        raise AssertionError("no event for op-uuid-2")

    def test_dispatch_high_risk_empty_allowlist_refuses(self):
        bridge, _, mqtt = self._make_bridge()
        # First call gets confirm token
        bridge.dispatch({"id": "a", "command": "deauth_attack",
                         "args": {"target_bssid": "aa:bb:cc:dd:ee:ff"}})
        token = None
        for call in mqtt.publish.call_args_list:
            if call.args[0] == "drifter/marauder/event":
                ev = json.loads(call.args[1])
                if ev["id"] == "a":
                    token = ev.get("confirm_token")
                    break
        assert token, "expected token on first call"

        mqtt.publish.reset_mock()
        # Second call with token still refuses (allowlist empty)
        bridge.dispatch({"id": "b", "command": "deauth_attack",
                         "args": {"target_bssid": "aa:bb:cc:dd:ee:ff"},
                         "confirm_token": token})
        found = False
        for call in mqtt.publish.call_args_list:
            if call.args[0] == "drifter/marauder/event":
                ev = json.loads(call.args[1])
                if ev["id"] == "b":
                    found = True
                    assert ev["ok"] is False
                    assert "allowlist" in ev["response"].lower()
        assert found
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_marauder_bridge.py::TestDispatch -v`
Expected: 3 FAIL

- [ ] **Step 3: Implement `Bridge.dispatch`**

Append to `src/marauder_bridge.py`:

```python
import json

import marauder_allowlist as ma
from marauder_features import passive as passive_feat


class Bridge:
    """Service-level orchestrator: holds transport + MQTT + allowlist +
    storage; dispatches commands; manages locks and confirmations.
    """

    def __init__(self, *, transport, mqtt_client, allowlist_scope,
                 session_writer):
        self.transport = transport
        self.mqtt = mqtt_client
        self.allowlist = allowlist_scope  # dict from load_marauder_allowlist
        self.storage = session_writer
        self.lock = CommandLock()
        self.confirms = PendingConfirms(ttl_s=120)

    # ── MQTT helpers ─────────────────────────────────────────────────
    def _publish(self, topic: str, payload: dict, retain: bool = False) -> None:
        body = json.dumps(payload, separators=(",", ":"))
        self.mqtt.publish(topic, body, qos=0, retain=retain)

    def _publish_event(self, op_id: str | None, ok: bool, response: str,
                       **extra) -> None:
        ev = {"id": op_id, "ok": ok, "response": response, "ts": time.time()}
        ev.update(extra)
        self._publish("drifter/marauder/event", ev)

    # ── Dispatch ─────────────────────────────────────────────────────
    def dispatch(self, payload: dict) -> None:
        op_id = payload.get("id")
        command = payload.get("command", "")
        args = payload.get("args") or {}
        confirm_token = payload.get("confirm_token")

        # 1) Risk classification
        risk = classify_risk(command)

        # 2) HIGH risk → confirm flow
        if risk == "HIGH":
            if not confirm_token:
                # First leg — issue token
                token = self.confirms.register(command, args)
                self._publish_event(op_id, False,
                                    "Confirmation required",
                                    confirm_token=token,
                                    expires_in_s=120)
                return
            # Second leg — validate token
            popped = self.confirms.pop(confirm_token)
            if popped is None:
                self._publish_event(op_id, False,
                                    "Invalid or expired confirm_token")
                return
            command, args = popped

            # 3) Allowlist gate
            category = self._command_to_allowlist_category(command)
            if category is not None:
                ok, reason = ma.is_target_allowed(self.allowlist, category, **args)
                if not ok:
                    self._publish_event(op_id, False,
                                        reason,
                                        scope=f"marauder.{category}")
                    return

        # 4) Acquire command lock
        if not self.lock.try_acquire(command, op_id or ""):
            held = self.lock.held_by()
            self._publish_event(op_id, False,
                                f"command locked (in use by {held})")
            return

        # 5) Execute via feature dispatcher
        try:
            result = self._execute(command, args)
            self._publish_event(op_id, result["ok"], result["response"])
        finally:
            # LOW-risk scans hold the lock for their duration; the timer that
            # ends the scan also releases. For now, always release here;
            # duration-based release lands when we add the timer in Task 1.18.
            self.lock.release()

    def _command_to_allowlist_category(self, command: str) -> str | None:
        if command in {"deauth_attack", "beacon_spam_list",
                       "beacon_spam_random", "beacon_spam_rickroll",
                       "probe_flood"}:
            return "wifi"
        if command in {"ble_spam_swift_pair", "ble_spam_easy_setup",
                       "ble_spam_apple_proximity", "ble_spam_all"}:
            return "ble"
        if command in {"evilportal_start"}:
            return "evilportal"
        return None

    def _execute(self, command: str, args: dict) -> dict:
        # Phase 1 dispatch table — extended in Phases 2/3/4
        if command == "scan_ap":
            return passive_feat.start_scan(self.transport, mode="ap",
                                            duration_s=args.get("duration_s", 60))
        if command == "scan_sta":
            return passive_feat.start_scan(self.transport, mode="sta",
                                            duration_s=args.get("duration_s", 60))
        if command == "scan_probes":
            return passive_feat.start_scan(self.transport, mode="probe",
                                            duration_s=args.get("duration_s", 60))
        if command == "stop":
            return passive_feat.stop_scan(self.transport)
        return {"ok": False, "response": f"command not implemented in this phase: {command}"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_marauder_bridge.py -v`
Expected: 8 PASS

- [ ] **Step 5: Commit**

```bash
git -C /home/kali/drifter add tests/test_marauder_bridge.py src/marauder_bridge.py
git -C /home/kali/drifter commit -m "$(cat <<'EOF'
feat(marauder): bridge — Bridge.dispatch with risk + confirm + allowlist gates

Phase 1 dispatch covers scan_ap/sta/probe + stop. HIGH-risk commands
get a confirm_token on first POST and pass through the allowlist gate
on second POST. Empty allowlist refuses with explicit scope/category.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 1.18: Bridge — main() service entry + signal handling + duration timer

**Files:**
- Modify: `src/marauder_bridge.py`

- [ ] **Step 1: Implement `main()` and the duration-timer release**

Append to `src/marauder_bridge.py`:

```python
import signal
import os
import sys

# paho-mqtt import is deferred so the test suite can run without it installed
def _make_mqtt_client(client_id: str):
    """Defer paho import so tests that monkey-patch can run without it."""
    from config import MQTT_HOST, MQTT_PORT, make_mqtt_client  # type: ignore
    return make_mqtt_client(client_id), MQTT_HOST, MQTT_PORT


def _publish_status(bridge: Bridge, state: str) -> None:
    bridge._publish("drifter/marauder/status", {
        "state": state,
        "mode": bridge.transport.mode,
        "transport": bridge.transport.mode,
        "hw_detail": bridge.transport.hw_detail,
        "ts": time.time(),
    }, retain=True)


def main(argv=None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    # 1) Transport autodetect
    from marauder_transport import MarauderTransport
    transport = MarauderTransport()
    transport.autodetect()

    # 2) Allowlist
    scope = ma.load_marauder_allowlist()

    # 3) Session storage
    from marauder_storage import SessionWriter
    state_root = Path("/opt/drifter/state/marauder")
    state_root.mkdir(parents=True, exist_ok=True)
    storage = SessionWriter(state_root=state_root)

    # 4) MQTT
    mqtt_client, host, port = _make_mqtt_client("drifter-marauder")
    mqtt_client.connect(host, port, keepalive=60)

    bridge = Bridge(transport=transport, mqtt_client=mqtt_client,
                    allowlist_scope=scope, session_writer=storage)

    # 5) Reader thread → MQTT scan events (only if direct transport)
    if transport.mode == "direct":
        from marauder_protocol import parse_event
        def line_handler(line: str) -> None:
            ev = parse_event(line)
            if ev is None:
                return
            topic_for_type = {
                "ap": "drifter/marauder/scan/ap",
                "station": "drifter/marauder/scan/sta",
                "probe": "drifter/marauder/scan/probe",
            }
            topic = topic_for_type.get(ev["type"])
            if topic:
                bridge._publish(topic, ev)
            # also unknown events go to error stream at DEBUG level
            elif ev["type"] == "unknown":
                log.debug("unknown line: %s", ev.get("raw", "")[:120])
        transport.start(line_callback=line_handler)

    # 6) MQTT command subscribe
    def on_message(client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
        except Exception as e:
            log.warning("invalid cmd payload: %s", e)
            return
        bridge.dispatch(payload)

    mqtt_client.on_message = on_message
    mqtt_client.subscribe("drifter/marauder/cmd", qos=0)

    # 7) Initial status publish
    initial_state = "no_hardware" if transport.mode == "none" else "idle"
    _publish_status(bridge, initial_state)

    # 8) Signal handling
    stop_event = threading.Event()
    def handle_signal(signum, frame):
        log.info("signal %s received — shutting down", signum)
        stop_event.set()
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    # 9) Background loop — status heartbeat every 30s, sweep stale confirms
    last_status = 0.0
    mqtt_client.loop_start()
    try:
        while not stop_event.is_set():
            time.sleep(0.5)
            now = time.time()
            if now - last_status > 30:
                state = "no_hardware" if transport.mode == "none" else "idle"
                _publish_status(bridge, state)
                last_status = now
            bridge.confirms.sweep()
    finally:
        mqtt_client.loop_stop()
        if transport.mode == "direct":
            transport.stop()
        try:
            mqtt_client.disconnect()
        except Exception:
            pass
        log.info("marauder bridge exiting cleanly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Smoke-test the import path (no hardware required)**

Run:
```bash
PYTHONPATH=/home/kali/drifter/src python3 -c "
import marauder_bridge
print('main is callable:', callable(marauder_bridge.main))
print('Bridge class exists:', hasattr(marauder_bridge, 'Bridge'))
"
```
Expected: both `True` lines printed without traceback.

- [ ] **Step 3: Commit**

```bash
git -C /home/kali/drifter add src/marauder_bridge.py
git -C /home/kali/drifter commit -m "$(cat <<'EOF'
feat(marauder): bridge — main() entry point + signal handling

Wires transport autodetect, allowlist load, session storage, MQTT
client, reader thread (direct mode), command subscribe, status
heartbeat every 30s, confirm-sweep every 0.5s. SIGTERM/SIGINT triggers
clean teardown (stop reader thread, close transport, MQTT disconnect).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 1.19: Systemd unit

**Files:**
- Create: `services/drifter-marauder.service`

- [ ] **Step 1: Write the unit file**

```ini
[Unit]
Description=MZ1312 DRIFTER Marauder bridge (Wi-Fi/BLE recon + active)
After=network-online.target mosquitto.service drifter-flipper.service
Wants=mosquitto.service
StartLimitIntervalSec=120
StartLimitBurst=5

[Service]
Type=simple
User=drifter
Group=drifter
SupplementaryGroups=dialout
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONPATH=/opt/drifter/src
StateDirectory=drifter-marauder
WorkingDirectory=/var/lib/drifter-marauder
ExecStart=/opt/drifter/venv/bin/python /opt/drifter/src/marauder_bridge.py
Restart=on-failure
RestartSec=10

# Hardening
ProtectHome=true
PrivateTmp=true
ProtectSystem=full
ReadWritePaths=/opt/drifter/state /opt/drifter/etc /var/lib/drifter-marauder
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
LockPersonality=true
RestrictSUIDSGID=true
NoNewPrivileges=true
DeviceAllow=char-ttyACM rw
DeviceAllow=char-ttyUSB rw
DevicePolicy=closed

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Validate with `systemd-analyze verify`**

Run: `sudo systemd-analyze verify /home/kali/drifter/services/drifter-marauder.service 2>&1 || true`
Expected: No errors (warnings about "[Install] section directives" / pre-deploy paths are acceptable).

- [ ] **Step 3: Deploy + reload + start (manual operator step at deploy time, but verify now)**

Run:
```bash
sudo cp /home/kali/drifter/services/drifter-marauder.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable drifter-marauder
sudo systemctl start drifter-marauder
sleep 5
systemctl status drifter-marauder --no-pager -n 20
```
Expected: status `active (running)` (no hardware present → service stays idle in no_hardware state but the process runs).

- [ ] **Step 4: Verify MQTT status retained**

Run: `mosquitto_sub -t 'drifter/marauder/status' -C 1 -W 5`
Expected: JSON with `"state":"no_hardware"` (or `"idle"` if a real ESP32 is somehow plugged in).

- [ ] **Step 5: Commit**

```bash
git -C /home/kali/drifter add services/drifter-marauder.service
git -C /home/kali/drifter commit -m "$(cat <<'EOF'
feat(marauder): systemd unit drifter-marauder.service

User=drifter + dialout group for USB serial access. ProtectHome=true
with StateDirectory keeps Kismet-style sandboxing. DeviceAllow scoped
to char-ttyACM + char-ttyUSB so the service can't reach unrelated /dev
entries. StartLimit guards against restart-loops.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 1.20: OPSEC dashboard — mount `/api/marauder/status` + `/api/marauder/cmd`

**Files:**
- Modify: `src/opsec_dashboard.py`

- [ ] **Step 1: Locate the existing route table in `opsec_dashboard.py`**

`opsec_dashboard.py:857` defines `class OpsecHandler(BaseHTTPRequestHandler)`. Routes are dispatched inside `do_GET` and `do_POST`. Find those methods (search for `def do_GET` and `def do_POST`).

- [ ] **Step 2: Add a small marauder client helper module**

Create `src/opsec_marauder_client.py`:

```python
"""Thin client used by opsec_dashboard.py to talk to drifter-marauder
over MQTT. Subscribes to status (retained) so /api/marauder/status is
near-instant; publishes commands on /api/marauder/cmd.
"""

import json
import threading
import time
import uuid

_STATUS = {"state": "unknown", "transport": "unknown", "ts": 0}
_LOCK = threading.Lock()


def install(mqtt_client) -> None:
    """Hook the existing mqtt_client to keep _STATUS fresh."""
    mqtt_client.subscribe("drifter/marauder/status", qos=0)

    prev_on_message = mqtt_client.on_message
    def on_message(client, userdata, msg):
        if msg.topic == "drifter/marauder/status":
            try:
                with _LOCK:
                    _STATUS.update(json.loads(msg.payload.decode()))
            except Exception:
                pass
        if prev_on_message:
            prev_on_message(client, userdata, msg)
    mqtt_client.on_message = on_message


def get_status() -> dict:
    with _LOCK:
        return dict(_STATUS)


def publish_cmd(mqtt_client, command: str, args: dict | None = None,
                confirm_token: str | None = None) -> str:
    op_id = uuid.uuid4().hex
    payload = {"id": op_id, "command": command, "args": args or {}}
    if confirm_token:
        payload["confirm_token"] = confirm_token
    mqtt_client.publish("drifter/marauder/cmd",
                        json.dumps(payload, separators=(",", ":")),
                        qos=0, retain=False)
    return op_id
```

- [ ] **Step 3: Wire into `opsec_dashboard.py`**

Near the top of `opsec_dashboard.py`, after the other imports:

```python
import opsec_marauder_client as marauder_client
```

In `_start_mqtt()` (around line 238), after the existing subscribe calls add:

```python
marauder_client.install(client)
```

In `OpsecHandler.do_GET` (the existing `if self.path == ...` chain), add at the top of the branch list:

```python
        if self.path == "/api/marauder/status":
            return self._respond_json(marauder_client.get_status())
```

In `OpsecHandler.do_POST`:

```python
        if self.path == "/api/marauder/cmd":
            if not _is_local_peer(self.client_address[0]):
                return self._respond_json({"ok": False, "response": "remote not allowed"},
                                          status=403)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length).decode() or "{}")
            except Exception as e:
                return self._respond_json({"ok": False, "response": f"bad body: {e}"},
                                          status=400)
            op_id = marauder_client.publish_cmd(
                _mqtt,  # the module-level mqtt client in opsec_dashboard
                command=body.get("command", ""),
                args=body.get("args") or {},
                confirm_token=body.get("confirm_token"),
            )
            return self._respond_json({"ok": True, "op_id": op_id,
                                       "note": "command published; subscribe to drifter/marauder/event"})
```

Assumption: `opsec_dashboard.py` already exposes `_respond_json` and `_is_local_peer` helpers — verify by searching the file. If they're named differently, adapt the calls (do NOT rename existing helpers).

- [ ] **Step 4: Restart opsec dashboard + verify**

Run:
```bash
sudo systemctl restart drifter-opsec
sleep 3
curl -fsS http://127.0.0.1:8090/api/marauder/status
```
Expected: JSON with `state` field (`no_hardware` if no ESP32 / Flipper marauder module present, `idle` otherwise).

```bash
curl -fsS -X POST http://127.0.0.1:8090/api/marauder/cmd \
  -H 'Content-Type: application/json' \
  -d '{"command":"scan_ap","args":{"duration_s":10}}'
```
Expected: `{"ok":true,"op_id":"<hex>","note":"command published..."}`.

```bash
# In another terminal:
mosquitto_sub -t 'drifter/marauder/event' -C 1 -W 5
```
Expected: an event with the matching `op_id` echoing back the outcome (`{"ok":false,"response":"no transport available"}` if no hardware).

- [ ] **Step 5: Commit**

```bash
git -C /home/kali/drifter add src/opsec_dashboard.py src/opsec_marauder_client.py
git -C /home/kali/drifter commit -m "$(cat <<'EOF'
feat(marauder): opsec — /api/marauder/status + /api/marauder/cmd routes

Thin MQTT-backed client keeps marauder status cached from the retained
drifter/marauder/status topic. POST /cmd gates on _is_local_peer
(reuses existing helper), validates JSON, publishes to
drifter/marauder/cmd, returns op_id for the caller to correlate with
drifter/marauder/event.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 1.21: OPSEC dashboard — `/api/marauder/probe` + `/api/marauder/stop` + `/api/marauder/scan/recent`

**Files:**
- Modify: `src/opsec_dashboard.py`
- Modify: `src/opsec_marauder_client.py`

- [ ] **Step 1: Extend the MQTT client to keep a ring of recent scan events**

Append to `src/opsec_marauder_client.py`:

```python
import collections

_RING_CAP = 200
_RINGS: dict[str, collections.deque[dict]] = {
    "ap": collections.deque(maxlen=_RING_CAP),
    "sta": collections.deque(maxlen=_RING_CAP),
    "probe": collections.deque(maxlen=_RING_CAP),
}


def _install_scan_rings(mqtt_client) -> None:
    """Subscribe to scan streams and keep rolling rings for /scan/recent."""
    mqtt_client.subscribe("drifter/marauder/scan/ap", qos=0)
    mqtt_client.subscribe("drifter/marauder/scan/sta", qos=0)
    mqtt_client.subscribe("drifter/marauder/scan/probe", qos=0)
    prev = mqtt_client.on_message
    def on_message(client, userdata, msg):
        try:
            if msg.topic == "drifter/marauder/scan/ap":
                _RINGS["ap"].append(json.loads(msg.payload.decode()))
            elif msg.topic == "drifter/marauder/scan/sta":
                _RINGS["sta"].append(json.loads(msg.payload.decode()))
            elif msg.topic == "drifter/marauder/scan/probe":
                _RINGS["probe"].append(json.loads(msg.payload.decode()))
        except Exception:
            pass
        if prev:
            prev(client, userdata, msg)
    mqtt_client.on_message = on_message


def get_scan_recent(stream: str, n: int = 200) -> list[dict]:
    ring = _RINGS.get(stream)
    if ring is None:
        return []
    n = max(1, min(int(n), _RING_CAP))
    return list(ring)[-n:]
```

Modify `install()` to also call `_install_scan_rings(mqtt_client)` (right after the existing `mqtt_client.subscribe(...)` call).

- [ ] **Step 2: Add the three new routes to `opsec_dashboard.py`**

In `do_POST`:

```python
        if self.path == "/api/marauder/probe":
            if not _is_local_peer(self.client_address[0]):
                return self._respond_json({"ok": False, "response": "remote not allowed"},
                                          status=403)
            op_id = marauder_client.publish_cmd(_mqtt, command="probe")
            return self._respond_json({"ok": True, "op_id": op_id})

        if self.path == "/api/marauder/stop":
            if not _is_local_peer(self.client_address[0]):
                return self._respond_json({"ok": False, "response": "remote not allowed"},
                                          status=403)
            op_id = marauder_client.publish_cmd(_mqtt, command="stop")
            return self._respond_json({"ok": True, "op_id": op_id})
```

In `do_GET` (parse query string for `stream=` and `n=`):

```python
        if self.path.startswith("/api/marauder/scan/recent"):
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            stream = qs.get("stream", ["ap"])[0]
            n = int(qs.get("n", ["200"])[0])
            events = marauder_client.get_scan_recent(stream, n=n)
            return self._respond_json({"stream": stream,
                                       "count": len(events),
                                       "events": events})
```

- [ ] **Step 3: Restart + verify**

Run:
```bash
sudo systemctl restart drifter-opsec
sleep 3
curl -fsS http://127.0.0.1:8090/api/marauder/scan/recent?stream=ap | python3 -m json.tool
```
Expected: `{"stream":"ap","count":0,"events":[]}` (no scan run yet).

```bash
curl -fsS -X POST http://127.0.0.1:8090/api/marauder/probe
```
Expected: `{"ok":true,"op_id":"..."}`.

- [ ] **Step 4: Commit**

```bash
git -C /home/kali/drifter add src/opsec_dashboard.py src/opsec_marauder_client.py
git -C /home/kali/drifter commit -m "$(cat <<'EOF'
feat(marauder): opsec — /probe + /stop + /scan/recent routes

200-deep MQTT ring buffers per stream (ap/sta/probe). Routes for
operator-initiated re-probe and emergency stop. All POSTs gated by
_is_local_peer. /scan/recent supports stream= + n= query params.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 1.22: Bench test script

**Files:**
- Create: `scripts/test-bench-marauder.sh`

- [ ] **Step 1: Write the script**

```bash
#!/usr/bin/env bash
# MZ1312 DRIFTER — Marauder bench tests.
# Modes:
#   probe              — runs autodetect via /api/marauder/probe + reads status
#   passive            — runs scan_ap for 30s, prints event count from MQTT
#   deauth_detect      — runs detector for 60s, prints any deauths seen
#   allowlist_refuse   — sends deauth_attack to a BSSID NOT in allowlist,
#                        asserts the bridge refuses
#
# Phase 4 portal_dryrun lands when the EvilPortal feature is implemented.

set -euo pipefail

MODE="${1:-probe}"
OPSEC_BASE="http://127.0.0.1:8090"
MQTT_HOST="127.0.0.1"
MQTT_PORT="1883"

die() { echo "FAIL: $*" >&2; exit 1; }
ok()  { echo "OK:   $*"; }

require() {
    command -v "$1" >/dev/null || die "missing dependency: $1"
}

require curl
require mosquitto_sub
require jq

case "$MODE" in
    probe)
        echo "→ POST /api/marauder/probe"
        curl -fsS -X POST "$OPSEC_BASE/api/marauder/probe" | jq .
        sleep 1
        echo "→ GET /api/marauder/status"
        status=$(curl -fsS "$OPSEC_BASE/api/marauder/status")
        echo "$status" | jq .
        state=$(echo "$status" | jq -r .state)
        case "$state" in
            idle)         ok "transport found — service idle";;
            no_hardware)  ok "no hardware present — service correctly in no_hardware state";;
            *)            die "unexpected state: $state";;
        esac
        ;;
    passive)
        echo "→ POST /cmd scan_ap duration_s=30 (running in background, listening for events)"
        op_id=$(curl -fsS -X POST "$OPSEC_BASE/api/marauder/cmd" \
            -H 'Content-Type: application/json' \
            -d '{"command":"scan_ap","args":{"duration_s":30}}' | jq -r .op_id)
        ok "op_id=$op_id"

        echo "→ Listening for drifter/marauder/scan/ap for 35s …"
        count=$(timeout 35s mosquitto_sub -h "$MQTT_HOST" -p "$MQTT_PORT" \
                -t 'drifter/marauder/scan/ap' -v 2>/dev/null | wc -l || true)
        if [ "$count" -eq 0 ]; then
            echo "WARN: no scan events received (no hardware, no APs in range, or service idle)"
        else
            ok "received $count scan/ap events"
        fi
        ;;
    deauth_detect)
        echo "→ POST /cmd deauth_detect (no confirm, no allowlist — LOW risk per §5.2)"
        curl -fsS -X POST "$OPSEC_BASE/api/marauder/cmd" \
            -H 'Content-Type: application/json' \
            -d '{"command":"deauth_detect","args":{"duration_s":60}}' | jq .
        echo "→ Listening for drifter/marauder/event {type:deauth_seen} for 65s …"
        timeout 65s mosquitto_sub -h "$MQTT_HOST" -p "$MQTT_PORT" \
            -t 'drifter/marauder/event' -v 2>/dev/null | grep deauth_seen || \
            echo "(no deauths observed — environment may be quiet, this is fine)"
        ;;
    allowlist_refuse)
        echo "→ Test: deauth_attack to BSSID NOT in allowlist must be refused"
        # 1) First call: should get confirm_token
        r1=$(curl -fsS -X POST "$OPSEC_BASE/api/marauder/cmd" \
            -H 'Content-Type: application/json' \
            -d '{"command":"deauth_attack","args":{"bssid":"de:ad:be:ef:00:00","ssid":"NOT_IN_ALLOWLIST"}}')
        op_id_1=$(echo "$r1" | jq -r .op_id)

        # Subscribe briefly to the event topic to get the token
        event_line=$(timeout 3s mosquitto_sub -h "$MQTT_HOST" -p "$MQTT_PORT" \
                     -t 'drifter/marauder/event' -C 2 -W 3 2>/dev/null \
                     | grep "$op_id_1" || true)
        token=$(echo "$event_line" | jq -r .confirm_token 2>/dev/null || echo "")
        [ -z "$token" ] && die "did not receive a confirm_token for op_id=$op_id_1"
        ok "got confirm_token (length=${#token})"

        # 2) Second call with token must be refused due to empty allowlist
        r2=$(curl -fsS -X POST "$OPSEC_BASE/api/marauder/cmd" \
            -H 'Content-Type: application/json' \
            -d "{\"command\":\"deauth_attack\",\"args\":{\"bssid\":\"de:ad:be:ef:00:00\",\"ssid\":\"NOT_IN_ALLOWLIST\"},\"confirm_token\":\"$token\"}")
        op_id_2=$(echo "$r2" | jq -r .op_id)

        event_line_2=$(timeout 3s mosquitto_sub -h "$MQTT_HOST" -p "$MQTT_PORT" \
                       -t 'drifter/marauder/event' -C 2 -W 3 2>/dev/null \
                       | grep "$op_id_2" || true)
        echo "$event_line_2" | grep -q '"ok":false' || die "expected refusal, got: $event_line_2"
        echo "$event_line_2" | grep -qi 'allowlist' || die "refusal reason missing 'allowlist': $event_line_2"
        ok "allowlist refusal correctly fired"
        ;;
    *)
        die "unknown mode: $MODE (want probe|passive|deauth_detect|allowlist_refuse)"
        ;;
esac
```

- [ ] **Step 2: Make it executable + smoke `probe` mode**

Run:
```bash
chmod +x /home/kali/drifter/scripts/test-bench-marauder.sh
/home/kali/drifter/scripts/test-bench-marauder.sh probe
```
Expected: prints status JSON and ends with `OK: no hardware present...` (assuming no ESP32 connected).

- [ ] **Step 3: Commit**

```bash
git -C /home/kali/drifter add scripts/test-bench-marauder.sh
git -C /home/kali/drifter commit -m "$(cat <<'EOF'
test(marauder): bench script — probe/passive/deauth_detect/allowlist_refuse

Real-hardware bench modes that drive the running service via the
HTTP API and verify MQTT outcomes. probe + allowlist_refuse work
without any hardware present (validates the no-hardware + refusal
paths). passive + deauth_detect need a real ESP32 to produce events.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 1.23: `/healthz` integration — verify drifter-marauder lands in the right bucket

**Files:**
- (Verification only; the config.py change from Task 0.3 is what makes this work.)

- [ ] **Step 1: Restart the dashboard to pick up updated config + check healthz**

Run:
```bash
sudo systemctl restart drifter-dashboard
sleep 3
curl -fsS http://127.0.0.1:8080/healthz | python3 -m json.tool | grep -A2 -B2 marauder
```
Expected: `drifter-marauder` appears in `services`. With the service running but no hardware, it should be `true` (process is alive). With no hardware, `/healthz.status` should remain `ok-hw-pending` and `drifter-marauder` should NOT appear in `services_failed`.

- [ ] **Step 2: Stop the service and re-check that healthz correctly marks it pending**

Run:
```bash
sudo systemctl stop drifter-marauder
sleep 2
curl -fsS http://127.0.0.1:8080/healthz | python3 -c "
import json, sys
d = json.load(sys.stdin)
print('status:', d['status'])
print('marauder in services:', d['services'].get('drifter-marauder'))
print('marauder in failed:', 'drifter-marauder' in d['services_failed'])
print('marauder in hw_pending:', 'drifter-marauder' in d['services_hw_pending'])
"
sudo systemctl start drifter-marauder  # bring it back
```
Expected: depending on how `web_dashboard_state.py` classifies inactive foot-mode services with no hardware probe, it should land in `services_hw_pending`, NOT `services_failed`. If it lands in failed, see the next step.

- [ ] **Step 3 (only if needed): Teach hw-probe to surface marauder USB hardware**

If `services_failed` contains `drifter-marauder` when stopped + no hardware, the dashboard's hw-pending classifier needs a Marauder hint. Search `src/web_dashboard_handlers.py` or `src/hw_probe.py` for the existing kismet hw-pending logic (likely searches for monitor-mode Wi-Fi adapter). Add a parallel check:

```python
def has_marauder_hardware() -> bool:
    """Return True if any known Marauder ESP32 / Flipper marauder module
    appears to be present. Used by /healthz to classify drifter-marauder
    as hw_pending vs failed when the service is inactive."""
    from pathlib import Path
    import re
    BY_ID = Path("/dev/serial/by-id")
    if BY_ID.exists():
        known = [("303a", "1001"), ("303a", "1014"), ("10c4", "ea60")]
        rx = re.compile(r"_([0-9a-f]{4})_([0-9a-f]{4})_", re.I)
        for entry in BY_ID.iterdir():
            m = rx.search(entry.name)
            if m and (m.group(1).lower(), m.group(2).lower()) in known:
                return True
    # Optionally probe drifter-flipper for marauder module — skipped here
    # to keep the probe synchronous + fast.
    return False
```

Wire it into the existing hw_pending list builder following the kismet pattern. Restart dashboard, re-test.

- [ ] **Step 4: Commit (if Step 3 changes happened)**

```bash
git -C /home/kali/drifter add src/web_dashboard_handlers.py src/hw_probe.py
git -C /home/kali/drifter commit -m "$(cat <<'EOF'
feat(marauder): healthz — classify drifter-marauder as hw_pending vs failed

When the service is inactive and no ESP32 Marauder hardware is
detected on /dev/serial/by-id, /healthz puts drifter-marauder in
services_hw_pending (HTTP 200) rather than services_failed (HTTP 503).
Matches the kismet classification pattern.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 1.24: Phase 1 acceptance — end-to-end smoke (the green light)

**Files:**
- (Verification only — no code changes.)

- [ ] **Step 1: Run all unit + integration tests**

Run: `cd /home/kali/drifter && pytest tests/test_marauder_*.py -v`
Expected: All PASS, no errors.

- [ ] **Step 2: Service is active and idle**

Run: `systemctl is-active drifter-marauder`
Expected: `active`.

Run: `curl -fsS http://127.0.0.1:8090/api/marauder/status | jq .state`
Expected: `"idle"` (if hardware present) or `"no_hardware"` (if not).

- [ ] **Step 3: /healthz green**

Run: `curl -fsS http://127.0.0.1:8080/healthz | jq '{status, services_failed, services_hw_pending}'`
Expected: `status` is `ok` or `ok-hw-pending`. `services_failed` does not contain `drifter-marauder`.

- [ ] **Step 4: Bench probe mode passes**

Run: `/home/kali/drifter/scripts/test-bench-marauder.sh probe`
Expected: ends with an `OK:` line.

- [ ] **Step 5: Bench allowlist_refuse passes**

Run: `/home/kali/drifter/scripts/test-bench-marauder.sh allowlist_refuse`
Expected: ends with `OK: allowlist refusal correctly fired`.

- [ ] **Step 6: No retained creds on the bus (sanity even though Phase 4 not implemented)**

Run: `mosquitto_sub -R -t 'drifter/marauder/#' -W 3 2>/dev/null | head -20`
Expected: only `status` (retained), no `cred_capture`-anything, no raw form posts.

- [ ] **Step 7: Tag the Phase 1 acceptance commit (no new code, just a marker)**

```bash
git -C /home/kali/drifter tag -a marauder-phase1-accept -m "Phase 1 (core + passive recon) acceptance criteria met"
```

(Push later with `git push origin marauder-phase1-accept` when you're ready.)

---

## Phase 2 — Active Wi-Fi

Adds deauth-detect, deauth-attack, beacon-spam variants, probe-flood. All HIGH-risk commands except `deauth_detect`. Refuses random/rickroll beacon spam unconditionally per the `BEACON_SPAM_*_REFUSE` config flags from Task 0.3.

---

### Task 2.1: Protocol — active Wi-Fi command builders

**Files:**
- Modify: `src/marauder_protocol.py`
- Modify: `tests/test_marauder_protocol.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_marauder_protocol.py`:

```python
class TestActiveWifiBuilders:
    def test_cmd_attack_deauth_single_no_target(self):
        assert mp.cmd_attack_deauth() == "attack -t deauth\r\n"

    def test_cmd_attack_deauth_single_with_target(self):
        assert mp.cmd_attack_deauth(target_idx=3, mode="single") == \
            "attack -t deauth -a 3\r\n"

    def test_cmd_attack_deauth_all(self):
        assert mp.cmd_attack_deauth(mode="all") == "attack -t deauth -c\r\n"

    def test_cmd_attack_deauth_detect(self):
        assert mp.cmd_attack_deauth_detect() == "attack -t deauth -d\r\n"

    def test_cmd_attack_beacon_random(self):
        assert mp.cmd_attack_beacon(mode="random") == "attack -t beacon -r\r\n"

    def test_cmd_attack_beacon_rickroll(self):
        assert mp.cmd_attack_beacon(mode="rickroll") == "attack -t rickroll\r\n"

    def test_cmd_attack_beacon_list(self):
        assert mp.cmd_attack_beacon(mode="list", list_idx=2) == \
            "attack -t beacon -l 2\r\n"

    def test_cmd_attack_beacon_list_requires_idx(self):
        import pytest
        with pytest.raises(ValueError, match="list_idx"):
            mp.cmd_attack_beacon(mode="list")

    def test_cmd_attack_probe_flood(self):
        assert mp.cmd_attack_probe_flood(list_idx=1) == "attack -t probe -l 1\r\n"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_marauder_protocol.py::TestActiveWifiBuilders -v`
Expected: 9 FAIL.

- [ ] **Step 3: Implement the builders**

Append to `src/marauder_protocol.py` (after the existing builder functions, before `_PARSERS`):

```python
def cmd_attack_deauth(target_idx: int | None = None,
                     mode: str = "single") -> str:
    if mode == "all":
        return "attack -t deauth -c\r\n"
    if mode == "single":
        if target_idx is None:
            return "attack -t deauth\r\n"
        return f"attack -t deauth -a {int(target_idx)}\r\n"
    raise ValueError(f"unknown deauth mode={mode}")


def cmd_attack_deauth_detect() -> str:
    return "attack -t deauth -d\r\n"


def cmd_attack_beacon(mode: str, list_idx: int | None = None) -> str:
    if mode == "random":
        return "attack -t beacon -r\r\n"
    if mode == "rickroll":
        return "attack -t rickroll\r\n"
    if mode == "list":
        if list_idx is None:
            raise ValueError("list_idx required for beacon mode=list")
        return f"attack -t beacon -l {int(list_idx)}\r\n"
    raise ValueError(f"unknown beacon mode={mode}")


def cmd_attack_probe_flood(list_idx: int) -> str:
    return f"attack -t probe -l {int(list_idx)}\r\n"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_marauder_protocol.py -v`
Expected: 25 PASS.

- [ ] **Step 5: Commit**

```bash
git -C /home/kali/drifter add tests/test_marauder_protocol.py src/marauder_protocol.py
git -C /home/kali/drifter commit -m "$(cat <<'EOF'
feat(marauder): protocol — active Wi-Fi command builders

deauth (single/all/no-target), deauth_detect, beacon (random/
rickroll/list), probe_flood. beacon mode=list raises if list_idx
missing (caller bug; loud failure preferred over silent malformed
CLI line).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2.2: Protocol — parse deauth + beacon TX events

**Files:**
- Modify: `src/marauder_protocol.py`
- Modify: `tests/test_marauder_protocol.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_marauder_protocol.py`:

```python
class TestParseActiveEvents:
    def test_parse_deauth_seen(self):
        line = "Deauth detected from aa:bb:cc:dd:ee:ff -> 11:22:33:44:55:66"
        ev = mp.parse_event(line)
        assert ev["type"] == "deauth_seen"
        assert ev["from_mac"] == "aa:bb:cc:dd:ee:ff"
        assert ev["to_mac"] == "11:22:33:44:55:66"

    def test_parse_deauth_tx(self):
        line = "Sent deauth pkt #1240 target=aa:bb:cc:dd:ee:ff"
        ev = mp.parse_event(line)
        assert ev["type"] == "deauth_tx"
        assert ev["pkt_n"] == 1240
        assert ev["target_bssid"] == "aa:bb:cc:dd:ee:ff"

    def test_parse_beacon_tx(self):
        line = 'Sent beacon pkt #42 ssid="ACME-Pentest-Guest"'
        ev = mp.parse_event(line)
        assert ev["type"] == "beacon_tx"
        assert ev["pkt_n"] == 42
        assert ev["ssid"] == "ACME-Pentest-Guest"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_marauder_protocol.py::TestParseActiveEvents -v`
Expected: 3 FAIL.

- [ ] **Step 3: Implement the parsers**

Add to `src/marauder_protocol.py` (before the `_PARSERS` list):

```python
_RE_DEAUTH_SEEN = re.compile(
    r"^Deauth detected from\s*(?P<from_mac>[0-9a-fA-F:]{17})\s*"
    r"(?:->|→)\s*(?P<to_mac>[0-9a-fA-F:]{17})\s*$"
)


def _build_deauth_seen(m: re.Match) -> dict:
    return {"from_mac": m.group("from_mac").lower(),
            "to_mac": m.group("to_mac").lower()}


_RE_DEAUTH_TX = re.compile(
    r"^Sent deauth pkt #(?P<pkt_n>\d+)\s+target=(?P<target>[0-9a-fA-F:]{17})\s*$"
)


def _build_deauth_tx(m: re.Match) -> dict:
    return {"pkt_n": int(m.group("pkt_n")),
            "target_bssid": m.group("target").lower()}


_RE_BEACON_TX = re.compile(
    r'^Sent beacon pkt #(?P<pkt_n>\d+)\s+ssid="(?P<ssid>.*)"\s*$'
)


def _build_beacon_tx(m: re.Match) -> dict:
    return {"pkt_n": int(m.group("pkt_n")), "ssid": m.group("ssid")}
```

Extend the `_PARSERS` list (order does not matter — distinct prefixes):

```python
_PARSERS.extend([
    (_RE_DEAUTH_SEEN, "deauth_seen", _build_deauth_seen),
    (_RE_DEAUTH_TX, "deauth_tx", _build_deauth_tx),
    (_RE_BEACON_TX, "beacon_tx", _build_beacon_tx),
])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_marauder_protocol.py -v`
Expected: 28 PASS.

- [ ] **Step 5: Commit**

```bash
git -C /home/kali/drifter add tests/test_marauder_protocol.py src/marauder_protocol.py
git -C /home/kali/drifter commit -m "$(cat <<'EOF'
feat(marauder): protocol — parse deauth + beacon TX events

deauth_seen (passive detection), deauth_tx (we are attacking), beacon_tx
(we are spamming). Used by the active_wifi feature module to drive
attack/status updates and the deauth-detector watch mode.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2.3: Audit session writer (HIGH-risk command JSON record)

**Files:**
- Modify: `src/marauder_storage.py`
- Modify: `tests/test_marauder_storage.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_marauder_storage.py`:

```python
class TestAuditSessionRecord:
    def test_write_attack_record_round_trip(self, tmp_path):
        s = ms.SessionWriter(state_root=tmp_path)
        ms.write_attack_audit(state_root=tmp_path, record={
            "id": "abc123",
            "operator_ip": "10.42.0.5",
            "started_ts": 1779600000.0,
            "ended_ts": 1779600060.0,
            "mode": "deauth_attack",
            "target_bssid": "aa:bb:cc:dd:ee:ff",
            "target_ssid": "ACME-Pentest-Guest",
            "allowlist_path": "/opt/drifter/etc/audit_targets.yaml",
            "allowlist_sha256": "deadbeef",
            "confirm_token_consumed": "tok-uuid",
            "packets_sent": 1240,
            "transport": "direct",
            "marauder_fw_banner": "Marauder v0.13.4",
            "stop_reason": "duration_elapsed",
        })
        files = list((tmp_path / "attacks").glob("*.json"))
        assert len(files) == 1
        data = json.loads(files[0].read_text())
        assert data["id"] == "abc123"
        assert data["allowlist_sha256"] == "deadbeef"

    def test_write_attack_record_rejects_missing_required_fields(self, tmp_path):
        """Invariant: every audit record contains the documented required
        fields, or it doesn't get written. Catches a class of caller bugs
        where attack lifecycle code forgets a field."""
        import pytest
        with pytest.raises(ValueError, match="missing required"):
            ms.write_attack_audit(state_root=tmp_path, record={
                "id": "abc",
                # missing operator_ip, mode, etc.
            })
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_marauder_storage.py::TestAuditSessionRecord -v`
Expected: 2 FAIL.

- [ ] **Step 3: Implement `write_attack_audit`**

Append to `src/marauder_storage.py`:

```python
ATTACK_REQUIRED_FIELDS = {
    "id", "operator_ip", "started_ts", "ended_ts", "mode",
    "allowlist_path", "allowlist_sha256",
    "confirm_token_consumed", "transport", "stop_reason",
}


def write_attack_audit(*, state_root: Path | str, record: dict) -> Path:
    """Write a HIGH-risk attack session record to attacks/<id>.json.

    Raises ValueError if the record is missing required fields. Returns
    the written file path. Idempotent — overwrites existing file with
    same id (the lifecycle ensures only one writer per session).
    """
    missing = ATTACK_REQUIRED_FIELDS - set(record.keys())
    if missing:
        raise ValueError(f"missing required attack-audit fields: {sorted(missing)}")
    root = Path(state_root)
    out_dir = root / "attacks"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{record['id']}.json"
    out_path.write_text(json.dumps(record, indent=2))
    return out_path
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_marauder_storage.py -v`
Expected: 6 PASS.

- [ ] **Step 5: Commit**

```bash
git -C /home/kali/drifter add tests/test_marauder_storage.py src/marauder_storage.py
git -C /home/kali/drifter commit -m "$(cat <<'EOF'
feat(marauder): storage — write_attack_audit with required-field invariant

Per-attack audit record at state/marauder/attacks/<id>.json. Required-
field check (operator_ip, mode, allowlist_sha256, transport, stop_reason,
etc.) is a hard invariant — caller bugs that omit a field surface
loudly via ValueError instead of producing a partial audit record.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2.4: Active Wi-Fi feature — deauth_detect (LOW)

**Files:**
- Modify: `src/marauder_features/active_wifi.py`
- Create: `tests/test_marauder_active_wifi.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_marauder_active_wifi.py
import sys
from pathlib import Path
from unittest.mock import MagicMock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from marauder_features import active_wifi as aw


class TestDeauthDetect:
    def test_start_detect_sends_correct_command(self):
        transport = MagicMock()
        transport.mode = "direct"
        result = aw.start_deauth_detect(transport, duration_s=60)
        transport.send.assert_called_once_with("attack -t deauth -d\r\n")
        assert result["ok"] is True
        assert result["duration_s"] == 60

    def test_no_hardware_refused(self):
        transport = MagicMock()
        transport.mode = "none"
        result = aw.start_deauth_detect(transport, duration_s=60)
        assert result["ok"] is False
        transport.send.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_marauder_active_wifi.py -v`
Expected: 2 FAIL.

- [ ] **Step 3: Implement**

Replace the body of `src/marauder_features/active_wifi.py`:

```python
"""MZ1312 DRIFTER — Marauder bridge module: active Wi-Fi (deauth/beacon/probe-flood)."""

import marauder_protocol as mp

MAX_ATTACK_DURATION_S = 300


def start_deauth_detect(transport, *, duration_s: int) -> dict:
    """Passive deauth frame listener. LOW risk — no RF emission."""
    if transport.mode == "none":
        return {"ok": False, "response": "no transport available"}
    capped = min(int(duration_s), MAX_ATTACK_DURATION_S)
    transport.send(mp.cmd_attack_deauth_detect())
    return {"ok": True, "response": "deauth_detect started",
            "duration_s": capped}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_marauder_active_wifi.py -v`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git -C /home/kali/drifter add tests/test_marauder_active_wifi.py src/marauder_features/active_wifi.py
git -C /home/kali/drifter commit -m "$(cat <<'EOF'
feat(marauder): features.active_wifi — start_deauth_detect (LOW risk)

Passive listen for deauth frames. Same shape as passive.start_scan
but routed through the deauth-detect CLI. Bridge classifies LOW so
no confirm/allowlist needed.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2.5: Active Wi-Fi — deauth_attack with target resolution

**Files:**
- Modify: `src/marauder_features/active_wifi.py`
- Modify: `tests/test_marauder_active_wifi.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_marauder_active_wifi.py`:

```python
class TestDeauthAttack:
    def test_attack_with_bssid_in_allowlist(self):
        transport = MagicMock()
        transport.mode = "direct"
        scope = {"wifi": [{"bssid": "aa:bb:cc:dd:ee:ff"}], "ble": [], "evilportal": []}
        result = aw.start_deauth_attack(
            transport, scope,
            bssid="aa:bb:cc:dd:ee:ff", ssid="ACME-Pentest", duration_s=60,
        )
        assert result["ok"] is True
        # Marauder firmware doesn't take a raw BSSID; the bridge sends the
        # no-target form and relies on the operator having pre-selected. For
        # the spec's design, this is fine — see §7.
        transport.send.assert_called_once_with("attack -t deauth\r\n")

    def test_attack_out_of_scope_refused(self):
        transport = MagicMock()
        transport.mode = "direct"
        scope = {"wifi": [{"bssid": "aa:bb:cc:dd:ee:ff"}], "ble": [], "evilportal": []}
        result = aw.start_deauth_attack(
            transport, scope,
            bssid="11:22:33:44:55:66", ssid="NotAuthorized", duration_s=60,
        )
        assert result["ok"] is False
        assert "allowlist" in result["response"].lower()
        transport.send.assert_not_called()

    def test_attack_duration_capped_at_300(self):
        transport = MagicMock()
        transport.mode = "direct"
        scope = {"wifi": [{"bssid": "aa:bb:cc:dd:ee:ff"}], "ble": [], "evilportal": []}
        result = aw.start_deauth_attack(
            transport, scope,
            bssid="aa:bb:cc:dd:ee:ff", ssid="x", duration_s=9999,
        )
        assert result["duration_s"] == 300
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_marauder_active_wifi.py::TestDeauthAttack -v`
Expected: 3 FAIL.

- [ ] **Step 3: Implement**

Append to `src/marauder_features/active_wifi.py`:

```python
import marauder_allowlist as ma


def start_deauth_attack(transport, allowlist_scope: dict, *,
                        bssid: str, ssid: str, duration_s: int) -> dict:
    if transport.mode == "none":
        return {"ok": False, "response": "no transport available"}

    # Allowlist re-check (defense in depth — Bridge also gates).
    ok, reason = ma.is_target_allowed(allowlist_scope, "wifi",
                                       bssid=bssid, ssid=ssid)
    if not ok:
        return {"ok": False, "response": reason}

    capped = min(int(duration_s), MAX_ATTACK_DURATION_S)
    transport.send(mp.cmd_attack_deauth())
    return {"ok": True, "response": f"deauth_attack started target={bssid}",
            "duration_s": capped, "target_bssid": bssid, "target_ssid": ssid}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_marauder_active_wifi.py -v`
Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git -C /home/kali/drifter add tests/test_marauder_active_wifi.py src/marauder_features/active_wifi.py
git -C /home/kali/drifter commit -m "$(cat <<'EOF'
feat(marauder): features.active_wifi — start_deauth_attack with scope check

Re-validates the target against the wifi allowlist (defense in depth
even though Bridge already gated). 300s hard cap. Returns the target
ssid/bssid in the result so the bridge can include them in
drifter/marauder/attack/status updates.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2.6: Active Wi-Fi — beacon_spam variants (with hard refusals)

**Files:**
- Modify: `src/marauder_features/active_wifi.py`
- Modify: `tests/test_marauder_active_wifi.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_marauder_active_wifi.py`:

```python
class TestBeaconSpam:
    def test_beacon_spam_random_refused(self):
        transport = MagicMock()
        transport.mode = "direct"
        result = aw.start_beacon_spam(transport, allowlist_scope={"wifi": []},
                                       mode="random", duration_s=60)
        assert result["ok"] is False
        assert "random" in result["response"].lower()
        transport.send.assert_not_called()

    def test_beacon_spam_rickroll_refused_when_no_wildcard(self):
        transport = MagicMock()
        transport.mode = "direct"
        result = aw.start_beacon_spam(transport, allowlist_scope={"wifi": []},
                                       mode="rickroll", duration_s=60)
        assert result["ok"] is False
        assert "rickroll" in result["response"].lower()
        transport.send.assert_not_called()

    def test_beacon_spam_rickroll_allowed_with_wildcard(self):
        transport = MagicMock()
        transport.mode = "direct"
        scope = {"wifi": [{"ssid": "*"}], "ble": [], "evilportal": []}
        result = aw.start_beacon_spam(transport, scope,
                                       mode="rickroll", duration_s=60)
        assert result["ok"] is True
        transport.send.assert_called_once_with("attack -t rickroll\r\n")

    def test_beacon_spam_list_all_in_scope(self, tmp_path):
        list_path = tmp_path / "list.txt"
        list_path.write_text("ACME-Pentest\nACME-Guest\n")
        transport = MagicMock()
        transport.mode = "direct"
        scope = {"wifi": [{"ssid": "ACME-Pentest"}, {"ssid": "ACME-Guest"}],
                 "ble": [], "evilportal": []}
        result = aw.start_beacon_spam(transport, scope, mode="list",
                                       beacon_list_path=str(list_path),
                                       list_idx=0, duration_s=60)
        assert result["ok"] is True

    def test_beacon_spam_list_partial_out_of_scope_refused(self, tmp_path):
        list_path = tmp_path / "list.txt"
        list_path.write_text("ACME-Pentest\nNotAllowed\n")
        transport = MagicMock()
        transport.mode = "direct"
        scope = {"wifi": [{"ssid": "ACME-Pentest"}], "ble": [], "evilportal": []}
        result = aw.start_beacon_spam(transport, scope, mode="list",
                                       beacon_list_path=str(list_path),
                                       list_idx=0, duration_s=60)
        assert result["ok"] is False
        assert "NotAllowed" in result["response"]
        transport.send.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_marauder_active_wifi.py::TestBeaconSpam -v`
Expected: 5 FAIL.

- [ ] **Step 3: Implement**

Append to `src/marauder_features/active_wifi.py`:

```python
from pathlib import Path

try:
    import config
except ImportError:
    config = None  # tests may run without the full drifter env


def _refuse_random_flag() -> bool:
    return getattr(config, "BEACON_SPAM_RANDOM_REFUSE", True) if config else True


def _refuse_rickroll_flag() -> bool:
    return getattr(config, "BEACON_SPAM_RICKROLL_REFUSE", True) if config else True


def _has_wildcard_wifi_scope(scope: dict) -> bool:
    return any(
        (entry.get("ssid") == "*") for entry in scope.get("wifi", [])
    )


def start_beacon_spam(transport, allowlist_scope: dict, *,
                     mode: str, duration_s: int,
                     beacon_list_path: str | None = None,
                     list_idx: int | None = None) -> dict:
    if transport.mode == "none":
        return {"ok": False, "response": "no transport available"}

    if mode == "random":
        if _refuse_random_flag():
            return {"ok": False,
                    "response": "beacon_spam random refused unconditionally "
                                "(see BEACON_SPAM_RANDOM_REFUSE in config.py)"}
        capped = min(int(duration_s), MAX_ATTACK_DURATION_S)
        transport.send(mp.cmd_attack_beacon(mode="random"))
        return {"ok": True, "response": "beacon_spam random started",
                "duration_s": capped}

    if mode == "rickroll":
        if _refuse_rickroll_flag() and not _has_wildcard_wifi_scope(allowlist_scope):
            return {"ok": False,
                    "response": "beacon_spam rickroll refused (set wifi[].ssid='*' "
                                "in allowlist AND flip BEACON_SPAM_RICKROLL_REFUSE)"}
        capped = min(int(duration_s), MAX_ATTACK_DURATION_S)
        transport.send(mp.cmd_attack_beacon(mode="rickroll"))
        return {"ok": True, "response": "beacon_spam rickroll started",
                "duration_s": capped}

    if mode == "list":
        if not beacon_list_path or list_idx is None:
            return {"ok": False,
                    "response": "beacon_spam list requires beacon_list_path + list_idx"}
        try:
            entries = [
                line.strip() for line in Path(beacon_list_path).read_text().splitlines()
                if line.strip()
            ]
        except OSError as e:
            return {"ok": False, "response": f"cannot read beacon list: {e}"}
        if not entries:
            return {"ok": False, "response": "beacon list is empty"}
        out_of_scope = [
            ssid for ssid in entries
            if not ma.is_target_allowed(allowlist_scope, "wifi", ssid=ssid, bssid="")[0]
        ]
        if out_of_scope:
            return {"ok": False,
                    "response": f"beacon list contains out-of-scope SSIDs: {out_of_scope[:5]}"}
        capped = min(int(duration_s), MAX_ATTACK_DURATION_S)
        transport.send(mp.cmd_attack_beacon(mode="list", list_idx=int(list_idx)))
        return {"ok": True, "response": f"beacon_spam list ({len(entries)} SSIDs) started",
                "duration_s": capped, "list_size": len(entries)}

    return {"ok": False, "response": f"unknown beacon mode={mode}"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_marauder_active_wifi.py -v`
Expected: 10 PASS.

- [ ] **Step 5: Commit**

```bash
git -C /home/kali/drifter add tests/test_marauder_active_wifi.py src/marauder_features/active_wifi.py
git -C /home/kali/drifter commit -m "$(cat <<'EOF'
feat(marauder): features.active_wifi — beacon_spam (list/random/rickroll)

random + rickroll refused unconditionally unless config.BEACON_SPAM_*_
REFUSE flipped (random) or wifi[].ssid='*' present in allowlist
(rickroll). list mode reads SSID list file, refuses if ANY entry is
out of allowlist scope.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2.7: Bridge — wire active Wi-Fi commands into `_execute`

**Files:**
- Modify: `src/marauder_bridge.py`
- Modify: `tests/test_marauder_bridge.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_marauder_bridge.py`:

```python
class TestActiveWifiDispatch:
    def test_deauth_detect_dispatches_without_confirm(self):
        bridge, transport, mqtt = TestDispatch()._make_bridge()
        bridge.dispatch({"id": "x", "command": "deauth_detect",
                         "args": {"duration_s": 30}})
        transport.send.assert_called_once_with("attack -t deauth -d\r\n")

    def test_deauth_attack_in_scope_executes_after_confirm(self):
        bridge, transport, mqtt = TestDispatch()._make_bridge()
        bridge.allowlist = {"wifi": [{"bssid": "aa:bb:cc:dd:ee:ff"}],
                            "ble": [], "evilportal": []}

        # First call → token
        bridge.dispatch({"id": "a", "command": "deauth_attack",
                         "args": {"bssid": "aa:bb:cc:dd:ee:ff",
                                  "ssid": "ACME"}})
        token = None
        for call in mqtt.publish.call_args_list:
            if call.args[0] == "drifter/marauder/event":
                ev = json.loads(call.args[1])
                if ev["id"] == "a":
                    token = ev.get("confirm_token")
        assert token

        # Second call with token → executes
        mqtt.publish.reset_mock()
        bridge.dispatch({"id": "b", "command": "deauth_attack",
                         "args": {"bssid": "aa:bb:cc:dd:ee:ff",
                                  "ssid": "ACME"},
                         "confirm_token": token})
        # find the matching event with id=b
        found = False
        for call in mqtt.publish.call_args_list:
            if call.args[0] == "drifter/marauder/event":
                ev = json.loads(call.args[1])
                if ev["id"] == "b":
                    found = True
                    assert ev["ok"] is True
        assert found
        transport.send.assert_called_with("attack -t deauth\r\n")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_marauder_bridge.py::TestActiveWifiDispatch -v`
Expected: 2 FAIL.

- [ ] **Step 3: Extend `Bridge._execute` to cover active Wi-Fi commands**

In `src/marauder_bridge.py`, add `from marauder_features import active_wifi as aw_feat` near the top, and extend `_execute()` (add these branches **after** the existing passive branches):

```python
        if command == "deauth_detect":
            return aw_feat.start_deauth_detect(self.transport,
                                                duration_s=args.get("duration_s", 60))
        if command == "deauth_attack":
            return aw_feat.start_deauth_attack(self.transport, self.allowlist,
                                                bssid=args.get("bssid", ""),
                                                ssid=args.get("ssid", ""),
                                                duration_s=args.get("duration_s", 60))
        if command == "beacon_spam_random":
            return aw_feat.start_beacon_spam(self.transport, self.allowlist,
                                              mode="random",
                                              duration_s=args.get("duration_s", 60))
        if command == "beacon_spam_rickroll":
            return aw_feat.start_beacon_spam(self.transport, self.allowlist,
                                              mode="rickroll",
                                              duration_s=args.get("duration_s", 60))
        if command == "beacon_spam_list":
            return aw_feat.start_beacon_spam(self.transport, self.allowlist,
                                              mode="list",
                                              beacon_list_path=args.get("beacon_list_path"),
                                              list_idx=args.get("list_idx"),
                                              duration_s=args.get("duration_s", 60))
        if command == "probe_flood":
            return {"ok": True, "response": "probe_flood not wired in this task; lands when allowlist + builder both exist"}
```

- [ ] **Step 4: Extend `_command_to_allowlist_category`**

Add the new HIGH-risk command names to the existing branch — already there from Task 1.17 (`deauth_attack`, `beacon_spam_*`, `probe_flood` already mapped to `wifi`). No change needed; verify by grep.

Run: `grep -A 3 "_command_to_allowlist_category" /home/kali/drifter/src/marauder_bridge.py`

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_marauder_bridge.py -v`
Expected: 10 PASS.

- [ ] **Step 6: Commit**

```bash
git -C /home/kali/drifter add tests/test_marauder_bridge.py src/marauder_bridge.py
git -C /home/kali/drifter commit -m "$(cat <<'EOF'
feat(marauder): bridge — wire active Wi-Fi commands into dispatch

deauth_detect (LOW, runs immediately) + deauth_attack / beacon_spam_*
(HIGH, gated by confirm + allowlist) plumbed through Bridge._execute.
probe_flood placeholder until Task 2.8 implements its builder + scope
check.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2.8: Probe flood + active Wi-Fi acceptance

**Files:**
- Modify: `src/marauder_features/active_wifi.py`
- Modify: `src/marauder_bridge.py`
- Modify: `tests/test_marauder_active_wifi.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_marauder_active_wifi.py`:

```python
class TestProbeFlood:
    def test_probe_flood_all_in_scope(self, tmp_path):
        list_path = tmp_path / "list.txt"
        list_path.write_text("AcmeWifi\nAcmeGuest\n")
        transport = MagicMock()
        transport.mode = "direct"
        scope = {"wifi": [{"ssid": "AcmeWifi"}, {"ssid": "AcmeGuest"}],
                 "ble": [], "evilportal": []}
        result = aw.start_probe_flood(transport, scope,
                                       beacon_list_path=str(list_path),
                                       list_idx=0, duration_s=30)
        assert result["ok"] is True
        transport.send.assert_called_once_with("attack -t probe -l 0\r\n")

    def test_probe_flood_partial_out_of_scope_refused(self, tmp_path):
        list_path = tmp_path / "list.txt"
        list_path.write_text("AcmeWifi\nBadGuest\n")
        transport = MagicMock()
        transport.mode = "direct"
        scope = {"wifi": [{"ssid": "AcmeWifi"}], "ble": [], "evilportal": []}
        result = aw.start_probe_flood(transport, scope,
                                       beacon_list_path=str(list_path),
                                       list_idx=0, duration_s=30)
        assert result["ok"] is False
        assert "BadGuest" in result["response"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_marauder_active_wifi.py::TestProbeFlood -v`
Expected: 2 FAIL.

- [ ] **Step 3: Implement `start_probe_flood`**

Append to `src/marauder_features/active_wifi.py`:

```python
def start_probe_flood(transport, allowlist_scope: dict, *,
                     beacon_list_path: str, list_idx: int,
                     duration_s: int) -> dict:
    if transport.mode == "none":
        return {"ok": False, "response": "no transport available"}
    try:
        entries = [
            line.strip() for line in Path(beacon_list_path).read_text().splitlines()
            if line.strip()
        ]
    except OSError as e:
        return {"ok": False, "response": f"cannot read probe list: {e}"}
    if not entries:
        return {"ok": False, "response": "probe list is empty"}
    out_of_scope = [
        ssid for ssid in entries
        if not ma.is_target_allowed(allowlist_scope, "wifi", ssid=ssid, bssid="")[0]
    ]
    if out_of_scope:
        return {"ok": False,
                "response": f"probe list contains out-of-scope SSIDs: {out_of_scope[:5]}"}
    capped = min(int(duration_s), MAX_ATTACK_DURATION_S)
    transport.send(mp.cmd_attack_probe_flood(list_idx=int(list_idx)))
    return {"ok": True, "response": f"probe_flood ({len(entries)} SSIDs) started",
            "duration_s": capped, "list_size": len(entries)}
```

In `src/marauder_bridge.py`, replace the `probe_flood` placeholder branch in `_execute()`:

```python
        if command == "probe_flood":
            return aw_feat.start_probe_flood(self.transport, self.allowlist,
                                              beacon_list_path=args.get("beacon_list_path"),
                                              list_idx=args.get("list_idx", 0),
                                              duration_s=args.get("duration_s", 60))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_marauder_active_wifi.py tests/test_marauder_bridge.py -v`
Expected: All PASS.

- [ ] **Step 5: Commit + tag Phase 2 acceptance**

```bash
git -C /home/kali/drifter add tests/test_marauder_active_wifi.py src/marauder_features/active_wifi.py src/marauder_bridge.py
git -C /home/kali/drifter commit -m "$(cat <<'EOF'
feat(marauder): features.active_wifi — start_probe_flood + bridge wire

Probe flood reads SSID list, refuses if any SSID out of wifi
allowlist scope, sends attack -t probe -l <idx>. 300s cap. Same
allowlist contract as beacon_spam list mode.

Phase 2 (active Wi-Fi) complete.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"

git -C /home/kali/drifter tag -a marauder-phase2-accept -m "Phase 2 (active Wi-Fi) acceptance criteria met"
```

---

## Phase 3 — BLE recon + spam

BLE scan/AirTag/skim (LOW), BLE spam (HIGH with `area_authorized` allowlist semantics), iOS-crash collateral warning gate.

---

### Task 3.1: Protocol — BLE command builders

**Files:**
- Modify: `src/marauder_protocol.py`
- Modify: `tests/test_marauder_protocol.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_marauder_protocol.py`:

```python
class TestBLEBuilders:
    def test_cmd_ble_scan_all(self):
        assert mp.cmd_ble_scan("all") == "blescan -t all\r\n"

    def test_cmd_ble_scan_airtag(self):
        assert mp.cmd_ble_scan("airtag") == "blescan -t airtag\r\n"

    def test_cmd_ble_scan_skim(self):
        assert mp.cmd_ble_scan("skim") == "blescan -t skim\r\n"

    def test_cmd_ble_scan_unknown_raises(self):
        import pytest
        with pytest.raises(ValueError, match="ble scan"):
            mp.cmd_ble_scan("bogus")

    def test_cmd_ble_spam_variants(self):
        assert mp.cmd_ble_spam("swift") == "blespam -t swift\r\n"
        assert mp.cmd_ble_spam("samsung") == "blespam -t samsung\r\n"
        assert mp.cmd_ble_spam("apple") == "blespam -t apple\r\n"
        assert mp.cmd_ble_spam("all") == "blespam -t all\r\n"

    def test_cmd_ble_spam_unknown_raises(self):
        import pytest
        with pytest.raises(ValueError, match="ble spam"):
            mp.cmd_ble_spam("bogus")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_marauder_protocol.py::TestBLEBuilders -v`
Expected: 6 FAIL.

- [ ] **Step 3: Implement the builders**

Append to `src/marauder_protocol.py` (after the active Wi-Fi builders):

```python
_BLE_SCAN_MODES = {"all", "airtag", "skim"}
_BLE_SPAM_MODES = {"swift", "samsung", "apple", "all"}


def cmd_ble_scan(mode: str) -> str:
    if mode not in _BLE_SCAN_MODES:
        raise ValueError(f"unknown ble scan mode={mode} (want one of {_BLE_SCAN_MODES})")
    return f"blescan -t {mode}\r\n"


def cmd_ble_spam(mode: str) -> str:
    if mode not in _BLE_SPAM_MODES:
        raise ValueError(f"unknown ble spam mode={mode} (want one of {_BLE_SPAM_MODES})")
    return f"blespam -t {mode}\r\n"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_marauder_protocol.py -v`
Expected: 34 PASS.

- [ ] **Step 5: Commit**

```bash
git -C /home/kali/drifter add tests/test_marauder_protocol.py src/marauder_protocol.py
git -C /home/kali/drifter commit -m "$(cat <<'EOF'
feat(marauder): protocol — BLE command builders

blescan {all,airtag,skim}, blespam {swift,samsung,apple,all}. Unknown
modes raise ValueError (caller bug, not silent malformed CLI).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3.2: Protocol — parse BLE events

**Files:**
- Modify: `src/marauder_protocol.py`
- Modify: `tests/test_marauder_protocol.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_marauder_protocol.py`:

```python
class TestParseBLEEvents:
    def test_parse_airtag(self):
        line = "BLE: AirTag spotted aa:bb:cc:dd:ee:ff RSSI -55"
        ev = mp.parse_event(line)
        assert ev["type"] == "airtag"
        assert ev["mac"] == "aa:bb:cc:dd:ee:ff"
        assert ev["rssi"] == -55

    def test_parse_skimmer(self):
        line = "BLE: skimmer fingerprint aa:bb:cc:dd:ee:ff"
        ev = mp.parse_event(line)
        assert ev["type"] == "skimmer"
        assert ev["mac"] == "aa:bb:cc:dd:ee:ff"

    def test_parse_ble_device(self):
        line = 'BLE: device aa:bb:cc:dd:ee:ff name="Galaxy Buds Pro" RSSI -72'
        ev = mp.parse_event(line)
        assert ev["type"] == "ble_device"
        assert ev["mac"] == "aa:bb:cc:dd:ee:ff"
        assert ev["name"] == "Galaxy Buds Pro"
        assert ev["rssi"] == -72

    def test_parse_ble_device_no_name(self):
        line = 'BLE: device aa:bb:cc:dd:ee:ff name="" RSSI -85'
        ev = mp.parse_event(line)
        assert ev["type"] == "ble_device"
        assert ev["name"] == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_marauder_protocol.py::TestParseBLEEvents -v`
Expected: 4 FAIL.

- [ ] **Step 3: Implement the parsers**

Add to `src/marauder_protocol.py`:

```python
_RE_AIRTAG = re.compile(
    r"^BLE:\s*AirTag spotted\s*(?P<mac>[0-9a-fA-F:]{17})\s*RSSI\s*(?P<rssi>-?\d+)\s*$"
)


def _build_airtag(m: re.Match) -> dict:
    return {"mac": m.group("mac").lower(), "rssi": int(m.group("rssi"))}


_RE_SKIMMER = re.compile(
    r"^BLE:\s*skimmer fingerprint\s*(?P<mac>[0-9a-fA-F:]{17})\s*$"
)


def _build_skimmer(m: re.Match) -> dict:
    return {"mac": m.group("mac").lower()}


_RE_BLE_DEVICE = re.compile(
    r'^BLE:\s*device\s*(?P<mac>[0-9a-fA-F:]{17})\s*'
    r'name="(?P<name>.*)"\s*RSSI\s*(?P<rssi>-?\d+)\s*$'
)


def _build_ble_device(m: re.Match) -> dict:
    return {"mac": m.group("mac").lower(),
            "name": m.group("name"),
            "rssi": int(m.group("rssi"))}


_PARSERS.extend([
    (_RE_AIRTAG, "airtag", _build_airtag),
    (_RE_SKIMMER, "skimmer", _build_skimmer),
    (_RE_BLE_DEVICE, "ble_device", _build_ble_device),
])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_marauder_protocol.py -v`
Expected: 38 PASS.

- [ ] **Step 5: Commit**

```bash
git -C /home/kali/drifter add tests/test_marauder_protocol.py src/marauder_protocol.py
git -C /home/kali/drifter commit -m "$(cat <<'EOF'
feat(marauder): protocol — parse BLE events (airtag/skimmer/device)

airtag + skimmer are specific patterns the dashboard surfaces in their
own streams. ble_device is the generic catch-all from blescan -t all.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3.3: Allowlist — BLE category check (area_authorized + per-MAC)

**Files:**
- Modify: `src/marauder_allowlist.py`
- Modify: `tests/test_marauder_allowlist.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_marauder_allowlist.py`:

```python
class TestIsTargetAllowedBLE:
    def test_empty_ble_refuses(self):
        ok, reason = ma.is_target_allowed(
            {"wifi": [], "ble": [], "evilportal": []},
            "ble", mac="aa:bb:cc:dd:ee:ff", action="scan",
        )
        assert ok is False
        assert "empty" in reason.lower()

    def test_specific_mac_allows_for_targeted_action(self):
        scope = {"wifi": [], "ble": [{"mac": "aa:bb:cc:dd:ee:ff"}],
                 "evilportal": []}
        ok, reason = ma.is_target_allowed(
            scope, "ble", mac="aa:bb:cc:dd:ee:ff", action="targeted",
        )
        assert ok is True

    def test_specific_mac_does_NOT_allow_indiscriminate_spam(self):
        """Per-MAC scope only authorizes targeted operations. Spam
        requires the area_authorized entry."""
        scope = {"wifi": [], "ble": [{"mac": "aa:bb:cc:dd:ee:ff"}],
                 "evilportal": []}
        ok, reason = ma.is_target_allowed(
            scope, "ble", mac="aa:bb:cc:dd:ee:ff", action="spam",
        )
        assert ok is False
        assert "area_authorized" in reason

    def test_area_authorized_allows_spam(self):
        scope = {"wifi": [],
                 "ble": [{"area_authorized": True, "area_label": "ACME lab 204"}],
                 "evilportal": []}
        ok, reason = ma.is_target_allowed(
            scope, "ble", mac=None, action="spam",
        )
        assert ok is True
        assert "ACME lab 204" in reason

    def test_area_authorized_without_label_refused(self):
        """Operator must provide an area_label — friction point."""
        scope = {"wifi": [],
                 "ble": [{"area_authorized": True}],
                 "evilportal": []}
        ok, reason = ma.is_target_allowed(
            scope, "ble", mac=None, action="spam",
        )
        assert ok is False
        assert "area_label" in reason
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_marauder_allowlist.py::TestIsTargetAllowedBLE -v`
Expected: 5 FAIL.

- [ ] **Step 3: Replace `_check_ble` stub**

In `src/marauder_allowlist.py`, replace the existing `_check_ble` function:

```python
def _check_ble(entries: list[dict], fields: dict) -> tuple[bool, str]:
    action = fields.get("action", "targeted")  # 'targeted' | 'spam' | 'scan'
    mac = (fields.get("mac") or "").lower()

    if action in ("targeted", "scan"):
        for entry in entries:
            if "mac" in entry and entry["mac"].lower() == mac:
                return True, f"matched ble mac={mac}"
        return False, "no per-mac match in ble allowlist"

    if action == "spam":
        for entry in entries:
            if entry.get("area_authorized") is True:
                label = entry.get("area_label")
                if not label:
                    return False, ("ble area_authorized entry missing area_label "
                                   "— operator must record where authorization applies")
                return True, f"matched area_authorized: {label}"
        return False, "no area_authorized:true entry in ble allowlist for spam"

    return False, f"unknown ble action={action}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_marauder_allowlist.py -v`
Expected: 15 PASS.

- [ ] **Step 5: Commit**

```bash
git -C /home/kali/drifter add tests/test_marauder_allowlist.py src/marauder_allowlist.py
git -C /home/kali/drifter commit -m "$(cat <<'EOF'
feat(marauder): allowlist — BLE category (mac + area_authorized)

Per-MAC entries authorize targeted ops (scan, targeted). Spam requires
area_authorized:true PLUS a non-empty area_label (friction point —
forces operator to write down where they're authorized).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3.4: BLE feature — scan (all/airtag/skim) + spam dispatch

**Files:**
- Modify: `src/marauder_features/ble.py`
- Create: `tests/test_marauder_ble.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_marauder_ble.py
import sys
from pathlib import Path
from unittest.mock import MagicMock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from marauder_features import ble


class TestBLEScan:
    def test_scan_all(self):
        transport = MagicMock()
        transport.mode = "direct"
        result = ble.start_scan(transport, mode="all", duration_s=30)
        assert result["ok"] is True
        transport.send.assert_called_once_with("blescan -t all\r\n")

    def test_scan_airtag(self):
        transport = MagicMock()
        transport.mode = "direct"
        result = ble.start_scan(transport, mode="airtag", duration_s=30)
        transport.send.assert_called_once_with("blescan -t airtag\r\n")

    def test_scan_skim(self):
        transport = MagicMock()
        transport.mode = "direct"
        result = ble.start_scan(transport, mode="skim", duration_s=30)
        transport.send.assert_called_once_with("blescan -t skim\r\n")

    def test_unknown_mode_refused(self):
        transport = MagicMock()
        transport.mode = "direct"
        result = ble.start_scan(transport, mode="bogus", duration_s=30)
        assert result["ok"] is False


class TestBLESpam:
    def _scope_authorized(self):
        return {"wifi": [], "evilportal": [],
                "ble": [{"area_authorized": True, "area_label": "test lab"}]}

    def test_spam_swift_authorized(self):
        transport = MagicMock(); transport.mode = "direct"
        result = ble.start_spam(transport, self._scope_authorized(),
                                mode="swift", duration_s=60)
        assert result["ok"] is True
        assert result["area_label_at_runtime"] == "test lab"

    def test_spam_unauthorized_refused(self):
        transport = MagicMock(); transport.mode = "direct"
        scope = {"wifi": [], "ble": [], "evilportal": []}
        result = ble.start_spam(transport, scope, mode="swift", duration_s=60)
        assert result["ok"] is False
        transport.send.assert_not_called()

    def test_spam_duration_capped_at_300(self):
        transport = MagicMock(); transport.mode = "direct"
        result = ble.start_spam(transport, self._scope_authorized(),
                                mode="swift", duration_s=9999)
        assert result["duration_s"] == 300

    def test_spam_apple_emits_collateral_warning_first_time(self):
        transport = MagicMock(); transport.mode = "direct"
        ble.reset_collateral_warning_state()  # test helper
        result = ble.start_spam(transport, self._scope_authorized(),
                                mode="apple", duration_s=60, acked_warning=True)
        assert result["ok"] is True
        assert result["collateral_warning_emitted"] is True

    def test_spam_apple_requires_warning_ack_on_first_run(self):
        transport = MagicMock(); transport.mode = "direct"
        ble.reset_collateral_warning_state()
        result = ble.start_spam(transport, self._scope_authorized(),
                                mode="apple", duration_s=60, acked_warning=False)
        assert result["ok"] is False
        assert "collateral" in result["response"].lower()
        transport.send.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_marauder_ble.py -v`
Expected: 9 FAIL.

- [ ] **Step 3: Implement**

Replace the body of `src/marauder_features/ble.py`:

```python
"""MZ1312 DRIFTER — Marauder bridge module: BLE recon + spam."""

import marauder_allowlist as ma
import marauder_protocol as mp

MAX_SCAN_DURATION_S = 600
MAX_ATTACK_DURATION_S = 300

# Per-process state: track whether apple-proximity collateral warning
# has been emitted in this service-start lifetime.
_apple_warned: bool = False


def reset_collateral_warning_state() -> None:
    """Test helper — also called on service start in production."""
    global _apple_warned
    _apple_warned = False


def start_scan(transport, *, mode: str, duration_s: int) -> dict:
    if transport.mode == "none":
        return {"ok": False, "response": "no transport available"}
    try:
        cmd = mp.cmd_ble_scan(mode)
    except ValueError as e:
        return {"ok": False, "response": str(e)}
    capped = min(int(duration_s), MAX_SCAN_DURATION_S)
    transport.send(cmd)
    return {"ok": True, "response": f"ble scan started mode={mode}",
            "duration_s": capped}


def start_spam(transport, allowlist_scope: dict, *,
              mode: str, duration_s: int,
              acked_warning: bool = False) -> dict:
    """BLE indiscriminate spam. Requires area_authorized scope. For
    'apple' and 'all', first invocation per service-start ALSO requires
    acked_warning=True (collateral warning has been shown to operator).
    """
    global _apple_warned
    if transport.mode == "none":
        return {"ok": False, "response": "no transport available"}

    ok, reason = ma.is_target_allowed(allowlist_scope, "ble",
                                       mac=None, action="spam")
    if not ok:
        return {"ok": False, "response": reason}

    area_label = next(
        (e.get("area_label") for e in allowlist_scope.get("ble", [])
         if e.get("area_authorized")),
        None,
    )

    try:
        cmd = mp.cmd_ble_spam(mode)
    except ValueError as e:
        return {"ok": False, "response": str(e)}

    warning_emitted = False
    if mode in ("apple", "all") and not _apple_warned:
        if not acked_warning:
            return {"ok": False,
                    "response": "ble apple proximity spam: collateral warning not yet acked. "
                                "This affects ALL nearby iOS devices, can crash iOS<17. "
                                "Re-send with acked_warning=true to proceed."}
        _apple_warned = True
        warning_emitted = True

    capped = min(int(duration_s), MAX_ATTACK_DURATION_S)
    transport.send(cmd)
    return {"ok": True,
            "response": f"ble spam started mode={mode}",
            "duration_s": capped,
            "area_label_at_runtime": area_label,
            "collateral_warning_emitted": warning_emitted}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_marauder_ble.py -v`
Expected: 9 PASS.

- [ ] **Step 5: Commit**

```bash
git -C /home/kali/drifter add tests/test_marauder_ble.py src/marauder_features/ble.py
git -C /home/kali/drifter commit -m "$(cat <<'EOF'
feat(marauder): features.ble — scan (all/airtag/skim) + spam (gated)

start_scan is LOW-risk dispatch (no allowlist check). start_spam
gates on area_authorized in ble allowlist + captures area_label into
return for the bridge to record in the attack audit. apple/all modes
require acked_warning=true the FIRST time per service-start
(iOS-crash collateral warning).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3.5: Bridge — wire BLE commands into `_execute`

**Files:**
- Modify: `src/marauder_bridge.py`
- Modify: `tests/test_marauder_bridge.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_marauder_bridge.py`:

```python
class TestBLEDispatch:
    def test_ble_scan_airtag_low_risk(self):
        bridge, transport, mqtt = TestDispatch()._make_bridge()
        bridge.dispatch({"id": "x", "command": "ble_scan_airtag",
                         "args": {"duration_s": 60}})
        transport.send.assert_called_once_with("blescan -t airtag\r\n")

    def test_ble_spam_high_risk_refused_when_empty(self):
        bridge, transport, mqtt = TestDispatch()._make_bridge()

        # First call → confirm_token
        bridge.dispatch({"id": "a", "command": "ble_spam_swift_pair",
                         "args": {"duration_s": 30}})
        token = None
        for call in mqtt.publish.call_args_list:
            if call.args[0] == "drifter/marauder/event":
                ev = json.loads(call.args[1])
                if ev["id"] == "a":
                    token = ev.get("confirm_token")
        assert token

        # Second call → refused (empty allowlist)
        mqtt.publish.reset_mock()
        bridge.dispatch({"id": "b", "command": "ble_spam_swift_pair",
                         "args": {"duration_s": 30},
                         "confirm_token": token})
        for call in mqtt.publish.call_args_list:
            if call.args[0] == "drifter/marauder/event":
                ev = json.loads(call.args[1])
                if ev["id"] == "b":
                    assert ev["ok"] is False
                    assert "empty" in ev["response"].lower() or \
                           "area_authorized" in ev["response"].lower()
                    transport.send.assert_not_called()
                    return
        raise AssertionError("no event for op_id=b")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_marauder_bridge.py::TestBLEDispatch -v`
Expected: 2 FAIL.

- [ ] **Step 3: Wire into `Bridge._execute`**

In `src/marauder_bridge.py`, add `from marauder_features import ble as ble_feat` and add to `_execute()` (after the active Wi-Fi branches):

```python
        if command in ("ble_scan_all", "ble_scan_airtag", "ble_scan_skim"):
            mode = command.split("_")[-1]
            if mode == "airtag":
                mode_arg = "airtag"
            elif mode == "skim":
                mode_arg = "skim"
            else:
                mode_arg = "all"
            return ble_feat.start_scan(self.transport, mode=mode_arg,
                                        duration_s=args.get("duration_s", 60))
        if command in ("ble_spam_swift_pair", "ble_spam_easy_setup",
                       "ble_spam_apple_proximity", "ble_spam_all"):
            mode_map = {
                "ble_spam_swift_pair": "swift",
                "ble_spam_easy_setup": "samsung",
                "ble_spam_apple_proximity": "apple",
                "ble_spam_all": "all",
            }
            return ble_feat.start_spam(self.transport, self.allowlist,
                                        mode=mode_map[command],
                                        duration_s=args.get("duration_s", 60),
                                        acked_warning=args.get("acked_warning", False))
```

`_command_to_allowlist_category` already maps the ble_spam_* commands to `"ble"` (Task 1.17). Verify with grep.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_marauder_bridge.py -v`
Expected: 12 PASS.

- [ ] **Step 5: Commit + tag Phase 3 acceptance**

```bash
git -C /home/kali/drifter add tests/test_marauder_bridge.py src/marauder_bridge.py
git -C /home/kali/drifter commit -m "$(cat <<'EOF'
feat(marauder): bridge — wire BLE scan + spam commands

ble_scan_{all,airtag,skim} LOW-risk → immediate execute. ble_spam_*
HIGH-risk → confirm + ble allowlist gate (area_authorized required).
acked_warning is passed through for apple/all collateral-warning gate.

Phase 3 (BLE) complete.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"

git -C /home/kali/drifter tag -a marauder-phase3-accept -m "Phase 3 (BLE recon + spam) acceptance criteria met"
```

---


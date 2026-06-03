#!/usr/bin/env python3
"""
MZ1312 DRIFTER — HID Injection Service (drifter-hid).

FOOT-ONLY. Consumes drifter/hid/command and runs the authoritative
ARM → CONFIRM → RUN state machine for the Rubber Ducky / BadUSB
capability. Two selectable backends:

  * flipper — push the DuckyScript .txt to the Flipper's /ext/badusb and
    fire it via flipper_bridge (over drifter/flipper/command). The bridge
    classifies badusb/loader/storage-write as HIGH, but drifter-hid issues
    the bridge's confirm token itself once the operator's RUN lands, so
    that downstream confirm is plumbing — NOT an independent second human
    gate. The SINGLE authoritative human gate is the ARM → CONFIRM → RUN
    state machine below: there is NO code path from payload-upload to
    keystroke-injection that skips the operator's CONFIRM.
  * native — Pi USB-gadget HID (Stage 6). On a node booted with the
    native profile (dr_mode ∈ {peripheral, otg} + /dev/hidg0 + a bindable
    UDC) the hid_gadget configfs lifecycle binds the Pi as a USB boot
    keyboard and writes the hid_ducky-compiled 8-byte reports to
    /dev/hidg0 on RUN. On THIS live vehicle node the USB-C port boots
    'host', so native cleanly refuses 'not configured'. It NEVER
    fakes-ready and NEVER injects unless the boot profile + UDC are real.
    Enabling the profile is the reboot-gated 'drifter hid enable-native'
    opt-in — never auto-toggled while live.

Safety model (spec §2.2/2.3/2.5), enforced by DESIGN not policy:
  * ARM validates (payload exists + compiles, backend ready), stores a
    pending entry, and publishes an ARMED preview. NOTHING is typed.
  * CONFIRM must match a pending id within 60s. Else REJECT/EXPIRE.
  * RUN fires only on a confirmed id and is SINGLE-SHOT — the pending
    entry is popped before execution (no replay without re-ARM).
  * No auto-run, no triggers (insert/time/geofence/event), no
    persistence, no covert/evasion features. Not built.
  * Full append-only JSONL audit (/opt/drifter/state/hid_audit.log) +
    publish drifter/hid/audit (retained=false). Peer IP on every
    operator event.

UNCAGED TECHNOLOGY — EST 1991
"""
from __future__ import annotations

import json
import logging
import signal
import time
import uuid
from pathlib import Path

import hid_ducky
import hid_gadget
from config import MQTT_HOST, MQTT_PORT, TOPICS, make_mqtt_client

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [HID] %(message)s',
    datefmt='%H:%M:%S',
)

# ── Storage ───────────────────────────────────────────────────────────
HID_PAYLOAD_DIR = Path('/opt/drifter/state/hid_payloads')
HID_AUDIT_LOG = Path('/opt/drifter/state/hid_audit.log')

# Where the device-tree usb dr_mode is read from on the Pi. Backend B
# (native) refuses unless dr_mode ∈ {peripheral, otg}. On this live node
# it is 'host' (the USB-C power port), so native is NOT configured.
_DEVICE_TREE_GLOBS = (
    '/proc/device-tree/soc/usb@*/dr_mode',
    '/proc/device-tree/axi/usb@*/dr_mode',
    '/proc/device-tree/*usb*/dr_mode',
)
_UDC_DIR = Path('/sys/class/udc')
_HIDG0 = Path('/dev/hidg0')

# Confirm window — mirrors flipper_bridge HIGH-risk path exactly.
CONFIRM_EXPIRY_S = 60

VALID_BACKENDS = ('native', 'flipper')

running = True


# ═══════════════════════════════════════════════════════════════════
#  Native backend readiness (Stage 5: refusal path only)
# ═══════════════════════════════════════════════════════════════════

def read_dr_mode() -> str:
    """Read the USB controller dr_mode from the device tree.

    Returns 'host' / 'peripheral' / 'otg' / 'unknown'. Never raises — a
    missing node reads as 'unknown'. On this Pi 5 node the USB-C port is
    'host' so the native backend stays unconfigured.
    """
    import glob
    for pattern in _DEVICE_TREE_GLOBS:
        for path in glob.glob(pattern):
            try:
                raw = Path(path).read_bytes()
            except OSError:
                continue
            val = raw.split(b'\x00', 1)[0].decode('ascii', 'replace').strip()
            if val:
                return val
    return 'unknown'


def native_status() -> dict:
    """Honest native-backend readiness. NEVER fakes ready.

    Stage 6: readiness is delegated to the hid_gadget configfs lifecycle.
    Native is reported ready ONLY when the boot profile is real —
    dr_mode ∈ {peripheral, otg} AND /dev/hidg0 present AND a bindable UDC
    exists. On this live node the USB-C port boots 'host', so native is
    reported not configured and every gadget op hard-refuses. Reports the
    boot role read from the device tree + /sys/class/udc + /dev/hidg0
    presence; the UI consumes this read-only and can never flip dr_mode.
    """
    dr_mode = read_dr_mode()
    udcs = hid_gadget.list_udcs(_UDC_DIR)
    hidg0_present = _HIDG0.exists()
    configured = dr_mode in ('peripheral', 'otg')
    ready, ready_reason = hid_gadget.gadget_ready(_gadget_controller())
    if not configured:
        reason = (
            f"native backend not configured (requires boot profile) — "
            f"dr_mode={dr_mode} (need peripheral/otg). Run 'drifter hid enable-native' "
            "+ reboot. The USB-C port is the Pi 5 power input; enabling is a "
            "deliberate reboot-gated opt-in."
        )
    else:
        reason = ready_reason
    return {
        'dr_mode': dr_mode,
        'hidg0_present': hidg0_present,
        'bound': configured and hidg0_present and bool(udcs),
        'boot_profile': 'gadget' if configured else 'host',
        'configured': configured,
        'ready': ready,
        'reason': reason,
    }


def _gadget_controller() -> hid_gadget.GadgetController:
    """Build a GadgetController bound to this module's device paths.

    Kept tiny so tests can monkeypatch hid_gadget.read_dr_mode / paths and
    have native_status + the RUN path follow the same readiness verdict.
    """
    return hid_gadget.GadgetController(
        hidg_path=_HIDG0,
        udc_dir=_UDC_DIR,
        dr_mode_reader=read_dr_mode,
    )


# ═══════════════════════════════════════════════════════════════════
#  Payload storage
# ═══════════════════════════════════════════════════════════════════

def payload_paths(payload_id: str):
    return (HID_PAYLOAD_DIR / f'{payload_id}.txt',
            HID_PAYLOAD_DIR / f'{payload_id}.meta.json')


def load_payload(payload_id: str) -> dict | None:
    """Return {'script', 'meta'} for a stored payload, or None if absent.

    Path-traversal guarded: a payload_id with a path separator or '..' is
    rejected (returns None) so a relayed id can never escape the dir.
    """
    if (not payload_id or '/' in payload_id or '\\' in payload_id
            or '..' in payload_id):
        return None
    txt, meta = payload_paths(payload_id)
    if not txt.exists():
        return None
    try:
        script = txt.read_text(encoding='utf-8')
    except OSError:
        return None
    meta_obj = {}
    if meta.exists():
        try:
            meta_obj = json.loads(meta.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            meta_obj = {}
    return {'script': script, 'meta': meta_obj}


# ═══════════════════════════════════════════════════════════════════
#  Audit
# ═══════════════════════════════════════════════════════════════════

def audit(mqtt_client, event: str, peer: str = '', **fields) -> None:
    """Append one JSONL record to the HID audit log AND publish it.

    Append-only. Never raises — a failed audit write is logged at WARNING
    but must not break the safety state machine. The peer IP is recorded
    on every operator-initiated event.
    """
    record = {'ts': time.time(), 'event': event, 'peer': peer}
    record.update(fields)
    try:
        HID_AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with HID_AUDIT_LOG.open('a', encoding='utf-8') as fh:
            fh.write(json.dumps(record, default=str) + '\n')
    except OSError as e:
        log.warning("hid audit write failed: %s", e)
    if mqtt_client is not None:
        try:
            mqtt_client.publish(
                TOPICS['hid_audit'], json.dumps(record, default=str),
                qos=1, retain=False)
        except Exception as e:
            log.warning("hid audit publish failed: %s", e)
    log.info("hid-audit %s", json.dumps(record, default=str))


# ═══════════════════════════════════════════════════════════════════
#  ARM → CONFIRM → RUN state machine
# ═══════════════════════════════════════════════════════════════════

# Pending ARMED entries keyed by arm id:
#   {id, payload_id, backend, line_count, keystrokes, sha256, ts, peer}
pending_confirms: dict[str, dict] = {}


class HidStateMachine:
    """The authoritative ARM→CONFIRM→RUN gate for drifter-hid.

    Holds no UI / I/O assumptions beyond an injected MQTT client and a
    flipper-relay callable so it is fully unit-testable. This state
    machine is the SINGLE authoritative human gate: keystrokes only fire
    after an operator CONFIRM (RUN) of a matching, unexpired, armed id.
    The flipper relay it drives is HIGH-risk at the bridge, but drifter-hid
    supplies that bridge confirm itself, so the bridge does not add a
    second human gate — it is downstream plumbing of this one.
    """

    def __init__(self, mqtt_client, flipper_fire=None):
        self.mqtt = mqtt_client
        # flipper_fire(payload_id, script, arm_id) -> (ok: bool, detail: str)
        self._flipper_fire = flipper_fire or self._default_flipper_fire

    # ── readiness ──
    def status_payload(self) -> dict:
        armed = None
        # Surface the single most-recent pending entry (UI shows one at a time).
        if pending_confirms:
            entry = max(pending_confirms.values(), key=lambda e: e['ts'])
            expires_in = max(0, int(CONFIRM_EXPIRY_S - (time.time() - entry['ts'])))
            armed = {
                'id': entry['id'],
                'payload_id': entry['payload_id'],
                'backend': entry['backend'],
                'expires_in': expires_in,
            }
        return {
            'native': native_status(),
            'flipper': self._flipper_readiness(),
            'armed': armed,
            'ts': time.time(),
        }

    def _flipper_readiness(self) -> dict:
        """Honest Flipper-backend readiness from the retained status topic.

        We do not fabricate a connection — absent/false reads as not ready.
        The dashboard republishes drifter/flipper/status; the service-side
        view here is conservative (the bridge is the authority on RUN).
        """
        return {
            'connected': None,   # authoritative readiness lives in the bridge;
            'badusb_ready': None,  # the API merges the live flipper/status topic
            'note': 'flipper readiness resolved by flipper_bridge at fire time',
        }

    # ── command entry point ──
    def handle(self, data: dict) -> None:
        command = (data.get('command') or '').strip()
        peer = data.get('peer') or ''
        if command == 'hid_arm':
            self._arm(data, peer)
        elif command == 'hid_confirm':
            self._confirm(data, peer)
        elif command == 'hid_cancel':
            self._cancel(data, peer)
        else:
            audit(self.mqtt, 'REJECT', peer, reason='unknown command',
                  command=command)

    def _publish_status(self) -> None:
        if self.mqtt is not None:
            try:
                self.mqtt.publish(TOPICS['hid_status'],
                                  json.dumps(self.status_payload(), default=str),
                                  qos=1, retain=False)
            except Exception as e:
                log.warning("hid status publish failed: %s", e)

    def _publish_result(self, payload: dict) -> None:
        if self.mqtt is not None:
            try:
                self.mqtt.publish(TOPICS['hid_result'],
                                  json.dumps(payload, default=str), qos=1)
            except Exception as e:
                log.warning("hid result publish failed: %s", e)

    # ── ARM ──
    def _arm(self, data: dict, peer: str) -> None:
        payload_id = (data.get('payload_id') or '').strip()
        backend = (data.get('backend') or '').strip()
        if backend not in VALID_BACKENDS:
            audit(self.mqtt, 'REJECT', peer, reason='bad backend',
                  backend=backend, payload_id=payload_id)
            return
        if not payload_id:
            audit(self.mqtt, 'REJECT', peer, reason='empty payload_id',
                  backend=backend)
            return

        loaded = load_payload(payload_id)
        if loaded is None:
            audit(self.mqtt, 'REJECT', peer, reason='payload not found',
                  payload_id=payload_id, backend=backend)
            self._publish_result({'ok': False, 'event': 'ARM',
                                  'error': 'payload not found',
                                  'payload_id': payload_id})
            return

        script = loaded['script']
        layout = (loaded.get('meta') or {}).get('target_layout', 'us')
        # Validate by compiling. A parse error blocks the ARM — nothing
        # gets armed for a payload that cannot compile.
        try:
            compiled = hid_ducky.compile_ducky(script, layout=layout)
        except hid_ducky.DuckyParseError as e:
            audit(self.mqtt, 'REJECT', peer, reason='compile error',
                  payload_id=payload_id, backend=backend,
                  error=str(e), line=getattr(e, 'line', None))
            self._publish_result({'ok': False, 'event': 'ARM',
                                  'error': str(e),
                                  'line': getattr(e, 'line', None)})
            return

        # Backend readiness gate. Native is ready ONLY when the real boot
        # profile is present (dr_mode peripheral/otg + /dev/hidg0 + bindable
        # UDC). On this host-mode bench it cleanly refuses 'not configured'.
        if backend == 'native':
            ns = native_status()
            if not ns['ready']:
                audit(self.mqtt, 'REJECT', peer, reason='native not ready',
                      payload_id=payload_id, backend=backend,
                      dr_mode=ns['dr_mode'], detail=ns['reason'])
                self._publish_result({'ok': False, 'event': 'ARM',
                                      'backend': 'native',
                                      'error': ns['reason'],
                                      'dr_mode': ns['dr_mode']})
                return

        # backend == 'flipper' — readiness is resolved authoritatively by
        # the bridge at fire time; we accept the ARM and let the bridge's
        # HIGH-risk confirm gate be the second check.
        sha = hid_ducky.sha256_source(script)
        arm_id = f'arm-{uuid.uuid4().hex[:8]}'
        first, last = hid_ducky.preview_lines(script, n=1)
        entry = {
            'id': arm_id,
            'payload_id': payload_id,
            'backend': backend,
            'line_count': compiled.line_count,
            'keystrokes': compiled.keystrokes,
            'sha256': sha,
            'ts': time.time(),
            'peer': peer,
        }
        pending_confirms[arm_id] = entry
        audit(self.mqtt, 'ARM', peer, id=arm_id, payload_id=payload_id,
              backend=backend, line_count=compiled.line_count,
              keystrokes=compiled.keystrokes, sha256=sha)
        # Publish the ARMED preview. NOTHING is typed.
        self._publish_result({
            'ok': True, 'event': 'ARMED', 'id': arm_id,
            'payload_id': payload_id, 'backend': backend,
            'line_count': compiled.line_count,
            'keystrokes': compiled.keystrokes,
            'sha256': sha,
            'first_line': first[0] if first else '',
            'last_line': last[-1] if last else '',
            'expires_in': CONFIRM_EXPIRY_S,
        })
        self._publish_status()

    # ── CONFIRM (→ RUN) ──
    def _confirm(self, data: dict, peer: str) -> None:
        arm_id = (data.get('id') or '').strip()
        if not arm_id:
            audit(self.mqtt, 'REJECT', peer, reason='empty id',
                  command='hid_confirm')
            return
        entry = pending_confirms.get(arm_id)
        if entry is None:
            audit(self.mqtt, 'REJECT', peer, reason='no pending id', id=arm_id)
            self._publish_result({'ok': False, 'event': 'CONFIRM',
                                  'id': arm_id,
                                  'error': 'no pending armed payload'})
            return
        # Expiry — identical 60s window to the flipper HIGH-risk path.
        if time.time() - entry['ts'] > CONFIRM_EXPIRY_S:
            pending_confirms.pop(arm_id, None)
            audit(self.mqtt, 'EXPIRE', peer, id=arm_id,
                  payload_id=entry['payload_id'], backend=entry['backend'])
            self._publish_result({'ok': False, 'event': 'EXPIRE', 'id': arm_id,
                                  'error': 'confirmation expired (>60s)'})
            self._publish_status()
            return

        audit(self.mqtt, 'CONFIRM', peer, id=arm_id,
              payload_id=entry['payload_id'], backend=entry['backend'])
        # SINGLE-SHOT — pop BEFORE executing so a replayed confirm cannot
        # re-fire without a fresh ARM.
        pending_confirms.pop(arm_id, None)
        self._run(entry, peer)
        self._publish_status()

    def _run(self, entry: dict, peer: str) -> None:
        backend = entry['backend']
        payload_id = entry['payload_id']
        loaded = load_payload(payload_id)
        if loaded is None:
            audit(self.mqtt, 'REJECT', peer, reason='payload vanished',
                  id=entry['id'], payload_id=payload_id)
            self._publish_result({'ok': False, 'event': 'RUN', 'id': entry['id'],
                                  'error': 'payload no longer exists'})
            return

        if backend == 'native':
            self._run_native(entry, loaded['script'], peer)
            return

        t0 = time.time()
        ok, detail = self._flipper_fire(payload_id, loaded['script'], entry['id'])
        duration_ms = int((time.time() - t0) * 1000)
        audit(self.mqtt, 'RUN', peer, id=entry['id'], payload_id=payload_id,
              backend='flipper', sha256=entry['sha256'],
              line_count=entry['line_count'], keystrokes=entry['keystrokes'],
              duration_ms=duration_ms, success=ok, detail=detail)
        self._publish_result({
            'ok': ok, 'event': 'RUN', 'id': entry['id'],
            'payload_id': payload_id, 'backend': 'flipper',
            'keystrokes': entry['keystrokes'], 'sha256': entry['sha256'],
            'duration_ms': duration_ms, 'detail': detail,
        })

    def _run_native(self, entry: dict, script: str, peer: str) -> None:
        """RUN on the NATIVE Pi-gadget backend (Stage 6).

        Reachable ONLY after a native ARM (which itself only succeeds when
        the real boot profile is present) and the operator's CONFIRM. The
        gadget controller re-checks dr_mode ∈ {peripheral, otg} on EVERY
        op and hard-refuses otherwise — so on this host-mode node this path
        fails closed (BLOCKED) and NOTHING is typed. We bind the gadget,
        write the compiled key-down/key-up frames (honoring compiled
        DELAYs) to /dev/hidg0, then unbind so the Pi does not linger as a
        keyboard. Emits GADGET_BIND / GADGET_UNBIND audit events."""
        arm_id = entry['id']
        payload_id = entry['payload_id']
        ctrl = _gadget_controller()
        # Compile to 8-byte frames (us layout per the payload meta).
        loaded_meta = (load_payload(payload_id) or {}).get('meta') or {}
        layout = loaded_meta.get('target_layout', 'us')
        try:
            compiled = hid_ducky.compile_ducky(script, layout=layout)
        except hid_ducky.DuckyParseError as e:
            audit(self.mqtt, 'BLOCKED', peer, id=arm_id, backend='native',
                  detail=f'compile error at RUN: {e}')
            self._publish_result({'ok': False, 'event': 'RUN', 'id': arm_id,
                                  'backend': 'native', 'error': str(e)})
            return

        # Bind (== plug in). The controller hard-refuses unless dr_mode ∈
        # {peripheral, otg}. On the host-mode bench this raises GadgetError
        # → BLOCKED, fail-closed, no keystrokes.
        t0 = time.time()
        try:
            udc = ctrl.bind()
        except hid_gadget.GadgetError as e:
            audit(self.mqtt, 'BLOCKED', peer, id=arm_id, backend='native',
                  detail=str(e))
            self._publish_result({'ok': False, 'event': 'RUN', 'id': arm_id,
                                  'backend': 'native', 'error': str(e)})
            return
        audit(self.mqtt, 'GADGET_BIND', peer, id=arm_id, payload_id=payload_id,
              backend='native', udc=udc)

        ok = True
        detail = ''
        written = 0
        try:
            written = ctrl.write_reports(
                compiled.reports, default_delay_ms=compiled.default_delay_ms)
            detail = f'wrote {written} HID frames to {ctrl.hidg_path}'
        except hid_gadget.GadgetError as e:
            ok = False
            detail = str(e)
        except OSError as e:
            ok = False
            detail = f'hidg0 write failed: {e}'
        finally:
            # Always unbind (== unplug) so the Pi does not linger as a
            # keyboard after the job, even on a write error.
            try:
                ctrl.unbind()
                audit(self.mqtt, 'GADGET_UNBIND', peer, id=arm_id,
                      backend='native', udc=udc)
            except hid_gadget.GadgetError as e:
                log.warning("native unbind refused: %s", e)

        duration_ms = int((time.time() - t0) * 1000)
        audit(self.mqtt, 'RUN', peer, id=arm_id, payload_id=payload_id,
              backend='native', sha256=entry['sha256'],
              line_count=entry['line_count'], keystrokes=entry['keystrokes'],
              duration_ms=duration_ms, success=ok, frames=written, detail=detail)
        self._publish_result({
            'ok': ok, 'event': 'RUN', 'id': arm_id,
            'payload_id': payload_id, 'backend': 'native',
            'keystrokes': entry['keystrokes'], 'sha256': entry['sha256'],
            'duration_ms': duration_ms, 'frames': written, 'detail': detail,
        })

    # ── CANCEL ──
    def _cancel(self, data: dict, peer: str) -> None:
        arm_id = (data.get('id') or '').strip()
        if not arm_id:
            audit(self.mqtt, 'REJECT', peer, reason='empty id',
                  command='hid_cancel')
            return
        entry = pending_confirms.pop(arm_id, None)
        if entry is None:
            audit(self.mqtt, 'REJECT', peer, reason='no pending id',
                  command='hid_cancel', id=arm_id)
            return
        audit(self.mqtt, 'REJECT', peer, reason='operator cancel', id=arm_id,
              payload_id=entry['payload_id'], backend=entry['backend'])
        self._publish_result({'ok': True, 'event': 'CANCELLED', 'id': arm_id})
        self._publish_status()

    # ── default Flipper fire (relay through the bridge) ──
    def _default_flipper_fire(self, payload_id: str, script: str, arm_id: str):
        """Push the payload to the Flipper /ext/badusb and fire it.

        Both the storage-write and the badusb/loader fire go through
        drifter/flipper/command, where flipper_bridge classifies them HIGH
        and requires a confirm token. drifter-hid supplies that token
        itself, immediately after the operator's HID-layer CONFIRM (RUN).
        So the bridge confirm is NOT an independent second human gate — it
        is downstream plumbing. The one authoritative human gate is the HID
        ARM→CONFIRM→RUN state machine; this method only runs after it.
        (The bridge confirm has no peer ACL of its own; it is relied on
        only because the MQTT broker is loopback-bound — see Stage 5 notes.)
        """
        if self.mqtt is None:
            return False, 'mqtt offline'
        on_flipper = f'/ext/badusb/{payload_id}.txt'
        write_id = f'{arm_id}-write'
        fire_id = f'{arm_id}-fire'
        try:
            # 1) storage write the payload (HIGH-risk in the bridge → confirm)
            self.mqtt.publish(TOPICS['flipper_command'], json.dumps({
                'command': f'storage write {on_flipper}\n{script}',
                'id': write_id,
            }), qos=1)
            self.mqtt.publish(TOPICS['flipper_command'], json.dumps({
                'command': 'confirm', 'id': write_id,
            }), qos=1)
            # 2) fire via the BadUSB app (HIGH-risk → bridge confirm too)
            self.mqtt.publish(TOPICS['flipper_command'], json.dumps({
                'command': f'loader open BadUSB {on_flipper}',
                'id': fire_id,
            }), qos=1)
            self.mqtt.publish(TOPICS['flipper_command'], json.dumps({
                'command': 'confirm', 'id': fire_id,
            }), qos=1)
        except Exception as e:
            return False, f'relay failed: {e}'
        return True, f'relayed badusb fire for {on_flipper}'


# ═══════════════════════════════════════════════════════════════════
#  MQTT wiring
# ═══════════════════════════════════════════════════════════════════

def handle_message(machine: HidStateMachine):
    def on_message(client, userdata, msg):
        try:
            data = json.loads(msg.payload)
        except (json.JSONDecodeError, TypeError) as e:
            log.warning("bad hid command payload: %s", e)
            return
        if not isinstance(data, dict):
            return
        machine.handle(data)
    return on_message


def _expire_stale(machine: HidStateMachine) -> None:
    now = time.time()
    stale = [k for k, v in pending_confirms.items()
             if now - v['ts'] > CONFIRM_EXPIRY_S]
    for k in stale:
        entry = pending_confirms.pop(k, None)
        if entry:
            audit(machine.mqtt, 'EXPIRE', entry.get('peer', ''), id=k,
                  payload_id=entry['payload_id'], backend=entry['backend'])


def main():
    global running

    def _sig(_s, _f):
        global running
        running = False

    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)

    log.info("DRIFTER HID Injection Service starting...")
    ns = native_status()
    log.info("native backend: dr_mode=%s configured=%s (%s)",
             ns['dr_mode'], ns['configured'], ns['reason'])

    mqtt_client = make_mqtt_client("drifter-hid")
    connected = False
    while not connected and running:
        try:
            mqtt_client.connect(MQTT_HOST, MQTT_PORT, 60)
            connected = True
        except Exception as e:
            log.warning("Waiting for MQTT broker... (%s)", e)
            time.sleep(3)
    if not running:
        return

    machine = HidStateMachine(mqtt_client)
    mqtt_client.on_message = handle_message(machine)
    mqtt_client.subscribe(TOPICS['hid_command'])
    mqtt_client.loop_start()
    # Publish an initial status so the cockpit sees honest readiness even
    # with no Flipper attached and native unconfigured.
    machine._publish_status()
    log.info("HID Injection Service is LIVE (Flipper + native backends; "
             "native ready=%s)", ns['ready'])

    while running:
        _expire_stale(machine)
        time.sleep(1)

    mqtt_client.loop_stop()
    mqtt_client.disconnect()


if __name__ == '__main__':
    main()

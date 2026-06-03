#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Native USB-gadget HID lifecycle (configfs).

Backend A of the drifter-hid capability: the Pi 5 enumerates as a USB
boot keyboard via dwc2 + libcomposite, exposing /dev/hidg0, and the
DuckyScript-compiled 8-byte HID reports are written to that device.

LOAD-BEARING SAFETY (spec §2.6 / §8):

  * EVERY gadget operation (create / bind / write / teardown) FIRST checks
    that dr_mode ∈ {peripheral, otg}. dr_mode is read from the device tree
    (/proc/device-tree/.../dr_mode) corroborated with /sys/class/udc. If
    the port is in 'host' mode (the live vehicle node's normal boot — the
    USB-C port is the power input) every op HARD-REFUSES with a clear
    reason and NOTHING is created, bound, or written.
  * On this host-mode bench, the only path the tests exercise is the
    refusal path — no real configfs is touched.
  * The configfs root is INJECTABLE (GadgetController(root=...)) so tests
    run against a temp dir without /sys/kernel/config.

This module is pure-ish lifecycle: it performs filesystem writes against
configfs, but holds no MQTT / UI assumptions. The drifter-hid service
(hid_inject.py) drives it; hid_inject keeps the authoritative
ARM→CONFIRM→RUN gate. This module NEVER fires on its own.

UNCAGED TECHNOLOGY — EST 1991
"""
from __future__ import annotations

import glob
import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)

# ── configfs / device paths ───────────────────────────────────────────
CONFIGFS_ROOT = Path('/sys/kernel/config/usb_gadget')
GADGET_NAME = 'drifter_hid'
HIDG0 = Path('/dev/hidg0')

_DEVICE_TREE_GLOBS = (
    '/proc/device-tree/soc/usb@*/dr_mode',
    '/proc/device-tree/axi/usb@*/dr_mode',
    '/proc/device-tree/*usb*/dr_mode',
)
_UDC_DIR = Path('/sys/class/udc')

# dr_mode values that permit gadget (peripheral) operation.
GADGET_DR_MODES = ('peripheral', 'otg')

# USB identifiers — idVendor 0x1d6b (Linux Foundation), idProduct 0x0104
# (Multifunction Composite Gadget), per spec §8.
ID_VENDOR = '0x1d6b'
ID_PRODUCT = '0x0104'
BCD_DEVICE = '0x0100'
BCD_USB = '0x0200'

# Standard USB HID boot-keyboard report descriptor (8-byte reports).
# Usage Page (Generic Desktop), Usage (Keyboard), 8 modifier bits, 1
# reserved byte, 6 key array bytes, plus LED output report.
BOOT_KEYBOARD_REPORT_DESC = bytes([
    0x05, 0x01,  # Usage Page (Generic Desktop)
    0x09, 0x06,  # Usage (Keyboard)
    0xA1, 0x01,  # Collection (Application)
    0x05, 0x07,  # Usage Page (Key Codes)
    0x19, 0xE0,  # Usage Minimum (224)
    0x29, 0xE7,  # Usage Maximum (231)
    0x15, 0x00,  # Logical Minimum (0)
    0x25, 0x01,  # Logical Maximum (1)
    0x75, 0x01,  # Report Size (1)
    0x95, 0x08,  # Report Count (8)
    0x81, 0x02,  # Input (Data, Variable, Absolute) — modifier byte
    0x95, 0x01,  # Report Count (1)
    0x75, 0x08,  # Report Size (8)
    0x81, 0x01,  # Input (Constant) — reserved byte
    0x95, 0x05,  # Report Count (5)
    0x75, 0x01,  # Report Size (1)
    0x05, 0x08,  # Usage Page (LEDs)
    0x19, 0x01,  # Usage Minimum (1)
    0x29, 0x05,  # Usage Maximum (5)
    0x91, 0x02,  # Output (Data, Variable, Absolute) — LED report
    0x95, 0x01,  # Report Count (1)
    0x75, 0x03,  # Report Size (3)
    0x91, 0x01,  # Output (Constant) — LED padding
    0x95, 0x06,  # Report Count (6)
    0x75, 0x08,  # Report Size (8)
    0x15, 0x00,  # Logical Minimum (0)
    0x25, 0x65,  # Logical Maximum (101)
    0x05, 0x07,  # Usage Page (Key Codes)
    0x19, 0x00,  # Usage Minimum (0)
    0x29, 0x65,  # Usage Maximum (101)
    0x81, 0x00,  # Input (Data, Array) — key array
    0xC0,        # End Collection
])

REPORT_LEN = 8


class GadgetError(RuntimeError):
    """Raised when a native gadget op is refused or fails.

    The single most important refusal is `dr_mode not in {peripheral, otg}`
    — on this live host-mode node that is the only path exercised.
    """


def read_dr_mode() -> str:
    """Read the USB controller dr_mode from the device tree.

    Returns 'host' / 'peripheral' / 'otg' / 'unknown'. Never raises — a
    missing node reads as 'unknown'. dr_mode is a boot-time device-tree
    property and CANNOT be hot-switched at runtime, which is exactly why
    enabling the native backend is a reboot-gated opt-in (spec §2/§2.6).
    """
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


def list_udcs(udc_dir: Path = _UDC_DIR) -> list[str]:
    """Available USB Device Controllers (the bind targets). Empty if none."""
    try:
        if udc_dir.exists():
            return sorted(p.name for p in udc_dir.iterdir())
    except OSError:
        pass
    return []


class GadgetController:
    """configfs USB-gadget lifecycle for the NATIVE HID backend.

    Every public op (create / bind / unbind / write_reports / teardown)
    first calls `_require_gadget_mode()` which HARD-REFUSES (GadgetError)
    unless dr_mode ∈ {peripheral, otg}. On a host-mode bench this refusal
    is the only reachable path.

    `root` (the configfs usb_gadget dir) is injectable so tests can point
    it at a temp dir; `hidg_path` (the /dev/hidgN device) is likewise
    injectable. Neither default touches real configfs unless dr_mode is
    already peripheral/otg AND the op is invoked.
    """

    def __init__(
        self,
        root: Path = CONFIGFS_ROOT,
        name: str = GADGET_NAME,
        hidg_path: Path = HIDG0,
        udc_dir: Path = _UDC_DIR,
        dr_mode_reader=read_dr_mode,
    ):
        self.root = Path(root)
        self.name = name
        self.gdir = self.root / name
        self.hidg_path = Path(hidg_path)
        self.udc_dir = Path(udc_dir)
        self._dr_mode_reader = dr_mode_reader

    # ── safety gate ──
    def gadget_mode_ok(self) -> tuple[bool, str]:
        """(ok, reason). ok only when dr_mode ∈ {peripheral, otg}."""
        dr_mode = self._dr_mode_reader()
        if dr_mode not in GADGET_DR_MODES:
            return False, (
                f"refusing native gadget op: dr_mode={dr_mode!r} "
                f"(need peripheral/otg). The USB-C port is the Pi 5 power "
                f"input — enable with 'drifter hid enable-native' + reboot."
            )
        return True, f"dr_mode={dr_mode}"

    def _require_gadget_mode(self) -> None:
        ok, reason = self.gadget_mode_ok()
        if not ok:
            raise GadgetError(reason)

    def pick_udc(self) -> str:
        """The UDC to bind to. Refuses (GadgetError) if none present."""
        udcs = list_udcs(self.udc_dir)
        if not udcs:
            raise GadgetError(
                "no USB Device Controller in /sys/class/udc — gadget cannot "
                "bind (is dwc2 loaded and the port in peripheral mode?)")
        return udcs[0]

    # ── lifecycle ──
    def _write(self, rel: str, value: str) -> None:
        target = self.gdir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value)

    def create(self) -> None:
        """Create the configfs gadget tree (idVendor/idProduct, strings,
        boot-keyboard report_desc, config, function symlink).

        HARD-REFUSES unless dr_mode ∈ {peripheral, otg}. Idempotent: an
        existing tree is left in place (mkdir(exist_ok=True))."""
        self._require_gadget_mode()
        self.gdir.mkdir(parents=True, exist_ok=True)
        self._write('idVendor', ID_VENDOR)
        self._write('idProduct', ID_PRODUCT)
        self._write('bcdDevice', BCD_DEVICE)
        self._write('bcdUSB', BCD_USB)
        # English (0x409) strings.
        strings = self.gdir / 'strings' / '0x409'
        strings.mkdir(parents=True, exist_ok=True)
        (strings / 'manufacturer').write_text('MZ1312 UNCAGED TECHNOLOGY')
        (strings / 'product').write_text('DRIFTER HID')
        (strings / 'serialnumber').write_text('drifter-hid-0001')
        # HID function — boot keyboard, 8-byte reports.
        fn = self.gdir / 'functions' / 'hid.usb0'
        fn.mkdir(parents=True, exist_ok=True)
        (fn / 'protocol').write_text('1')      # keyboard
        (fn / 'subclass').write_text('1')       # boot interface
        (fn / 'report_length').write_text(str(REPORT_LEN))
        (fn / 'report_desc').write_bytes(BOOT_KEYBOARD_REPORT_DESC)
        # Config c.1
        cfg = self.gdir / 'configs' / 'c.1'
        cfg.mkdir(parents=True, exist_ok=True)
        cfg_strings = cfg / 'strings' / '0x409'
        cfg_strings.mkdir(parents=True, exist_ok=True)
        (cfg_strings / 'configuration').write_text('DRIFTER HID config')
        (cfg / 'MaxPower').write_text('250')
        # Symlink function → config (== expose the keyboard interface).
        link = cfg / 'hid.usb0'
        if not link.exists():
            try:
                link.symlink_to(fn)
            except OSError:
                # On a temp-dir test fs the symlink may fail; record a
                # plain marker so create() stays idempotent and inspectable.
                link.mkdir(parents=True, exist_ok=True)

    def is_created(self) -> bool:
        return self.gdir.exists()

    def is_bound(self) -> bool:
        """Bound == the UDC attribute is non-empty (gadget 'plugged in')."""
        udc = self.gdir / 'UDC'
        try:
            return udc.exists() and bool(udc.read_text().strip())
        except OSError:
            return False

    def bind(self) -> str:
        """Bind the gadget to a UDC (== 'plug in' the virtual keyboard).

        HARD-REFUSES unless dr_mode ∈ {peripheral, otg}. Returns the UDC
        name bound. Creates the tree first if absent."""
        self._require_gadget_mode()
        if not self.is_created():
            self.create()
        udc = self.pick_udc()
        self._write('UDC', udc)
        return udc

    def unbind(self) -> None:
        """Unbind from the UDC (== 'unplug'). Refuses off gadget mode."""
        self._require_gadget_mode()
        udc = self.gdir / 'UDC'
        if udc.exists():
            udc.write_text('\n')

    def write_reports(self, reports, default_delay_ms: int = 0) -> int:
        """Write compiled 8-byte HID frames to /dev/hidg0.

        `reports` is a list of (frame_bytes_or_None, post_delay_ms) tuples
        as produced by hid_ducky.CompiledPayload.reports: a key-down then
        an all-zero key-up frame per keystroke, with (None, ms) standalone
        DELAY markers. Honors the compiled DELAYs (and default delay).

        HARD-REFUSES unless dr_mode ∈ {peripheral, otg} AND the gadget is
        bound. Returns the number of 8-byte frames actually written.

        NOTE: this is only reachable after hid_inject's ARM→CONFIRM→RUN
        gate — it never fires on its own.
        """
        self._require_gadget_mode()
        if not self.is_bound():
            raise GadgetError(
                "refusing to write reports: gadget not bound to a UDC "
                "(call bind() after enabling the native boot profile)")
        written = 0
        with self.hidg_path.open('wb', buffering=0) as fh:
            for frame, delay_ms in reports:
                if frame is not None:
                    if len(frame) != REPORT_LEN:
                        raise GadgetError(
                            f"HID frame must be {REPORT_LEN} bytes, "
                            f"got {len(frame)}")
                    fh.write(frame)
                    fh.flush()
                    written += 1
                wait = delay_ms if delay_ms else default_delay_ms
                if wait:
                    time.sleep(wait / 1000.0)
        return written

    def teardown(self) -> None:
        """Unbind + tear down the configfs tree (== 'unplug' permanently).

        HARD-REFUSES off gadget mode. Best-effort and idempotent: removes
        symlinks, then rmdir's the function / config / strings dirs in the
        order configfs requires, then the gadget dir. So the Pi does not
        linger as a keyboard after a job (spec §8)."""
        self._require_gadget_mode()
        if not self.gdir.exists():
            return
        # Unbind first.
        try:
            udc = self.gdir / 'UDC'
            if udc.exists() and udc.read_text().strip():
                udc.write_text('\n')
        except OSError:
            pass
        # Remove the function symlink from the config.
        link = self.gdir / 'configs' / 'c.1' / 'hid.usb0'
        try:
            if link.is_symlink():
                link.unlink()
            elif link.exists():
                _rmdir(link)
        except OSError:
            pass
        # rmdir strings / configs / functions, then the gadget itself.
        for rel in (
            'configs/c.1/strings/0x409',
            'configs/c.1',
            'functions/hid.usb0',
            'strings/0x409',
        ):
            _rmdir(self.gdir / rel)
        _rmdir(self.gdir)

    def status(self) -> dict:
        """Read-only lifecycle snapshot for the UI / API.

        NEVER mutates and NEVER flips dr_mode — purely reports the boot
        role (spec §2.6: the UI can never flip dr_mode)."""
        dr_mode = self._dr_mode_reader()
        configured = dr_mode in GADGET_DR_MODES
        return {
            'dr_mode': dr_mode,
            'configured': configured,
            'boot_profile': 'gadget' if configured else 'host',
            'udcs': list_udcs(self.udc_dir),
            'created': self.is_created(),
            'bound': self.is_bound(),
            'hidg0_present': self.hidg_path.exists(),
        }


def _rmdir(path: Path) -> None:
    """Best-effort rmdir of a (possibly non-empty test) dir; never raises."""
    try:
        if path.is_symlink():
            path.unlink()
            return
        if not path.exists():
            return
        for child in path.iterdir():
            if child.is_symlink() or child.is_file():
                child.unlink()
            else:
                _rmdir(child)
        path.rmdir()
    except OSError as e:
        log.debug("rmdir %s failed (non-fatal): %s", path, e)


# Convenience for callers that want a single readiness verdict.
def gadget_ready(controller: GadgetController | None = None) -> tuple[bool, str]:
    """(ready, reason). ready only when dr_mode ∈ {peripheral,otg},
    /dev/hidg0 exists, and a UDC is bindable. Honest — never fakes ready."""
    ctrl = controller or GadgetController()
    ok, reason = ctrl.gadget_mode_ok()
    if not ok:
        return False, reason
    if not ctrl.hidg_path.exists():
        return False, "native gadget mode set but /dev/hidg0 absent"
    if not list_udcs(ctrl.udc_dir):
        return False, "no UDC available to bind"
    return True, "native gadget ready"

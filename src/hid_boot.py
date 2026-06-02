#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Native HID boot-profile editor (drifter hid enable/disable).

The ONLY supported way to switch the Pi 5 USB-C port from its normal
host role into the dwc2 peripheral/otg role required for the native
USB-gadget HID backend. Per the load-bearing hardware constraint
(spec §2.6 / §2/§8):

  * The Pi 5's ONLY dwc2-capable port is the USB-C connector, which is
    ALSO the board's power input. Flipping dr_mode to peripheral/otg on a
    live vehicle node can disturb the power/enumeration relationship — so
    this is a DELIBERATE, REBOOT-GATED opt-in, NEVER auto-applied.
  * enable-native writes a FENCED, IDEMPOTENT managed block into
    config.txt (markers '# >>> drifter-hid managed >>>' / '# <<< drifter-hid
    managed <<<'), appends 'modules-load=dwc2' / 'libcomposite' load, then
    PRINTS a reboot warning naming the USB-C power-port hazard and EXITS.
    It NEVER reboots automatically.
  * disable-native removes the managed block (reverses it).
  * BOTH are root-gated.
  * dr_mode is a boot-time device-tree property — it cannot be hot-switched
    at runtime, which is exactly why a reboot is the natural safety gate.

The config path is INJECTABLE (default /boot/firmware/config.txt) so tests
operate on a TEMP file and NEVER the real boot config.

UNCAGED TECHNOLOGY — EST 1991
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

DEFAULT_CONFIG_PATH = Path('/boot/firmware/config.txt')

BLOCK_START = '# >>> drifter-hid managed >>>'
BLOCK_END = '# <<< drifter-hid managed <<<'

# The managed block content (between the fences). dr_mode=peripheral turns
# the USB-C dwc2 port into a gadget; modules-load brings up the stack.
_MANAGED_BODY = (
    "# Managed by 'drifter hid enable-native'. DO NOT edit by hand.\n"
    "# Switches the Pi 5 USB-C port (the board POWER INPUT) to dwc2\n"
    "# peripheral mode for the native USB-gadget HID backend. Remove with\n"
    "# 'drifter hid disable-native'.\n"
    "dtoverlay=dwc2,dr_mode=peripheral\n"
    "modules-load=dwc2,libcomposite\n"
)

REBOOT_WARNING = (
    "\n"
    "  ============================================================\n"
    "   REBOOT REQUIRED — and READ THIS FIRST.\n"
    "  ============================================================\n"
    "   The native HID backend needs the Pi 5 USB-C port in dwc2\n"
    "   PERIPHERAL mode. On the Pi 5 that USB-C port is ALSO the\n"
    "   board's POWER INPUT. Flipping its role can disturb the\n"
    "   power/enumeration relationship with whatever feeds it.\n"
    "\n"
    "   * dr_mode is a BOOT-TIME property — it takes effect only\n"
    "     after a reboot. Nothing changed at runtime; the node is\n"
    "     still in host mode RIGHT NOW.\n"
    "   * This command did NOT reboot. Reboot deliberately, when the\n"
    "     vehicle is parked and you understand the power implication.\n"
    "   * To revert: 'drifter hid disable-native' then reboot.\n"
    "  ============================================================\n"
)


class BootProfileError(RuntimeError):
    """Raised on a boot-profile edit failure (e.g. not root, no config)."""


def _require_root() -> None:
    if os.geteuid() != 0:
        raise BootProfileError(
            "must be root to edit the boot config — run with sudo")


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding='utf-8')
    except FileNotFoundError as e:
        raise BootProfileError(f"boot config not found: {path}") from e
    except OSError as e:
        raise BootProfileError(f"cannot read {path}: {e}") from e


def has_managed_block(text: str) -> bool:
    return BLOCK_START in text and BLOCK_END in text


def _strip_managed_block(text: str) -> str:
    """Remove any existing fenced managed block (idempotent)."""
    if BLOCK_START not in text:
        return text
    out_lines = []
    skipping = False
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped == BLOCK_START:
            skipping = True
            continue
        if stripped == BLOCK_END:
            skipping = False
            continue
        if not skipping:
            out_lines.append(line)
    return ''.join(out_lines)


def _managed_block() -> str:
    return f"{BLOCK_START}\n{_MANAGED_BODY}{BLOCK_END}\n"


def enable_native(config_path: Path = DEFAULT_CONFIG_PATH,
                  check_root: bool = True) -> bool:
    """Write the fenced managed block into config.txt. Idempotent.

    Returns True if the file was changed, False if it already matched
    (re-run is a no-op). NEVER reboots. Root-gated unless check_root=False
    (tests inject a temp path and skip the root check).
    """
    if check_root:
        _require_root()
    path = Path(config_path)
    text = _read(path)
    # Strip any prior managed block, then append a fresh one — this makes
    # re-runs idempotent (the block is always exactly one, current copy).
    base = _strip_managed_block(text)
    if base and not base.endswith('\n'):
        base += '\n'
    new_text = base + _managed_block()
    if new_text == text:
        return False
    try:
        path.write_text(new_text, encoding='utf-8')
    except OSError as e:
        raise BootProfileError(f"cannot write {path}: {e}") from e
    return True


def disable_native(config_path: Path = DEFAULT_CONFIG_PATH,
                   check_root: bool = True) -> bool:
    """Remove the fenced managed block from config.txt. Idempotent.

    Returns True if a block was removed, False if none was present.
    NEVER reboots. Root-gated unless check_root=False.
    """
    if check_root:
        _require_root()
    path = Path(config_path)
    text = _read(path)
    if not has_managed_block(text):
        return False
    new_text = _strip_managed_block(text)
    try:
        path.write_text(new_text, encoding='utf-8')
    except OSError as e:
        raise BootProfileError(f"cannot write {path}: {e}") from e
    return True


# ── CLI entry (invoked by bin/drifter) ─────────────────────────────────

def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("usage: hid_boot.py {enable-native|disable-native} "
              "[--config PATH]", file=sys.stderr)
        return 2
    action = argv[0]
    config_path = DEFAULT_CONFIG_PATH
    if '--config' in argv:
        i = argv.index('--config')
        try:
            config_path = Path(argv[i + 1])
        except IndexError:
            print("--config requires a path", file=sys.stderr)
            return 2

    try:
        if action == 'enable-native':
            changed = enable_native(config_path)
            if changed:
                print(f"drifter hid: managed block written to {config_path}")
            else:
                print(f"drifter hid: managed block already present in "
                      f"{config_path} (no change)")
            # WARN + EXIT. Never auto-reboot.
            print(REBOOT_WARNING)
            return 0
        elif action == 'disable-native':
            removed = disable_native(config_path)
            if removed:
                print(f"drifter hid: managed block removed from {config_path}")
            else:
                print(f"drifter hid: no managed block in {config_path} "
                      f"(no change)")
            print("\n  Reboot to return the USB-C port to host mode.\n")
            return 0
        else:
            print(f"unknown action: {action}", file=sys.stderr)
            return 2
    except BootProfileError as e:
        print(f"drifter hid: {e}", file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

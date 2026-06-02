#!/usr/bin/env python3
"""
MZ1312 DRIFTER — DuckyScript 1.0 (subset) compiler.

Pure, I/O-free, fully unit-testable. Compiles a DuckyScript source string
into a list of 8-byte USB HID boot-keyboard reports plus inter-report
delays. Shared by both HID backends:

  * NATIVE (Pi gadget) — writes the compiled 8-byte frames to /dev/hidg0.
  * FLIPPER — the raw .txt is pushed to the Flipper (its firmware has its
    own interpreter); we still compile here to VALIDATE + COUNT keystrokes
    so the operator confirm preview is real, never a blind fire.

Supported DuckyScript 1.0 subset (the intersection both backends honour):
  REM, STRING, STRINGLN, DELAY, DEFAULTDELAY/DEFAULT_DELAY,
  ENTER, GUI/WINDOWS, CTRL/CONTROL, ALT, SHIFT, modifier combos,
  arrow keys, function keys, and other named keys, plus REPEAT.

HARD-ERRORS (DuckyParseError) on any unknown token, malformed modifier
combo, unmapped STRING character, or out-of-range DELAY — never silently
skipped. This mirrors the repo's "honest bench answer" ethos.

Layout: 'us' keymap only (the default and the only one shipped this stage).

UNCAGED TECHNOLOGY — EST 1991
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

# ── USB HID boot-keyboard report ──────────────────────────────────────
# Each report is 8 bytes: [modifier, reserved(0x00), k1, k2, k3, k4, k5, k6].
# We only ever press one usage key at a time (plus modifiers), so k2..k6
# stay zero. A key-down report is followed by an all-zero key-up report.
REPORT_LEN = 8

# Modifier bitmasks (left-hand modifiers — the standard DuckyScript set).
MOD_NONE = 0x00
MOD_LCTRL = 0x01
MOD_LSHIFT = 0x02
MOD_LALT = 0x04
MOD_LGUI = 0x08

# DEFAULT_DELAY bounds (ms). DuckyScript delays are whole milliseconds.
_MIN_DELAY_MS = 0
_MAX_DELAY_MS = 10 * 60 * 1000  # 10 minutes — generous but bounded.


class DuckyParseError(ValueError):
    """Raised on any malformed / unknown DuckyScript token.

    Carries the 1-based source line number so the API can surface
    `{ok:false, error, line}` to the operator.
    """

    def __init__(self, message: str, line: int):
        self.line = line
        super().__init__(f"line {line}: {message}")


# ── 'us' layout keymap ────────────────────────────────────────────────
# Maps a printable character → (usage_id, needs_shift). USB HID Usage IDs
# for the boot keyboard (Usage Page 0x07).
_US_UNSHIFTED: Dict[str, int] = {
    'a': 0x04, 'b': 0x05, 'c': 0x06, 'd': 0x07, 'e': 0x08, 'f': 0x09,
    'g': 0x0A, 'h': 0x0B, 'i': 0x0C, 'j': 0x0D, 'k': 0x0E, 'l': 0x0F,
    'm': 0x10, 'n': 0x11, 'o': 0x12, 'p': 0x13, 'q': 0x14, 'r': 0x15,
    's': 0x16, 't': 0x17, 'u': 0x18, 'v': 0x19, 'w': 0x1A, 'x': 0x1B,
    'y': 0x1C, 'z': 0x1D,
    '1': 0x1E, '2': 0x1F, '3': 0x20, '4': 0x21, '5': 0x22, '6': 0x23,
    '7': 0x24, '8': 0x25, '9': 0x26, '0': 0x27,
    '\n': 0x28,  # ENTER (used by STRINGLN trailing newline)
    ' ': 0x2C,
    '-': 0x2D, '=': 0x2E, '[': 0x2F, ']': 0x30, '\\': 0x31,
    ';': 0x33, "'": 0x34, '`': 0x35, ',': 0x36, '.': 0x37, '/': 0x38,
    '\t': 0x2B,  # TAB
}

# Shifted characters share the usage id of their unshifted partner with
# the LSHIFT modifier set.
_US_SHIFTED: Dict[str, str] = {
    '!': '1', '@': '2', '#': '3', '$': '4', '%': '5', '^': '6',
    '&': '7', '*': '8', '(': '9', ')': '0',
    '_': '-', '+': '=', '{': '[', '}': ']', '|': '\\',
    ':': ';', '"': "'", '~': '`', '<': ',', '>': '.', '?': '/',
}


def _char_to_usage(ch: str, line: int) -> Tuple[int, bool]:
    """Return (usage_id, needs_shift) for a single character (us layout).

    HARD-ERRORS on any character outside the us keymap — never silently
    dropped (a dropped char in an injected payload is a correctness bug,
    not a cosmetic one).
    """
    if ch in _US_UNSHIFTED:
        return _US_UNSHIFTED[ch], False
    if ch.isupper() and ch.lower() in _US_UNSHIFTED:
        return _US_UNSHIFTED[ch.lower()], True
    if ch in _US_SHIFTED:
        return _US_UNSHIFTED[_US_SHIFTED[ch]], True
    raise DuckyParseError(
        f"character {ch!r} is not mappable in the 'us' layout", line)


# ── Named keys (standalone or in modifier combos) ─────────────────────
# Maps a DuckyScript key token → usage id. Modifier tokens are handled
# separately (they contribute a bit, not a usage key).
_NAMED_KEYS: Dict[str, int] = {
    'ENTER': 0x28, 'RETURN': 0x28,
    'ESC': 0x29, 'ESCAPE': 0x29,
    'BACKSPACE': 0x2A,
    'TAB': 0x2B,
    'SPACE': 0x2C,
    'CAPSLOCK': 0x39,
    'DELETE': 0x4C, 'DEL': 0x4C,
    'INSERT': 0x49,
    'HOME': 0x4A,
    'END': 0x4D,
    'PAGEUP': 0x4B,
    'PAGEDOWN': 0x4E,
    'UP': 0x52, 'UPARROW': 0x52,
    'DOWN': 0x51, 'DOWNARROW': 0x51,
    'LEFT': 0x50, 'LEFTARROW': 0x50,
    'RIGHT': 0x4F, 'RIGHTARROW': 0x4F,
    'PRINTSCREEN': 0x46,
    'SCROLLLOCK': 0x47,
    'PAUSE': 0x48, 'BREAK': 0x48,
    'MENU': 0x65, 'APP': 0x65,
    'F1': 0x3A, 'F2': 0x3B, 'F3': 0x3C, 'F4': 0x3D, 'F5': 0x3E,
    'F6': 0x3F, 'F7': 0x40, 'F8': 0x41, 'F9': 0x42, 'F10': 0x43,
    'F11': 0x44, 'F12': 0x45,
}

# Modifier tokens → (bitmask). GUI/WINDOWS/CTRL/CONTROL/ALT/SHIFT.
_MODIFIER_TOKENS: Dict[str, int] = {
    'GUI': MOD_LGUI, 'WINDOWS': MOD_LGUI, 'WIN': MOD_LGUI,
    'CTRL': MOD_LCTRL, 'CONTROL': MOD_LCTRL,
    'ALT': MOD_LALT,
    'SHIFT': MOD_LSHIFT,
}

# Commands that take no argument and emit a single named key.
_BARE_KEY_COMMANDS = set(_NAMED_KEYS.keys())


@dataclass
class CompiledPayload:
    """Result of compiling a DuckyScript source.

    `reports` is a flat list of (8-byte-report, post_delay_ms) tuples in
    execution order. A STRING char emits a key-down then a key-up frame;
    DELAY emits no frame but a standalone delay marker (report is None).
    `keystrokes` counts key-down events (the human-meaningful number for
    the confirm preview). `line_count` is the source line count.
    """
    reports: List[Tuple[bytes, int]] = field(default_factory=list)
    keystrokes: int = 0
    line_count: int = 0
    default_delay_ms: int = 0

    def report_count(self) -> int:
        return sum(1 for r, _ in self.reports if r is not None)


def _down_up(usage: int, modifier: int) -> List[Tuple[bytes, int]]:
    """Build a key-down + key-up report pair for one usage+modifier."""
    down = bytes([modifier & 0xFF, 0x00, usage & 0xFF, 0, 0, 0, 0, 0])
    up = bytes(REPORT_LEN)
    return [(down, 0), (up, 0)]


def _parse_delay_arg(arg: str, line: int) -> int:
    try:
        ms = int(arg.strip())
    except (ValueError, TypeError):
        raise DuckyParseError(f"DELAY argument {arg!r} is not an integer",
                              line)
    if ms < _MIN_DELAY_MS or ms > _MAX_DELAY_MS:
        raise DuckyParseError(
            f"DELAY {ms} out of range [{_MIN_DELAY_MS}, {_MAX_DELAY_MS}]",
            line)
    return ms


def _emit_string(text: str, line: int) -> Tuple[List[Tuple[bytes, int]], int]:
    """Compile a STRING argument char-by-char. Returns (reports, keystrokes)."""
    out: List[Tuple[bytes, int]] = []
    keystrokes = 0
    for ch in text:
        usage, shift = _char_to_usage(ch, line)
        mod = MOD_LSHIFT if shift else MOD_NONE
        out.extend(_down_up(usage, mod))
        keystrokes += 1
    return out, keystrokes


def _emit_key_combo(tokens: List[str], line: int) -> Tuple[List[Tuple[bytes, int]], int]:
    """Compile a (modifier combo / named key) line.

    Examples: ['GUI','r'] ['CTRL','ALT','DELETE'] ['ENTER'] ['F5'].
    Modifier-only combos (e.g. just ['GUI']) emit the modifier with no
    usage key. A bare single character (e.g. STRING-less 'r' after a
    modifier) is mapped through the us layout.
    """
    modifier = MOD_NONE
    usage = 0x00
    saw_usage = False
    for tok in tokens:
        up = tok.upper()
        if up in _MODIFIER_TOKENS:
            modifier |= _MODIFIER_TOKENS[up]
            continue
        if up in _NAMED_KEYS:
            if saw_usage:
                raise DuckyParseError(
                    f"more than one non-modifier key in combo {tokens!r}",
                    line)
            usage = _NAMED_KEYS[up]
            saw_usage = True
            continue
        # Single printable char as the combo target (e.g. GUI r).
        if len(tok) == 1:
            if saw_usage:
                raise DuckyParseError(
                    f"more than one non-modifier key in combo {tokens!r}",
                    line)
            u, shift = _char_to_usage(tok, line)
            usage = u
            if shift:
                modifier |= MOD_LSHIFT
            saw_usage = True
            continue
        raise DuckyParseError(f"unknown token {tok!r}", line)
    out = _down_up(usage, modifier)
    keystrokes = 1
    return out, keystrokes


def compile_ducky(source: str, layout: str = 'us') -> CompiledPayload:
    """Compile a DuckyScript source string into a CompiledPayload.

    `layout` must be 'us' (the only keymap shipped this stage); any other
    value HARD-ERRORS rather than silently substituting the wrong map.

    Raises DuckyParseError (with .line) on any unknown token / unmappable
    character / malformed argument. Never silently skips a line.
    """
    if layout != 'us':
        raise DuckyParseError(
            f"layout {layout!r} not supported (only 'us' this stage)", 0)

    payload = CompiledPayload()
    lines = source.splitlines()
    payload.line_count = len(lines)
    default_delay_ms = 0
    # REPEAT applies to the PREVIOUS instruction line's emitted reports.
    last_emitted: List[Tuple[bytes, int]] = []
    last_keystrokes = 0

    for idx, raw_line in enumerate(lines, start=1):
        line = raw_line.rstrip('\r\n')
        stripped = line.strip()
        if stripped == '':
            # Blank line — no-op, but it resets REPEAT target (nothing to
            # repeat across a blank), matching common Ducky behaviour.
            continue

        # Split into command token + remainder (preserving the remainder
        # verbatim for STRING).
        parts = stripped.split(' ', 1)
        cmd = parts[0].upper()
        arg = parts[1] if len(parts) > 1 else ''

        emitted: List[Tuple[bytes, int]] = []
        emitted_keystrokes = 0

        if cmd == 'REM':
            # Comment — no keystrokes. Does not become a REPEAT target.
            continue

        elif cmd in ('DEFAULTDELAY', 'DEFAULT_DELAY'):
            default_delay_ms = _parse_delay_arg(arg, idx)
            payload.default_delay_ms = default_delay_ms
            continue

        elif cmd == 'DELAY':
            ms = _parse_delay_arg(arg, idx)
            # A standalone delay marker (no report).
            payload.reports.append((None, ms))
            # DELAY is not a REPEAT target in DuckyScript 1.0.
            continue

        elif cmd == 'REPEAT':
            try:
                n = int(arg.strip())
            except (ValueError, TypeError):
                raise DuckyParseError(
                    f"REPEAT argument {arg!r} is not an integer", idx)
            if n < 0:
                raise DuckyParseError(f"REPEAT count {n} is negative", idx)
            if not last_emitted:
                raise DuckyParseError(
                    "REPEAT with no preceding repeatable instruction", idx)
            for _ in range(n):
                # Re-emit the previous instruction's reports, honouring the
                # default delay between them just as the original did.
                for rpt, d in last_emitted:
                    payload.reports.append((rpt, d))
                payload.keystrokes += last_keystrokes
            # REPEAT itself is not a new REPEAT target.
            continue

        elif cmd in ('STRING', 'STRINGLN'):
            text = arg
            if cmd == 'STRINGLN':
                text = text + '\n'
            emitted, emitted_keystrokes = _emit_string(text, idx)

        elif cmd in _MODIFIER_TOKENS or cmd in _NAMED_KEYS:
            # Modifier combo or bare named key. Re-tokenise the WHOLE line
            # (cmd + arg) so 'CTRL ALT DELETE' / 'GUI r' parse correctly.
            tokens = stripped.split()
            emitted, emitted_keystrokes = _emit_key_combo(tokens, idx)

        else:
            raise DuckyParseError(f"unknown command {cmd!r}", idx)

        # Apply the inter-instruction default delay to the LAST report of
        # this instruction so playback paces correctly.
        if emitted and default_delay_ms > 0:
            rpt, _ = emitted[-1]
            emitted[-1] = (rpt, default_delay_ms)

        payload.reports.extend(emitted)
        payload.keystrokes += emitted_keystrokes
        last_emitted = list(emitted)
        last_keystrokes = emitted_keystrokes

    return payload


def sha256_source(source: str) -> str:
    """Stable sha256 of the DuckyScript source (used in audit + meta)."""
    return hashlib.sha256(source.encode('utf-8')).hexdigest()


def preview_lines(source: str, n: int = 1) -> Tuple[List[str], List[str]]:
    """Return (first_n, last_n) non-empty source lines for the ARM preview."""
    lines = [ln.strip() for ln in source.splitlines() if ln.strip()]
    if not lines:
        return [], []
    first = lines[:max(1, n)]
    last = lines[-max(1, n):]
    return first, last

"""systemd unit hygiene guards.

Two anti-patterns the audit found and this locks out of regressing:

1. Backgrounded ExecStartPost children (`ExecStartPost=... &`). systemd does
   NOT supervise a process a unit forks into the background — it is never
   restarted if it dies and is orphaned on stop. Each long-running process must
   be its own unit.
2. session_recorder.py double-run. It has its own drifter-session-recorder
   unit, so no OTHER unit may also launch it (that double-subscribes MQTT and
   duplicates recordings).
"""
import glob
import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVICES_DIR = os.path.join(REPO, "services")


def _exec_lines(text):
    return [l for l in text.splitlines() if l.startswith("Exec")]


def test_no_backgrounded_execstartpost_children():
    offenders = []
    for path in sorted(glob.glob(os.path.join(SERVICES_DIR, "*.service"))):
        with open(path) as f:
            text = f.read()
        for line in _exec_lines(text):
            # a trailing `&` (optionally before a closing quote) = fork-and-forget
            if re.search(r"&\s*['\"]?\s*$", line):
                offenders.append(f"{os.path.basename(path)}: {line.strip()}")
    assert not offenders, (
        "Backgrounded Exec* children are unsupervised — give each its own "
        "unit:\n  " + "\n  ".join(offenders)
    )


def test_session_recorder_launched_by_exactly_one_unit():
    launchers = []
    for path in sorted(glob.glob(os.path.join(SERVICES_DIR, "*.service"))):
        with open(path) as f:
            text = f.read()
        if any("session_recorder.py" in l for l in _exec_lines(text)):
            launchers.append(os.path.basename(path))
    assert launchers == ["drifter-session-recorder.service"], (
        "session_recorder.py must be launched by exactly its own unit, not "
        f"also by others (double-run). Launchers: {launchers}"
    )

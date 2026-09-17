#!/usr/bin/env python3
"""DRIFTER safe self-updater for the Raspberry Pi deployment.

Policy:
- follow only origin/main (overridable through environment);
- never rewrite a dirty checkout;
- update only by fast-forward;
- defer while live vehicle telemetry suggests the car is active;
- preflight the candidate before moving the checkout;
- deploy through the existing one-shot contract;
- automatically restore the previous commit if deployment fails.

This module is installed into /opt/drifter by install.sh. The git checkout it
updates defaults to /home/kali/drifter and can be overridden with DRIFTER_REPO.
"""
from __future__ import annotations

import fcntl
import json
import math
import os
import pwd
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(os.getenv("DRIFTER_REPO", "/home/kali/drifter"))
REPO_USER = os.getenv("DRIFTER_REPO_USER", "kali")
REMOTE = os.getenv("DRIFTER_UPDATE_REMOTE", "origin")
BRANCH = os.getenv("DRIFTER_UPDATE_BRANCH", "main")
STATE_PATH = Path(
    os.getenv("DRIFTER_UPDATE_STATE", "/opt/drifter/data/auto-update.json")
)
LOCK_PATH = Path(os.getenv("DRIFTER_UPDATE_LOCK", "/run/lock/drifter-update.lock"))
DEPLOY_TIMEOUT = max(180.0, float(os.getenv("DRIFTER_UPDATE_DEPLOY_TIMEOUT", "1200")))
TELEMETRY_PROBE_SECONDS = max(
    2.0, float(os.getenv("DRIFTER_UPDATE_TELEMETRY_PROBE_SEC", "8"))
)
RPM_ACTIVE_THRESHOLD = float(os.getenv("DRIFTER_UPDATE_ACTIVE_RPM", "300"))
SPEED_ACTIVE_THRESHOLD = float(os.getenv("DRIFTER_UPDATE_ACTIVE_SPEED", "1"))

RPM_TOPIC = "drifter/engine/rpm"
SPEED_TOPIC = "drifter/vehicle/speed"


def _utc() -> str:
    return datetime.now(UTC).isoformat()


def _save_state(payload: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"updated": _utc(), **payload}
    tmp = STATE_PATH.with_suffix(STATE_PATH.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, STATE_PATH)


def _run(
    argv: list[str],
    *,
    cwd: Path | None = None,
    timeout: float = 60.0,
    repo_user: bool = False,
) -> subprocess.CompletedProcess:
    command = list(argv)
    if repo_user and os.geteuid() == 0 and shutil.which("runuser"):
        try:
            pwd.getpwnam(REPO_USER)
        except KeyError:
            pass
        else:
            command = ["runuser", "-u", REPO_USER, "--", *command]
    try:
        return subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(command, 127, "", str(exc))


def _git(*args: str, timeout: float = 90.0) -> subprocess.CompletedProcess:
    return _run(
        ["git", "-c", f"safe.directory={REPO}", "-C", str(REPO), *args],
        timeout=timeout,
        repo_user=True,
    )


def _git_text(*args: str) -> str:
    result = _git(*args)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "git failed").strip())
    return result.stdout.strip()


def _telemetry_values(text: str) -> dict[str, float]:
    values: dict[str, float] = {}
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw or " " not in raw:
            continue
        topic, payload = raw.split(" ", 1)
        if topic not in {RPM_TOPIC, SPEED_TOPIC}:
            continue
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        value = data.get("value") if isinstance(data, dict) else data
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(numeric):
            values[topic] = numeric
    return values


def _vehicle_active() -> tuple[bool, dict[str, Any]]:
    if os.getenv("DRIFTER_AUTO_UPDATE_ALLOW_ACTIVE", "0") == "1":
        return False, {"bypass": True}
    if not shutil.which("mosquitto_sub"):
        return False, {"probe": "mosquitto_sub unavailable"}

    result = _run(
        [
            "mosquitto_sub",
            "-h",
            "127.0.0.1",
            "-v",
            "-t",
            RPM_TOPIC,
            "-t",
            SPEED_TOPIC,
            "-C",
            "2",
            "-W",
            str(int(TELEMETRY_PROBE_SECONDS)),
        ],
        timeout=TELEMETRY_PROBE_SECONDS + 3,
    )
    values = _telemetry_values(result.stdout)
    rpm = values.get(RPM_TOPIC)
    speed = values.get(SPEED_TOPIC)
    active = bool(
        (rpm is not None and rpm > RPM_ACTIVE_THRESHOLD)
        or (speed is not None and speed > SPEED_ACTIVE_THRESHOLD)
    )
    return active, {
        "rpm": rpm,
        "speed": speed,
        "probe_rc": result.returncode,
        "probe_seconds": TELEMETRY_PROBE_SECONDS,
    }


def _checkout_clean() -> bool:
    return not _git_text("status", "--porcelain")


def _current_branch() -> str:
    return _git_text("branch", "--show-current")


def _candidate_root() -> Path:
    path = Path(tempfile.mkdtemp(prefix="drifter-update-"))
    if os.geteuid() == 0:
        try:
            account = pwd.getpwnam(REPO_USER)
        except KeyError:
            return path
        os.chown(path, account.pw_uid, account.pw_gid)
    return path


def _preflight(target: str) -> tuple[bool, str]:
    diff = _git("diff", "--check", "HEAD", target)
    if diff.returncode != 0:
        return False, (diff.stderr or diff.stdout).strip()[:1500]

    tmp_root = _candidate_root()
    worktree = tmp_root / "candidate"
    added = False
    try:
        add = _git("worktree", "add", "--detach", str(worktree), target, timeout=120)
        if add.returncode != 0:
            return False, (add.stderr or add.stdout).strip()[:1500]
        added = True

        compile_result = _run(
            ["python3", "-m", "compileall", "-q", "src"],
            cwd=worktree,
            timeout=120,
        )
        if compile_result.returncode != 0:
            return False, (compile_result.stderr or compile_result.stdout).strip()[:1500]

        shell_result = _run(
            [
                "bash",
                "-n",
                "scripts/oneshot.sh",
                "scripts/deploy-cockpit-v4.sh",
                "scripts/post-deploy-check.sh",
                "install.sh",
            ],
            cwd=worktree,
            timeout=30,
        )
        if shell_result.returncode != 0:
            return False, (shell_result.stderr or shell_result.stdout).strip()[:1500]
        return True, "candidate compile + shell syntax + diff-check passed"
    finally:
        if added:
            _git("worktree", "remove", "--force", str(worktree), timeout=60)
        shutil.rmtree(tmp_root, ignore_errors=True)


def _deploy() -> subprocess.CompletedProcess:
    return _run(
        ["bash", str(REPO / "scripts" / "oneshot.sh"), "--skip-apt"],
        cwd=REPO,
        timeout=DEPLOY_TIMEOUT,
    )


def _rollback(previous: str) -> tuple[bool, str]:
    reset = _git("reset", "--hard", previous, timeout=90)
    if reset.returncode != 0:
        return False, f"git rollback failed: {(reset.stderr or reset.stdout).strip()[:1200]}"
    clean = _git("clean", "-fd", timeout=90)
    if clean.returncode != 0:
        return False, f"git rollback clean failed: {(clean.stderr or clean.stdout).strip()[:1200]}"
    deploy = _deploy()
    if deploy.returncode != 0:
        return False, (
            f"rollback deploy failed rc={deploy.returncode}: "
            f"{(deploy.stderr or deploy.stdout).strip()[-1200:]}"
        )
    return True, "previous commit restored, new files removed, and previous release redeployed"


def run_update() -> int:
    if os.getenv("DRIFTER_AUTO_UPDATE", "1") != "1":
        _save_state({"status": "disabled"})
        return 0
    if not (REPO / ".git").exists():
        _save_state({"status": "error", "error": f"repo missing: {REPO}"})
        return 1

    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0

        try:
            branch = _current_branch()
            if branch != BRANCH:
                _save_state(
                    {
                        "status": "deferred",
                        "reason": "checkout not on update branch",
                        "current_branch": branch,
                        "required_branch": BRANCH,
                    }
                )
                return 0
            if not _checkout_clean():
                _save_state({"status": "deferred", "reason": "repo has local changes"})
                return 0

            active, telemetry = _vehicle_active()
            if active:
                _save_state(
                    {
                        "status": "deferred",
                        "reason": "vehicle active",
                        "telemetry": telemetry,
                    }
                )
                return 0

            fetch = _git("fetch", "--prune", REMOTE, BRANCH, timeout=120)
            if fetch.returncode != 0:
                _save_state(
                    {
                        "status": "offline",
                        "reason": (fetch.stderr or fetch.stdout).strip()[:1000],
                    }
                )
                return 0

            previous = _git_text("rev-parse", "HEAD")
            target = _git_text("rev-parse", f"{REMOTE}/{BRANCH}")
            if previous == target:
                _save_state(
                    {
                        "status": "current",
                        "commit": previous,
                        "telemetry": telemetry,
                    }
                )
                return 0

            ancestor = _git("merge-base", "--is-ancestor", previous, target)
            if ancestor.returncode != 0:
                _save_state(
                    {
                        "status": "blocked",
                        "reason": "origin/main is not a fast-forward from local HEAD",
                        "current": previous,
                        "target": target,
                    }
                )
                return 1

            ok, detail = _preflight(target)
            if not ok:
                _save_state(
                    {
                        "status": "blocked",
                        "reason": "candidate preflight failed",
                        "detail": detail,
                        "current": previous,
                        "target": target,
                    }
                )
                return 1

            merge = _git("merge", "--ff-only", target, timeout=120)
            if merge.returncode != 0:
                _save_state(
                    {
                        "status": "error",
                        "reason": "fast-forward failed",
                        "detail": (merge.stderr or merge.stdout).strip()[:1000],
                        "current": previous,
                        "target": target,
                    }
                )
                return 1

            deploy = _deploy()
            if deploy.returncode == 0:
                _save_state(
                    {
                        "status": "updated",
                        "from": previous,
                        "to": target,
                        "preflight": detail,
                        "deploy_tail": deploy.stdout.strip()[-2000:],
                    }
                )
                return 0

            rollback_ok, rollback_detail = _rollback(previous)
            _save_state(
                {
                    "status": "rolled_back" if rollback_ok else "rollback_failed",
                    "from": previous,
                    "failed_target": target,
                    "deploy_rc": deploy.returncode,
                    "deploy_tail": (deploy.stderr or deploy.stdout).strip()[-2000:],
                    "rollback_ok": rollback_ok,
                    "rollback_detail": rollback_detail,
                }
            )
            return 1
        except Exception as exc:
            _save_state({"status": "error", "error": f"{type(exc).__name__}: {exc}"})
            return 1


def main() -> int:
    return run_update()


if __name__ == "__main__":
    raise SystemExit(main())

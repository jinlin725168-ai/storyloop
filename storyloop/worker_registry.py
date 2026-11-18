"""Worker registry and helpers shared by `workers` and `worker_land` (roadmap H)."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

from .models import StoryLoopError
from .storage import load_json, write_json

REGISTRY = Path(".storyloop/state/workers")
# Copied from the project into a worker; the rest of the control plane is per change or transient.
NOT_COPIED = {"state/workers", "state/touches", "run.lock", "worker.log"}


def _copy_control_plane(source: Path, target: Path) -> None:
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        if path.is_symlink() or any(relative.as_posix() == item or relative.as_posix().startswith(item + "/")
                                    for item in NOT_COPIED):
            continue
        destination = target / relative
        if path.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)


def _entries(project: Path) -> dict[str, dict]:
    directory = project / REGISTRY
    entries = {}
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        try:
            value = load_json(path)
        except (OSError, ValueError, StoryLoopError):
            continue
        if isinstance(value, dict) and value.get("version") == 1:
            entries[value["change_id"]] = value
    return entries


def _entry_path(project: Path, change_id: str) -> Path:
    return project / REGISTRY / f"{change_id}.json"


def save_entry(project: Path, entry: dict) -> None:
    write_json(_entry_path(project, entry["change_id"]), entry, max_bytes=64_000)


def _worker_status(target: Path, change_id: str) -> str:
    run = target / ".storyloop/state/runs" / f"{change_id}.json"
    try:
        if run.is_file():
            return load_json(run).get("status") or "UNKNOWN"
        return load_json(target / ".storyloop/state/changes" / f"{change_id}.json").get("status") or "UNKNOWN"
    except (OSError, ValueError, StoryLoopError):
        return "UNKNOWN"


def _alive(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        found = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True)
        return str(pid) in found.stdout
    try:
        waited, _ = os.waitpid(pid, os.WNOHANG)  # reap our own finished child, if it is one
        if waited == pid:
            return False
    except ChildProcessError:
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
